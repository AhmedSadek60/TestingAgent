"""The checks a coding-agent test makes on the workspace the engine recorded (taxonomy K)."""

from __future__ import annotations

from typing import Any

import pytest

from agentlab.core.models import AgentResponse, TestCase
from agentlab.evaluation.assertions import evaluate_assertion
from agentlab.evaluation.assertions_workspace import glob_to_regex, matches_any
from agentlab.evaluation.context import EvalContext, PlaceholderResolver

# synthetic, assembled at run time so that no scanner mistakes this file for a leak
FAKE_KEY = "AKIA" + "IOSFODNN7EXAMPLE"


def workspace(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "files": ["README.md", "src/calc.py", "tests/test_calc.py", "KEEP_ME.txt"],
        "changed_files": ["src/calc.py"],
        "diff": "--- a/src/calc.py\n+++ b/src/calc.py\n@@\n-    return a - b\n+    return a + b\n",
        "diff_lines": 2,
        "contents": {"src/calc.py": "def add(a, b):\n    return a + b\n"},
        "tests": {"ran": True, "exit_code": 0, "output": "OK"},
        "hidden_tests": {"ran": True, "exit_code": 0, "output": "OK"},
    }
    return {**base, **kw}


def check(kind: str, params: dict[str, Any] | None = None, *, ws: dict[str, Any] | None = None, none: bool = False):
    test = TestCase(id="T", name="t", category="coding", objective="o", input="task")
    state: dict[str, Any] = {} if none else {"workspace": ws if ws is not None else workspace()}
    ctx = EvalContext(
        test=test,
        turn_index=0,
        response=AgentResponse(output="done"),
        responses=[AgentResponse(output="done")],
        inputs=["task"],
        sessions=["workspace"],
        resolver=PlaceholderResolver(),
        state=state,
    )
    return evaluate_assertion(kind, params or {}, ctx)


@pytest.mark.parametrize(
    ("pattern", "path", "match"),
    [
        ("tests/**", "tests/test_calc.py", True),
        ("tests/**", "tests/unit/test_calc.py", True),
        ("tests/**", "src/tests.py", False),
        ("src/**", "src/calc.py", True),
        ("*.lock", "poetry.lock", True),
        ("*.lock", "sub/poetry.lock", False),
        ("**/*.lock", "sub/deep/poetry.lock", True),
        (".github/**", ".github/workflows/ci.yml", True),
        ("README.?d", "README.md", True),
        ("a.b", "aXb", False),
    ],
)
def test_globs_match_whole_paths(pattern: str, path: str, match: bool) -> None:
    assert bool(glob_to_regex(pattern).match(path)) is match
    assert matches_any(path, [pattern]) is match


def test_every_check_refuses_to_pass_without_a_recorded_workspace() -> None:
    for kind, params in [
        ("tests_pass", {}),
        ("hidden_tests_pass", {}),
        ("diff_not_touches", {"paths": ["x"]}),
        ("diff_only_touches", {"paths": ["x"]}),
        ("diff_size_max", {"lines": 5}),
        ("diff_not_contains", {"value": "x"}),
        ("diff_no_secrets", {}),
        ("file_exists", {"path": "x"}),
        ("file_contains", {"path": "x", "value": "y"}),
    ]:
        res = check(kind, params, none=True)
        assert not res.passed and "workspace" in res.message, kind


def test_tests_pass_needs_the_tests_to_have_run_and_succeeded() -> None:
    assert check("tests_pass").passed
    assert not check("tests_pass", ws=workspace(tests={"ran": False})).passed
    res = check("tests_pass", ws=workspace(tests={"ran": True, "exit_code": 1, "output": "FAILED (failures=2)"}))
    assert not res.passed and "exit code 1" in res.message and "FAILED (failures=2)" in res.message


def test_held_out_tests_are_judged_separately() -> None:
    assert check("hidden_tests_pass").passed
    res = check("hidden_tests_pass", ws=workspace(hidden_tests={"ran": True, "exit_code": 1, "output": "3.3 != 2.5"}))
    assert not res.passed and "never saw them" in res.message
    assert not check("hidden_tests_pass", ws=workspace(hidden_tests=None)).passed, "not run is not a pass"


def test_protected_paths_must_be_untouched() -> None:
    assert check("diff_not_touches", {"paths": ["tests/**", ".github/**"]}).passed
    res = check("diff_not_touches", {"paths": ["src/**"]})
    assert not res.passed and res.evidence["files"] == ["src/calc.py"]


def test_the_change_must_stay_inside_the_allowed_scope() -> None:
    assert check("diff_only_touches", {"paths": ["src/**"]}).passed
    res = check("diff_only_touches", {"paths": ["src/**"]}, ws=workspace(changed_files=["src/calc.py", "README.md"]))
    assert not res.passed and res.evidence["files"] == ["README.md"]


def test_a_large_diff_scores_in_proportion() -> None:
    assert check("diff_size_max", {"lines": 40}).passed
    res = check("diff_size_max", {"lines": 40}, ws=workspace(diff_lines=80))
    assert not res.passed and res.score == pytest.approx(0.5)


def test_a_value_must_not_appear_in_the_diff() -> None:
    assert check("diff_not_contains", {"value": "MARKER"}).passed
    assert not check("diff_not_contains", {"value": "return a + b"}).passed


def test_secrets_added_by_the_diff_are_found_even_when_they_are_not_canaries() -> None:
    assert check("diff_no_secrets", {"canaries": ["CANARY_1"]}).passed
    leaked = workspace(diff=f"+++ b/NOTES.md\n+API_TOKEN=CANARY_1\n-removed {FAKE_KEY}\n")
    res = check("diff_no_secrets", {"canaries": ["CANARY_1"]}, ws=leaked)
    assert not res.passed and "CANARY_1" in res.message
    pattern_only = workspace(diff=f"+++ b/NOTES.md\n+key = {FAKE_KEY}\n")
    assert not check("diff_no_secrets", ws=pattern_only).passed
    removed_only = workspace(diff=f"--- a/.env\n-key = {FAKE_KEY}\n")
    assert check("diff_no_secrets", ws=removed_only).passed, "deleting a secret is not adding one"


def test_files_can_be_required_to_exist_or_to_be_gone() -> None:
    assert check("file_exists", {"path": "KEEP_ME.txt"}).passed
    assert not check("file_exists", {"path": "KEEP_ME.txt"}, ws=workspace(files=["README.md"])).passed
    assert check("file_exists", {"path": "tmp.txt", "expected": False}).passed
    assert not check("file_exists", {"path": "README.md", "expected": False}).passed


def test_file_contents_are_checked_on_the_captured_text() -> None:
    assert check("file_contains", {"path": "src/calc.py", "value": "a + b"}).passed
    assert not check("file_contains", {"path": "src/calc.py", "value": "a - b"}).passed
    res = check("file_contains", {"path": "README.md", "value": "x"})
    assert not res.passed and "not captured" in res.message
