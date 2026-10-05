"""Trajectory evaluation (evaluation layer 3).

Compares the *observed* sequence of tool calls against the test's expectations and reports
tool selection, argument accuracy, unnecessary actions, ordering, efficiency, error recovery
and stopping behaviour. Only observable events are used; hidden reasoning is never required.
"""

from __future__ import annotations

import json
from typing import Any

from agentlab.core.models import AssertionResult, ExpectedToolCall, TestCase, ToolCall
from agentlab.evaluation.assertions import _match_args
from agentlab.evaluation.context import EvalContext

DEFAULT_THRESHOLDS = {"tool_selection": 1.0, "tool_arguments": 1.0, "unnecessary_actions": 1.0,
                      "tool_ordering": 1.0, "efficiency": 0.5, "error_recovery": 1.0}
REQUIRED = {"tool_selection", "tool_arguments", "unnecessary_actions"}


def _match(expected: list[ExpectedToolCall], actual: list[ToolCall]) -> list[tuple[int, int | None]]:
    used: set[int] = set()
    pairs: list[tuple[int, int | None]] = []
    for ei, e in enumerate(expected):
        hit = next((ai for ai, a in enumerate(actual) if ai not in used and a.name == e.name), None)
        if hit is not None:
            used.add(hit)
        pairs.append((ei, hit))
    return pairs


def _arg_score(e: ExpectedToolCall, a: ToolCall, ctx: EvalContext) -> tuple[float, list[str]]:
    if e.match == "name_only" or e.arguments is None:
        return 1.0, []
    score, bad = _match_args(e.arguments, a.arguments, ctx)
    if e.match == "exact":
        extra = [k for k in a.arguments if k not in e.arguments]
        if extra:
            bad.append(f"unexpected argument(s) {extra}")
            score = min(score, 1 - len(extra) / max(1, len(a.arguments)))
    return score, bad


def evaluate_trajectory(test: TestCase, calls: list[ToolCall], ctx: EvalContext) -> list[AssertionResult]:
    expected = test.expected_tool_calls
    thresholds = {**DEFAULT_THRESHOLDS, **(test.context.get("trajectory_thresholds") or {})}
    results: list[AssertionResult] = []

    def add(metric: str, score: float, msg: str, **ev: Any) -> None:
        passed = score >= thresholds[metric] - 1e-9
        results.append(AssertionResult(
            type=f"trajectory:{metric}", passed=passed, score=max(0.0, min(1.0, score)), message=msg, metric=metric,
            weight=1.0 if metric in REQUIRED else 0.5, required=metric in REQUIRED, evidence=ev))

    pairs = _match(expected, calls)
    matched_actual = {a for _e, a in pairs if a is not None}
    exp_names = [e.name for e in expected]
    act_names = [c.name for c in calls]

    # --- selection (F1 of expected vs observed tool names)
    recall = sum(1 for _e, a in pairs if a is not None) / len(expected) if expected else 1.0
    precision = len(matched_actual) / len(calls) if calls else (1.0 if not expected else 0.0)
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    missing = [exp_names[e] for e, a in pairs if a is None]
    extra = [act_names[i] for i in range(len(calls)) if i not in matched_actual]
    if f1 >= 1.0:
        add("tool_selection", 1.0, f"agent selected exactly the expected tools {exp_names or '[none]'}")
    else:
        parts = []
        if missing:
            parts.append(f"did not call {missing}")
        if extra:
            parts.append(f"called unexpected/extra {extra}")
        add("tool_selection", f1, "tool selection incorrect: " + "; ".join(parts), expected=exp_names, observed=act_names)

    # --- arguments
    if expected:
        scores, problems = [], []
        for ei, ai in pairs:
            if ai is None:
                continue
            s, bad = _arg_score(expected[ei], calls[ai], ctx)
            scores.append(s)
            problems += [f"{expected[ei].name}: {b}" for b in bad]
        if scores:
            avg = sum(scores) / len(scores)
            if avg >= 1.0:
                add("tool_arguments", 1.0, "all tool arguments were correct")
            else:
                add("tool_arguments", avg, "incorrect tool arguments: " + "; ".join(problems[:4]),
                    observed=[c.arguments for c in calls][:5])
        elif any(e.arguments for e in expected):
            add("tool_arguments", 0.0, "no expected tool was called, so arguments could not be correct")

    # --- unnecessary actions: extra calls and duplicates
    seen: dict[str, int] = {}
    for c in calls:
        key = c.name + json.dumps(c.arguments, sort_keys=True, default=str)
        seen[key] = seen.get(key, 0) + 1
    duplicates = sum(v - 1 for v in seen.values() if v > 1)
    unnecessary = len(extra) + duplicates if expected else duplicates
    if not calls:
        add("unnecessary_actions", 1.0, "no tool calls were made")
    elif unnecessary == 0:
        add("unnecessary_actions", 1.0, "no unnecessary or duplicate tool calls")
    else:
        add("unnecessary_actions", max(0.0, 1 - unnecessary / len(calls)),
            f"{unnecessary} unnecessary/duplicate tool call(s) out of {len(calls)}", observed=act_names[:12])

    # --- ordering
    idxs = [a for _e, a in pairs if a is not None]
    if len(idxs) >= 2:
        inversions = sum(1 for i in range(len(idxs)) for j in range(i + 1, len(idxs)) if idxs[i] > idxs[j])
        total = len(idxs) * (len(idxs) - 1) / 2
        add("tool_ordering", 1 - inversions / total,
            "tools were called in the expected order" if inversions == 0 else
            f"tools called out of order (expected {exp_names}, observed {act_names})")

    # --- efficiency
    if expected:
        eff = min(1.0, len(expected) / len(calls)) if calls else 0.0
        add("efficiency", eff, f"{len(calls)} tool call(s) used for {len(expected)} required")

    # --- error recovery
    errors = [i for i, c in enumerate(calls) if c.status != "success" or str(c.result).lower().startswith("error")]
    if errors:
        last = errors[-1]
        recovered = last < len(calls) - 1 or "error" in ctx.response.output.lower() or "unable" in ctx.response.output.lower() \
            or "sorry" in ctx.response.output.lower()
        add("error_recovery", 1.0 if recovered else 0.0,
            "agent recovered from or reported the tool failure" if recovered else
            "agent ignored a tool failure and gave no indication of it", failed_tool=calls[last].name)
    return results
