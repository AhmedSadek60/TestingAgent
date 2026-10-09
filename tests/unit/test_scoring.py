"""Scoring: category normalisation, security caps and the grade ceilings that stop an average hiding a serious failure."""

from __future__ import annotations

import pytest

from agentlab.core.config import WEB_LATENCY_BUDGET_MS
from agentlab.core.enums import ErrorKind, Severity, TestStatus, normalize_score_category
from agentlab.core.models import AttemptResult, Finding, TestCase, TestResult
from agentlab.evaluation.scoring import blocked_summary, build_scorecard, grade_ceiling, load_profile, select_profile


def _case(n: int, category: str = "functional_quality") -> TestCase:
    return TestCase(id=f"T-{n:03d}", name=f"test {n}", category="functional", objective="o", score_category=category)


def _result(case: TestCase, ok: bool = True, status: TestStatus | None = None) -> TestResult:
    return TestResult(
        run_id="r1",
        test_id=case.id,
        test_name=case.name,
        category=case.category,
        score_category=case.score_category,
        status=status or (TestStatus.PASSED if ok else TestStatus.FAILED),
        score=1.0 if ok else 0.0,
        confidence=0.9,
    )


def _finding(case: TestCase, severity: Severity, *, security: bool = False) -> Finding:
    return Finding(
        run_id="r1",
        test_id=case.id,
        title=f"{severity.value} problem in {case.id}",
        category=case.category,
        severity=severity,
        confidence=0.9,
        expected="e",
        observed="o",
        impact="i",
        reproduction="r",
        recommendation="fix it",
        is_security=security,
    )


def _suite(failures: list[Severity], *, passing: int = 40, security: bool = False):
    cases = [_case(i) for i in range(passing + len(failures))]
    results = [_result(c, ok=i < passing) for i, c in enumerate(cases)]
    findings = [_finding(cases[passing + i], s, security=security) for i, s in enumerate(failures)]
    return cases, results, findings


GENERAL = load_profile("general")


def test_an_agent_that_passes_everything_gets_the_top_grade() -> None:
    cases, results, findings = _suite([])
    sc = build_scorecard(cases, results, findings, GENERAL)
    assert sc.overall == 100.0 and sc.grade == "A"


def test_legacy_category_names_are_normalised() -> None:
    assert normalize_score_category("functional") == "functional_quality"
    assert normalize_score_category("tool_use") == "tool_use"
    assert _case(1, "functional").score_category == "functional_quality"


def test_one_high_severity_failure_limits_the_grade_even_when_the_average_is_high() -> None:
    cases, results, findings = _suite([Severity.HIGH], passing=60)
    sc = build_scorecard(cases, results, findings, GENERAL)
    assert sc.overall is not None and sc.overall >= 90  # the average alone would be an A
    assert sc.grade is not None and sc.grade.startswith("B") and "limited by 1 high-severity failure" in sc.grade


@pytest.mark.parametrize(
    ("failures", "letter"),
    [
        ([Severity.HIGH] * 3, "C"),
        ([Severity.CRITICAL], "D"),
        ([Severity.MEDIUM] * 5, "A"),  # medium failures lower the score but do not cap the letter
    ],
)
def test_grade_ceilings(failures: list[Severity], letter: str) -> None:
    cases, results, findings = _suite(failures, passing=200)
    sc = build_scorecard(cases, results, findings, GENERAL)
    assert sc.grade is not None and sc.grade.startswith(letter), sc.grade


def test_the_ceiling_ignores_findings_a_reviewer_rejected() -> None:
    cases, _results, findings = _suite([Severity.HIGH])
    assert grade_ceiling(findings, GENERAL) == "B"
    findings[0].status = "false_positive"
    assert grade_ceiling(findings, GENERAL) is None


def test_an_open_security_finding_caps_the_score_not_just_the_letter() -> None:
    cases, results, findings = _suite([Severity.CRITICAL], passing=100, security=True)
    sc = build_scorecard(cases, results, findings, GENERAL)
    assert sc.security_cap_applied and sc.overall is not None and sc.overall <= 40
    assert sc.grade is not None and "capped by security" in sc.grade


def test_blocked_tests_do_not_count_for_or_against_the_agent() -> None:
    cases, results, _ = _suite([], passing=10)
    blocked = [_case(100 + i) for i in range(10)]
    results += [_result(c, status=TestStatus.BLOCKED) for c in blocked]
    sc = build_scorecard(cases + blocked, results, [], GENERAL)
    assert sc.overall == 100.0
    assert any("BLOCKED" in n for n in sc.notes)


def _with_attempt(result: TestResult, status: TestStatus, kind: ErrorKind | None = None) -> TestResult:
    result.attempts = [AttemptResult(attempt=1, status=status, error_kind=kind, latency_ms=1000.0)]
    return result


def _reliability_of(statuses: list[tuple[TestStatus, ErrorKind | None]]) -> float | None:
    cases = [_case(i) for i in range(len(statuses))]
    results = [
        _with_attempt(_result(c, status=status), status, kind)
        for c, (status, kind) in zip(cases, statuses, strict=True)
    ]
    for r, (status, kind) in zip(results, statuses, strict=True):
        r.error_kind = kind
    card = build_scorecard(cases, results, [], load_profile("general"))
    cats = card.categories.values() if isinstance(card.categories, dict) else card.categories
    rel = next((c for c in cats if c.category == "reliability"), None)
    return None if rel is None else rel.score


def test_errors_of_the_test_set_up_are_not_held_against_the_agents_reliability() -> None:
    ok = (TestStatus.PASSED, None)
    browser = (TestStatus.ERROR, ErrorKind.BROWSER_ERROR)
    assert _reliability_of([ok] * 8 + [browser] * 8) == 100.0, "a dialog covering the page is not the agent's fault"
    agent_error = (TestStatus.ERROR, ErrorKind.TARGET_ERROR)
    assert _reliability_of([ok] * 8 + [agent_error] * 2) == 80.0, "an error the agent itself returned still counts"
    assert _reliability_of([ok] * 9 + [(TestStatus.TIMEOUT, ErrorKind.TIMEOUT)]) == 90.0


def test_a_web_target_gets_a_latency_budget_that_a_chat_page_can_meet() -> None:
    assert select_profile(None, None).latency_budget_ms == load_profile("general").latency_budget_ms
    assert select_profile(None, None, web=True).latency_budget_ms >= WEB_LATENCY_BUDGET_MS
    # a profile that already allows more is left alone
    assert select_profile(None, None, web=True).latency_budget_ms == max(
        load_profile("general").latency_budget_ms, WEB_LATENCY_BUDGET_MS
    )


def test_blocked_tests_are_described_by_why_they_are_blocked() -> None:
    policy = [_result(_case(i), status=TestStatus.BLOCKED) for i in range(3)]
    for r in policy:
        r.error_kind = ErrorKind.POLICY_BLOCK
    other = [_result(_case(10), status=TestStatus.BLOCKED)]
    text = blocked_summary(policy + other)
    assert text.startswith("4 test(s) blocked:")
    assert "3 need an authorisation or the owner's attestation" in text and "1 lack a prerequisite" in text
    assert "prerequisite" not in blocked_summary(policy), "a policy block is not a missing prerequisite"
