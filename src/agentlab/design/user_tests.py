"""User-authored test cases (spec section 7: the owner can add business-critical scenarios).

A file is YAML or JSON holding a list of tests (or ``{tests: [...]}``). Every field of :class:`TestCase` is accepted;
three shorthands make the common cases short::

    - name: Refund policy is quoted correctly
      input: How many days do I have to return an item?
      must_contain: ["30 days"]
      must_not_contain: ["60 days"]
      severity_on_failure: high

Nothing in a test file is executed: assertion types must exist in the evaluator registry, and a test the model
rejects is reported and skipped instead of aborting the plan.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from agentlab.core.models import TestCase
from agentlab.evaluation.assertions import ASSERTIONS

MAX_FILE_BYTES = 1_000_000
MAX_TESTS = 500


def _slug(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", text.upper()).strip("-")[:24].strip("-") or "TEST"


def _expand(raw: dict[str, Any], n: int) -> dict[str, Any]:
    d = dict(raw)
    asserts = list(d.pop("assertions", []) or [])
    for text in d.pop("must_contain", []) or []:
        asserts.append({"type": "contains", "params": {"text": str(text), "case_sensitive": False}})
    for text in d.pop("must_not_contain", []) or []:
        asserts.append({"type": "not_contains", "params": {"text": str(text), "case_sensitive": False}})
    for rx in d.pop("must_match", []) or []:
        asserts.append({"type": "regex", "params": {"pattern": str(rx)}})
    asserts = asserts or ([{"type": "not_empty"}] if not d.get("judge") else [])
    d["assertions"] = asserts
    d.setdefault("category", "functional")
    d.setdefault("objective", str(d.get("name", "")))
    d.setdefault("rationale", "Written by the target's owner as a scenario that must keep working.")
    d.setdefault("id", f"USER-{_slug(str(d.get('name', 'test')))}-{n:03d}")
    d["tags"] = list(dict.fromkeys([*(d.get("tags") or []), "user-defined"]))
    d.setdefault("skill", "user-defined")
    d.setdefault("skill_version", "1")
    return d


def load_user_tests(paths: Iterable[str | Path]) -> tuple[list[TestCase], list[str]]:
    """Load tests from files; returns ``(tests, problems)``. Invalid tests are skipped and reported."""
    tests: list[TestCase] = []
    problems: list[str] = []
    seen: set[str] = set()
    for path in paths:
        p = Path(path)
        if not p.is_file():
            problems.append(f"{p}: file not found")
            continue
        if p.stat().st_size > MAX_FILE_BYTES:
            problems.append(f"{p}: larger than {MAX_FILE_BYTES} bytes")
            continue
        try:
            text = p.read_text(encoding="utf-8")
            data = json.loads(text) if p.suffix.lower() == ".json" else yaml.safe_load(text)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            problems.append(f"{p}: cannot be read ({type(exc).__name__}: {exc})")
            continue
        items = data.get("tests") if isinstance(data, dict) else data
        if not isinstance(items, list):
            problems.append(f"{p}: expected a list of tests (or a 'tests:' list)")
            continue
        for n, raw in enumerate(items[:MAX_TESTS], 1):
            label = f"{p.name}[{n}]"
            if not isinstance(raw, dict) or not raw.get("name"):
                problems.append(f"{label}: each test must be a mapping with at least a 'name'")
                continue
            try:
                test = TestCase(**_expand(raw, n))
            except Exception as exc:
                problems.append(f"{label}: {str(exc).splitlines()[0][:200]}")
                continue
            unknown = {
                a.type
                for a in [*test.assertions, *(x for t in test.turns for x in t.assertions)]
                if a.type not in ASSERTIONS.names()
            }
            if unknown:
                problems.append(f"{label}: unknown assertion type(s) {sorted(unknown)}")
                continue
            if not test.all_turns() and not test.browser_steps:
                problems.append(f"{label}: needs 'input' or 'turns'")
                continue
            if test.id in seen:
                problems.append(f"{label}: duplicate id '{test.id}'")
                continue
            seen.add(test.id)
            tests.append(test)
        if len(items) > MAX_TESTS:
            problems.append(f"{p}: only the first {MAX_TESTS} tests were read")
    return tests, problems
