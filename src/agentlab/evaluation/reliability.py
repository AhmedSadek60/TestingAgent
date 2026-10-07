"""Reliability analysis over repeated attempts (spec sections 8-O and 32).

A test that passes once and fails repeatedly must never get a simple PASS. Repetition results
are reduced to :class:`ReliabilityStats`, an aggregate :class:`TestStatus`, and a *confidence*
that accounts for evidence type, sample size and consistency.
"""

from __future__ import annotations

import math
import re
import statistics
from collections.abc import Iterable

from agentlab.core.enums import TestStatus
from agentlab.core.models import AttemptResult, ReliabilityStats, TestCase

_WORD = re.compile(r"[a-z0-9']+")


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile (q in 0..100)."""
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * q / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return xs[int(k)]
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def output_variance(outputs: list[str]) -> float:
    """Mean pairwise Jaccard *distance* between the token sets of the outputs (0 = identical)."""
    sets = [set(_WORD.findall(o.lower())) for o in outputs if o is not None]
    if len(sets) < 2:
        return 0.0
    dists = []
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            union = sets[i] | sets[j]
            dists.append(1 - len(sets[i] & sets[j]) / len(union) if union else 0.0)
    return round(statistics.mean(dists), 4)


def failure_signature(attempt: AttemptResult) -> tuple[str, ...]:
    """Which checks failed. Equal signatures across attempts indicate a deterministic failure."""
    sig = [a.type for a in attempt.assertions if not a.passed and a.required and not a.evaluator_error]
    sig += [f"judge:{j.metric}" for j in attempt.judge if not j.passed]
    if attempt.error_kind:
        sig.append(f"error:{attempt.error_kind.value}")
    return tuple(sorted(set(sig)))


_COUNTED = {TestStatus.PASSED, TestStatus.FAILED, TestStatus.ERROR, TestStatus.TIMEOUT}


def compute_reliability(attempts: list[AttemptResult]) -> ReliabilityStats:
    executed = [a for a in attempts if a.status in _COUNTED]
    n = len(executed)
    passes = sum(1 for a in executed if a.status == TestStatus.PASSED)
    failures = [a for a in executed if a.status != TestStatus.PASSED]
    sigs = {failure_signature(a) for a in failures if a.status == TestStatus.FAILED}
    pass_rate = passes / n if n else 0.0
    lat = [a.latency_ms for a in executed if a.latency_ms]
    return ReliabilityStats(
        repetitions=n,
        passes=passes,
        pass_rate=round(pass_rate, 4),
        flaky=0 < passes < n,
        deterministic_failure=n >= 2
        and passes == 0
        and len(sigs) <= 1
        and all(a.status == TestStatus.FAILED for a in failures),
        timeout_rate=round(sum(1 for a in executed if a.status == TestStatus.TIMEOUT) / n, 4) if n else 0.0,
        error_rate=round(sum(1 for a in executed if a.status == TestStatus.ERROR) / n, 4) if n else 0.0,
        output_variance=output_variance([o for a in executed for o in a.outputs[-1:]]),
        latency_p50_ms=round(percentile(lat, 50), 2),
        latency_p95_ms=round(percentile(lat, 95), 2),
    )


def aggregate_status(attempts: list[AttemptResult], pass_threshold: float = 1.0) -> TestStatus:
    """Reduce attempt statuses to one test status.

    * every attempt stopped by a limit -> that STOPPED_DUE_* status (never reported as a failure)
    * every attempt blocked / skipped -> BLOCKED / SKIPPED
    * pass rate over executed attempts >= ``pass_threshold`` -> PASSED (flakiness is still reported)
    * otherwise the dominant failure kind: FAILED > TIMEOUT > ERROR
    """
    if not attempts:
        return TestStatus.SKIPPED
    statuses = [a.status for a in attempts]
    stopped = [s for s in statuses if s.is_stopped]
    executed = [a for a in attempts if a.status in _COUNTED]
    if not executed:
        if stopped:
            return stopped[0]
        if all(s == TestStatus.BLOCKED for s in statuses):
            return TestStatus.BLOCKED
        if all(s == TestStatus.SKIPPED for s in statuses):
            return TestStatus.SKIPPED
        return statuses[0]
    passes = sum(1 for a in executed if a.status == TestStatus.PASSED)
    if passes / len(executed) >= pass_threshold - 1e-9:
        return TestStatus.PASSED
    if any(a.status == TestStatus.FAILED for a in executed):
        return TestStatus.FAILED
    if any(a.status == TestStatus.TIMEOUT for a in executed):
        return TestStatus.TIMEOUT
    return TestStatus.ERROR


def attempt_score(attempt: AttemptResult) -> float:
    """Weighted mean of deterministic and judge scores for one attempt (0..1)."""
    parts: list[tuple[float, float]] = [(a.score, a.weight) for a in attempt.assertions]
    parts += [(j.score, j.weight) for j in attempt.judge if not j.error or j.votes]
    total = sum(w for _s, w in parts)
    if not total:
        return 1.0 if attempt.status == TestStatus.PASSED else 0.0
    return sum(s * w for s, w in parts) / total


def evidence_factor(attempts: Iterable[AttemptResult]) -> float:
    """1.0 when the verdict rests on deterministic evidence; lower when it leans on judges."""
    det_w = judge_w = judge_conf = 0.0
    for a in attempts:
        det_w += sum(x.weight for x in a.assertions)
        for j in a.judge:
            judge_w += j.weight
            judge_conf += j.weight * j.confidence * (0.5 + 0.5 * j.agreement)
    if det_w + judge_w == 0:
        return 0.5
    judge_c = (judge_conf / judge_w) if judge_w else 1.0
    return (det_w * 1.0 + judge_w * judge_c * 0.85) / (det_w + judge_w)


def compute_confidence(test: TestCase, attempts: list[AttemptResult], stats: ReliabilityStats | None) -> float:
    """Confidence in the *verdict* (spec section 32), as the product of explicit factors.

    * evidence      deterministic evidence is trusted fully; judge evidence scaled by agreement/confidence
    * expectation   an explicit expected output or required assertion makes the verdict better grounded
    * sample size   ``1 - 0.15/sqrt(n)`` so a single sample never reaches certainty
    * consistency   flaky behaviour (pass rate near 0.5) lowers confidence in any single verdict
    """
    executed = [a for a in attempts if a.status in _COUNTED]
    if not executed:
        return 0.0
    ev = evidence_factor(executed)
    has_expectation = bool(
        test.expected_output
        or test.expected_tool_calls
        or any(s.required for s in test.assertions)
        or any(t.assertions for t in test.turns)
    )
    expectation = 1.0 if has_expectation else 0.8
    sample = 1 - 0.15 / math.sqrt(len(executed))
    consistency = 1.0
    if stats and stats.repetitions > 1:
        consistency = 0.5 + 0.5 * abs(2 * stats.pass_rate - 1)
    return round(max(0.0, min(1.0, ev * expectation * sample * consistency)), 4)
