"""TestExecutor: lifecycle of one test (gate -> attempts -> evaluation -> aggregation).

Statuses follow the spec lifecycle (section 16). A test that cannot run because a prerequisite is
missing is BLOCKED (never FAILED); budgets produce STOPPED_DUE_* statuses (never FAILED); a target
that does not answer in time is TIMEOUT; infrastructure/evaluator problems are ERROR with a typed
error kind. Repetitions are reduced with reliability statistics so a flaky test never gets a plain PASS.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from statistics import mean
from typing import Any

from agentlab.adapters.base import AgentAdapter, TargetRuntime
from agentlab.adapters.static import NullAdapter
from agentlab.core.config import AgentLabConfig
from agentlab.core.enums import ErrorKind, EventType, RiskClass, TestStatus
from agentlab.core.errors import AgentLabError, CredentialError, PolicyBlocked
from agentlab.core.ids import utcnow
from agentlab.core.models import (
    AgentProfile,
    AssertionResult,
    AttemptResult,
    TestCase,
    TestResult,
)
from agentlab.evaluation.context import PlaceholderResolver
from agentlab.evaluation.findings import assess
from agentlab.evaluation.judge import JudgeEngine
from agentlab.evaluation.reliability import (
    aggregate_status,
    attempt_score,
    compute_confidence,
    compute_reliability,
)
from agentlab.evaluation.scoring import FAILED_SCORE_CEILING
from agentlab.execution.capabilities import describe_missing, missing_capabilities
from agentlab.execution.engines import (
    ENGINES,
    AttemptEnv,
    AttemptOutcome,
    ExecutionEngine,
    needs_adapter,
    pick_engine,
)
from agentlab.execution.evaluate import (
    decide_attempt_status,
    run_assertions,
    run_judge,
    run_trajectory,
    trajectory_summary,
)
from agentlab.execution.limits import (
    CancellationToken,
    CancelledByUser,
    LimitReached,
    LimitTracker,
    repetitions_for,
)
from agentlab.security.gate import AuthorizationGate
from agentlab.storage.artifacts import ArtifactStore
from agentlab.storage.db import Store
from agentlab.tracing import EventBus, TraceRecorder


@dataclass
class ExecutionDeps:
    run_id: str
    runtime: TargetRuntime
    gate: AuthorizationGate
    config: AgentLabConfig
    limits: LimitTracker
    bus: EventBus
    cancel: CancellationToken
    resolver: PlaceholderResolver
    judge: JudgeEngine | None = None
    artifacts: ArtifactStore | None = None
    store: Store | None = None
    profile: AgentProfile | None = None
    engines: dict[str, ExecutionEngine] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.engines:
            self.engines = {name: cls() for name, cls in ENGINES.items()}


class TestExecutor:
    __test__ = False

    def __init__(self, deps: ExecutionDeps) -> None:
        self.d = deps
        self.known_sources: set[str] = set(deps.profile.documents) if deps.profile else set()
        if deps.runtime.spec.mock:
            self.known_sources |= set(deps.runtime.spec.mock.knowledge)
        if deps.runtime.spec.llm:
            self.known_sources |= set(deps.runtime.spec.llm.knowledge)

    # ------------------------------------------------------------------ public
    async def run_test(self, test: TestCase) -> TestResult:
        d = self.d
        base: dict[str, Any] = dict(
            run_id=d.run_id,
            test_id=test.id,
            test_name=test.name,
            category=test.category,
            score_category=test.score_category,
        )
        d.bus.emit(
            d.run_id,
            EventType.TEST_STARTED,
            {"name": test.name, "category": test.category, "objective": test.objective, "skill": test.skill},
            test.id,
        )
        if d.cancel.cancelled:
            return self._finish(TestResult(**base, status=TestStatus.SKIPPED, blocked_reason="run was cancelled"), test)
        try:
            d.limits.check_run()
        except LimitReached as lr:
            return self._finish(TestResult(**base, status=lr.status, blocked_reason=str(lr)), test)

        decision = d.gate.decide(test)
        if decision.blocked:
            kind = ErrorKind.POLICY_BLOCK if decision.block_kind == "policy" else None
            return self._finish(
                TestResult(**base, status=TestStatus.BLOCKED, blocked_reason=decision.blocked_reason, error_kind=kind),
                test,
            )
        if (
            test.judge
            and not test.assertions
            and not any(t.assertions for t in test.all_turns())
            and not test.expected_tool_calls
            and not (d.judge and d.judge.enabled)
        ):
            return self._finish(
                TestResult(
                    **base,
                    status=TestStatus.BLOCKED,
                    blocked_reason=(
                        "all of this test's criteria need an LLM judge but none is configured (judge criteria: "
                        f"{[c.metric for c in test.judge]})"
                    ),
                ),
                test,
            )

        adapter = self._adapter_for(test)
        if adapter is None and not needs_adapter(test, d.engines):
            adapter = NullAdapter(d.runtime.spec, d.runtime.ctx)  # a static check needs no live interface
        if adapter is None:
            return self._finish(
                TestResult(
                    **base,
                    status=TestStatus.BLOCKED,
                    blocked_reason=(
                        f"no usable interface for this test (wanted {test.required_interfaces or 'any'}, "
                        f"available {self.d.runtime.available()}; errors: {self.d.runtime.errors or 'none'})"
                    ),
                ),
                test,
            )

        missing = self._missing_capabilities(test, adapter)
        if missing:
            return self._finish(
                TestResult(**base, status=TestStatus.BLOCKED, blocked_reason=missing),
                test,
            )

        reps = self._repetitions(test, decision.risk)
        started = utcnow()
        attempts: list[AttemptResult] = []
        trace_ids: list[str] = []
        artifact_ids: list[str] = []
        error_kind: ErrorKind | None = None
        for n in range(1, reps + 1):
            if d.cancel.cancelled:
                break
            try:
                d.limits.check_run()
            except LimitReached as lr:
                attempts.append(AttemptResult(attempt=n, status=lr.status, error=str(lr)))
                break
            attempt, trace_id, art = await self._attempt(test, adapter, n)
            attempts.append(attempt)
            if trace_id:
                trace_ids.append(trace_id)
            artifact_ids += art
            if attempt.status.is_stopped or attempt.status == TestStatus.SKIPPED:
                break
        if attempts and all(a.status == TestStatus.ERROR for a in attempts):
            error_kind = attempts[-1].error_kind
        return self._finish(self._assemble(test, base, attempts, trace_ids, artifact_ids, started, error_kind), test)

    # ------------------------------------------------------------------ helpers
    def _adapter_for(self, test: TestCase) -> AgentAdapter | None:
        for iface in test.required_interfaces:
            a = self.d.runtime.adapter(iface)
            if a:
                return a
        if test.required_interfaces:
            return None
        return self.d.runtime.adapter()

    def _missing_capabilities(self, test: TestCase, adapter: AgentAdapter) -> str | None:
        """Tests that must *plant* something in the target (canary, document, poisoned tool output) can only run
        where the interface lets AgentLab do that; otherwise they are BLOCKED with the reason, never faked."""
        needs = list(test.context.get("requires_capabilities") or [])
        if not needs:
            return None
        gate = self.d.gate
        missing = missing_capabilities(
            needs,
            adapter.capabilities,
            known_canaries=bool(self.d.runtime.spec.known_canaries),
            environment={
                "workspace": gate.sandbox_available,
                "local_site": gate.browser_available and gate.locality != "remote",
            },
        )
        return describe_missing(adapter.kind, missing) if missing else None

    def _repetitions(self, test: TestCase, risk: RiskClass) -> int:
        return repetitions_for(self.d.config, test, risk)

    async def _attempt(
        self, test: TestCase, adapter: AgentAdapter, n: int
    ) -> tuple[AttemptResult, str | None, list[str]]:
        d = self.d
        trace = TraceRecorder(d.run_id, test.id, n, d.bus)
        budget = d.limits.budget_for(test)
        engine = pick_engine(test, d.engines)
        env = AttemptEnv(
            run_id=d.run_id,
            attempt=n,
            adapter=adapter,
            trace=trace,
            budget=budget,
            limits=d.limits,
            cancel=d.cancel,
            resolver=d.resolver,
            extras={
                "artifacts": d.artifacts,
                "store": d.store,
                "config": d.config,
                "runtime": d.runtime,
                "gate": d.gate,
                **d.extras,
            },
        )
        t0 = time.perf_counter()
        outcome = AttemptOutcome()
        err: AgentLabError | None = None
        try:
            outcome = await engine.run(test, env)
            err = outcome.error if not outcome.responses else None
        except CancelledByUser:
            return AttemptResult(attempt=n, status=TestStatus.SKIPPED, error="cancelled"), None, []
        except LimitReached as lr:
            outcome.stopped = lr
        except (CredentialError, PolicyBlocked) as exc:
            err = exc
        except asyncio.CancelledError:
            raise
        except AgentLabError as exc:
            err = exc
        except Exception as exc:  # engine defects must not crash the run; they are INFRASTRUCTURE errors
            err = AgentLabError(f"{type(exc).__name__}: {exc}")
            trace.record(EventType.ERROR, {"kind": err.kind.value, "message": str(err)})
        latency = round((time.perf_counter() - t0) * 1000, 2)

        deterministic: list[AssertionResult] = []
        judges: list = []
        notes: list[str] = []
        status = TestStatus.ERROR
        error_text: str | None = None
        error_kind: ErrorKind | None = None
        if err is not None and not outcome.responses and not outcome.timed_out:
            error_text, error_kind = str(err), err.kind
            trace.record(EventType.ERROR, {"kind": err.kind.value, "message": str(err)})
            status = TestStatus.BLOCKED if isinstance(err, CredentialError) else TestStatus.ERROR
            if isinstance(err, CredentialError):
                error_kind = ErrorKind.CREDENTIAL_ERROR
        else:
            deterministic = run_assertions(test, outcome, d.resolver, d.profile, self.known_sources, trace)
            deterministic += run_trajectory(test, outcome, d.resolver, d.profile, self.known_sources, trace)
            stopped_status = outcome.stopped.status if outcome.stopped else None
            if outcome.stopped and test.context.get("limit_is_finding"):
                deterministic.append(
                    AssertionResult(
                        type="limit_exceeded",
                        passed=False,
                        score=0.0,
                        message=(f"agent did not terminate within the configured budget: {outcome.stopped}"),
                        evidence={"status": outcome.stopped.status.value},
                        required=True,
                    )
                )
                stopped_status = None  # for runaway-behaviour tests the limit *is* the observation
            if not outcome.timed_out and stopped_status is None:
                judges, notes = await run_judge(test, outcome, deterministic, d.judge, trace)
            has_det = bool(deterministic) or bool(test.expected_tool_calls)
            status, note = decide_attempt_status(
                deterministic,
                judges,
                has_deterministic=has_det,
                timed_out=outcome.timed_out,
                stopped_status=stopped_status,
            )
            if note:
                notes.append(note)
                error_text, error_kind = note, ErrorKind.EVALUATOR_ERROR
            if status == TestStatus.TIMEOUT:
                error_text, error_kind = "target did not respond within the test timeout", ErrorKind.TIMEOUT
            if status.is_stopped and outcome.stopped:
                error_text = str(outcome.stopped)
            resp_err = next((r.error for r in outcome.responses if r.error), None)
            if resp_err and status == TestStatus.FAILED and error_kind is None:
                error_text, error_kind = resp_err, ErrorKind.TARGET_ERROR

        traj = trajectory_summary(outcome)
        if notes:
            traj["notes"] = notes
        attempt = AttemptResult(
            attempt=n,
            status=status,
            assertions=deterministic,
            judge=judges,
            trajectory=traj,
            latency_ms=latency,
            tokens=budget.tokens,
            cost_usd=round(budget.cost, 8),
            steps=budget.steps,
            error=error_text,
            error_kind=error_kind,
            outputs=[r.output for r in outcome.responses],
        )
        trace_id, artifact_ids = self._persist_trace(test, trace, outcome, attempt)
        attempt.trace_id = trace_id
        return attempt, trace_id, artifact_ids

    def _persist_trace(
        self, test: TestCase, trace: TraceRecorder, outcome: AttemptOutcome, attempt: AttemptResult
    ) -> tuple[str | None, list[str]]:
        d = self.d
        ids = list(outcome.artifacts)
        art_id = None
        if d.artifacts is not None:
            ref = d.artifacts.put_json(
                trace.trace.model_dump(mode="json"),
                kind="trace",
                name=f"{test.id}-attempt{trace.trace.attempt}.trace.json",
                run_id=d.run_id,
                test_key=test.id,
                sensitivity="restricted",
            )
            art_id = ref.id
            ids.append(ref.id)
            if d.store is not None:
                try:
                    d.store.register_artifact(ref)
                except Exception:  # noqa: S110 - artifact already registered by an identical attempt
                    pass
        if d.store is not None:
            d.store.save_trace(trace.trace, art_id)
        return trace.trace.id, ids

    def _assemble(
        self,
        test: TestCase,
        base: dict[str, Any],
        attempts: list[AttemptResult],
        trace_ids: list[str],
        artifact_ids: list[str],
        started: Any,
        error_kind: ErrorKind | None,
    ) -> TestResult:
        threshold = self.d.config.evaluation.pass_threshold
        status = aggregate_status(attempts, threshold)
        stats = compute_reliability(attempts)
        executed = [a for a in attempts if a.status in {TestStatus.PASSED, TestStatus.FAILED}]
        score = mean(attempt_score(a) for a in executed) if executed else 0.0
        if status == TestStatus.FAILED:
            score = min(score, FAILED_SCORE_CEILING)
        elif status == TestStatus.PASSED and stats.flaky:
            score = min(score, 0.5 + 0.5 * stats.pass_rate)
        res = TestResult(
            **base,
            status=status,
            score=round(score, 4),
            attempts=attempts,
            reliability=stats if attempts else None,
            error_kind=error_kind,
            started_at=started,
            finished_at=utcnow(),
            latency_ms=round(mean(a.latency_ms for a in attempts), 2) if attempts else 0.0,
            tokens=sum(a.tokens for a in attempts),
            cost_usd=round(sum(a.cost_usd for a in attempts), 8),
            evidence=sorted(set(artifact_ids)),
            trace_ids=trace_ids,
        )
        res.confidence = compute_confidence(test, attempts, stats)
        if status in {TestStatus.FAILED, TestStatus.TIMEOUT} or status.is_stopped:
            sev, rca = assess(test, res, production=self.d.runtime.spec.safety.production)
            res.root_cause, res.root_cause_confidence = rca.cause, rca.confidence
            if status in {TestStatus.FAILED, TestStatus.TIMEOUT}:
                res.severity = sev.severity
            if status.is_stopped:
                res.blocked_reason = next((a.error for a in attempts if a.error), None)
        return res

    def _finish(self, result: TestResult, test: TestCase) -> TestResult:
        d = self.d
        if result.finished_at is None:
            result.finished_at = utcnow()
        if result.status.is_stopped:
            d.bus.emit(
                d.run_id,
                EventType.LIMIT_REACHED,
                {"status": result.status.value, "reason": result.blocked_reason},
                test.id,
            )
        d.bus.emit(
            d.run_id,
            EventType.TEST_COMPLETED,
            {
                "status": result.status.value,
                "score": result.score,
                "confidence": result.confidence,
                "severity": result.severity.value if result.severity else None,
                "reason": result.blocked_reason,
                "latency_ms": result.latency_ms,
                "tokens": result.tokens,
                "cost_usd": result.cost_usd,
                "root_cause": result.root_cause.value if result.root_cause else None,
            },
            test.id,
        )
        if d.store is not None:
            d.store.save_result(result)
        return result


__all__ = ["ExecutionDeps", "TestExecutor"]
