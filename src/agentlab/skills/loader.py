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

from agentlab.core.models import AssertionSpec, TestCase
from agentlab.core.models.base import Model
from agentlab.core.models.testcase import BrowserStep, ExpectedToolCall, JudgeCriterion, Turn
from agentlab.skills.model import REQUIRED_DOC_SECTIONS, UNTRUSTED, Skill, SkillManifest, TestTemplate

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


# The nested structures of a test, and the model each one becomes. Their keys are checked before a run, because a
# misspelt key, or an assertion's settings written beside ``type`` instead of under ``params``, would otherwise only
# show up as a skill that quietly yields no tests.
NESTED_TEST_FIELDS: dict[str, type[Model]] = {
    "assertions": AssertionSpec,
    "turns": Turn,
    "judge": JudgeCriterion,
    "expected_tool_calls": ExpectedToolCall,
    "browser_steps": BrowserStep,
}


# Fields of a test that AgentLab fills in itself, so a template that sets one would be silently ignored.
SET_BY_AGENTLAB = {
    "id": "built from the skill's id_prefix and the template's id",
    "rationale": "built from the template's 'reasons'",
    "skill": "the skill's own name",
    "skill_version": "the skill's own version",
    "status": "set by the planner",
}


def _has_placeholder(value: Any) -> bool:
    """Whether a template value contains ``[[ ... ]]`` / ``[% ... %]``, i.e. is only known once a plan is made."""
    if isinstance(value, str):
        return "[[" in value or "[%" in value or "[#" in value
    if isinstance(value, dict):
        return any(_has_placeholder(k) or _has_placeholder(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_placeholder(v) for v in value)
    return False


def _item_problems(label: str, model: type[Model], item: Any) -> list[str]:
    """What is wrong with one nested item of a template's test, without rendering it."""
    if _has_placeholder(item) and not isinstance(item, dict):
        return []  # the whole item is a placeholder: only its rendered value can be judged
    if not isinstance(item, dict):
        return [f"{label}: must be a mapping, not {type(item).__name__}"]
    problems: list[str] = []
    unknown = sorted(k for k in item if isinstance(k, str) and not _has_placeholder(k) and k not in model.model_fields)
    if unknown:
        hint = ""
        if model is AssertionSpec:
            hint = " (the settings of an assertion go under 'params', for example {type: contains, params: {value: x}})"
        problems.append(f"{label}: unknown keys {unknown}; allowed: {sorted(model.model_fields)}{hint}")
    if model is AssertionSpec:
        from agentlab.evaluation.assertions import ASSERTIONS

        kind = item.get("type")
        if kind is None:
            problems.append(f"{label}: missing 'type'")
        elif isinstance(kind, str) and not _has_placeholder(kind) and kind not in ASSERTIONS.names():
            problems.append(
                f"{label}: unknown assertion type '{kind}' (see docs/evaluation.md for the {len(ASSERTIONS.names())} kinds)"
            )
        params = item.get("params")
        if params is not None and not isinstance(params, dict) and not _has_placeholder(params):
            problems.append(f"{label}: 'params' must be a mapping")
    if not problems and not _has_placeholder(item):
        try:
            model.model_validate(item)
        except ValidationError as exc:
            first = exc.errors()[0]
            problems.append(f"{label}: {'.'.join(map(str, first['loc'])) or 'value'}: {first['msg']}")
    return problems


def _template_problems(t: TestTemplate) -> list[str]:
    problems: list[str] = []
    for field_name, model in NESTED_TEST_FIELDS.items():
        value = t.test.get(field_name)
        if value is None:
            continue
        if not isinstance(value, list):
            if not _has_placeholder(value):
                problems.append(f"template '{t.id}': '{field_name}' must be a list")
            continue
        for n, item in enumerate(value, 1):
            label = f"template '{t.id}': {field_name}[{n}]"
            problems += _item_problems(label, model, item)
            if field_name == "turns" and isinstance(item, dict) and isinstance(item.get("assertions"), list):
                for m, inner in enumerate(item["assertions"], 1):
                    problems += _item_problems(f"{label}.assertions[{m}]", AssertionSpec, inner)
    return problems


def validate_templates(manifest: SkillManifest) -> list[str]:
    """Static checks so a broken template is reported at load time rather than failing mid-run."""
    problems: list[str] = []
    fields = set(TestCase.model_fields)
    seen: set[str] = set()
    for t in manifest.templates:
        if t.id in seen:
            problems.append(f"template id '{t.id}' is duplicated")
        seen.add(t.id)
        unknown = sorted(set(t.test) - fields)
        if unknown:
            problems.append(f"template '{t.id}': unknown test fields {unknown}")
        for key, reason in SET_BY_AGENTLAB.items():
            if key in t.test:
                problems.append(f"template '{t.id}': '{key}' cannot be set by a template ({reason})")
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
        problems += _template_problems(t)
    return problems


def discover_skill_dirs(root: Path) -> list[Path]:
    root = Path(root)
    if (root / "skill.yaml").is_file():
        return [root]
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / "skill.yaml").is_file()) if root.is_dir() else []
