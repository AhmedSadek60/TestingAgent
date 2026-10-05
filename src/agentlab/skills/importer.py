"""Importing third-party skills as *untrusted drafts*.

A third-party skill file (a ``SKILL.md`` from a repository, a gist, another tool's skill directory) is
potentially hostile input. The importer therefore never executes or interprets it. It reads text only (size
capped, no binaries, no symlinks), scans it for injection and dangerous-command indicators, records the license
and origin and writes a **draft** skill in AgentLab's own format whose methodology holds the original text
inside a quoted block. A human then adapts the useful knowledge and promotes the skill; nothing imported is ever
selected automatically (see ``docs/decisions/0003-skills-trust-model.md``).
"""

from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field

from agentlab.core.errors import UserError
from agentlab.core.models.base import Model
from agentlab.security.redactor import get_redactor
from agentlab.security.untrusted import injection_indicators
from agentlab.skills.loader import load_skill_dir
from agentlab.skills.model import NAME, REQUIRED_DOC_SECTIONS

MAX_FILE_BYTES = 200_000
MAX_FILES = 40
TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".yaml", ".yml", ".json", ".toml"}
EXECUTABLE_SUFFIXES = {".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd", ".py", ".js", ".ts", ".rb", ".pl", ".exe", ".so"}
INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤﻿]")
DANGEROUS = [
    ("pipes a download into a shell", re.compile(r"(curl|wget)[^\n|]*\|\s*(sudo\s+)?(ba|z)?sh", re.I)),
    ("recursive delete", re.compile(r"\brm\s+-rf?\s+(/|~|\$HOME)", re.I)),
    ("reads credential files", re.compile(r"(~/\.ssh|id_rsa|\.aws/credentials|\.npmrc|\.env\b)", re.I)),
    (
        "asks the agent to disable safety",
        re.compile(r"(disable|bypass|ignore)\s+(all\s+)?(safety|guardrails?|permissions?)", re.I),
    ),
    ("hidden instruction block", re.compile(r"<\s*(important|system|secret)\s*>", re.I)),
]
LICENSES = [
    ("MIT", re.compile(r"\bMIT License\b|Permission is hereby granted, free of charge", re.I)),
    ("Apache-2.0", re.compile(r"Apache License,? Version 2\.0", re.I)),
    ("BSD", re.compile(r"\bBSD[- ]?\d?-?Clause\b|Redistribution and use in source and binary forms", re.I)),
    ("GPL", re.compile(r"GNU (?:Lesser |Affero )?General Public License", re.I)),
    ("CC-BY", re.compile(r"Creative Commons", re.I)),
]
MARKER = "IMPORTED-UNREVIEWED"


class ImportReport(Model):
    name: str
    source: str
    destination: str | None = None
    license: str = "unknown"
    files_read: list[str] = Field(default_factory=list)
    files_ignored: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    secrets_redacted: int = 0
    blocked: bool = False
    block_reason: str | None = None

    @property
    def risky(self) -> bool:
        return bool(self.warnings)


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s if NAME.match(s) else "imported-skill"


def _detect_license(texts: dict[str, str]) -> str:
    for name, text in texts.items():
        if re.search(r"licen[sc]e", Path(name).name, re.I):
            for spdx, rx in LICENSES:
                if rx.search(text):
                    return spdx
            return "unrecognised"
    for text in texts.values():
        m = re.search(r"^license:\s*([A-Za-z0-9.\- ]+)\s*$", text, re.M | re.I)
        if m:
            return m.group(1).strip()
    return "unknown"


def _collect(source: Path, report: ImportReport) -> dict[str, str]:
    texts: dict[str, str] = {}
    files = [source] if source.is_file() else sorted(p for p in source.rglob("*") if p.is_file() or p.is_symlink())
    for p in files[: MAX_FILES + 10]:
        rel = p.name if source.is_file() else str(p.relative_to(source))
        if p.is_symlink():
            report.files_ignored.append(f"{rel} (symbolic link)")
            continue
        if p.suffix.lower() in EXECUTABLE_SUFFIXES:
            report.files_ignored.append(f"{rel} (executable or script content is never imported)")
            continue
        if p.suffix.lower() not in TEXT_SUFFIXES and not re.search(r"licen[sc]e|readme", p.name, re.I):
            report.files_ignored.append(f"{rel} (not a text file)")
            continue
        if len(texts) >= MAX_FILES:
            report.files_ignored.append(f"{rel} (file limit reached)")
            continue
        try:
            raw = p.read_bytes()
        except OSError as exc:
            report.files_ignored.append(f"{rel} (unreadable: {exc})")
            continue
        if len(raw) > MAX_FILE_BYTES:
            report.files_ignored.append(f"{rel} (larger than {MAX_FILE_BYTES} bytes)")
            continue
        if b"\x00" in raw:
            report.files_ignored.append(f"{rel} (binary content)")
            continue
        texts[rel] = raw.decode("utf-8", errors="replace")
        report.files_read.append(rel)
    return texts


def scan(texts: dict[str, str], report: ImportReport) -> None:
    for rel, text in texts.items():
        hits = injection_indicators(text)
        if hits:
            report.warnings.append(f"{rel}: contains instruction-like text aimed at an AI ({len(hits)} indicator(s))")
        for label, rx in DANGEROUS:
            if rx.search(text):
                report.warnings.append(f"{rel}: {label}")
        if INVISIBLE.search(text):
            report.warnings.append(f"{rel}: contains invisible or bidirectional control characters")
    if report.license in {"unknown", "unrecognised"}:
        report.warnings.append("no recognisable license: do not redistribute the imported text")
    if report.license.startswith("GPL"):
        report.warnings.append("copyleft license: review before combining with Apache-2.0 content")


def _frontmatter(text: str) -> tuple[dict[str, object], str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}, text
    return (data if isinstance(data, dict) else {}), m.group(2)


def import_skill(
    source: str | Path, dest_root: str | Path, *, name: str | None = None, origin: str | None = None
) -> ImportReport:
    """Create a draft skill under ``dest_root/<name>`` from a third-party skill file or directory."""
    src = Path(source)
    if not src.exists():
        raise UserError(f"skill source not found: {src}")
    report = ImportReport(name=name or "", source=origin or str(src))
    texts = _collect(src, report)
    if not texts:
        report.blocked, report.block_reason = True, "no importable text files were found"
        return report
    main_key = next((k for k in texts if Path(k).name.lower() == "skill.md"), None) or next(
        (k for k in texts if k.lower().endswith((".md", ".markdown"))), None
    )
    if main_key is None:
        report.blocked, report.block_reason = True, "no Markdown skill document was found"
        return report
    meta, body = _frontmatter(texts[main_key])
    report.name = _slug(name or str(meta.get("name") or (src.stem if src.is_file() else src.name)))
    report.license = _detect_license(texts)
    scan(texts, report)
    redacted, hits = get_redactor().redact_text(body)
    report.secrets_redacted = sum(hits.values()) if isinstance(hits, dict) else 0
    if report.secrets_redacted:
        report.warnings.append(f"{report.secrets_redacted} secret-like value(s) were redacted from the imported text")
    description = str(meta.get("description") or "Imported third-party skill (unreviewed).")[:300]
    dest = Path(dest_root) / report.name
    if dest.exists():
        raise UserError(f"{dest} already exists; choose another name or remove it")
    dest.mkdir(parents=True)
    manifest: dict[str, Any] = {
        "name": report.name,
        "version": "0.1.0",
        "title": str(meta.get("title") or report.name.replace("-", " ").title()),
        "description": description,
        "kind": "tests",
        "status": "draft",
        "category": "imported",
        "applicability": {"always": False},
        # the import date lives in SKILL.md: the manifest model only accepts origin, license and sources
        "provenance": {"origin": origin or str(src), "license": report.license, "sources": [origin or str(src)]},
        "limitations": [
            "Imported draft: not reviewed, no tests defined. A human must adapt it before it can be promoted."
        ],
    }
    (dest / "skill.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")
    quoted = "\n".join(f"> {line}" if line.strip() else ">" for line in redacted.strip().splitlines())
    sections = "\n".join(
        f"## {s}\n\nTODO (human review): adapt from the quoted source below.\n" for s in REQUIRED_DOC_SECTIONS
    )
    warn = "\n".join(f"- {w}" for w in report.warnings) or "- none"
    (dest / "SKILL.md").write_text(
        f"# {manifest['title']}\n\n"
        f"**{MARKER}** — this skill was imported from `{report.source}` on {datetime.now(UTC).date()} "
        f"(license: {report.license}). It is an untrusted draft: nothing below is executed, followed or selected.\n\n"
        f"### Import warnings\n\n{warn}\n\n{sections}\n"
        f"## Original text (quoted, unreviewed)\n\n{quoted}\n",
        encoding="utf-8",
    )
    report.destination = str(dest)
    return report


def promote_skill(draft_dir: str | Path, dest_root: str | Path, *, reviewer: str) -> Path:
    """Copy a reviewed draft into a *local* skills directory. Refuses while the draft is unreviewed or invalid."""
    src = Path(draft_dir)
    skill = load_skill_dir(src, trust="local")
    if MARKER in skill.doc:
        raise UserError(f"{src.name} still carries the {MARKER} marker; review and remove it first")
    if "TODO (human review)" in skill.doc:
        raise UserError(f"{src.name} still has TODO sections")
    if skill.problems:
        raise UserError(f"{src.name} is not valid: {'; '.join(skill.problems[:3])}")
    if not reviewer.strip():
        raise UserError("a reviewer name is required to promote a skill")
    dest = Path(dest_root) / src.name
    if dest.exists():
        raise UserError(f"{dest} already exists")
    shutil.copytree(src, dest)
    data = yaml.safe_load((dest / "skill.yaml").read_text(encoding="utf-8")) or {}
    data["status"] = "stable" if data.get("status") == "draft" else data.get("status", "stable")
    data.setdefault("provenance", {})["author"] = f"reviewed by {reviewer}"
    (dest / "skill.yaml").write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return dest


__all__ = ["MARKER", "ImportReport", "import_skill", "promote_skill", "scan"]
