"""Loading and validating skill directories.

A skill directory contains ``skill.yaml`` and ``SKILL.md``. The loader never executes anything from
the directory: YAML is parsed with ``safe_load``, size limits apply, and a ``generator`` (Python) is
only honoured for built-in and plug-in skills. Everything else is declarative data.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from agentlab.core.models import TestCase
from agentlab.skills.model import REQUIRED_DOC_SECTIONS, UNTRUSTED, Skill, SkillManifest
from agentlab.skills.templating import TemplateRenderer

MAX_MANIFEST_BYTES = 256 * 1024
MAX_DOC_BYTES = 256 * 1024
TRUSTED_GENERATOR_PREFIX = "agentlab.skills.builtin."
FOREACH_SOURCES = {
    "tools",
    "facts",
    "documents",
    "requirements",
    "conflicts",
    "side_effect_tools",
    "outbound_tools",
    "destructive_tools",
    "read_tools",
}


def _hash(*parts: bytes) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(hashlib.sha256(p).digest())
    return h.hexdigest()


def doc_sections(markdown: str) -> set[str]:
    return {m.group(1).strip().lower() for m in re.finditer(r"^#{1,3}\s+(.+?)\s*$", markdown, re.M)}


def load_skill_dir(path: Path, *, trust: str = "local", require_doc: bool = True) -> Skill:
    """Load one skill. Problems are recorded on the skill (never raised) so a bad skill cannot break a run."""
    path = Path(path)
    problems: list[str] = []
    manifest_path = path / "skill.yaml"
    doc_path = path / "SKILL.md"
    placeholder = SkillManifest(
        name="invalid-skill", version="0.0.0", title="invalid", description="invalid", status="deprecated", trust=trust
    )
    if not manifest_path.is_file():
        return Skill(manifest=placeholder, source=str(path), problems=[f"{path.name}: skill.yaml is missing"])
    raw = manifest_path.read_bytes()
    if len(raw) > MAX_MANIFEST_BYTES:
        return Skill(
            manifest=placeholder,
            source=str(path),
            problems=[f"{path.name}: skill.yaml exceeds {MAX_MANIFEST_BYTES} bytes"],
        )
    try:
        data: Any = yaml.safe_load(raw.decode("utf-8")) or {}
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        return Skill(manifest=placeholder, source=str(path), problems=[f"{path.name}: invalid YAML ({exc})"])
    if not isinstance(data, dict):
        return Skill(manifest=placeholder, source=str(path), problems=[f"{path.name}: skill.yaml must be a mapping"])
    data = dict(data)
    data["trust"] = trust  # a manifest can never grant itself trust
    try:
        manifest = SkillManifest.model_validate(data)
    except ValidationError as exc:
        msg = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:6])
        return Skill(manifest=placeholder, source=str(path), problems=[f"{path.name}: invalid manifest ({msg})"])

    doc = ""
    if doc_path.is_file():
        doc_raw = doc_path.read_bytes()
        if len(doc_raw) > MAX_DOC_BYTES:
            problems.append(f"SKILL.md exceeds {MAX_DOC_BYTES} bytes")
        else:
            doc = doc_raw.decode("utf-8", errors="replace")
    elif require_doc:
        problems.append("SKILL.md is missing")
    if doc and require_doc:
        present = doc_sections(doc)
        missing = [s for s in REQUIRED_DOC_SECTIONS if s.lower() not in present]
        if missing and trust not in UNTRUSTED:
            problems.append(f"SKILL.md lacks required sections: {missing}")
    if path.name != manifest.name and trust == "builtin":
        problems.append(f"directory name '{path.name}' differs from skill name '{manifest.name}'")

    if manifest.generator:
        if trust in {"builtin", "plugin"}:
            if trust == "builtin" and not manifest.generator.startswith(TRUSTED_GENERATOR_PREFIX):
                problems.append(f"built-in generators must live under {TRUSTED_GENERATOR_PREFIX}*")
            if ":" not in manifest.generator:
                problems.append("generator must be 'module:function'")
        else:
            problems.append(
                "Python generators are only allowed for built-in and plug-in skills; "
                "use declarative 'templates' instead"
            )
    if manifest.kind == "tests" and not manifest.generator and not manifest.templates:
        problems.append("a 'tests' skill needs a generator or at least one template")
    problems += validate_templates(manifest)
    return Skill(manifest=manifest, doc=doc, source=str(path), content_hash=_hash(raw, doc.encode()), problems=problems)


def validate_templates(manifest: SkillManifest) -> list[str]:
    """Static checks so a broken template is reported at load time rather than failing mid-run."""
    problems: list[str] = []
    fields = set(TestCase.model_fields)
    seen: set[str] = set()
    probe = TemplateRenderer({"profile": None, "tools": [], "item": None, "ctx": None})
    for t in manifest.templates:
        if t.id in seen:
            problems.append(f"template id '{t.id}' is duplicated")
        seen.add(t.id)
        unknown = sorted(set(t.test) - fields)
        if unknown:
            problems.append(f"template '{t.id}': unknown test fields {unknown}")
        for req in ("name", "objective"):
            if req not in t.test:
                problems.append(f"template '{t.id}': missing '{req}'")
        if t.foreach and t.foreach not in FOREACH_SOURCES:
            problems.append(f"template '{t.id}': foreach must be one of {sorted(FOREACH_SOURCES)}")
        if t.when:
            try:
                from agentlab.skills.templating import _ENV

                _ENV.compile_expression(t.when.strip().removeprefix("[[").removesuffix("]]").strip())
            except Exception as exc:
                problems.append(f"template '{t.id}': invalid 'when' expression ({exc})")
        _ = probe
    return problems


def discover_skill_dirs(root: Path) -> list[Path]:
    root = Path(root)
    if (root / "skill.yaml").is_file():
        return [root]
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / "skill.yaml").is_file()) if root.is_dir() else []
