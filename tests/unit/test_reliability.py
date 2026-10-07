"""Repetitions are reduced to one status, to reliability statistics and to a confidence that says how far to trust it.

A test that passes once and fails repeatedly must never get a plain PASS, and a limit or a missing prerequisite must never
look like a failure of the agent. The figures are the ones docs/evaluation.md states.
"""

from __future__ import annotations

import math

import pytest

from agentlab.core.enums import TestStatus as Status
from agentlab.core.models import AssertionResult, AttemptResult, JudgeResult, TestCase
from agentlab.evaluation.reliability import (
    aggregate_status,
    attempt_score,
    compute_confidence,
    compute_reliability,
    evidence_factor,
    failure_signature,
    output_variance,
    percentile,
)

PASSED, FAILED, ERROR, TIMEOUT = Status.PASSED, Status.FAILED, Status.ERROR, Status.TIMEOUT


def check(
    kind: str = "contains", *, passed: bool = True, weight: float = 1.0, required: bool = True
) -> AssertionResult:
    return AssertionResult(
        type=kind, passed=passed, score=1.0 if passed else 0.0, message="m", weight=weight, required=required
    )


def attempt(
    status: Status,
    *,
    latency: float = 100.0,
    output: str = "o",
    checks: list[AssertionResult] | None = None,
    judge: list[JudgeResult] | None = None,
) -> AttemptResult:
    return AttemptResult(
        attempt=1, status=status, latency_ms=latency, outputs=[output], assertions=checks or [], judge=judge or []
    )


def make_case(**changes: object) -> TestCase:
    return TestCase(id="T-001", name="t", category="functional", objective="o", input="x", **changes)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------------- status
def test_a_test_passes_only_when_enough_of_its_attempts_passed() -> None:
    mixed = [attempt(PASSED), attempt(PASSED), attempt(FAILED)]
    assert aggregate_status(mixed) == FAILED, "the default asks for every attempt"
    assert aggregate_status(mixed, 2 / 3) == PASSED
    assert aggregate_status(mixed, 0.7) == FAILED
    assert aggregate_status([attempt(PASSED)] * 3) == PASSED


def test_the_failure_kind_that_dominates_names_the_status_of_a_test_that_did_not_pass() -> None:
    assert aggregate_status([attempt(TIMEOUT), attempt(FAILED)]) == FAILED
    assert aggregate_status([attempt(TIMEOUT), attempt(ERROR)]) == TIMEOUT
    assert aggregate_status([attempt(ERROR), attempt(ERROR)]) == ERROR


def test_attempts_that_never_ran_do_not_count_against_the_ones_that_did() -> None:
    assert aggregate_status([attempt(Status.STOPPED_DUE_TO_COST), attempt(PASSED)]) == PASSED
    assert aggregate_status([attempt(Status.BLOCKED), attempt(FAILED)]) == FAILED


def test_a_limit_a_missing_prerequisite_or_nothing_at_all_is_never_reported_as_a_failure() -> None:
    assert aggregate_status([attempt(Status.STOPPED_DUE_TO_COST)] * 2) == Status.STOPPED_DUE_TO_COST
    assert aggregate_status([attempt(Status.STOPPED_DUE_TO_STEP_LIMIT), attempt(Status.BLOCKED)]) == (
        Status.STOPPED_DUE_TO_STEP_LIMIT
    )
    assert aggregate_status([attempt(Status.BLOCKED)] * 2) == Status.BLOCKED
    assert aggregate_status([attempt(Status.SKIPPED)]) == Status.SKIPPED
    assert aggregate_status([]) == Status.SKIPPED


# ---------------------------------------------------------------------------------------- statistics
def test_the_statistics_describe_only_the_attempts_that_ran() -> None:
    stats = compute_reliability(
        [
            attempt(PASSED, latency=100),
            attempt(FAILED, latency=300),
            attempt(PASSED, latency=200),
            attempt(TIMEOUT, latency=400),
            attempt(Status.BLOCKED, latency=9999),
        ]
    )
    assert (stats.repetitions, stats.passes, stats.pass_rate) == (4, 2, 0.5)
    assert stats.flaky and not stats.deterministic_failure
    assert stats.timeout_rate == 0.25 and stats.error_rate == 0.0
    assert stats.latency_p50_ms == 250.0 and stats.latency_p95_ms == pytest.approx(385.0)


def test_a_test_that_always_passes_or_always_fails_is_not_flaky() -> None:
    assert not compute_reliability([attempt(PASSED)] * 3).flaky
    assert not compute_reliability([attempt(FAILED)] * 3).flaky


def test_a_failure_every_time_for_the_same_reason_is_deterministic() -> None:
    same = [attempt(FAILED, checks=[check(passed=False)]) for _ in range(3)]
    assert compute_reliability(same).deterministic_failure


def test_failures_for_different_reasons_are_not_called_deterministic() -> None:
    reasons = [
        attempt(FAILED, checks=[check("contains", passed=False)]),
        attempt(FAILED, checks=[check("regex", passed=False)]),
    ]
    assert not compute_reliability(reasons).deterministic_failure


def test_one_failed_attempt_is_not_a_pattern_and_an_error_is_not_a_deterministic_failure() -> None:
    assert not compute_reliability([attempt(FAILED)]).deterministic_failure
    assert not compute_reliability([attempt(ERROR), attempt(ERROR)]).deterministic_failure


def test_the_signature_of_a_failure_is_which_checks_failed_and_not_how() -> None:
    a = attempt(FAILED, checks=[check("contains", passed=False), check("regex", passed=True)])
    b = attempt(FAILED, checks=[check("contains", passed=False)])
    assert failure_signature(a) == failure_signature(b) == ("contains",)
    soft = attempt(FAILED, checks=[check("contains", passed=False, required=False)])
    assert failure_signature(soft) == ()


def test_percentiles_interpolate_between_the_samples() -> None:
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([1, 2, 3, 4], 95) == pytest.approx(3.85)
    assert percentile([5], 95) == 5 and percentile([], 50) == 0.0


def test_output_variance_is_the_distance_between_the_words_of_the_replies() -> None:
    assert output_variance(["a b c", "a b c"]) == 0.0
    assert output_variance(["a b", "c d"]) == 1.0
    assert output_variance(["a b c d"]) == 0.0
    assert 0 < output_variance(["a b c", "a b d"]) < 1


# ----------------------------------------------------------------------------------------------- score
def test_an_attempts_score_is_the_weighted_mean_of_its_checks() -> None:
    a = attempt(FAILED, checks=[check(weight=1.0), check("regex", passed=False, weight=3.0)])
    assert attempt_score(a) == 0.25


def test_a_judge_criterion_counts_as_much_as_its_weight_says() -> None:
    low = JudgeResult(metric="m", score=0.0, passed=False, confidence=0.9, rubric="r", weight=3.0)
    a = attempt(FAILED, checks=[check(weight=1.0)], judge=[low])
    assert attempt_score(a) == 0.25, "one passing check (weight 1) against one failed criterion (weight 3)"
    light = low.model_copy(update={"weight": 1.0})
    assert attempt_score(attempt(FAILED, checks=[check(weight=1.0)], judge=[light])) == 0.5


def test_an_attempt_without_checks_scores_its_status() -> None:
    assert attempt_score(attempt(PASSED)) == 1.0 and attempt_score(attempt(FAILED)) == 0.0


# ------------------------------------------------------------------------------------------ confidence
def judged(confidence: float, agreement: float = 1.0) -> JudgeResult:
    return JudgeResult(
        metric="m", score=0.9, passed=True, confidence=confidence, rubric="r", agreement=agreement, strategy="single"
    )


def test_deterministic_evidence_is_trusted_fully_and_a_judge_is_trusted_less() -> None:
    assert evidence_factor([attempt(PASSED, checks=[check()])]) == 1.0
    assert evidence_factor([attempt(PASSED, judge=[judged(0.8)])]) == pytest.approx(0.8 * 0.85)
    both = evidence_factor([attempt(PASSED, checks=[check()], judge=[judged(1.0)])])
    assert 0.9 < both < 1.0
    assert evidence_factor([attempt(PASSED)]) == 0.5, "with no evidence at all, a verdict is a coin toss"


def test_confidence_is_the_product_of_evidence_expectation_sample_size_and_consistency() -> None:
    expecting = make_case(expected_output="x")
    one = compute_confidence(expecting, [attempt(PASSED, checks=[check()])], None)
    assert one == pytest.approx(1 - 0.15 / math.sqrt(1)), "a single sample never reaches certainty"
    four = [attempt(PASSED, checks=[check()])] * 4
    assert compute_confidence(expecting, four, compute_reliability(four)) == pytest.approx(1 - 0.15 / 2)
    flaky = [attempt(PASSED, checks=[check()]), attempt(FAILED, checks=[check(passed=False)])]
    assert compute_confidence(expecting, flaky, compute_reliability(flaky)) == pytest.approx(
        (1 - 0.15 / math.sqrt(2)) * 0.5, abs=1e-4
    ), "a pass rate near one half halves the trust in any single verdict"


def test_a_test_without_an_expectation_is_trusted_a_fifth_less() -> None:
    plain = make_case(assertions=[])
    with_check = make_case(assertions=[{"type": "contains", "params": {"value": "x"}}])  # type: ignore[list-item]
    attempts = [attempt(PASSED, checks=[check()])]
    assert compute_confidence(plain, attempts, None) == pytest.approx(0.8 * 0.85)
    assert compute_confidence(with_check, attempts, None) == pytest.approx(0.85)


def test_a_test_nothing_executed_has_no_confidence() -> None:
    assert compute_confidence(make_case(), [attempt(Status.BLOCKED)], None) == 0.0
