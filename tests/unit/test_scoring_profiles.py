"""Every setting of a scoring profile changes the scorecard, so a profile never carries a dial that does nothing.

A profile used to declare ``pass_threshold`` and nothing read it. The guard at the bottom fails when a field is added to
``ScoringProfile`` without either a test below that shows it matters or a place in ``NOTES`` (fields that only describe
the profile).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from agentlab.core.enums import Severity, TestStatus
from agentlab.core.models import AttemptResult, Finding, Scorecard, TestCase, TestResult
from agentlab.evaluation.scoring import ScoringProfile, build_scorecard, list_profiles, load_profile

NOTES = {"name", "description", "applies_to"}  # they describe the profile; nothing is computed from them


def case(n: int, category: str = "functional_quality") -> TestCase:
    return TestCase(id=f"T-{n:03d}", name=f"t{n}", category="functional", objective="o", score_category=category)


def result(c: TestCase, *, ok: bool = True, latency_ms: float = 0.0, cost: float = 0.0, tokens: int = 0) -> TestResult:
    status = TestStatus.PASSED if ok else TestStatus.FAILED
    attempt = AttemptResult(attempt=1, status=status, latency_ms=latency_ms, cost_usd=cost, tokens=tokens)
    return TestResult(
        run_id="r",
        test_id=c.id,
        test_name=c.name,
        category=c.category,
        score_category=c.score_category,
        status=status,
        score=1.0 if ok else 0.0,
        confidence=0.9,
        attempts=[attempt],
        latency_ms=latency_ms,
        cost_usd=cost,
        tokens=tokens,
    )


def finding(c: TestCase, severity: Severity, *, security: bool = False) -> Finding:
    return Finding(
        run_id="r",
        test_id=c.id,
        title=f"{severity.value} in {c.id}",
        category=c.category,
        severity=severity,
        confidence=0.9,
        expected="e",
        observed="o",
        impact="i",
        reproduction="r",
        recommendation="f",
        is_security=security,
    )


def profile(**changes: Any) -> ScoringProfile:
    return load_profile("general").model_copy(update=changes)


def score_of(
    profile_: ScoringProfile, cases: list[TestCase], results: list[TestResult], findings: list[Finding] | None = None
) -> Scorecard:
    return build_scorecard(cases, results, findings or [], profile_)


def category(sc: Scorecard, name: str) -> Any:
    return next(c for c in sc.categories if c.category == name)


def test_weights_decide_how_much_a_category_counts() -> None:
    cases = [case(1, "functional_quality"), case(2, "conversational")]
    results = [result(cases[0], ok=True), result(cases[1], ok=False)]
    light = score_of(profile(weights={"functional_quality": 9, "conversational": 1}), cases, results)
    heavy = score_of(profile(weights={"functional_quality": 1, "conversational": 9}), cases, results)
    assert light.overall is not None and heavy.overall is not None and light.overall > heavy.overall + 50


def test_security_caps_set_the_ceiling_an_open_security_finding_allows() -> None:
    cases = [case(i) for i in range(30)]
    results = [result(c) for c in cases]
    findings = [finding(cases[0], Severity.HIGH, security=True)]
    strict = score_of(profile(security_caps={"high": 30.0}), cases, results, findings)
    loose = score_of(profile(security_caps={"high": 80.0}), cases, results, findings)
    assert strict.overall == 30.0 and loose.overall == 80.0


def test_the_latency_budget_sets_the_performance_score() -> None:
    cases = [case(i) for i in range(5)]
    results = [result(c, latency_ms=4000) for c in cases]
    tight = category(score_of(profile(latency_budget_ms=2000), cases, results), "performance")
    roomy = category(score_of(profile(latency_budget_ms=8000), cases, results), "performance")
    assert tight.score == 50.0 and roomy.score == 100.0


def test_the_cost_budget_sets_the_cost_score() -> None:
    cases = [case(i) for i in range(5)]
    results = [result(c, cost=0.10) for c in cases]
    tight = category(score_of(profile(cost_budget_usd_per_test=0.05), cases, results), "cost_efficiency")
    roomy = category(score_of(profile(cost_budget_usd_per_test=0.50), cases, results), "cost_efficiency")
    assert tight.score == 50.0 and roomy.score == 100.0


def test_the_token_budget_sets_the_cost_score_when_no_cost_is_reported() -> None:
    cases = [case(i) for i in range(5)]
    results = [result(c, tokens=4000) for c in cases]
    tight = category(score_of(profile(token_budget_per_test=2000), cases, results), "cost_efficiency")
    roomy = category(score_of(profile(token_budget_per_test=8000), cases, results), "cost_efficiency")
    assert tight.score == 50.0 and roomy.score == 100.0


def test_grades_are_the_thresholds_of_the_letters() -> None:
    cases = [case(i) for i in range(10)]
    results = [result(c, ok=i < 8) for i, c in enumerate(cases)]  # 80 points
    assert score_of(profile(grades={"A": 90, "B": 80, "C": 70, "D": 60}), cases, results).grade == "B"
    assert score_of(profile(grades={"A": 80, "B": 70, "C": 60, "D": 50}), cases, results).grade == "A"


def test_grade_ceilings_name_the_best_letter_a_serious_failure_allows() -> None:
    cases = [case(i) for i in range(60)]
    results = [result(c) for c in cases]
    findings = [finding(cases[0], Severity.HIGH)]
    assert (score_of(profile(grade_ceilings={"high": "B"}), cases, results, findings).grade or "").startswith("B")
    assert (score_of(profile(grade_ceilings={"high": "D"}), cases, results, findings).grade or "").startswith("D")


def test_many_high_is_how_many_high_failures_count_as_several() -> None:
    cases = [case(i) for i in range(60)]
    results = [result(c) for c in cases]
    findings = [finding(cases[i], Severity.HIGH) for i in range(2)]
    ceilings = {"many_high": "D", "high": "B"}
    two_is_several = score_of(profile(many_high=2, grade_ceilings=ceilings), cases, results, findings)
    two_is_not = score_of(profile(many_high=3, grade_ceilings=ceilings), cases, results, findings)
    assert (two_is_several.grade or "").startswith("D") and (two_is_not.grade or "").startswith("B")


CHECKS: dict[str, Callable[[], None]] = {
    "weights": test_weights_decide_how_much_a_category_counts,
    "security_caps": test_security_caps_set_the_ceiling_an_open_security_finding_allows,
    "latency_budget_ms": test_the_latency_budget_sets_the_performance_score,
    "cost_budget_usd_per_test": test_the_cost_budget_sets_the_cost_score,
    "token_budget_per_test": test_the_token_budget_sets_the_cost_score_when_no_cost_is_reported,
    "grades": test_grades_are_the_thresholds_of_the_letters,
    "grade_ceilings": test_grade_ceilings_name_the_best_letter_a_serious_failure_allows,
    "many_high": test_many_high_is_how_many_high_failures_count_as_several,
}


def test_every_setting_of_a_profile_has_a_test_that_shows_it_matters() -> None:
    declared = set(ScoringProfile.model_fields)
    unaccounted = declared - NOTES - set(CHECKS)
    assert not unaccounted, f"profile setting(s) {sorted(unaccounted)} have no test showing they change a scorecard"
    assert not (set(CHECKS) | NOTES) - declared, "a test or note names a setting the profile no longer has"


@pytest.mark.parametrize("name", list_profiles())
def test_the_shipped_profiles_set_only_settings_that_exist(name: str) -> None:
    assert load_profile(name).name == name
