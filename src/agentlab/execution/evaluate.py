"""Evaluation of one attempt: deterministic assertions, trajectory metrics, then the LLM judge.

Order matters (spec section 13/58): deterministic evidence first, the judge sees those results and
only supplements them. A judge that errors or is uncertain never turns into a failure on its own.
"""

from __future__ import annotations

from typing import Any

from agentlab.core.enums import EventType, TestStatus
from agentlab.core.models import AgentProfile, AssertionResult, AssertionSpec, JudgeResult, TestCase
from agentlab.evaluation.assertions import evaluate_assertion
from agentlab.evaluation.context import EvalContext, PlaceholderResolver
from agentlab.evaluation.judge import JudgeEngine, JudgeEvidence
from agentlab.evaluation.trajectory import evaluate_trajectory
from agentlab.execution.engines import AttemptOutcome
from agentlab.tracing import TraceRecorder


def _ctx(
    test: TestCase,
    outcome: AttemptOutcome,
    idx: int,
    resolver: PlaceholderResolver,
    profile: AgentProfile | None,
    known: set[str],
) -> EvalContext:
    return EvalContext(
        test=test,
        turn_index=idx,
        response=outcome.responses[idx],
        responses=outcome.responses,
        inputs=outcome.inputs,
        sessions=outcome.sessions,
        resolver=resolver,
        profile=profile,
        known_sources=known,
        state=outcome.state,
    )


def _record(trace: TraceRecorder, res: AssertionResult) -> None:
    trace.record(
        EventType.ASSERTION_EVALUATED,
        {
            "assertion": res.type,
            "status": "passed" if res.passed else "failed",
            "score": res.score,
            "message": res.message,
            "turn": res.turn_index,
            "required": res.required,
        },
    )
    if not res.passed and res.type in {
        "no_canary_leak",
        "no_secret_leak",
        "no_injection_followed",
        "no_destructive_without_confirmation",
        "no_external_recipient",
    }:
        trace.record(EventType.SECURITY_ALERT, {"assertion": res.type, "message": res.message})


def run_assertions(
    test: TestCase,
    outcome: AttemptOutcome,
    resolver: PlaceholderResolver,
    profile: AgentProfile | None,
    known: set[str],
    trace: TraceRecorder,
) -> list[AssertionResult]:
    results: list[AssertionResult] = []
    n = len(outcome.responses)
    if n == 0:
        return results
    last = n - 1
    pairs: list[tuple[AssertionSpec, int]] = []
    for i, turn in enumerate(test.all_turns()):
        for spec in turn.assertions:
            pairs.append((spec, i))
    for spec in test.assertions:
        pairs.append((spec, spec.turn if spec.turn is not None else last))
    for spec, idx in pairs:
        if idx >= n:
            res = AssertionResult(
                type=spec.type,
                passed=False,
                score=0.0,
                required=False,
                weight=spec.weight,
                message=f"turn {idx + 1} was not reached (test stopped early)",
                turn_index=idx,
            )
        else:
            ctx = _ctx(test, outcome, idx, resolver, profile, known)
            res = evaluate_assertion(spec.type, spec.params, ctx)
            res.turn_index = idx
            res.evidence.setdefault("input", outcome.inputs[idx][:300])
        res.weight = spec.weight
        res.metric = spec.metric or res.metric or spec.type
        res.severity = spec.severity
        res.required = spec.required
        if spec.description and res.passed:
            res.message = f"{spec.description}: {res.message}"
        results.append(res)
        _record(trace, res)
    return results


def run_trajectory(
    test: TestCase,
    outcome: AttemptOutcome,
    resolver: PlaceholderResolver,
    profile: AgentProfile | None,
    known: set[str],
    trace: TraceRecorder,
) -> list[AssertionResult]:
    if not outcome.responses:
        return []
    wants = bool(test.expected_tool_calls) or "trajectory" in test.evaluation_metrics
    if not wants:
        return []
    ctx = _ctx(test, outcome, len(outcome.responses) - 1, resolver, profile, known)
    results = evaluate_trajectory(test, outcome.tool_calls, ctx)
    for r in results:
        _record(trace, r)
    return results


async def run_judge(
    test: TestCase,
    outcome: AttemptOutcome,
    deterministic: list[AssertionResult],
    judge: JudgeEngine | None,
    trace: TraceRecorder,
) -> tuple[list[JudgeResult], list[str]]:
    """Returns (results, notes). Notes explain why a requested criterion could not be judged."""
    if not test.judge:
        return [], []
    if judge is None or not judge.enabled:
        return [], [f"judge criteria {[c.metric for c in test.judge]} were not evaluated: no LLM judge configured"]
    ev = JudgeEvidence(
        test, outcome.inputs, outcome.responses, [{"type": d.type, "passed": d.passed} for d in deterministic]
    )
    results: list[JudgeResult] = []
    for crit in test.judge:
        r = await judge.judge(test, crit, ev)
        results.append(r)
        trace.record(
            EventType.JUDGE_EVALUATED,
            {
                "metric": r.metric,
                "score": r.score,
                "passed": r.passed,
                "confidence": r.confidence,
                "agreement": r.agreement,
                "uncertain": r.uncertain,
                "error": r.error,
                "strategy": r.strategy,
                "judges": [v.judge for v in r.votes],
                "rubric": r.rubric[:400],
            },
        )
    return results, []


def decide_attempt_status(
    deterministic: list[AssertionResult],
    judges: list[JudgeResult],
    *,
    has_deterministic: bool,
    timed_out: bool,
    stopped_status: TestStatus | None,
) -> tuple[TestStatus, str | None]:
    """Reduce evaluation results to an attempt status and an explanatory note."""
    if stopped_status is not None:
        return stopped_status, None
    if timed_out:
        return TestStatus.TIMEOUT, "target did not respond within the test timeout"
    required_fail = [a for a in deterministic if not a.passed and a.required and not a.evaluator_error]
    broken = [a for a in deterministic if a.evaluator_error and a.required]
    judge_fail = [j for j in judges if j.votes and not j.uncertain and not j.passed]
    unusable = [j for j in judges if not j.votes or j.uncertain]
    if required_fail or judge_fail:
        return TestStatus.FAILED, None
    if broken:
        # the check could not run: that is a problem with the test or the evaluator, never a verdict on the target
        return TestStatus.ERROR, f"a check could not be evaluated ({broken[0].type}: {broken[0].message})"
    if unusable and not has_deterministic:
        return TestStatus.ERROR, (
            "LLM judge was unavailable or uncertain and no deterministic assertion could decide; "
            "the verdict needs human review"
        )
    return TestStatus.PASSED, None


def trajectory_summary(outcome: AttemptOutcome) -> dict[str, Any]:
    calls = outcome.tool_calls
    side = {"send_email", "delete_file", "transfer_funds", "post_message", "delete_record"}
    summary: dict[str, Any] = {
        "tool_calls": [{"name": c.name, "arguments": c.arguments, "status": c.status} for c in calls[:50]],
        "tool_call_count": len(calls),
        "tool_errors": any(c.status != "success" for c in calls),
        "contexts_retrieved": sum(len(r.contexts) for r in outcome.responses),
        "side_effects": any(c.name in side for c in calls),
        "turns": len(outcome.responses),
    }
    handoffs = [
        f"{e.data.get('from') or '?'} -> {e.data.get('to') or '?'}"
        for r in outcome.responses
        for e in r.events
        if e.type == "handoff"
    ]
    if handoffs:
        summary["handoffs"] = handoffs[:30]
    retries = sum(r.retries for r in outcome.responses)
    if retries:
        summary["retries"] = retries
    return summary
