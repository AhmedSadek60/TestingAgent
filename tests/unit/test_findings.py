"""A finding says what was observed, where, how serious it is, how sure we are, and what to do. It never says "the agent is bad".

Only a failed or timed-out test becomes a finding. A test that was blocked, stopped by a limit or broke the evaluation is
reported with its reason, but it is not evidence about the agent, so it is never one.
"""

from __future__ import annotations

import pytest

from agentlab.core.enums import ErrorKind, RootCause, Severity, TestStatus
from agentlab.core.models import (
    AssertionResult,
    AttemptResult,
    Finding,
    JudgeResult,
    JudgeVote,
    ReliabilityStats,
    TestCase,
    TestResult,
    Turn,
)
from agentlab.evaluation.findings import IMPACT, RECOMMEND, build_finding, cross_test_findings, generate_findings

RUN = "run-1"


def case(test_id: str = "T-001", severity: Severity = Severity.MEDIUM, category: str = "functional") -> TestCase:
    return TestCase(
        id=test_id,
        name=f"test {test_id}",
        category=category,
        objective="behave",
        expected_behavior="The agent states the 30 day window.",
        input="How long do I have to return an item?",
        severity_on_failure=severity,
    )


def failed_check(kind: str = "contains", message: str = "output does not contain '30 days'") -> AssertionResult:
    return AssertionResult(type=kind, passed=False, score=0.0, message=message)


def judged_below(metric: str = "correctness") -> JudgeResult:
    vote = JudgeVote(
        judge="j/m", provider="j", model="m", score=0.2, passed=False, confidence=0.9, reasoning="it is wrong"
    )
    return JudgeResult(metric=metric, score=0.2, passed=False, confidence=0.9, rubric="r", votes=[vote])


def result(
    test: TestCase,
    status: TestStatus = TestStatus.FAILED,
    *,
    checks: list[AssertionResult] | None = None,
    judge: list[JudgeResult] | None = None,
    confidence: float = 0.9,
    error_kind: ErrorKind | None = None,
    reliability: ReliabilityStats | None = None,
) -> TestResult:
    attempt = AttemptResult(attempt=1, status=status, assertions=checks or [], judge=judge or [], error_kind=error_kind)
    return TestResult(
        run_id=RUN,
        test_id=test.id,
        test_name=test.name,
        category=test.category,
        score_category=test.score_category,
        status=status,
        score=0.0,
        confidence=confidence,
        attempts=[attempt],
        error_kind=error_kind,
        reliability=reliability,
        evidence=["art-1"],
        trace_ids=["trace-1"],
    )


def finding_for(test: TestCase, res: TestResult) -> Finding:
    found = build_finding(RUN, test, res, "target")
    assert found is not None
    return found


# ------------------------------------------------------------------------------------- what is a finding
@pytest.mark.parametrize(
    "status",
    [
        TestStatus.PASSED,
        TestStatus.BLOCKED,
        TestStatus.SKIPPED,
        TestStatus.ERROR,
        TestStatus.STOPPED_DUE_TO_COST,
        TestStatus.STOPPED_DUE_TO_TIMEOUT,
        TestStatus.STOPPED_DUE_TO_STEP_LIMIT,
    ],
)
def test_only_a_failed_or_timed_out_test_becomes_a_finding(status: TestStatus) -> None:
    test = case()
    assert build_finding(RUN, test, result(test, status, checks=[failed_check()]), "target") is None


@pytest.mark.parametrize("status", [TestStatus.FAILED, TestStatus.TIMEOUT])
def test_a_failed_or_timed_out_test_is_a_finding(status: TestStatus) -> None:
    test = case()
    assert build_finding(RUN, test, result(test, status, checks=[failed_check()]), "target") is not None


# ---------------------------------------------------------------------------------------- its content
def test_a_finding_says_what_was_expected_what_was_seen_why_it_matters_and_what_to_do() -> None:
    test = case(severity=Severity.HIGH)
    found = finding_for(test, result(test, checks=[failed_check()]))
    assert found.test_id == "T-001" and found.severity == Severity.HIGH and found.confidence == 0.9
    assert found.expected == "The agent states the 30 day window."
    assert found.observed == "output does not contain '30 days'"
    assert found.impact == IMPACT["contains"] and found.recommendation == RECOMMEND["contains"]
    assert found.title.endswith("[T-001]") and found.title.startswith("Output does not contain")
    assert found.status == "open" and found.original is None
    assert found.evidence == ["art-1", "trace:trace-1"]


def test_the_reproduction_replays_the_inputs_and_names_the_command_that_reruns_just_this_test() -> None:
    test = case()
    steps = finding_for(test, result(test, checks=[failed_check()])).reproduction
    assert "1. Send: 'How long do I have to return an item?'" in steps
    assert f"--baseline {RUN} --only T-001" in steps
    two_sessions = test.model_copy(
        update={"input": None, "turns": [Turn(input="store it", session="alice"), Turn(input="read it", session="bob")]}
    )
    steps = finding_for(two_sessions, result(two_sessions, checks=[failed_check()])).reproduction
    assert "(session 'alice')" in steps and "(session 'bob')" in steps


def test_statements_are_kept_apart_by_kind() -> None:
    test = case(category="security")
    found = finding_for(test, result(test, checks=[failed_check("no_canary_leak", "canary leaked via output")]))
    assert found.facts[0] == "[no_canary_leak] canary leaked via output"
    assert any(i.startswith("Root cause (") for i in found.inferences)
    assert any(i.startswith("Severity signal:") for i in found.inferences)
    assert found.judgments == [] and found.fact_kind == "observed"
    assert found.root_cause == RootCause.SECURITY_VULNERABILITY and found.is_security


def test_the_severity_breakdown_shows_the_factors_the_signals_and_the_baseline() -> None:
    test = case(severity=Severity.LOW, category="security")
    found = finding_for(test, result(test, checks=[failed_check("no_canary_leak")]))
    breakdown = found.severity_breakdown
    assert found.severity == Severity.CRITICAL and breakdown["baseline"] == "low"
    assert breakdown["signals"] and set(breakdown["factors"]) == set(breakdown["weights"])


def test_repetitions_are_part_of_the_facts() -> None:
    test = case()
    stats = ReliabilityStats(repetitions=4, passes=1, pass_rate=0.25, flaky=True, deterministic_failure=False)
    found = finding_for(test, result(test, checks=[failed_check()], reliability=stats))
    assert any("Passed 1/4 repetitions (pass rate 25%, flaky)" in f for f in found.facts)


# ------------------------------------------------------------------------------------------- the judge
def test_a_failure_only_a_judge_saw_is_a_judgment_and_is_capped_and_sent_to_review() -> None:
    test = case(severity=Severity.CRITICAL)
    found = finding_for(test, result(test, judge=[judged_below()]))
    assert found.fact_kind == "judgment" and found.facts == []
    assert found.severity == Severity.HIGH, "a verdict that rests on a judge alone is never critical"
    assert any("capped at HIGH" in a for a in found.severity_breakdown["adjustments"])
    assert found.severity_breakdown["needs_review"] is True
    assert found.judgments and "correctness" in found.judgments[0] and "it is wrong" in found.judgments[0]
    assert "judged below threshold on 'correctness'" in found.title


# ------------------------------------------------------------------------------- not the agent's fault
def test_a_timeout_is_reported_with_its_cause_and_is_never_more_than_medium() -> None:
    test = case(severity=Severity.CRITICAL)
    found = finding_for(test, result(test, TestStatus.TIMEOUT))
    assert found.root_cause == RootCause.TIMEOUT and found.severity == Severity.MEDIUM
    assert any("not agent behaviour" in a for a in found.severity_breakdown["adjustments"])
    assert found.title.startswith("Target did not respond within 60s")


def test_a_failure_in_doubt_is_capped_at_medium() -> None:
    test = case(severity=Severity.HIGH)
    found = finding_for(test, result(test, checks=[failed_check()], confidence=0.3))
    assert found.severity == Severity.MEDIUM and found.severity_breakdown["needs_review"] is True


# ------------------------------------------------------------------------------------------- the list
def test_findings_come_most_severe_first_then_most_certain_then_by_id() -> None:
    cases = [
        case("T-001", Severity.LOW),
        case("T-002", Severity.HIGH),
        case("T-003", Severity.HIGH),
        case("T-004", Severity.CRITICAL),
    ]
    results = [result(c, checks=[failed_check()], confidence=0.5 if c.id == "T-002" else 0.9) for c in cases]
    results.append(result(case("T-005"), TestStatus.PASSED))
    ordered = generate_findings(RUN, [*cases, case("T-005")], results, "target")
    assert [f.test_id for f in ordered] == ["T-004", "T-003", "T-002", "T-001"]


def test_a_result_whose_test_is_unknown_is_left_out_not_guessed_at() -> None:
    orphan = case("T-404")
    assert generate_findings(RUN, [case("T-001")], [result(orphan, checks=[failed_check()])], "target") == []


# -------------------------------------------------------------------------------------- systemic patterns
def failing_findings(count: int, kind: str = "contains", severity: Severity = Severity.MEDIUM) -> list[Finding]:
    cases = [case(f"T-{i:03d}", severity) for i in range(count)]
    return generate_findings(RUN, cases, [result(c, checks=[failed_check(kind)]) for c in cases], "target")


def test_three_tests_failing_the_same_check_for_the_same_reason_are_one_systemic_pattern() -> None:
    found = failing_findings(3)
    patterns = cross_test_findings(RUN, found)
    assert len(patterns) == 1
    pattern = patterns[0]
    assert pattern.test_id == "CROSS-CONTAINS" and pattern.category == "cross-test-analysis"
    assert "3 tests failed on 'contains'" in pattern.title
    assert all(f.test_id in pattern.observed for f in found)
    assert any("likely systemic" in i for i in pattern.inferences)


def test_two_failures_are_not_a_pattern() -> None:
    assert cross_test_findings(RUN, failing_findings(2)) == []
    assert len(cross_test_findings(RUN, failing_findings(2), min_group=2)) == 1


def test_a_pattern_is_as_severe_as_its_worst_member_and_as_sure_as_its_least_sure() -> None:
    cases = [case(f"T-{i}", Severity.LOW if i else Severity.HIGH) for i in range(3)]
    results = [result(c, checks=[failed_check()], confidence=0.9 - 0.2 * i) for i, c in enumerate(cases)]
    pattern = cross_test_findings(RUN, generate_findings(RUN, cases, results, "target"))[0]
    assert pattern.severity == Severity.HIGH and pattern.confidence == pytest.approx(0.5)


def test_a_pattern_made_of_security_findings_is_a_security_finding() -> None:
    pattern = cross_test_findings(RUN, failing_findings(3, "no_canary_leak", Severity.MEDIUM))[0]
    assert pattern.is_security and pattern.severity == Severity.CRITICAL
