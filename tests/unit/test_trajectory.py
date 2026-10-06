"""Trajectory evaluation compares the tools an agent called with the tools the test expected, from observable calls only.

Selection, arguments and unnecessary actions can fail a test; ordering, efficiency and error recovery are reported and
never fail one alone. The thresholds are the ones docs/evaluation.md states.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentlab.core.enums import TestStatus
from agentlab.core.models import AgentResponse, AssertionResult, ExpectedToolCall, TestCase, ToolCall
from agentlab.evaluation.context import EvalContext, PlaceholderResolver
from agentlab.evaluation.trajectory import DEFAULT_THRESHOLDS, REQUIRED, evaluate_trajectory
from agentlab.execution.evaluate import decide_attempt_status


def call(name: str, status: str = "success", result: Any = "ok", **arguments: Any) -> ToolCall:
    return ToolCall(name=name, arguments=arguments, status=status, result=result)


def expect(name: str, match: str = "subset", **arguments: Any) -> ExpectedToolCall:
    return ExpectedToolCall(name=name, arguments=arguments or None, match=match)  # type: ignore[arg-type]


def evaluate(
    expected: list[ExpectedToolCall],
    calls: list[ToolCall],
    *,
    output: str = "done",
    context: dict[str, Any] | None = None,
) -> dict[str, AssertionResult]:
    test = TestCase(
        id="T-001",
        name="t",
        category="tools",
        objective="o",
        input="x",
        expected_tool_calls=expected,
        context=context or {},
    )
    response = AgentResponse(output=output, tool_calls=calls)
    ctx = EvalContext(
        test=test,
        turn_index=0,
        response=response,
        responses=[response],
        inputs=["x"],
        sessions=["default"],
        resolver=PlaceholderResolver(),
    )
    return {r.metric or r.type: r for r in evaluate_trajectory(test, calls, ctx)}


def failing(results: dict[str, AssertionResult]) -> set[str]:
    return {name for name, r in results.items() if not r.passed}


# ------------------------------------------------------------------------------------------------ the happy path
def test_the_expected_tools_called_once_each_in_order_pass_every_metric() -> None:
    results = evaluate(
        [expect("search", query={"contains": "refund"}), expect("send", to="a@example.com")],
        [call("search", query="Refund policy"), call("send", to="a@example.com")],
    )
    assert failing(results) == set()
    assert set(results) == {"tool_selection", "tool_arguments", "unnecessary_actions", "tool_ordering", "efficiency"}
    assert all(r.type == f"trajectory:{name}" for name, r in results.items())


def test_selection_arguments_and_unnecessary_actions_can_fail_a_test_and_the_rest_cannot() -> None:
    results = evaluate([expect("a"), expect("b")], [call("a"), call("b")])
    required = {name for name, r in results.items() if r.required}
    assert required == REQUIRED == {"tool_selection", "tool_arguments", "unnecessary_actions"}
    assert not results["tool_ordering"].required and not results["efficiency"].required
    assert DEFAULT_THRESHOLDS["efficiency"] == 0.5 and DEFAULT_THRESHOLDS["tool_selection"] == 1.0


# ------------------------------------------------------------------------------------------------ selection
def test_a_tool_that_was_not_called_lowers_selection_and_is_named() -> None:
    results = evaluate([expect("a"), expect("b")], [call("a")])
    selection = results["tool_selection"]
    assert not selection.passed and selection.score == pytest.approx(2 / 3)
    assert "did not call ['b']" in selection.message


def test_a_tool_nobody_asked_for_lowers_selection_and_counts_as_an_unnecessary_action() -> None:
    results = evaluate([expect("a")], [call("a"), call("delete_everything")])
    assert not results["tool_selection"].passed and "unexpected/extra ['delete_everything']" in (
        results["tool_selection"].message
    )
    assert results["unnecessary_actions"].score == 0.5


def test_calling_a_tool_when_none_was_expected_is_a_selection_failure() -> None:
    results = evaluate([], [call("lookup")])
    assert not results["tool_selection"].passed and results["tool_selection"].score == 0.0
    clean = evaluate([], [])
    assert clean["tool_selection"].passed and clean["unnecessary_actions"].passed


# ----------------------------------------------------------------------------------------------- arguments
def test_wrong_arguments_fail_and_the_message_names_the_argument() -> None:
    results = evaluate([expect("weather", city="Paris")], [call("weather", city="London")])
    assert not results["tool_arguments"].passed and "city" in results["tool_arguments"].message
    assert results["tool_selection"].passed, "the right tool was chosen"


def test_a_literal_argument_must_be_equal_ignoring_case_and_spacing_and_not_merely_similar() -> None:
    assert evaluate([expect("weather", city="paris")], [call("weather", city="  Paris ")])["tool_arguments"].passed
    assert not evaluate([expect("weather", city="Paris")], [call("weather", city="Paris, France")])[
        "tool_arguments"
    ].passed


def test_by_default_extra_arguments_are_allowed_and_exact_match_refuses_them() -> None:
    sent = [call("weather", city="Paris", units="metric")]
    assert evaluate([expect("weather", city="Paris")], sent)["tool_arguments"].passed
    assert not evaluate([expect("weather", match="exact", city="Paris")], sent)["tool_arguments"].passed


def test_name_only_ignores_the_arguments() -> None:
    results = evaluate([expect("weather", match="name_only", city="Paris")], [call("weather", city="anything")])
    assert results["tool_arguments"].passed


def test_an_argument_can_be_matched_by_pattern_or_type_or_choice() -> None:
    sent = [call("send", to="ana@example.com", count=3, kind="Urgent")]
    wanted = [
        ExpectedToolCall(
            name="send",
            arguments={
                "to": {"regex": r"@example\.com$"},
                "count": {"type": "integer"},
                "kind": {"any_of": ["urgent", "normal"]},
            },
        )
    ]
    assert evaluate(wanted, sent)["tool_arguments"].passed


def test_no_matched_call_means_the_arguments_could_not_be_right() -> None:
    results = evaluate([expect("weather", city="Paris")], [call("other")])
    assert results["tool_arguments"].score == 0.0


# ------------------------------------------------------------------------------------ the other metrics
def test_an_identical_call_made_twice_is_a_duplicate() -> None:
    results = evaluate([expect("a")], [call("a", q=1), call("a", q=1)])
    assert not results["unnecessary_actions"].passed and results["unnecessary_actions"].score == 0.0


def test_calls_in_the_wrong_order_are_reported_but_never_required() -> None:
    results = evaluate([expect("a"), expect("b")], [call("b"), call("a")])
    ordering = results["tool_ordering"]
    assert not ordering.passed and ordering.score == 0.0 and not ordering.required
    assert results["tool_selection"].passed


def test_a_detour_lowers_efficiency_which_has_a_lower_bar_and_is_never_required() -> None:
    padded = evaluate([expect("a")], [call("a"), call("a", q=2), call("a", q=3), call("a", q=4)])
    assert padded["efficiency"].score == 0.25 and not padded["efficiency"].passed
    assert evaluate([expect("a")], [call("a"), call("a", q=2)])["efficiency"].passed, "0.5 is the bar"


def test_a_failed_call_that_was_neither_retried_nor_reported_is_an_error_recovery_failure() -> None:
    ignored = evaluate([expect("a")], [call("a", status="error", result="error: boom")], output="All done!")
    assert not ignored["error_recovery"].passed and ignored["error_recovery"].message.startswith("agent ignored")


@pytest.mark.parametrize(
    ("calls", "output"),
    [
        ([call("a", status="error", result="error: boom"), call("a", q=2)], "done"),  # retried
        ([call("a", status="error", result="error: boom")], "Sorry, the lookup failed."),  # told the user
    ],
)
def test_retrying_or_reporting_a_failed_call_is_recovery(calls: list[ToolCall], output: str) -> None:
    assert evaluate([expect("a")], calls, output=output)["error_recovery"].passed


def test_a_test_may_move_the_bars() -> None:
    padded = [call("a"), call("a", q=2), call("a", q=3)]  # efficiency 1/3
    assert not evaluate([expect("a")], padded)["efficiency"].passed
    lenient = evaluate([expect("a")], padded, context={"trajectory_thresholds": {"efficiency": 0.3}})
    assert lenient["efficiency"].passed


# -------------------------------------------------------------------------------------- what it decides
def verdict(results: dict[str, AssertionResult]) -> TestStatus:
    status, _note = decide_attempt_status(
        list(results.values()), [], has_deterministic=True, timed_out=False, stopped_status=None
    )
    return status


def test_a_wrong_selection_fails_the_attempt() -> None:
    assert verdict(evaluate([expect("a")], [call("b")])) == TestStatus.FAILED


def test_wrong_order_alone_does_not_fail_the_attempt() -> None:
    assert verdict(evaluate([expect("a"), expect("b")], [call("b"), call("a")])) == TestStatus.PASSED
