"""TestDesignerAgent (spec section 7): turns what discovery learned into an explainable, editable test plan.

The plan exists *before* anything runs. For every skill it says whether the skill applies and why; for every test it
says why the test exists, what evidence in the profile led to it, how risky it is, whether the authorisation gate and
the environment will let it run (or whether it will be BLOCKED, and why) and what it is expected to cost. It also says
which parts of the taxonomy (A-Q) and which of the 28 security categories (N1-N28) are covered, partly covered, not
covered (with the reason) or not applicable - so a missing area is visible instead of silently absent.

Nothing here talks to the target. The designer is deterministic; the optional LLM suggestions live in
``agentlab.design.llm`` and are merged through the same ``add_tests`` path as user-authored tests.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import hashlib
import json
import logging
from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any, cast

from agentlab.adapters.base import AdapterCapabilities, TargetRuntime
from agentlab.core.config import AgentLabConfig, Pricing
from agentlab.core.enums import RiskClass, Suite, SuiteKind
from agentlab.core.errors import UserError
from agentlab.core.models import AgentProfile, TestCase, TestResult
from agentlab.design.models import (
    BudgetEstimate,
    CoverageEntry,
    CoverageStatus,
    Origin,
    PlannedTest,
    PlanWarning,
    TestPlan,
)
from agentlab.design.taxonomy import (
    ALWAYS_RELEVANT,
    LETTER_RELEVANCE,
    N_RELEVANCE,
    SECURITY_CATEGORIES,
    TAXONOMY,
    letters_for,
    security_codes,
)
from agentlab.execution.capabilities import ENVIRONMENT_CAPABILITIES, describe_missing, missing_capabilities
from agentlab.execution.engines import needs_adapter
from agentlab.execution.limits import repetitions_for
from agentlab.security.credentials import CredentialManager
from agentlab.security.gate import AuthorizationGate
from agentlab.skills import IdAllocator, SkillContext, SkillRegistry
from agentlab.skills.context import Draft
from agentlab.skills.model import Skill, SkillMatch

log = logging.getLogger(__name__)

SUITES: dict[str, str] = {
    SuiteKind.DISCOVERY.value: "A few cheap checks that show whether the target works at all (functional and conversational basics)",
    SuiteKind.FUNCTIONAL.value: "Behaviour and quality of every capability found; no adversarial tests",
    SuiteKind.SECURITY.value: "Safety and security only (taxonomy N), authorised and non-destructive",
    SuiteKind.BROWSER.value: "Browser and UI behaviour (taxonomy J)",
    SuiteKind.RELIABILITY.value: "Repeatability, latency and cost behaviour (taxonomy O, P and Q)",
    SuiteKind.FULL.value: "Everything that applies to the target",
    SuiteKind.REGRESSION.value: "The tests of an earlier plan, re-run unchanged so the two runs are comparable",
}


def check_suite(value: str) -> Suite:
    """``value`` as a suite, or a UserError that names the suites there are."""
    if value not in SUITES:
        raise UserError(f"unknown suite '{value}' (known: {', '.join(SUITES)})")
    return cast(Suite, value)


# letters a suite is about; a letter outside the suite is "not applicable", not a gap
SUITE_LETTERS: dict[str, set[str]] = {
    "discovery": {"A"},
    "functional": set(TAXONOMY) - {"N"},
    "security": {"N"},
    "browser": {"J"},
    "reliability": {"O", "P", "Q"},
}
DISCOVERY_PER_SKILL = 3


class _KnownCredentials(CredentialManager):
    """Planning only needs to know *which* credential profiles exist, never their values."""

    def __init__(self, names: Iterable[str]) -> None:
        super().__init__(None)
        self._names = set(names)

    def has(self, name: str) -> bool:
        return name in self._names


# ---------------------------------------------------------------------------------- small pure helpers
def profile_hash(profile: AgentProfile) -> str:
    """Identity of what discovery learned: two plans from different profiles are not directly comparable."""
    core = {
        "types": [(t.type.value, round(t.confidence, 2)) for t in profile.types],
        "tools": sorted((t.name, t.side_effects) for t in profile.tools),
        "interfaces": sorted(profile.interfaces),
        "capabilities": sorted((c.capability, c.detected) for c in profile.capability_matrix),
        "documents": sorted(profile.documents),
    }
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()[:16]


def signature(test: TestCase) -> str:
    """What a test *does* (its inputs, planted context, interfaces and checks), ignoring its id, name and prose."""
    turns = [(t.session, t.input, tuple(t.attachments)) for t in test.all_turns()]
    checks = [(a.type, json.dumps(a.params, sort_keys=True, default=str)) for a in test.assertions]
    for t in test.all_turns():
        checks += [(a.type, json.dumps(a.params, sort_keys=True, default=str)) for a in t.assertions]
    tools = [(c.name, json.dumps(c.arguments, sort_keys=True, default=str)) for c in test.expected_tool_calls]
    steps = [s.model_dump_json() for s in test.browser_steps]
    payload = [
        turns,
        sorted(checks),
        tools,
        steps,
        sorted(test.required_interfaces),
        json.dumps(test.context, sort_keys=True, default=str),
        sorted(j.metric + j.rubric for j in test.judge),
        test.repetitions,  # the same check repeated five times measures flakiness; run once it does not
    ]
    return hashlib.sha1(json.dumps(payload, default=str).encode()).hexdigest()  # noqa: S324 - not a security use


def _calls_per_attempt(test: TestCase) -> int:
    load = test.context.get("load")
    if isinstance(load, dict) and load.get("sessions"):
        return max(1, int(load["sessions"]) * max(1, int(load.get("rounds", 1))))
    if test.browser_steps:
        return max(1, sum(1 for s in test.browser_steps if s.action == "chat"))
    return max(1, len(test.all_turns()))


def _skill_in_suite(skill: Skill, suite: str) -> bool:
    letters = set(skill.manifest.taxonomy)
    if suite in {"full", "regression"}:
        return True
    if suite == "discovery":
        return bool(letters & {"A", "B"}) or skill.manifest.applicability.always
    if suite == "functional":
        return bool(letters - {"N"}) or not letters
    return bool(letters & SUITE_LETTERS.get(suite, set(TAXONOMY)))


def _test_in_suite(p: PlannedTest, suite: str) -> bool:
    letters = set(p.taxonomy)
    if suite in {"full", "regression"}:
        return True
    if suite == "discovery":
        return bool(letters & {"A", "B"}) and "N" not in letters
    if suite == "functional":
        return "N" not in letters
    if suite == "browser":
        return "J" in letters or bool(p.test.browser_steps) or "web" in p.test.required_interfaces
    return bool(letters & SUITE_LETTERS.get(suite, set(TAXONOMY)))


def _priority(p: PlannedTest, skill_score: float = 0.5) -> float:
    """Higher means 'keep this test when the plan has to be trimmed'."""
    return p.test.severity_on_failure.rank * 10 + (4 if "N" in p.taxonomy else 0) + skill_score * 3 + p.wave * 0.1


def _price(config: AgentLabConfig, provider: str | None, model: str | None) -> Pricing | None:
    if not provider:
        return None
    try:
        cfg = config.provider(provider)
    except UserError:
        return None
    name = model or cfg.model
    return cfg.pricing.get(name) if name else None


class TestDesignerAgent:
    """Builds, sizes, explains and (after a first wave) deepens a :class:`TestPlan`."""

    __test__ = False

    def __init__(self, registry: SkillRegistry | None = None, *, load_plugins: bool = True) -> None:
        self._registry = registry
        self._load_plugins = load_plugins

    def registry_for(self, config: AgentLabConfig) -> SkillRegistry:
        if self._registry is None:
            self._registry = SkillRegistry.default(config.skill_dirs, load_plugins=self._load_plugins)
        return self._registry

    # ================================================================== design (wave 1)
    def design(
        self,
        ctx: SkillContext,
        *,
        suite: str = "full",
        include: Iterable[str] | None = None,
        exclude: Iterable[str] | None = None,
        max_tests: int | None = None,
        user_tests: Sequence[TestCase] = (),
        trim: bool = True,
        selection: Sequence[tuple[Skill, SkillMatch]] | None = None,
    ) -> TestPlan:
        """Wave 1. ``selection`` lets the caller run skill selection as its own step (the orchestrator does, so the
        two phases are observable); without it the designer selects the skills itself."""
        kind = check_suite(suite)
        if kind == "regression":
            raise UserError("a regression plan is built from an earlier plan: use TestDesignerAgent.regression()")
        if kind == "discovery":
            ctx = dataclasses.replace(ctx, intensity="quick")
        registry = self.registry_for(ctx.config)
        plan = self._new_plan(ctx, kind)
        gate = self._gate(ctx)
        ids = IdAllocator()
        seen: dict[str, str] = {}
        dropped_outside_suite = 0
        for problem in registry.problems:
            plan.warnings.append(PlanWarning(level="info", code="skill_problem", message=problem))

        chosen = list(selection) if selection is not None else registry.select(ctx, include=include, exclude=exclude)
        for skill, match in chosen:
            if match.selected and not _skill_in_suite(skill, suite):
                match.selected, match.skipped_reason = False, f"not part of the '{suite}' suite"
            plan.skills.append(match)
            if not match.selected:
                continue
            try:
                run = registry.generate(skill, ctx, ids, wave=1)
            except Exception as exc:  # a faulty skill must not stop the plan; it is reported instead
                plan.warnings.append(
                    PlanWarning(
                        code="skill_failed",
                        message=f"skill '{skill.name}' failed while generating tests ({type(exc).__name__}: {exc}); "
                        "it contributes no tests",
                    )
                )
                match.selected, match.skipped_reason = False, f"generator error: {type(exc).__name__}"
                continue
            if run.notes:
                plan.skill_notes[skill.name] = list(run.notes)
            planned = [self._planned(ctx, gate, skill, p_draft, 1, "skill", match.score) for p_draft in run.drafts]
            kept: list[PlannedTest] = []
            for p in planned:
                if not _test_in_suite(p, suite):
                    dropped_outside_suite += 1
                    continue
                dup = seen.get(signature(p.test))
                if dup:
                    plan.warnings.append(
                        PlanWarning(
                            level="info",
                            code="duplicate_test",
                            message=f"{p.id} does the same as {dup}; it was not added",
                        )
                    )
                    continue
                seen[signature(p.test)] = p.id
                kept.append(p)
            cap = DISCOVERY_PER_SKILL if suite == "discovery" else ctx.config.evaluation.max_tests_per_skill
            if len(kept) > cap:
                ranked = sorted(kept, key=lambda p: -_priority(p, match.score))
                keep_ids = {p.id for p in ranked[:cap]}
                note = (
                    f"first {cap} per skill for a quick look"
                    if suite == "discovery"
                    else f"over this skill's cap of {cap} tests (evaluation.max_tests_per_skill)"
                )
                for p in kept:
                    if p.id not in keep_ids:
                        p.selected, p.deselected_reason = False, note
            plan.tests += kept
        if dropped_outside_suite:
            plan.assumptions.append(
                f"{dropped_outside_suite} generated test(s) fall outside the '{suite}' suite and were not planned."
            )
        self.add_tests(plan, ctx, user_tests, origin="user", gate=gate)
        return self.finalize(plan, ctx, trim=trim, max_tests=max_tests)

    # ================================================================== regression
    def regression(self, previous: TestPlan, ctx: SkillContext, *, trim: bool = False) -> TestPlan:
        """Re-plan the *same* tests of an earlier plan (same ids, inputs and checks) against the current target.

        A regression comparison is only meaningful when the tests did not change, so nothing is regenerated; the
        gate and the environment are re-evaluated because those may have changed since the earlier run."""
        registry = self.registry_for(ctx.config)
        plan = self._new_plan(ctx, "regression")
        plan.parent_plan_id = previous.id
        plan.intensity = previous.intensity
        gate = self._gate(ctx)
        for old in previous.selected_tests():
            skill = registry.get(old.skill) if old.skill in registry else None
            test = old.test.model_copy(deep=True)
            if skill is None and old.origin == "skill":
                plan.warnings.append(
                    PlanWarning(
                        code="skill_missing",
                        message=f"skill '{old.skill}' is no longer installed; {old.id} is re-run from the stored plan",
                    )
                )
            elif skill is not None and skill.version != old.skill_version:
                plan.warnings.append(
                    PlanWarning(
                        level="info",
                        code="skill_version_changed",
                        message=f"skill '{old.skill}' changed from {old.skill_version} to {skill.version}; "
                        f"{old.id} is re-run as it was planned, so the comparison stays like for like",
                    )
                )
            p = self._planned_from_test(ctx, gate, test, old.skill, old.skill_version, old.wave, old.origin)
            p.taxonomy, p.security_categories = list(old.taxonomy), list(old.security_categories)
            p.reasons = [f"re-run of {old.id} from plan {previous.id}", *old.reasons]
            p.evidence = list(old.evidence)
            plan.tests.append(p)
        plan.skills = [m.model_copy(deep=True) for m in previous.skills]
        plan.skill_notes = {k: list(v) for k, v in previous.skill_notes.items()}
        plan.assumptions.append("Regression plan: tests are unchanged copies of the earlier plan's tests.")
        return self.finalize(plan, ctx, trim=trim)

    # ================================================================== adding tests (user / LLM / adaptive)
    def add_tests(
        self,
        plan: TestPlan,
        ctx: SkillContext,
        tests: Sequence[TestCase | Draft],
        *,
        origin: Origin = "user",
        wave: int | None = None,
        gate: AuthorizationGate | None = None,
        skill_name: str | None = None,
        reasons: Sequence[str] = (),
    ) -> list[PlannedTest]:
        """Add tests that were not produced by a skill. They go through the same gate and prediction as the rest."""
        gate = gate or self._gate(ctx)
        used = {p.id for p in plan.tests}
        sigs = {signature(p.test) for p in plan.tests}
        added: list[PlannedTest] = []
        for item in tests:
            draft = item if isinstance(item, Draft) else Draft(test=item)
            test = draft.test.model_copy(deep=True)
            if test.id in used:
                new = f"{test.id}-{origin[:1].upper()}{len([u for u in used if u.startswith(test.id)]) + 1}"
                plan.warnings.append(
                    PlanWarning(
                        level="info",
                        code="renamed_test",
                        message=f"{test.id} already exists in the plan; renamed to {new}",
                    )
                )
                test.id = new
            if signature(test) in sigs:
                plan.warnings.append(
                    PlanWarning(level="info", code="duplicate_test", message=f"{test.id} duplicates a planned test")
                )
                continue
            name = skill_name or test.skill or {"user": "user-defined", "llm": "llm-suggested"}.get(origin, origin)
            version = test.skill_version or "1"
            test.skill, test.skill_version = name, version
            p = self._planned_from_test(ctx, gate, test, name, version, wave or plan.wave, origin)
            if isinstance(item, Draft):
                p.taxonomy = letters_for(test, item.taxonomy) if item.taxonomy else p.taxonomy
                p.reasons, p.evidence = list(item.reasons), list(item.evidence)
            p.reasons = [*reasons, *p.reasons] or [f"{origin}-supplied test"]
            used.add(test.id)
            sigs.add(signature(test))
            plan.tests.append(p)
            added.append(p)
        return added

    async def enhance(
        self,
        plan: TestPlan,
        ctx: SkillContext,
        providers: Any,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> TestPlan:
        """Optionally add model-suggested safe scenarios (see ``agentlab.design.llm``), then re-finalise the plan."""
        from agentlab.design.llm import suggest_tests

        sug = await suggest_tests(providers, ctx, plan, provider=provider, model=model)
        added = self.add_tests(plan, ctx, sug.drafts, origin="llm", skill_name="llm-suggested")
        if added:
            plan.assumptions.append(
                f"{len(added)} test(s) were suggested by {sug.label} and are unverified; review them before relying on them."
            )
        for why in sug.rejected:
            plan.warnings.append(PlanWarning(level="info", code="llm_suggestion_rejected", message=why))
        return self.finalize(plan, ctx)

    # ================================================================== adaptive second wave
    def adapt(self, plan: TestPlan, results: Sequence[TestResult], ctx: SkillContext) -> TestPlan | None:
        """Wave 2: dig deeper where wave 1 found weaknesses and re-check results that were not stable.

        It never adds tests where nothing was found (no failure, no flakiness), so a clean first wave ends the run
        and the report says wave 2 was not needed."""
        from agentlab.design.adaptive import analyse_wave, describe_variant, recheck_test, variants_for

        analysis = analyse_wave(plan, results)
        if not analysis.failed_ids and not analysis.flaky:
            return None
        registry = self.registry_for(ctx.config)
        cfg = ctx.config.planning
        wave = plan.wave + 1
        deep_ctx = dataclasses.replace(ctx, intensity="thorough", previous=list(results))
        child = self._new_plan(deep_ctx, plan.suite)
        child.wave, child.parent_plan_id = wave, plan.id
        gate = self._gate(ctx)
        ids = IdAllocator()
        ids.used |= {p.id for p in plan.tests}
        seen = {signature(p.test) for p in plan.tests}
        budget = cfg.adaptive_max_tests
        matches: dict[str, SkillMatch] = {}

        def match_for(name: str, reason: str) -> SkillMatch:
            if name not in matches:
                known = next((m for m in plan.skills if m.skill == name), None)
                matches[name] = SkillMatch(
                    skill=name,
                    version=known.version if known else "1",
                    selected=True,
                    kind=known.kind if known else "tests",
                    taxonomy=list(known.taxonomy) if known else [],
                    trust=known.trust if known else "local",
                    score=0.9,
                    reasons=[reason],
                )
            return matches[name]

        # 1. the same checks, rephrased: is the weakness tied to one wording?
        for tid in analysis.failed_ids:
            old = plan.get(tid)
            for kind, variant in variants_for(old.test, wave, min(cfg.adaptive_variants_per_failure, budget)):
                if budget <= 0 or signature(variant) in seen:
                    continue
                seen.add(signature(variant))
                p = self._planned_from_test(deep_ctx, gate, variant, old.skill, old.skill_version, wave, "adaptive")
                p.taxonomy, p.security_categories = list(old.taxonomy), list(old.security_categories)
                p.reasons = [
                    f"{old.id} failed in wave 1 ({analysis.failure_summary.get(tid, 'see its result')}). This variant "
                    f"({describe_variant(kind)}) keeps the same checks to find out whether the weakness depends on "
                    "the wording."
                ]
                p.evidence = [f"wave 1 result: {old.id} FAILED", *old.evidence[:2]]
                match_for(old.skill, f"wave 1 failed test(s) of this skill: {', '.join(analysis.failed_ids[:4])}")
                child.tests.append(p)
                budget -= 1
        # 2. a deeper pass of the skills that produced failures (more intensity, duplicates removed)
        for name, failed_ids in analysis.failed_skills.items():
            if name not in registry or budget <= 0:
                continue
            skill = registry.get(name)
            if not skill.usable:
                continue
            try:
                run = registry.generate(skill, deep_ctx, ids, wave=wave)
            except Exception as exc:
                child.warnings.append(
                    PlanWarning(code="skill_failed", message=f"deeper pass of '{name}' failed: {type(exc).__name__}")
                )
                continue
            fresh: list[PlannedTest] = []
            for d in run.drafts:
                p = self._planned(deep_ctx, gate, skill, d, wave, "adaptive", 0.9)
                if signature(p.test) in seen:
                    continue
                seen.add(signature(p.test))
                p.reasons = [
                    f"Wave 1 failed {', '.join(failed_ids[:3])} ({analysis.failure_summary.get(name, 'see results')}); "
                    "this test comes from a deeper pass of the same skill.",
                    *p.reasons,
                ]
                p.test.tags = list(dict.fromkeys([*p.test.tags, "adaptive", f"wave:{wave}"]))
                fresh.append(p)
            fresh.sort(key=lambda p: -_priority(p, 0.9))
            fresh = fresh[: max(0, min(budget, ctx.config.evaluation.max_tests_per_skill))]
            if fresh:
                match_for(name, f"wave 1 failed {len(failed_ids)} test(s) of this skill")
                child.tests += fresh
                budget -= len(fresh)
        # 3. unstable results: repeat them to measure the pass rate
        for r in analysis.flaky:
            if budget <= 0:
                break
            old = plan.get(r.test_id)
            clone = recheck_test(old.test, cfg.adaptive_repetitions, r, wave)
            p = self._planned_from_test(deep_ctx, gate, clone, old.skill, old.skill_version, wave, "adaptive")
            p.taxonomy, p.security_categories = list(old.taxonomy), list(old.security_categories)
            seen_part = f"passed {r.reliability.passes} of {r.reliability.repetitions}" if r.reliability else "unstable"
            p.reasons = [
                f"{old.id} was unstable in wave 1 ({seen_part}); it is repeated {cfg.adaptive_repetitions} times "
                "to estimate how reliable the behaviour is."
            ]
            p.evidence = list(old.evidence)
            match_for(old.skill, "wave 1 result was unstable")
            child.tests.append(p)
            budget -= 1
        if not child.tests:
            return None
        child.skills = list(matches.values())
        child.assumptions.append(
            f"Wave {wave} was added because wave 1 had {len(analysis.failed_ids)} failed and "
            f"{len(analysis.flaky)} unstable test(s). It only deepens what wave 1 found; it does not widen coverage."
        )
        return self.finalize(child, deep_ctx, trim=True, max_tests=cfg.adaptive_max_tests)

    # ================================================================== reproduction
    def restrict(self, plan: TestPlan, ctx: SkillContext, test_ids: Iterable[str]) -> TestPlan:
        """Keep only the requested tests (to reproduce a finding). The others stay in the plan, deselected, so what
        was left out is visible rather than silently missing. An entry may be a shell-style pattern (``MEM-*``)."""
        requested = list(dict.fromkeys(test_ids))
        present = [p.id for p in plan.tests]
        keep: set[str] = set()
        missing: list[str] = []
        for want in requested:
            found = (
                fnmatch.filter(present, want) if any(c in want for c in "*?[") else [t for t in present if t == want]
            )
            keep.update(found)
            if not found:
                missing.append(want)
        if missing:
            raise UserError(
                f"test(s) {', '.join(missing)} are not in this plan. Follow-up tests (ids ending in -W2...) exist only "
                "while a run is in progress, and CROSS-... findings summarise several tests; use the id of a wave 1 test"
            )
        for p in plan.tests:
            if p.id in keep:
                p.selected, p.deselected_reason = True, None
            elif p.selected:
                p.selected, p.deselected_reason = False, "not requested (--only)"
        plan.assumptions.append(
            f"The plan is restricted to the {len(keep)} requested test(s): {', '.join(sorted(keep))}."
        )
        return self.finalize(plan, ctx, trim=False)

    # ================================================================== finalisation
    def finalize(
        self, plan: TestPlan, ctx: SkillContext, *, trim: bool = True, max_tests: int | None = None
    ) -> TestPlan:
        """(Re)compute everything derived from the plan's tests: trimming, budget, coverage, warnings, hash."""
        cap = max_tests or ctx.config.planning.max_tests
        plan.limits = {
            "max_tests": cap,
            "max_tests_per_skill": ctx.config.evaluation.max_tests_per_skill,
            "max_tokens": ctx.config.limits.max_tokens,
            "max_cost_usd": ctx.config.limits.max_cost_usd,
            "max_execution_time_seconds": ctx.config.limits.max_execution_time_seconds,
        }
        if trim:
            self._trim(plan, ctx, cap)
        plan.budget = self._budget(plan, ctx)
        self._coverage(plan, ctx)
        for m in plan.skills:
            mine = [p for p in plan.selected_tests() if p.skill == m.skill]
            m.tests = len(mine)
            m.predicted_blocked = sum(1 for p in mine if p.predicted == "blocked")
        self._warnings(plan, ctx)
        plan.summary = self._summary(plan)
        plan.plan_hash = plan.compute_hash()
        return plan

    # ------------------------------------------------------------------ plan construction helpers
    def _new_plan(self, ctx: SkillContext, suite: Suite) -> TestPlan:
        plan = TestPlan(
            target=ctx.profile.target_name,
            suite=suite,
            intensity=ctx.intensity,
            profile_hash=profile_hash(ctx.profile),
            inputs=design_inputs_summary(ctx),
        )
        cfg = ctx.config.planning
        plan.assumptions.append(
            f"Estimates assume {cfg.seconds_per_call:g}s and {cfg.tokens_per_call} tokens per target call "
            "(planning.* settings); real usage is measured during the run."
        )
        if not ctx.judge_available:
            plan.assumptions.append(
                "No independent LLM judge is configured: tests whose only criteria are judged are predicted BLOCKED; "
                "deterministic checks still run."
            )
        if not ctx.docker_available:
            plan.assumptions.append("Docker is unavailable: tests that need an isolated sandbox are predicted BLOCKED.")
        if not ctx.browser_available:
            plan.assumptions.append("No browser engine is available: browser tests are predicted BLOCKED.")
        return plan

    def _gate(self, ctx: SkillContext) -> AuthorizationGate:
        return AuthorizationGate(
            ctx.target,
            ctx.config,
            credentials=_KnownCredentials(ctx.credential_names),
            interfaces=list(ctx.interfaces),
            sandbox_available=ctx.docker_available,
            judge_available=ctx.judge_available,
            browser_available=ctx.browser_available,
            unavailable=ctx.unavailable,
        )

    def _planned(
        self,
        ctx: SkillContext,
        gate: AuthorizationGate,
        skill: Skill,
        draft: Draft,
        wave: int,
        origin: Origin,
        score: float,
    ) -> PlannedTest:
        p = self._planned_from_test(ctx, gate, draft.test, skill.name, skill.version, wave, origin)
        p.taxonomy = letters_for(draft.test, draft.taxonomy)
        p.reasons, p.evidence = list(draft.reasons), list(draft.evidence)
        return p

    def _planned_from_test(
        self,
        ctx: SkillContext,
        gate: AuthorizationGate,
        test: TestCase,
        skill: str,
        version: str,
        wave: int,
        origin: Origin,
    ) -> PlannedTest:
        risk, gate_reasons, kind, reason = self.predict(ctx, gate, test)
        p = PlannedTest(
            test=test,
            skill=skill,
            skill_version=version,
            wave=wave,
            taxonomy=letters_for(test, []),
            security_categories=security_codes(test),
            reasons=[],
            risk=risk,
            gate_reasons=gate_reasons,
            predicted="blocked" if reason else "runnable",
            blocked_kind=kind,
            blocked_reason=reason,
            origin=origin,
        )
        self._estimate(ctx, p)
        return p

    # ------------------------------------------------------------------ prediction
    def predict(
        self, ctx: SkillContext, gate: AuthorizationGate, test: TestCase
    ) -> tuple[RiskClass, list[str], str | None, str | None]:
        """(effective risk, gate reasons, block kind, block reason) - the same rules the executor will apply."""
        d = gate.decide(test)
        reasons = list(d.reasons)
        if d.blocked:
            return d.risk, reasons, d.block_kind, d.blocked_reason
        if (
            test.judge
            and not test.assertions
            and not any(t.assertions for t in test.all_turns())
            and not test.expected_tool_calls
            and not ctx.judge_available
        ):
            why = (
                "all of this test's criteria need an LLM judge but none is configured "
                f"(judge criteria: {[c.metric for c in test.judge]})"
            )
            return d.risk, reasons, "prerequisite", why
        kind = self._interface_for(ctx, test)
        if kind is None and not needs_adapter(test):
            return d.risk, reasons, None, None  # a static check runs without any interface
        if kind is None:
            why = (
                f"no usable interface for this test (wanted {test.required_interfaces or 'any'}, "
                f"available {list(ctx.interfaces) or 'none'})"
            )
            return d.risk, reasons, "prerequisite", why
        needs = list(test.context.get("requires_capabilities") or [])
        if needs:
            caps = ctx.adapter_capabilities.get(kind)
            env = {
                "workspace": ctx.docker_available,
                "local_site": ctx.browser_available and gate.locality == "local",
            }
            if caps is None:
                needs = [n for n in needs if n in ENVIRONMENT_CAPABILITIES]  # interface capabilities unknown
                caps = AdapterCapabilities()
            missing = missing_capabilities(needs, caps, known_canaries=bool(ctx.target.known_canaries), environment=env)
            if missing:
                return d.risk, reasons, "prerequisite", describe_missing(kind, missing)
        return d.risk, reasons, None, None

    @staticmethod
    def _interface_for(ctx: SkillContext, test: TestCase) -> str | None:
        for iface in test.required_interfaces:
            if iface in ctx.interfaces:
                return iface
        if test.required_interfaces:
            return None
        for iface in TargetRuntime.PRIORITY:
            if iface in ctx.interfaces:
                return iface
        return next(iter(ctx.interfaces), None)

    # ------------------------------------------------------------------ estimates
    def _estimate(self, ctx: SkillContext, p: PlannedTest) -> None:
        if p.predicted == "blocked":
            p.est_attempts = p.est_calls = p.est_judge_calls = p.est_tokens = 0
            p.est_seconds = 0.0
            return
        cfg = ctx.config
        t = p.test
        attempts = repetitions_for(cfg, t, p.risk)
        calls = _calls_per_attempt(t)
        judges = max(1, len(cfg.evaluation.judges))
        judge_calls = attempts * len(t.judge) * judges if (t.judge and ctx.judge_available) else 0
        p.est_attempts = attempts
        p.est_calls = attempts * calls
        p.est_judge_calls = judge_calls
        p.est_seconds = round(min(p.est_calls * cfg.planning.seconds_per_call, attempts * t.timeout), 2)
        target_tokens = min(p.est_calls * cfg.planning.tokens_per_call, attempts * t.max_tokens)
        p.est_tokens = int(target_tokens + judge_calls * cfg.planning.judge_tokens_per_call)

    def _budget(self, plan: TestPlan, ctx: SkillContext) -> BudgetEstimate:
        cfg = ctx.config
        run = plan.runnable()
        b = BudgetEstimate(
            tests=len(run),
            attempts=sum(p.est_attempts for p in run),
            target_calls=sum(p.est_calls for p in run),
            judge_calls=sum(p.est_judge_calls for p in run),
            est_tokens=sum(p.est_tokens for p in run),
            serial_seconds=round(sum(p.est_seconds for p in run), 1),
        )
        kind = self._primary_interface(ctx)
        caps = ctx.adapter_capabilities.get(kind) if kind else None
        parallel = max(1, cfg.max_parallel) if (caps is None or caps.parallel_sessions) else 1
        chains: dict[str, float] = defaultdict(float)
        for p in run:
            if p.test.isolation_key:
                chains[p.test.isolation_key] += p.est_seconds
        b.est_wall_seconds = round(max(b.serial_seconds / parallel, max(chains.values(), default=0.0)), 1)
        # cost: only where pricing is configured for the provider that would be called
        total = 0.0
        known = False
        notes: list[str] = []
        judge_tokens = b.judge_calls * cfg.planning.judge_tokens_per_call
        target_tokens = max(0, b.est_tokens - judge_tokens)
        if ctx.target.llm:
            price = _price(cfg, ctx.target.llm.provider, ctx.target.llm.model)
            if price:
                known = True
                total += self._tokens_to_usd(target_tokens, price)
            else:
                notes.append(f"no pricing configured for target provider '{ctx.target.llm.provider}'")
        else:
            notes.append("the target's own model cost is only known if its interface reports usage")
        if b.judge_calls:
            judges = cfg.evaluation.judges
            prices = [_price(cfg, j.provider, j.model) for j in judges]
            if judges and all(prices):
                known = True
                total += sum(self._tokens_to_usd(judge_tokens / len(judges), pr) for pr in prices if pr)
            else:
                notes.append("no pricing configured for the judge provider(s)")
        b.est_cost_usd = round(total, 4) if known else None
        b.cost_note = (
            "estimated from configured provider pricing (assumes 70% input / 30% output tokens)"
            if known
            else "not estimated: " + ("; ".join(notes) or "no priced provider is involved")
        )
        lim = cfg.limits
        if b.est_tokens > lim.max_tokens:
            b.notes.append(f"estimated tokens {b.est_tokens} exceed limits.max_tokens={lim.max_tokens}")
        if b.est_cost_usd is not None and b.est_cost_usd > lim.max_cost_usd:
            b.notes.append(f"estimated cost ${b.est_cost_usd} exceeds limits.max_cost_usd=${lim.max_cost_usd}")
        if b.est_wall_seconds > lim.max_execution_time_seconds:
            b.notes.append(
                f"estimated time {b.est_wall_seconds:.0f}s exceeds limits.max_execution_time_seconds="
                f"{lim.max_execution_time_seconds:.0f}"
            )
        b.within_limits = not b.notes
        return b

    @staticmethod
    def _tokens_to_usd(tokens: float, price: Pricing) -> float:
        return tokens * 0.7 / 1e6 * price.input_per_mtok + tokens * 0.3 / 1e6 * price.output_per_mtok

    def _primary_interface(self, ctx: SkillContext) -> str | None:
        for iface in TargetRuntime.PRIORITY:
            if iface in ctx.interfaces:
                return iface
        return next(iter(ctx.interfaces), None)

    # ------------------------------------------------------------------ trimming (coverage-preserving)
    def _trim(self, plan: TestPlan, ctx: SkillContext, max_tests: int) -> None:
        token_budget = int(ctx.config.limits.max_tokens * 0.9)
        score = {m.skill: m.score for m in plan.skills}
        runnable = plan.runnable()

        def too_big(ts: list[PlannedTest]) -> bool:
            return len(ts) > max_tests or sum(p.est_tokens for p in ts) > token_budget

        if not too_big(runnable):
            return

        def prio(p: PlannedTest) -> float:
            return _priority(p, score.get(p.skill, 0.5))

        # every taxonomy area and security category keeps its most important runnable test, user tests always stay
        best: dict[str, PlannedTest] = {}
        for p in sorted(runnable, key=prio, reverse=True):
            for key in [*p.taxonomy, *p.security_categories]:
                best.setdefault(key, p)
        keep = {id(p) for p in best.values()} | {id(p) for p in runnable if p.origin == "user"}
        chosen = [p for p in runnable if id(p) in keep]
        for p in sorted((p for p in runnable if id(p) not in keep), key=prio, reverse=True):
            trial = [*chosen, p]
            if not too_big(trial):
                chosen.append(p)
        chosen_ids = {id(p) for p in chosen}
        trimmed = 0
        for p in runnable:
            if id(p) not in chosen_ids:
                p.selected = False
                p.deselected_reason = (
                    f"trimmed to fit the plan budget (max {max_tests} tests, {token_budget} estimated tokens); "
                    "every taxonomy area and security category keeps its most severe test"
                )
                trimmed += 1
        if trimmed:
            plan.warnings.append(
                PlanWarning(
                    code="plan_trimmed",
                    message=f"{trimmed} lower-priority test(s) were deselected to fit the budget. They remain listed "
                    "in the plan and can be re-selected, or raise planning.max_tests / limits.max_tokens.",
                )
            )

    # ------------------------------------------------------------------ coverage
    @staticmethod
    def signals(ctx: SkillContext) -> set[str]:
        """What discovery observed about the target, as the words the relevance tables use."""
        sig: set[str] = set()
        if ctx.tools:
            sig.add("tools")
        if ctx.documents:
            sig.add("documents")
        for name, types in {
            "rag": ("rag", "research"),
            "memory": ("memory",),
            "browser": ("browser", "computer_use"),
            "coding": ("coding", "repository"),
            "multi_agent": ("multi_agent", "supervisor", "sub_agents"),
            "mcp": ("mcp",),
            "autonomous": ("autonomous", "long_running", "react", "planning", "workflow"),
        }.items():
            if any(ctx.has_type(t, 0.35) for t in types) or ctx.capability(name):
                sig.add(name)
        return sig

    def _coverage(self, plan: TestPlan, ctx: SkillContext) -> None:
        sel = plan.selected_tests()
        scope = SUITE_LETTERS.get(plan.suite)
        sig = self.signals(ctx)
        deselected = [p for p in plan.tests if not p.selected]

        def entry(key: str, name: str, mine: list[PlannedTest], gap_note: str, applicable: bool) -> CoverageEntry:
            runnable = sum(1 for p in mine if p.predicted == "runnable")
            blocked = len(mine) - runnable
            skills = sorted({p.skill for p in mine})
            status: CoverageStatus
            note = ""
            if not applicable:
                status, note = "not_applicable", gap_note
            elif mine:
                status = "covered" if runnable and not blocked else ("partial" if runnable else "not_covered")
                if blocked:
                    reasons = sorted({(p.blocked_reason or "")[:100] for p in mine if p.predicted == "blocked"})
                    note = f"{blocked} of {len(mine)} test(s) predicted BLOCKED: {'; '.join(reasons[:2])}"
                lost = [p for p in deselected if key in (*p.taxonomy, *p.security_categories)]
                if lost:
                    note = (note + "; " if note else "") + f"{len(lost)} more deselected (budget or skill cap)"
            else:
                status, note = "not_covered", gap_note
            return CoverageEntry(
                key=key,
                name=name,
                status=status,
                tests=len(mine),
                runnable=runnable,
                blocked=blocked,
                skills=skills,
                note=note,
            )

        plan.coverage = []
        for letter, name in TAXONOMY.items():
            mine = [p for p in sel if letter in p.taxonomy]
            applicable, note = True, ""
            if scope is not None and letter not in scope:
                applicable, note = False, f"outside the '{plan.suite}' suite"
            elif letter not in ALWAYS_RELEVANT and not self._letter_relevant(letter, ctx, sig):
                applicable, note = (
                    False,
                    f"no sign of {' / '.join(LETTER_RELEVANCE.get(letter, []))} in the target profile",
                )
            else:
                note = self._gap_reason(plan, ctx, letter) or "no installed skill generated a test for this area"
            plan.coverage.append(entry(letter, name, mine, note, applicable))

        plan.security_coverage = []
        for cat in SECURITY_CATEGORIES:
            mine = [p for p in sel if cat.code in p.security_categories]
            applicable, note = True, ""
            needs = N_RELEVANCE.get(cat.code)
            if scope is not None and "N" not in scope:
                applicable, note = False, f"outside the '{plan.suite}' suite"
            elif needs and not (set(needs) & sig):
                applicable, note = False, f"requires {' or '.join(needs)}, none observed in the target profile"
            else:
                note = self._gap_reason(plan, ctx, cat.code, cat.skills) or cat.not_covered_hint
            plan.security_coverage.append(entry(cat.code, cat.name, mine, note, applicable))

    @staticmethod
    def _letter_relevant(letter: str, ctx: SkillContext, sig: set[str]) -> bool:
        if any(ctx.has_type(t, 0.35) for t in LETTER_RELEVANCE.get(letter, [])):
            return True
        return {
            "C": "memory" in sig,
            "D": "rag" in sig,
            "E": "tools" in sig,
            "F": "autonomous" in sig,
            "G": "autonomous" in sig,
            "H": "multi_agent" in sig,
            "I": "mcp" in sig,
            "J": "browser" in sig,
            "K": "coding" in sig,
            "L": "documents" in sig,
            "M": ctx.capability("multimodal"),
        }.get(letter, False)

    @staticmethod
    def _gap_reason(plan: TestPlan, ctx: SkillContext, area: str, owners: Sequence[str] | None = None) -> str:
        """Why an applicable area has no tests: the skills that own it were not selected, or said what they skipped.

        Skills tag such notes with the area they explain (``[N11] ...``); untagged notes are general remarks."""
        tag = f"[{area}] "
        parts: list[str] = []
        for m in plan.skills:
            if owners is not None and m.skill not in owners:
                continue
            if owners is None and area not in m.taxonomy:
                continue
            if not m.selected:
                if m.skipped_reason:
                    parts.append(f"skill '{m.skill}' not selected ({m.skipped_reason})")
                continue
            notes = plan.skill_notes.get(m.skill, [])
            tagged = [n[len(tag) :] for n in notes if n.startswith(tag)]
            parts += [f"{m.skill}: {n}" for n in tagged]
            if not tagged and owners is None and notes:
                parts.append(f"{m.skill}: {notes[0].split('] ', 1)[-1]}")
        return "; ".join(parts[:3])

    # ------------------------------------------------------------------ warnings and summary
    def _warnings(self, plan: TestPlan, ctx: SkillContext) -> None:
        keep = [
            w
            for w in plan.warnings
            if w.code
            in {
                "llm_suggestion_rejected",
                "skill_failed",
                "plan_trimmed",
                "duplicate_test",
                "renamed_test",
                "skill_problem",
                "skill_missing",
                "skill_version_changed",
            }
        ]
        plan.warnings = keep
        if not plan.selected_tests() and plan.suite != "regression":
            why = "; ".join(
                f"{m.skill}: {m.skipped_reason}"
                for m in plan.skills
                if not m.selected and m.skipped_reason and not m.skipped_reason.startswith("not part of")
            )
            plan.warnings.insert(
                0,
                PlanWarning(
                    level="blocker",
                    code="empty_plan",
                    message=f"No tests were planned for the '{plan.suite}' suite on this target. "
                    + (f"Skills that were considered: {why[:400]}" if why else "No skill produced a test."),
                ),
            )
        if not ctx.interfaces and not ctx.unavailable:
            plan.warnings.insert(
                0,
                PlanWarning(
                    level="blocker",
                    code="no_interface",
                    message="The target exposes no interface AgentLab can drive; only static analysis of the repository "
                    "and documents is possible. Every dynamic test is predicted BLOCKED.",
                ),
            )
        blocked = plan.block_summary()
        sel = len(plan.selected_tests())
        if blocked and sel and sum(blocked.values()) / sel >= 0.25:
            top = "; ".join(f"{n} x {r[:110]}" for r, n in list(blocked.items())[:3])
            plan.warnings.append(
                PlanWarning(
                    code="many_blocked",
                    message=f"{sum(blocked.values())} of {sel} planned tests are predicted BLOCKED: {top}. "
                    "BLOCKED is not a failure: the report lists these as not tested.",
                )
            )
        if plan.budget.notes:
            plan.warnings.append(
                PlanWarning(
                    code="budget_exceeded",
                    message="The plan may exceed the configured limits: "
                    + "; ".join(plan.budget.notes)
                    + ". Choose a smaller suite or intensity, deselect tests or raise the limits.",
                )
            )
        gaps = [c.key for c in plan.coverage if c.status == "not_covered"]
        if gaps:
            plan.warnings.append(
                PlanWarning(
                    level="info",
                    code="coverage_gap",
                    message="Taxonomy areas that apply but have no runnable tests: " + ", ".join(gaps) + ".",
                )
            )
        if ctx.adapter_capabilities == {} and any(p.test.context.get("requires_capabilities") for p in plan.tests):
            plan.warnings.append(
                PlanWarning(
                    level="info",
                    code="capabilities_unknown",
                    message="Interface capabilities were not provided, so tests that must plant a canary, document or "
                    "tool output could not be pre-checked; the executor will BLOCK them if the interface cannot.",
                )
            )
        self._unchecked_requirements(plan, ctx)

    @staticmethod
    def _unchecked_requirements(plan: TestPlan, ctx: SkillContext) -> None:
        """A business rule the owner wrote becomes a test only when a model designed a scenario for it (no template can
        know what to ask). A rule that has none is named here: leaving it out silently would read as "checked"."""
        from agentlab.design.llm import requirements_of

        tested = {p.test.context.get("requirement") for p in plan.tests}
        missing = [r for r in requirements_of(ctx) if r not in tested]
        if not missing:
            return
        if plan.suite == "regression":
            why = "A regression run replays an earlier plan unchanged."
        elif not ctx.config.evaluation.llm_test_generation:
            why = (
                "Turning a rule into a scenario needs a model, and evaluation.llm_test_generation is off. Turn it on, "
                "or write the scenario yourself (--tests FILE)."
            )
        else:
            why = (
                "The model proposed no usable scenario for it (see the rejected suggestions). Write the scenario "
                "yourself (--tests FILE)."
            )
        shown = "; ".join(f"“{r[:80]}{'…' if len(r) > 80 else ''}”" for r in missing[:3])
        more = f" and {len(missing) - 3} more" if len(missing) > 3 else ""
        plan.warnings.append(
            PlanWarning(
                code="requirement_not_tested",
                message=f"{len(missing)} business rule(s) have no test, so nothing checks them: {shown}{more}. {why}",
            )
        )

    @staticmethod
    def _summary(plan: TestPlan) -> str:
        c = plan.counts()
        covered = [e.key for e in plan.coverage if e.status in {"covered", "partial"}]
        gaps = [e.key for e in plan.coverage if e.status == "not_covered"]
        sec = [e for e in plan.security_coverage if e.status in {"covered", "partial"}]
        text = (
            f"{c['tests']} tests from {c['skills_selected']} skills for '{plan.target}' "
            f"(suite {plan.suite}, intensity {plan.intensity}, wave {plan.wave}): {c['runnable']} runnable, "
            f"{c['blocked']} predicted BLOCKED. Taxonomy covered: {', '.join(covered) or 'none'}"
        )
        if gaps:
            text += f"; applicable but not covered: {', '.join(gaps)}"
        if plan.suite in {"full", "security"}:
            text += f". Security categories covered: {len(sec)} of {len(plan.security_coverage)}"
        return text + "."


def design_inputs_summary(ctx: SkillContext) -> dict[str, Any]:
    """The facts a plan was built from, for the plan header (no secrets: credential *names* only)."""
    return {
        "interfaces": list(ctx.interfaces),
        "judge_available": ctx.judge_available,
        "docker_available": ctx.docker_available,
        "browser_available": ctx.browser_available,
        "credential_profiles": list(ctx.credential_names),
        "documents": [d.name for d in ctx.documents] if ctx.documents else [],
        "tools": [t.name for t in ctx.tools],
        "intensity": ctx.intensity,
    }
