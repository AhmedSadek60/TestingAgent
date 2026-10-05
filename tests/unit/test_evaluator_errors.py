"""A check that cannot be evaluated is a problem with the test or the evaluator, never a failure of the target."""

from __future__ import annotations

from agentlab.core.enums import TestStatus
from agentlab.core.models import AgentResponse, AssertionResult, TestCase
from agentlab.evaluation.assertions import evaluate_assertion
from agentlab.evaluation.context import EvalContext, PlaceholderResolver
from agentlab.execution.evaluate import decide_attempt_status


def ctx(output: str = "hello") -> EvalContext:
    resp = AgentResponse(output=output)
    test = TestCase(id="T-001", name="t", category="functional", objective="o", input="hi")
    return EvalContext(
        test=test,
        turn_index=0,
        response=resp,
        responses=[resp],
        inputs=["hi"],
        sessions=["default"],
        resolver=PlaceholderResolver(),
    )


def test_missing_parameters_are_flagged_as_evaluator_errors() -> None:
    for params in ({}, {"values": []}, {"text": "hello"}):
        r = evaluate_assertion("contains", params, ctx())
        assert r.evaluator_error and not r.passed, params


def test_unknown_assertion_types_are_evaluator_errors() -> None:
    r = evaluate_assertion("does_not_exist", {}, ctx())
    assert r.evaluator_error and "unknown assertion type" in r.message


def test_a_real_failure_is_not_an_evaluator_error() -> None:
    r = evaluate_assertion("contains", {"values": ["goodbye"]}, ctx("hello"))
    assert not r.passed and not r.evaluator_error


def test_combinators_do_not_blame_the_target_for_a_broken_sub_check() -> None:
    broken = evaluate_assertion("any_of", {"checks": [{"type": "contains", "params": {}}]}, ctx())
    assert broken.evaluator_error
    real = evaluate_assertion("any_of", {"checks": [{"type": "contains", "params": {"values": ["x"]}}]}, ctx())
    assert not real.evaluator_error and not real.passed


def test_a_broken_check_makes_the_attempt_ERROR_not_FAILED() -> None:
    broken = AssertionResult(type="contains", passed=False, score=0.0, message="bad", evaluator_error=True)
    status, note = decide_attempt_status([broken], [], has_deterministic=True, timed_out=False, stopped_status=None)
    assert status == TestStatus.ERROR and "could not be evaluated" in (note or "")


def test_real_evidence_still_fails_the_attempt_even_if_another_check_is_broken() -> None:
    broken = AssertionResult(type="contains", passed=False, score=0.0, message="bad", evaluator_error=True)
    real = AssertionResult(type="not_contains", passed=False, score=0.0, message="leaked")
    status, _ = decide_attempt_status([broken, real], [], has_deterministic=True, timed_out=False, stopped_status=None)
    assert status == TestStatus.FAILED
