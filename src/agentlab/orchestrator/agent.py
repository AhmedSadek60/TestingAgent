"""TestOrchestratorAgent (spec section 9): the seventeen-phase pipeline from "a target" to "a scored, explained run".

``prepare`` runs phases 1-7 (validate, ingest, fingerprint, prepare the environment, select skills, design the plan,
classify risk): the target is understood and the plan exists, but nothing risky has run. ``execute`` runs phases 8-17
(execute, collect traces, evaluate deterministically, judge, cross-test / security / reliability analysis, score,
report, package). Every phase emits structured events, so a CLI, the REST API and the web UI all show the same
live progress. Phases 9-11 happen *inside* each test, so they are marked ``streamed`` and summarised when the
execution phase ends rather than pretending they run afterwards.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Callable
from typing import Any, Literal

from agentlab import __version__
from agentlab.adapters.base import TargetRuntime
from agentlab.core.enums import EventType, Phase, RiskClass, RunStatus, Severity, TestStatus
from agentlab.core.errors import AgentLabError, UserError
from agentlab.core.ids import new_id, utcnow
from agentlab.core.models import Finding, Scorecard, TargetSpec, TestCase, TestResult
from agentlab.design import SUITES, TestDesignerAgent
from agentlab.design.models import PlannedTest, PlanWarning, TestPlan
from agentlab.design.render import plan_markdown
from agentlab.design.user_tests import load_user_tests
from agentlab.discovery.agent import IngestedTarget, TargetDiscoveryAgent
from agentlab.evaluation.context import PlaceholderResolver
from agentlab.evaluation.findings import build_finding, cross_test_findings
from agentlab.evaluation.scoring import build_scorecard, load_profile, select_profile
from agentlab.execution.executor import ExecutionDeps, TestExecutor
from agentlab.execution.limits import CancellationToken, CancelledByUser, LimitReached, LimitTracker
from agentlab.execution.scheduler import Scheduler
from agentlab.orchestrator.analysis import (
    CrossTestAnalysis,
    ReliabilityAnalysis,
    ScopeNote,
    SecurityAnalysis,
    add_grade_note,
    analyse_cross_test,
    analyse_reliability,
    analyse_security,
    apply_adaptive_to_findings,
    assess_scope,
    is_diagnostic,
    is_security_test,
    scored_results,
)
from agentlab.orchestrator.environment import (
    build_skill_context,
    check_reachability,
    open_runtime,
    prepare_judge,
)
from agentlab.orchestrator.manifest import build_manifest
from agentlab.orchestrator.options import (
    EnvironmentReport,
    PhaseRecord,
    PreparedRun,
    RunOptions,
    RunOutcome,
)
from agentlab.security.egress import EgressPolicy
from agentlab.security.gate import AuthorizationGate
from agentlab.security.redactor import redact
from agentlab.services import Services
from agentlab.skills.context import INTENSITIES
from agentlab.tracing import Event, EventBus

log = logging.getLogger(__name__)
TOTAL_PHASES = len(Phase)
PHASE_INDEX = {p: i + 1 for i, p in enumerate(Phase)}
MAX_WAVES = 2  # wave 1 (the plan) plus at most one adaptive wave


class _PhaseScope:
    """Emits PhaseStarted / PhaseCompleted and keeps a :class:`PhaseRecord`. Usable as a context manager."""

    def __init__(
        self,
        agent: TestOrchestratorAgent,
        run_id: str,
        phase: Phase,
        records: list[PhaseRecord],
        streamed: bool = False,
    ) -> None:
        self.agent, self.run_id, self.phase, self.records, self.streamed = agent, run_id, phase, records, streamed
        self.detail: dict[str, Any] = {}
        self.status: Literal["completed", "skipped", "failed"] = "completed"
        self.note: str | None = None
        self._t0 = 0.0
        self._done = False

    def start(self) -> _PhaseScope:
        self._t0 = time.monotonic()
        self.agent.bus.emit(
            self.run_id,
            EventType.PHASE_STARTED,
            {
                "phase": self.phase.value,
                "index": PHASE_INDEX[self.phase],
                "total": TOTAL_PHASES,
                "streamed": self.streamed,
            },
        )
        return self

    def set(self, **detail: Any) -> None:
        self.detail.update(detail)

    def skip(self, why: str) -> None:
        self.status, self.note = "skipped", why

    def finish(self, **detail: Any) -> PhaseRecord | None:
        if self._done:
            return None
        self._done = True
        self.detail.update(detail)
        rec = PhaseRecord(
            phase=self.phase,
            index=PHASE_INDEX[self.phase],
            status=self.status,
            streamed=self.streamed,
            duration_s=round(time.monotonic() - self._t0, 3),
            detail=self.detail,
            note=self.note,
        )
        self.records.append(rec)
        self.agent.bus.emit(
            self.run_id,
            EventType.PHASE_COMPLETED,
            {
                "phase": self.phase.value,
                "index": rec.index,
                "total": TOTAL_PHASES,
                "status": rec.status,
                "streamed": self.streamed,
                "duration_s": rec.duration_s,
                "note": rec.note,
                "detail": self.detail,
            },
        )
        return rec

    def __enter__(self) -> _PhaseScope:
        return self.start()

    def __exit__(self, et: Any, ev: BaseException | None, tb: Any) -> None:
        if ev is not None:
            self.status, self.note = "failed", f"{type(ev).__name__}: {ev}"[:300]
        self.finish()


def prediction_check(planned: list[PlannedTest], results: list[TestResult]) -> dict[str, Any]:
    """How well the plan's BLOCKED prediction matched what happened (a self-check of the planner)."""
    by_id = {r.test_id: r for r in results}
    agree = 0
    compared = 0
    blocked_unexpectedly: list[str] = []
    ran_unexpectedly: list[str] = []
    for p in planned:
        r = by_id.get(p.id)
        if r is None or r.status == TestStatus.SKIPPED or r.status.is_stopped:
            continue
        compared += 1
        was_blocked = r.status == TestStatus.BLOCKED
        if (p.predicted == "blocked") == was_blocked:
            agree += 1
        elif was_blocked:
            blocked_unexpectedly.append(p.id)
        else:
            ran_unexpectedly.append(p.id)
    return {
        "compared": compared,
        "agree": agree,
        "blocked_unexpectedly": blocked_unexpectedly[:20],
        "ran_unexpectedly": ran_unexpectedly[:20],
    }


class TestOrchestratorAgent:
    __test__ = False

    def __init__(
        self, services: Services, *, designer: TestDesignerAgent | None = None, bus: EventBus | None = None
    ) -> None:
        self.services = services
        self.config = services.config
        self.designer = designer or TestDesignerAgent()
        self.bus = bus or EventBus()
        self._active: set[str] = set()
        self._tokens: dict[str, CancellationToken] = {}
        self.bus.subscribe(self._persist_event)

    # ------------------------------------------------------------------ plumbing
    def _persist_event(self, ev: Event) -> None:
        if ev.run_id in self._active:
            try:
                self.services.store.add_event(ev)
            except Exception:  # the live stream must survive a storage hiccup
                log.exception("could not persist event %s", ev.type)

    def _phase(self, run_id: str, phase: Phase, records: list[PhaseRecord], *, streamed: bool = False) -> _PhaseScope:
        return _PhaseScope(self, run_id, phase, records, streamed)

    def token_for(self, run_id: str) -> CancellationToken:
        """The cancellation token of a run (created on first use), so Ctrl-C or a cancel button can be wired before the
        run starts."""
        return self._tokens.setdefault(run_id, CancellationToken())

    def cancel(self, run_id: str, reason: str = "cancelled by user") -> bool:
        """Ask a running (or preparing) run to stop safely: running tests finish their step, the rest are SKIPPED,
        and the partial results are still analysed and reported."""
        token = self._tokens.get(run_id)
        if token is None:
            return False
        token.cancel(reason)
        return True

    def _artifact(
        self, run_id: str, kind: str, name: str, content: Any, *, media_type: str = "application/json"
    ) -> str | None:
        try:
            put = self.services.artifacts.put_json if media_type == "application/json" else self.services.artifacts.put
            kwargs: dict[str, Any] = {"kind": kind, "name": name, "run_id": run_id}
            if media_type != "application/json":
                kwargs["media_type"] = media_type
            ref = put(content, **kwargs)
            self.services.store.register_artifact(ref)
            return ref.id
        except Exception as exc:
            log.warning("could not store artifact %s: %s", name, exc)
            return None

    # ================================================================== phases 1-7
    async def prepare(self, spec: TargetSpec, options: RunOptions | None = None) -> PreparedRun:
        opts = options or RunOptions()
        cfg = self.config
        sv = self.services
        run_id = opts.run_id or new_id()
        records: list[PhaseRecord] = []
        warnings: list[str] = list(sv.startup_warnings)
        token = self._tokens.setdefault(run_id, CancellationToken())

        project = sv.store.ensure_project(opts.project)
        target = sv.store.add_target(project["id"], spec)
        mode = "regression" if opts.baseline_run_id else opts.suite
        sv.store.create_run(
            project["id"],
            target["id"],
            None,
            mode,
            {"agentlab_version": __version__},
            cfg.limits.model_dump(mode="json"),
            run_id=run_id,
        )
        self._active.add(run_id)
        self.bus.emit(
            run_id,
            EventType.RUN_STARTED,
            {"target": spec.name, "suite": mode, "intensity": opts.intensity, "plan_only": opts.plan_only},
        )
        workdir = sv.workdir(run_id)
        held_runtime: TargetRuntime | None = None
        held_ingest: IngestedTarget | None = None
        try:
            # ---- 1. input validation -------------------------------------------------------------------------
            with self._phase(run_id, Phase.INPUT_VALIDATION, records) as ph:
                user_tests, vwarn = await self._validate(spec, opts)
                warnings += vwarn
                docker_ok, docker_note = await sv.docker_status()
                browser_ok, browser_note = sv.browser_status()
                ph.set(
                    interfaces=spec.interfaces(),
                    warnings=len(vwarn),
                    user_tests=len(user_tests),
                    docker=docker_ok,
                    browser=browser_ok,
                )
            token.raise_if_cancelled()
            resolver = PlaceholderResolver(prefix=cfg.security.canary_prefix)
            resolver.register_known(spec.known_canaries)
            discoverer = TargetDiscoveryAgent(
                cfg,
                providers=sv.providers,
                credentials=sv.credentials,
                bus=self.bus,
                artifacts=sv.artifacts,
                docker_available=docker_ok,
                browser_available=browser_ok,
                workspace=workdir,
                run_id=run_id,
                web_discoverer=sv.web_discoverer,
                extras={"canary_secret": resolver.canary("system_secret")},
            )

            # ---- 2. target ingestion -------------------------------------------------------------------------
            with self._phase(run_id, Phase.TARGET_INGESTION, records) as ph:
                ingested = await discoverer.ingest(spec)
                held_ingest = ingested
                ra = ingested.repo_analysis
                ph.set(
                    repository=(
                        {"files": ra.file_count, "languages": ra.languages, "frameworks": ra.frameworks[:10]}
                        if ra
                        else None
                    ),
                    documents=len(ingested.documents),
                    openapi_endpoints=len(ingested.openapi.endpoints) if ingested.openapi else 0,
                    warnings=len(ingested.warnings),
                )
            token.raise_if_cancelled()

            # ---- 3. target fingerprinting --------------------------------------------------------------------
            with self._phase(run_id, Phase.TARGET_FINGERPRINTING, records) as ph:
                discovery = await discoverer.discover(
                    spec, probe=opts.probe, ingested=ingested, user_description=opts.objective or spec.objective
                )
                profile = discovery.profile
                warnings += discovery.warnings
                sv.store.save_profile(target["id"], run_id, profile)
                ph.set(
                    types=[{"type": t.type.value, "confidence": round(t.confidence, 2)} for t in profile.types[:5]],
                    tools=len(profile.tools),
                    modes=[m.value for m in profile.modes],
                    probed=discovery.probe is not None,
                )
            token.raise_if_cancelled()

            # ---- 4. environment preparation ------------------------------------------------------------------
            report = EnvironmentReport(
                docker=docker_ok,
                docker_note=docker_note,
                browser=browser_ok,
                browser_note=browser_note,
                sandbox_provider=cfg.security.sandbox.provider,
            )
            with self._phase(run_id, Phase.ENVIRONMENT_PREPARATION, records) as ph:
                runtime = await open_runtime(sv, spec, run_id=run_id, resolver=resolver, workdir=workdir)
                held_runtime = runtime
                report.interface_errors = dict(runtime.errors)
                await check_reachability(runtime, report)
                report.interfaces = runtime.available()
                judge = prepare_judge(sv, spec, profile, opts, report)
                caps = [a.capabilities for a in runtime.adapters.values()]
                report.canary_seeding = bool(spec.known_canaries) or any(c.canary_seeding for c in caps)
                report.parallelism = 1 if any(not c.parallel_sessions for c in caps) else max(1, cfg.max_parallel)
                for kind, why in runtime.errors.items():
                    report.warnings.append(f"interface '{kind}' is unavailable: {why}")
                if not report.docker:
                    report.warnings.append(f"Docker unavailable ({docker_note}); sandboxed tests will be BLOCKED")
                if not report.browser:
                    report.warnings.append(f"browser unavailable ({browser_note}); browser tests will be BLOCKED")
                warnings += report.warnings
                ph.set(**report.model_dump(mode="json", exclude={"warnings", "docker_note", "browser_note"}))
            token.raise_if_cancelled()

            fixtures_dir = workdir / "fixtures"
            ctx = build_skill_context(
                services=sv,
                spec=spec,
                profile=profile,
                ingested=ingested,
                discovery=discovery,
                runtime=runtime,
                report=report,
                options=opts,
                fixtures_dir=fixtures_dir,
                config=cfg,
            )
            registry = self.designer.registry_for(cfg)
            gate = AuthorizationGate(
                spec,
                cfg,
                credentials=sv.credentials,
                interfaces=runtime.available(),
                sandbox_available=report.docker,
                judge_available=report.judge,
                browser_available=report.browser,
            )

            # ---- 5. skill selection --------------------------------------------------------------------------
            selection: list[Any] = []
            with self._phase(run_id, Phase.SKILL_SELECTION, records) as ph:
                if opts.plan is not None or opts.baseline_run_id:
                    ph.skip("an existing plan is being executed; skills are not re-selected")
                else:
                    selection = registry.select(ctx, include=opts.include_skills, exclude=opts.exclude_skills)
                    chosen = [(s, m) for s, m in selection if m.selected]
                    for skill, m in chosen:
                        self.bus.emit(
                            run_id,
                            EventType.SKILL_SELECTED,
                            {
                                "skill": skill.name,
                                "version": skill.version,
                                "score": round(m.score, 3),
                                "reasons": m.reasons[:3],
                                "taxonomy": m.taxonomy,
                                "trust": m.trust,
                            },
                        )
                    ph.set(
                        considered=len(selection),
                        selected=[m.skill for _, m in chosen],
                        skipped=[{"skill": m.skill, "why": m.skipped_reason} for _, m in selection if not m.selected][
                            :40
                        ],
                    )
            token.raise_if_cancelled()

            # ---- 6. test plan generation ---------------------------------------------------------------------
            with self._phase(run_id, Phase.TEST_PLAN_GENERATION, records) as ph:
                plan = await self._design(spec, opts, ctx, selection, user_tests, ph)
                for kind, why in {**report.interface_errors, **report.unreachable}.items():
                    plan.warnings.append(
                        PlanWarning(
                            level="blocker",
                            code="interface_unavailable",
                            message=f"interface '{kind}' is unavailable ({why}); tests that need it cannot run, so "
                            "nothing is verified through it",
                        )
                    )
                suite = sv.store.save_suite(
                    project["id"],
                    target["id"],
                    f"{spec.name}/{plan.suite}",
                    plan.suite,
                    plan.test_cases(),
                    plan.model_dump(mode="json"),
                )
                sv.store.update_run(run_id, suite_id=suite["id"])
                self._store_plan(run_id, plan)
                c = plan.counts()
                self.bus.emit(
                    run_id,
                    EventType.TEST_PLAN_GENERATED,
                    {
                        "plan_id": plan.id,
                        "wave": plan.wave,
                        "plan_hash": plan.plan_hash,
                        "tests": c["tests"],
                        "runnable": c["runnable"],
                        "blocked": c["blocked"],
                        "skills": [m.skill for m in plan.skills if m.selected],
                        "estimated_cost_usd": plan.budget.est_cost_usd,
                        "estimated_target_calls": plan.budget.target_calls,
                    },
                )
                ph.set(plan_id=plan.id, plan_hash=plan.plan_hash, **c)
            token.raise_if_cancelled()

            # ---- 7. risk classification ----------------------------------------------------------------------
            with self._phase(run_id, Phase.RISK_CLASSIFICATION, records) as ph:
                by_risk = Counter(p.risk.value for p in plan.selected_tests())
                blocked_kinds = Counter(p.blocked_kind or "other" for p in plan.predicted_blocked())
                needs_auth = [
                    p.id for p in plan.predicted_blocked() if p.blocked_kind == "policy" and p.risk != RiskClass.SAFE
                ]
                ph.set(
                    by_risk=dict(by_risk),
                    predicted_blocked=dict(blocked_kinds),
                    awaiting_authorization=len(needs_auth),
                    note="risk classes are assigned per test by the AuthorizationGate; destructive tests never run "
                    "without the owner's explicit authorization",
                )
            scoring = select_profile(
                profile,
                opts.scoring_profile or cfg.evaluation.scoring_profile,
                production=spec.safety.production,
            )
            manifest = build_manifest(
                run_id=run_id,
                config=cfg,
                spec=spec,
                profile=profile,
                plan=plan,
                skills=[registry.get(m.skill) for m in plan.skills if m.selected and m.skill in registry],
                scoring=scoring,
                judge={
                    "enabled": report.judge,
                    "independent": report.judge_independent,
                    "strategy": cfg.evaluation.judge_strategy,
                    "judges": [j.model_dump(mode="json") for j in cfg.evaluation.judges] if report.judge else [],
                    "note": report.judge_note,
                },
                environment=redact(report.model_dump(mode="json")),
                options={
                    "seed": opts.seed,
                    "second_wave": opts.second_wave,
                    "intensity": opts.intensity,
                    "baseline_run_id": opts.baseline_run_id,
                    "user_test_files": [str(p.name) for p in opts.user_test_files],
                },
            )
            sv.store.update_run(run_id, manifest=manifest)
            return PreparedRun(
                run_id=run_id,
                spec=spec,
                options=opts,
                project_id=project["id"],
                target_id=target["id"],
                profile=profile,
                discovery=discovery,
                ingested=ingested,
                ctx=ctx,
                plan=plan,
                runtime=runtime,
                gate=gate,
                resolver=resolver,
                judge=judge,
                scoring=scoring,
                environment=report,
                bus=self.bus,
                workdir=workdir,
                manifest=manifest,
                phases=records,
                warnings=warnings,
                suite_id=suite["id"],
                selected_skills=[m.skill for m in plan.skills if m.selected],
            )
        except BaseException as exc:
            await self._abort(run_id, exc, held_runtime, held_ingest, workdir, opts)
            raise

    async def _abort(
        self, run_id: str, exc: BaseException, runtime: Any, ingested: Any, workdir: Any, opts: RunOptions
    ) -> None:
        cancelled = isinstance(exc, CancelledByUser | asyncio.CancelledError)
        status = RunStatus.CANCELLED if cancelled else RunStatus.FAILED
        info = {"kind": getattr(getattr(exc, "kind", None), "value", "USER_ERROR"), "message": str(exc)[:500]}
        try:
            self.services.store.update_run(
                run_id, status=status.value, finished_at=utcnow(), error=None if cancelled else info
            )
            self.bus.emit(
                run_id, EventType.RUN_CANCELLED if cancelled else EventType.RUN_FAILED, {"phase": "prepare", **info}
            )
        except Exception:
            log.exception("could not record the failed run")
        if runtime is not None:
            try:
                await runtime.close()
            except Exception:  # noqa: S110 - best-effort cleanup
                pass
        if ingested is not None:
            ingested.cleanup()
        if not opts.keep_workspace:
            import shutil

            shutil.rmtree(workdir, ignore_errors=True)
        self._active.discard(run_id)
        self._tokens.pop(run_id, None)

    async def _validate(self, spec: TargetSpec, opts: RunOptions) -> tuple[list[TestCase], list[str]]:
        cfg = self.config
        warns: list[str] = []
        suite = "regression" if opts.baseline_run_id else opts.suite
        if suite not in SUITES:
            raise UserError(f"unknown suite '{suite}' (known: {', '.join(SUITES)})")
        if opts.intensity not in INTENSITIES:
            raise UserError(f"unknown intensity '{opts.intensity}' (known: {', '.join(INTENSITIES)})")
        if suite == "regression" and not opts.baseline_run_id and opts.plan is None:
            raise UserError("a regression run needs the id of the run to compare against (baseline_run_id)")
        if not (
            spec.interfaces()
            or spec.repository
            or spec.documents
            or spec.description
            or opts.objective
            or spec.declared_tools
        ):
            raise UserError(
                "nothing to test: give the target a repository, a URL/API/MCP server/command, documents "
                "or at least a description"
            )
        if not spec.interfaces():
            warns.append(
                "the target has no runnable interface (api, web, command, mcp, llm or mock): AgentLab can analyse it "
                "and plan tests, but every test that must talk to it will be BLOCKED"
            )
        egress = EgressPolicy(
            block_metadata=cfg.security.block_metadata_endpoints, allow_private=cfg.security.allow_private_networks
        )
        urls = [
            spec.api.url if spec.api else None,
            spec.api.openapi_url if spec.api else None,
            spec.web.url if spec.web else None,
            spec.mcp.url if spec.mcp else None,
        ]
        for url in filter(None, urls):
            egress.check(url)  # raises PolicyBlocked for metadata endpoints / disallowed networks
        for name in spec.credentials:
            if not self.services.credentials.has(name):
                warns.append(f"credential profile '{name}' is not stored; tests that need it will be BLOCKED")
        if spec.safety.production and not cfg.security.allow_production_targets:
            warns.append(
                "the target is marked production: adversarial and HIGH_IMPACT tests are blocked unless "
                "security.allow_production_targets is enabled and the owner authorizes them"
            )
        profile_name = opts.scoring_profile or cfg.evaluation.scoring_profile
        if profile_name:
            load_profile(profile_name)  # raises UserError naming the known profiles
        registry = self.designer.registry_for(cfg)
        unknown = [n for n in (opts.include_skills or []) if n not in registry]
        if unknown:
            raise UserError(f"unknown skill(s) to include: {', '.join(unknown)} (known: {', '.join(registry.names())})")
        warns += [f"skill '{n}' to exclude is not installed" for n in (opts.exclude_skills or []) if n not in registry]
        user_tests = list(opts.user_tests)
        if opts.user_test_files:
            loaded, problems = load_user_tests(opts.user_test_files)
            if problems and not loaded and not user_tests:
                raise UserError("no usable tests in the supplied test files: " + "; ".join(problems[:5]))
            warns += problems
            user_tests += loaded
        return user_tests, warns

    async def _design(
        self,
        spec: TargetSpec,
        opts: RunOptions,
        ctx: Any,
        selection: list[Any],
        user_tests: list[TestCase],
        ph: _PhaseScope,
    ) -> TestPlan:
        plan = await self._design_plan(spec, opts, ctx, selection, user_tests, ph)
        if opts.only_tests:
            plan = self.designer.restrict(plan, ctx, opts.only_tests)
            ph.set(restricted_to=list(opts.only_tests))
        return plan

    async def _design_plan(
        self,
        spec: TargetSpec,
        opts: RunOptions,
        ctx: Any,
        selection: list[Any],
        user_tests: list[TestCase],
        ph: _PhaseScope,
    ) -> TestPlan:
        cfg = self.config
        if opts.plan is not None:
            plan = self.designer.finalize(opts.plan.model_copy(deep=True), ctx, trim=False)
            ph.set(source="approved plan")
            return plan
        if opts.baseline_run_id:
            run = self.services.store.get_run(opts.baseline_run_id)
            if not run.get("suite_id"):
                raise UserError(f"run '{opts.baseline_run_id}' has no stored plan to compare against")
            suite, _ = self.services.store.get_suite(run["suite_id"])
            if not suite.get("plan"):
                raise UserError(f"run '{opts.baseline_run_id}' has no stored plan to compare against")
            ph.set(source=f"regression of run {opts.baseline_run_id}")
            return self.designer.regression(TestPlan.model_validate(suite["plan"]), ctx)
        plan = self.designer.design(
            ctx,
            suite=opts.suite,
            include=opts.include_skills,
            exclude=opts.exclude_skills,
            max_tests=opts.max_tests,
            user_tests=user_tests,
            selection=selection,
        )
        if cfg.evaluation.llm_test_generation and self.services.providers.names():
            plan = await self.designer.enhance(plan, ctx, self.services.providers)
        ph.set(source="designed")
        return plan

    def _store_plan(self, run_id: str, plan: TestPlan) -> None:
        self._artifact(run_id, "plan", f"plan-wave{plan.wave}.json", plan.model_dump(mode="json"))
        self._artifact(
            run_id, "plan", f"plan-wave{plan.wave}.md", plan_markdown(plan, detail=True), media_type="text/markdown"
        )

    # ================================================================== phases 8-17
    async def execute(
        self,
        prepared: PreparedRun,
        *,
        cancel: CancellationToken | None = None,
        reporter: Callable[..., Any] | None = None,
    ) -> RunOutcome:
        p = prepared
        sv = self.services
        cfg = self.config
        run_id = p.run_id
        token = cancel or self._tokens.setdefault(run_id, CancellationToken())
        self._tokens[run_id] = token
        self._active.add(run_id)
        limits = LimitTracker(cfg.limits)
        if p.judge is not None:
            p.judge.usage_sink = lambda _kind, tokens, cost: limits.record(
                None, tokens=tokens, cost=cost, category="judge"
            )
        records = p.phases
        started = utcnow()
        sv.store.update_run(run_id, status=RunStatus.RUNNING.value, started_at=started)

        waves: list[TestPlan] = [p.plan]
        tests: dict[str, TestCase] = {t.id: t for t in p.plan.test_cases()}
        results: dict[str, TestResult] = {}
        findings: list[Finding] = []
        run_stop: LimitReached | None = None
        error: str | None = None
        status = RunStatus.COMPLETED

        deps = ExecutionDeps(
            run_id=run_id,
            runtime=p.runtime,
            gate=p.gate,
            config=cfg,
            limits=limits,
            bus=self.bus,
            cancel=token,
            resolver=p.resolver,
            judge=p.judge,
            artifacts=sv.artifacts,
            store=sv.store,
            profile=p.profile,
            extras={
                "fixtures_dir": p.ctx.fixtures_dir,
                "workdir": p.workdir,
                "sandbox": sv.sandbox,
                "credentials": sv.credentials,
                "providers": sv.providers,
            },
        )
        executor = TestExecutor(deps)

        async def on_result(res: TestResult) -> None:
            nonlocal run_stop
            results[res.test_id] = res
            test = tests.get(res.test_id)
            if test is not None and not is_diagnostic(test):
                f = build_finding(run_id, test, res, p.spec.name, production=p.spec.safety.production)
                if f is not None:
                    findings.append(f)
                    sv.store.save_findings(run_id, [f])
                    self.bus.emit(
                        run_id,
                        EventType.FINDING_CREATED,
                        {
                            "finding_id": f.id,
                            "title": f.title,
                            "severity": f.severity.value,
                            "security": f.is_security,
                            "confidence": f.confidence,
                        },
                        res.test_id,
                    )
                    if f.is_security:
                        self.bus.emit(
                            run_id,
                            EventType.SECURITY_ALERT,
                            {"title": f.title, "severity": f.severity.value, "test": res.test_id},
                            res.test_id,
                        )
            try:
                limits.check_run()
            except LimitReached as lr:
                run_stop = run_stop or lr

        scheduler = Scheduler(executor, max_parallel=cfg.max_parallel, on_result=on_result)
        streamed = [
            self._phase(run_id, ph, records, streamed=True)
            for ph in (Phase.TRACE_COLLECTION, Phase.DETERMINISTIC_EVALUATION, Phase.LLM_JUDGE_EVALUATION)
        ]
        execution = self._phase(run_id, Phase.TEST_EXECUTION, records).start()
        for s in streamed:
            s.start()
        try:
            await scheduler.run(p.plan.test_cases())
            if (
                p.options.second_wave
                and not token.cancelled
                and run_stop is None
                and len(waves) < MAX_WAVES
                and p.options.baseline_run_id is None
                and not p.options.only_tests
            ):
                child = self.designer.adapt(p.plan, list(results.values()), p.ctx)
                if child is not None:
                    waves.append(child)
                    for t in child.test_cases():
                        tests[t.id] = t
                    suite = sv.store.save_suite(
                        p.project_id,
                        p.target_id,
                        f"{p.spec.name}/{child.suite}",
                        child.suite,
                        child.test_cases(),
                        child.model_dump(mode="json"),
                    )
                    self._store_plan(run_id, child)
                    c = child.counts()
                    self.bus.emit(
                        run_id,
                        EventType.TEST_PLAN_GENERATED,
                        {
                            "plan_id": child.id,
                            "wave": child.wave,
                            "plan_hash": child.plan_hash,
                            "tests": c["tests"],
                            "runnable": c["runnable"],
                            "blocked": c["blocked"],
                            "reason": child.assumptions[-1] if child.assumptions else "",
                            "suite_id": suite["id"],
                        },
                    )
                    await scheduler.run(child.test_cases())
        except asyncio.CancelledError:
            token.cancel("the task was cancelled")
            status = RunStatus.CANCELLED
            self._finalize_early(run_id, status)
            raise
        except Exception as exc:
            log.exception("run %s failed during execution", run_id)
            error = f"{type(exc).__name__}: {exc}"
            status = RunStatus.FAILED
        all_results = list(results.values())
        n_asserts = sum(len(a.assertions) for r in all_results for a in r.attempts)
        failed_asserts = sum(
            1 for r in all_results for a in r.attempts for x in a.assertions if not x.passed and not x.evaluator_error
        )
        judged = [j for r in all_results for a in r.attempts for j in a.judge]
        execution.finish(
            tests=len(all_results),
            waves=len(waves),
            counts=dict(Counter(r.status.value for r in all_results)),
            limits=limits.summary(),
        )
        streamed[0].finish(traces=len(sv.store.list_traces(run_id)), artifacts=len(sv.store.list_artifacts(run_id)))
        streamed[1].finish(assertions=n_asserts, failed=failed_asserts)
        if p.judge is None:
            streamed[2].skip(p.environment.judge_note or "no independent judge available")
        streamed[2].finish(criteria=len(judged), uncertain=sum(1 for j in judged if j.uncertain))

        if token.cancelled:
            status = RunStatus.CANCELLED
        elif run_stop is not None and status == RunStatus.COMPLETED:
            status = RunStatus(run_stop.status.value)

        outcome = await self._analyse_and_finish(
            p, waves, list(tests.values()), all_results, findings, limits, status, error, started, reporter
        )
        return outcome

    def _finalize_early(self, run_id: str, status: RunStatus) -> None:
        try:
            self.services.store.update_run(run_id, status=status.value, finished_at=utcnow())
            self.bus.emit(run_id, EventType.RUN_CANCELLED, {"phase": "execute"})
        except Exception:
            log.exception("could not record the cancelled run")

    async def _analyse_and_finish(
        self,
        p: PreparedRun,
        waves: list[TestPlan],
        tests: list[TestCase],
        results: list[TestResult],
        findings: list[Finding],
        limits: LimitTracker,
        status: RunStatus,
        error: str | None,
        started: Any,
        reporter: Callable[..., Any] | None,
    ) -> RunOutcome:
        sv = self.services
        run_id = p.run_id
        records = p.phases
        cross: CrossTestAnalysis | None = None
        security: SecurityAnalysis | None = None
        reliability: ReliabilityAnalysis | None = None
        scorecard: Scorecard | None = None
        scope: ScopeNote | None = None
        planned_all = [pt for w in waves for pt in w.tests if pt.selected]
        try:
            # ---- 12. cross-test analysis --------------------------------------------------------------------
            with self._phase(run_id, Phase.CROSS_TEST_ANALYSIS, records) as ph:
                systemic = cross_test_findings(run_id, [f for f in findings if f.category != "cross-test-analysis"])
                for f in systemic:
                    findings.append(f)
                    self.bus.emit(
                        run_id,
                        EventType.FINDING_CREATED,
                        {"finding_id": f.id, "title": f.title, "severity": f.severity.value, "systemic": True},
                        f.test_id,
                    )
                cross = analyse_cross_test(tests, results, findings)
                folded = apply_adaptive_to_findings(findings, cross.adaptive)
                ph.set(
                    patterns=len(cross.patterns),
                    systemic_findings=len(systemic),
                    adaptive_followups=len(cross.adaptive),
                    findings_updated_with_wave2=folded,
                )
            # ---- 13. security analysis ----------------------------------------------------------------------
            with self._phase(run_id, Phase.SECURITY_ANALYSIS, records) as ph:
                sec = analyse_security(
                    planned_all,
                    results,
                    findings,
                    waves[0].security_coverage,
                    intensity=p.options.intensity,
                    judge_available=p.environment.judge,
                )
                security = sec
                if not any(is_security_test(t) for t in tests):
                    ph.skip("the plan contains no security tests (suite or target type)")
                ph.set(
                    posture=sec.posture,
                    categories=sec.verdict_counts,
                    attacks_succeeded=len(sec.attacks_succeeded),
                    leaks=len(sec.leaks),
                )
            # ---- 14. reliability analysis -------------------------------------------------------------------
            with self._phase(run_id, Phase.RELIABILITY_ANALYSIS, records) as ph:
                reliability = analyse_reliability(tests, results)
                ph.set(
                    verdict=reliability.verdict,
                    measured=reliability.measured_tests,
                    flaky=len(reliability.flaky),
                )
            # ---- 15. scoring --------------------------------------------------------------------------------
            with self._phase(run_id, Phase.SCORING, records) as ph:
                scored = scored_results(tests, results)
                scorecard = build_scorecard(tests, scored, findings, p.scoring)
                diagnostics = len(results) - len(scored)
                if diagnostics:
                    scorecard.notes.append(
                        f"{diagnostics} wave-2 variant/re-check test(s) are reported as evidence and not scored, so a "
                        "weakness is not counted twice."
                    )
                scope = assess_scope(waves[0], tests, results, restricted=bool(p.options.only_tests))
                if scope.limited:
                    scorecard.qualifiers.append(scope.text)
                    if scorecard.grade:
                        scorecard.grade = add_grade_note(scorecard.grade, scope.label)
                if security is not None:
                    scorecard.qualifiers.append(security.rating_note)
                if not p.environment.judge:
                    scorecard.qualifiers.append(
                        "No independent judge was used: quality criteria that need one were not evaluated."
                    )
                if status == RunStatus.CANCELLED:
                    scorecard.qualifiers.append("The run was cancelled: the score covers only the tests that ran.")
                elif status.value.startswith("stopped_due"):
                    scorecard.qualifiers.append(f"The run stopped early ({status.value}); some tests did not run.")
                sv.store.save_scorecard(run_id, scorecard)
                ph.set(overall=scorecard.overall, grade=scorecard.grade, profile=scorecard.profile)
            sv.store.save_findings(run_id, findings)
        except Exception as exc:
            log.exception("analysis of run %s failed", run_id)
            error = (error + "; " if error else "") + f"analysis failed: {type(exc).__name__}: {exc}"
            status = RunStatus.FAILED

        wall = limits.summary()
        pred = prediction_check(planned_all, results)
        outcome = RunOutcome(
            run_id=run_id,
            status=status,
            target=p.spec.name,
            profile=p.profile,
            plans=waves,
            tests=tests,
            results=results,
            findings=sorted(findings, key=lambda f: (-f.severity.rank, -f.confidence, f.test_id)),
            scorecard=scorecard,
            cross_test=cross,
            security=security,
            reliability=reliability,
            manifest=p.manifest,
            environment=p.environment,
            scoring=p.scoring,
            limits=wall,
            warnings=list(dict.fromkeys(p.warnings)),
            phases=records,
            error=error,
            started_at=started,
        )
        outcome.manifest["outcome"] = {
            "status": status.value,
            "counts": outcome.counts,
            "waves": len(waves),
            "findings": len(findings),
            "overall": scorecard.overall if scorecard else None,
            "cost_usd": wall["cost_usd"],
            "tokens": wall["tokens"],
            "elapsed_s": wall["elapsed_s"],
            "plan_prediction": pred,
            "scope": scope.model_dump(mode="json") if scope else None,
        }
        # ---- 16. report generation, 17. artifact packaging ---------------------------------------------------
        # A report is built from what is stored (exactly as `agentlab report` does later), so the run's analysis, its
        # manifest and the run row are persisted first.
        outcome.finished_at = utcnow()
        analysis_ids = [
            self._artifact(run_id, "analysis", "analysis.json", self._analysis_json(outcome)),
            self._artifact(run_id, "manifest", "manifest.json", outcome.manifest),
        ]
        self._persist_run(outcome, records)
        rep = reporter or getattr(sv, "reporter", None)
        with self._phase(run_id, Phase.REPORT_GENERATION, records) as ph:
            if rep is None:
                ph.skip("no report generator is configured")
            else:
                try:
                    outcome.report = await _maybe_await(rep(outcome, sv))
                    ph.set(formats=sorted(getattr(outcome.report, "formats", {}) or {}))
                except Exception as exc:
                    log.exception("report generation failed")
                    outcome.warnings.append(f"report generation failed: {type(exc).__name__}: {exc}")
                    ph.set(error=f"{type(exc).__name__}: {exc}")
                    ph.status = "failed"
        with self._phase(run_id, Phase.ARTIFACT_PACKAGING, records) as ph:
            ph.set(artifacts=len(sv.store.list_artifacts(run_id)), analysis_artifacts=[i for i in analysis_ids if i])
            if getattr(outcome.report, "bundle_id", None):
                ph.set(bundle=outcome.report.bundle_id)
        self._persist_run(outcome, records)
        end_type = {
            RunStatus.COMPLETED: EventType.RUN_COMPLETED,
            RunStatus.CANCELLED: EventType.RUN_CANCELLED,
            RunStatus.FAILED: EventType.RUN_FAILED,
        }.get(status, EventType.RUN_COMPLETED)
        self.bus.emit(run_id, end_type, outcome.summary())
        self._active.discard(run_id)
        self._tokens.pop(run_id, None)
        return outcome

    def _persist_run(self, o: RunOutcome, records: list[PhaseRecord]) -> None:
        """Write the run row's final state (called before the report is built and again once packaging is done)."""
        self.services.store.update_run(
            o.run_id,
            status=o.status.value,
            finished_at=o.finished_at,
            manifest=o.manifest,
            totals={
                **o.summary(),
                "phases": [r.model_dump(mode="json", exclude={"detail"}) for r in records],
            },
            error=None if o.error is None else {"kind": "INFRASTRUCTURE_ERROR", "message": o.error[:500]},
        )

    @staticmethod
    def _analysis_json(o: RunOutcome) -> dict[str, Any]:
        """Everything about a finished run that is not a row of its own: lets a run be reloaded and reported later."""
        return {
            "cross_test": o.cross_test.model_dump(mode="json") if o.cross_test else None,
            "security": o.security.model_dump(mode="json") if o.security else None,
            "reliability": o.reliability.model_dump(mode="json") if o.reliability else None,
            "scorecard": o.scorecard.model_dump(mode="json") if o.scorecard else None,
            "plan_prediction": o.manifest.get("outcome", {}).get("plan_prediction"),
            "phases": [r.model_dump(mode="json") for r in o.phases],
            "warnings": o.warnings,
            "limits": o.limits,
            "environment": o.environment.model_dump(mode="json"),
            "scoring_profile": o.scoring.model_dump(mode="json") if o.scoring else None,
            "error": o.error,
        }

    # ================================================================== convenience
    async def run(
        self,
        spec: TargetSpec,
        options: RunOptions | None = None,
        *,
        cancel: CancellationToken | None = None,
        reporter: Callable[..., Any] | None = None,
    ) -> RunOutcome:
        """Prepare and (unless ``plan_only``) execute a run; always releases the target and the workspace."""
        opts = options or RunOptions()
        prepared = await self.prepare(spec, opts)
        try:
            if opts.plan_only:
                return self.finish_plan_only(prepared)
            return await self.execute(prepared, cancel=cancel, reporter=reporter)
        finally:
            await prepared.aclose()

    def finish_plan_only(self, p: PreparedRun) -> RunOutcome:
        sv = self.services
        now = utcnow()
        outcome = RunOutcome(
            run_id=p.run_id,
            status=RunStatus.COMPLETED,
            target=p.spec.name,
            profile=p.profile,
            plans=[p.plan],
            tests=p.plan.test_cases(),
            results=[],
            findings=[],
            scorecard=None,
            cross_test=None,
            security=None,
            reliability=None,
            manifest=p.manifest,
            environment=p.environment,
            scoring=p.scoring,
            limits={},
            warnings=list(dict.fromkeys(p.warnings)),
            phases=p.phases,
            started_at=p.started_at,
            finished_at=now,
        )
        sv.store.update_run(
            p.run_id,
            status=RunStatus.COMPLETED.value,
            finished_at=now,
            totals={"plan_only": True, **p.plan.counts()},
        )
        self.bus.emit(p.run_id, EventType.RUN_COMPLETED, {"plan_only": True, **p.plan.counts()})
        self._active.discard(p.run_id)
        self._tokens.pop(p.run_id, None)
        return outcome


async def _maybe_await(value: Any) -> Any:
    return await value if asyncio.iscoroutine(value) or isinstance(value, asyncio.Future) else value


__all__ = ["TestOrchestratorAgent", "prediction_check"]
_ = (AgentLabError, Severity)  # re-exported for callers that catch/inspect run errors
