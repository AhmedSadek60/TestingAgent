#!/usr/bin/env python3
"""Validate the AI governance foundation. Standard library only.

Usage: python scripts/ai/validate_governance.py [--root PATH]
Exit status: 0 = OK (warnings allowed), 1 = governance errors found.
It never executes commands from .ai/project.json.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

AGENTS_MAX_LINES = 200

REQUIRED_FILES = [
    "AGENTS.md", "README.md", "CONTRIBUTING.md", "SECURITY.md",
    ".gitignore", ".editorconfig",
    ".ai/README.md", ".ai/project.json",
    ".ai/policies/security.md", ".ai/policies/git.md", ".ai/policies/testing.md",
    ".ai/policies/change-control.md", ".ai/policies/documentation.md",
    ".ai/policies/collaboration.md",
    ".ai/workflows/task-execution.md", ".ai/workflows/feature-development.md",
    ".ai/workflows/bug-fixing.md", ".ai/workflows/refactoring.md",
    ".ai/workflows/code-review.md", ".ai/workflows/incident-response.md",
    ".ai/templates/implementation-plan.md", ".ai/templates/adr.md",
    ".ai/templates/handoff.md", ".ai/templates/investigation.md",
    "docs/architecture/README.md", "docs/decisions/README.md",
    "docs/development/README.md",
    "scripts/ai/validate_governance.py",
    ".github/pull_request_template.md", ".github/CODEOWNERS.example",
    ".github/ISSUE_TEMPLATE/bug.yml", ".github/ISSUE_TEMPLATE/feature.yml",
    ".github/ISSUE_TEMPLATE/task.yml", ".github/ISSUE_TEMPLATE/ai-assisted-task.yml",
    ".github/workflows/ai-governance.yml",
]

AGENTS_HEADINGS = [
    "Purpose", "Scope", "Source of truth", "Repository discovery",
    "Task execution workflow", "Change management", "Testing and verification",
    "Git safety", "Security", "Dependencies", "Documentation", "Collaboration",
    "AI-agent behavior", "Completion and reporting",
]

PROJECT_JSON_KEYS = {
    "templateMode": bool,
    "project": dict,
    "technology": dict,
    "commands": dict,
    "architecture": dict,
}
COMMAND_KEYS = ["install", "build", "test", "lint", "format", "typecheck", "verify"]

# Root-level files that duplicate agent policy. Allowed only as thin adapters:
# short and explicitly pointing at AGENTS.md.
VENDOR_POLICY_FILES = [
    "CLAUDE.md", "GEMINI.md", ".cursorrules", ".windsurfrules", ".clinerules",
    ".kilocoderules", ".github/copilot-instructions.md",
]
VENDOR_POLICY_DIRS = [".cursor/rules", ".kiro/steering", ".kilo/rules", ".kilocode/rules",
                      ".windsurf/rules", ".clinerules"]
ADAPTER_MAX_LINES = 15

# Obvious secret shapes only (low false-positive). Built from fragments so this file
# does not match itself.
SECRET_PATTERNS = {
    "private key block": re.compile("-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE" + " KEY-----"),
    "AWS access key id": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "GitHub fine-grained token": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}\b"),
    "Slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "generic API key (sk-)": re.compile(r"\bsk-[A-Za-z0-9_\-]{32,}\b"),
}

SCAN_DIRS = [".ai", "docs", ".github"]
SCAN_ROOT_FILES = ["AGENTS.md", "README.md", "CONTRIBUTING.md", "SECURITY.md", ".gitignore"]
SKIP_SCAN = {"scripts/ai/validate_governance.py"}

errors: list[str] = []
warnings: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def check_required_files(root: Path) -> None:
    for rel in REQUIRED_FILES:
        p = root / rel
        if not p.is_file():
            err(f"Missing required file: {rel}")
        elif p.stat().st_size == 0:
            err(f"Required file is empty: {rel}")


def check_agents(root: Path) -> None:
    p = root / "AGENTS.md"
    if not p.is_file():
        return
    text = read(p)
    n = len(text.splitlines())
    if n > AGENTS_MAX_LINES:
        err(f"AGENTS.md has {n} lines (limit {AGENTS_MAX_LINES}). Move detail into .ai/ or docs/.")
    headings = [re.sub(r"^\d+\.\s*", "", h.strip()).lower()
                for h in re.findall(r"^##\s+(.+)$", text, flags=re.M)]
    for required in AGENTS_HEADINGS:
        if not any(h.startswith(required.lower()) for h in headings):
            err(f"AGENTS.md is missing required section heading: '{required}'")


def check_project_json(root: Path) -> None:
    p = root / ".ai/project.json"
    if not p.is_file():
        return
    try:
        data = json.loads(read(p))
    except json.JSONDecodeError as e:
        err(f".ai/project.json is not valid JSON: {e}")
        return
    if not isinstance(data, dict):
        err(".ai/project.json must be a JSON object")
        return
    for key, typ in PROJECT_JSON_KEYS.items():
        if key not in data:
            err(f".ai/project.json missing key: '{key}'")
        elif not isinstance(data[key], typ):
            err(f".ai/project.json key '{key}' must be of type {typ.__name__}")
    cmds = data.get("commands")
    if isinstance(cmds, dict):
        for k in COMMAND_KEYS:
            if k not in cmds:
                err(f".ai/project.json commands missing key: '{k}'")
            elif cmds[k] is not None and not isinstance(cmds[k], str):
                err(f".ai/project.json commands.{k} must be a string or null")
    if data.get("templateMode") is False:
        proj = data.get("project", {})
        if isinstance(proj, dict) and str(proj.get("name", "TBD")).strip().upper() in ("", "TBD"):
            err("templateMode is false but project.name is still TBD. Fill in project metadata.")
    elif data.get("templateMode") is True:
        print("info: templateMode is true (template state). Set it to false when adopting for a real project.")


def check_structure(root: Path) -> None:
    for d in [".ai/policies", ".ai/workflows", ".ai/templates", "docs/architecture",
              "docs/decisions", "docs/development", "scripts/ai", ".github/ISSUE_TEMPLATE",
              ".github/workflows"]:
        if not (root / d).is_dir():
            err(f"Missing required directory: {d}/")
    if (root / ".github/CODEOWNERS").is_file():
        text = read(root / ".github/CODEOWNERS")
        if re.search(r"@(?:ORG|YOUR[-_]?ORG|USERNAME|TEAM)\b", text, flags=re.I):
            err(".github/CODEOWNERS still contains placeholder owners (e.g. @ORG/...).")


def check_vendor_files(root: Path) -> None:
    for rel in VENDOR_POLICY_FILES:
        p = root / rel
        if p.is_file():
            check_adapter(rel, read(p))
    for rel in VENDOR_POLICY_DIRS:
        d = root / rel
        if d.is_file():
            continue  # e.g. .clinerules as a file is handled above
        if d.is_dir():
            for f in sorted(x for x in d.rglob("*") if x.is_file()):
                check_adapter(str(f.relative_to(root)), read(f))


def check_adapter(rel: str, text: str) -> None:
    lines = len(text.splitlines())
    if "AGENTS.md" not in text:
        err(f"{rel}: vendor-specific policy file does not reference AGENTS.md. "
            "Adapters must be thin pointers to the canonical policy.")
    elif lines > ADAPTER_MAX_LINES:
        err(f"{rel}: {lines} lines. Vendor adapters must be thin (<= {ADAPTER_MAX_LINES} lines) "
            "and must not duplicate AGENTS.md.")
    else:
        warn(f"{rel}: vendor adapter present; ensure it stays a thin pointer and is reviewed.")


def iter_scan_files(root: Path):
    for name in SCAN_ROOT_FILES:
        p = root / name
        if p.is_file():
            yield p
    for d in SCAN_DIRS:
        base = root / d
        if base.is_dir():
            for p in sorted(base.rglob("*")):
                if p.is_file():
                    yield p


def check_secrets(root: Path) -> None:
    for p in iter_scan_files(root):
        rel = str(p.relative_to(root))
        if rel in SKIP_SCAN:
            continue
        text = read(p)
        for label, pat in SECRET_PATTERNS.items():
            if pat.search(text):
                err(f"{rel}: possible secret detected ({label}). Remove it and rotate the credential.")


def check_workflow(root: Path) -> None:
    p = root / ".github/workflows/ai-governance.yml"
    if not p.is_file():
        return
    text = read(p)
    if "\t" in text:
        err("ai-governance.yml contains tab characters (invalid YAML indentation).")
    for needle in ("on:", "jobs:", "permissions:", "validate_governance.py"):
        if needle not in text:
            err(f"ai-governance.yml missing expected content: '{needle}'")
    if re.search(r"^\s+\w[\w-]*:\s*write\b", text, flags=re.M):
        err("ai-governance.yml grants a write permission; governance validation needs read-only.")


LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")


def check_links(root: Path) -> None:
    md_files = [p for p in iter_scan_files(root) if p.suffix == ".md"]
    for p in md_files:
        text = re.sub(r"<!--.*?-->", "", read(p), flags=re.S)
        text = re.sub(r"```.*?```", "", text, flags=re.S)
        text = re.sub(r"`[^`\n]*`", "", text)
        for target in LINK_RE.findall(text):
            if re.match(r"^(?:[a-z]+:|#|<)", target, flags=re.I):
                continue
            path = target.split("#", 1)[0]
            if not path:
                continue
            resolved = (root / path.lstrip("/")) if path.startswith("/") else (p.parent / path)
            if not resolved.exists():
                err(f"{p.relative_to(root)}: broken relative link -> {target}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=None, help="repository root (default: auto-detect)")
    args = ap.parse_args()
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[2]

    check_required_files(root)
    check_structure(root)
    check_agents(root)
    check_project_json(root)
    check_vendor_files(root)
    check_secrets(root)
    check_workflow(root)
    check_links(root)

    for w in warnings:
        print(f"warning: {w}")
    if errors:
        print(f"\nGovernance validation FAILED with {len(errors)} error(s):")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"Governance validation passed ({len(warnings)} warning(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
