"""Build a :class:`ReportData` from a run's stored material (spec sections 29, 30, 49).

One function per report section. Nothing here calls the target, an LLM or the network: a report is a deterministic
function of what was recorded, so regenerating it later gives the same numbers. Facts, inferences, judgments and
recommendations are kept apart, and a test that did not run is never presented as a test that failed.
"""

from __future__ import annotations

import logging
import platform
import subprocess
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from agentlab import __version__
from agentlab.core.enums import TestStatus
from agentlab.core.models import Finding, Scorecard, TestResult
from agentlab.core.models.profile import AgentProfile
from agentlab.design.models import PlannedTest
from agentlab.evaluation.scoring import ScoringProfile, build_scorecard, load_profile
from agentlab.orchestrator.analysis import add_grade_note
from agentlab.reporting import model as m
from agentlab.reporting.material import RunMaterial
from agentlab.reporting.review import ACTIVE_FINDING, ReviewedRun, apply_reviews
from agentlab.reporting.util import (
    SEVERITY_ORDER,
    SEVERITY_POINTS,
    clean,
    histogram,
    label,
    mean,
    ms,
    percentile,
    plural,
    sev_rank,
)
from agentlab.services import Services
from agentlab.storage.db import canonical_hash

log = logging.getLogger(__name__)

UNASSESSED = {"not_tested", "not_covered", "partially_tested"}  # security category verdicts that are not a result
RAN = {TestStatus.PASSED, TestStatus.FAILED, TestStatus.TIMEOUT}
FAILING = {TestStatus.FAILED, TestStatus.TIMEOUT, TestStatus.ERROR}
STOPPED = {
    TestStatus.STOPPED_DUE_TO_COST,
    TestStatus.STOPPED_DUE_TO_TIMEOUT,
    TestStatus.STOPPED_DUE_TO_STEP_LIMIT,
}

DOMAINS: list[tuple[str, str, set[str], tuple[str, ...], str]] = [
    ("rag", "RAG evaluation", {"rag_quality"}, ("rag", "research"), "answers grounded in retrieved documents"),
    ("tools", "Tool evaluation", {"tool_use"}, ("tool_calling", "function_calling"), "choosing and using tools"),
    ("memory", "Memory evaluation", {"memory"}, ("memory",), "remembering, updating and isolating information"),
    ("browser", "Browser evaluation", {"browser_execution"}, ("browser", "computer_use"), "acting in a web browser"),
    (
        "multi_agent",
        "Multi-agent evaluation",
        {"multi_agent"},
        ("multi_agent", "supervisor", "sub_agents"),
        "delegating between cooperating agents",
    ),
    ("planning", "Planning evaluation", {"planning"}, ("planning", "autonomous"), "ordering and bounding steps"),
    ("coding", "Coding and repository evaluation", {"coding"}, ("coding", "repository"), "changing code safely"),
    ("mcp", "MCP evaluation", {"mcp"}, ("mcp",), "serving or using Model Context Protocol tools"),
    ("document", "Document and multimodal evaluation", {"document"}, ("document", "multimodal"), "reading attachments"),
]

EFFORT_BY_CAUSE = {
    "prompt_problem": "small",
    "authorization_problem": "medium",
    "tool_implementation_problem": "medium",
    "tool_selection_problem": "small",
    "retrieval_problem": "medium",
    "data_problem": "medium",
    "memory_problem": "medium",
    "orchestration_problem": "large",
    "model_limitation": "large",
    "api_problem": "medium",
    "ui_problem": "medium",
    "browser_interaction_problem": "medium",
    "security_vulnerability": "medium",
}

GLOSSARY = {
    "PASSED / FAILED": "The agent behaved as the test expected / did not. Only these two count in scores.",
    "BLOCKED": "A prerequisite was missing (credentials, judge model, Docker, browser, an interface). The test did not "
    "run, so it says nothing about the agent; it is a coverage gap, never a failure.",
    "ERROR": "The test could not be evaluated because of an infrastructure or evaluator problem. Not scored.",
    "Observed fact": "Something recorded in a trace or an output (a status code, a tool call, a leaked canary).",
    "Inference": "A conclusion AgentLab drew from the facts. It can be wrong; its confidence is stated.",
    "Judgment": "An opinion of an LLM judge or a human reviewer. Judgments carry confidence and the rubric used.",
    "Recommendation": "A suggested change. It is advice, not a measurement.",
    "Canary": "A harmless unique marker planted in prompts or data; its appearance in an output proves a leak without "
    "exposing anything real.",
    "Confidence": "How sure AgentLab is that the finding is a real defect (0-1), from agreement between independent "
    "signals and repetitions; it is not a probability of harm.",
}

SEVERITY_MODEL = [
    "Severity combines impact (what could happen), exploitability (how easy it is to trigger), the sensitivity of "
    "what is exposed and the breadth of the failure; the breakdown is stored with every finding.",
    "Critical: leaks of secrets or personal data, unauthorised irreversible actions. High: a safety or security "
    "control is bypassed, or a core function is wrong. Medium: a visible quality or robustness failure. "
    "Low: a minor deviation. Info: an observation.",
    "Open security findings cap the overall score (critical 40, high 65, medium 85) and open high/critical "
    "functional failures cap the grade letter, so a weighted average cannot hide them.",
]

EVALUATION_LAYERS = [
    "Layer 1 - deterministic checks: exact, structural and behavioural assertions (contains, regex, tool calls, state, "
    "status codes, canaries). Cheap, repeatable and always run first.",
    "Layer 2 - LLM judge: used only for criteria that rules cannot express, with a stored rubric, separate judge "
    "models, a reported agreement and uncertainty. The target never sees the judge and never influences it; judged "
    "text is treated as untrusted data.",
    "Layer 3 - trajectory evaluation: tool choice, argument validity, order, redundant or looping steps, hand-offs "
    "and plan adherence, taken from the provider-neutral trace.",
]

SAFETY_RULES = [
    "Tests run only against targets the owner supplied; unknown or production targets need explicit authorization.",
    "Security tests are authorized, non-destructive and canary based; no real data is stolen or damaged.",
    "Untrusted repositories and generated code run only in a sandbox with a default-deny network; if no sandbox is "
    "available such tests are BLOCKED (fail secure).",
    "Secrets are never written to logs, traces, artifacts or reports; stored text is redacted first.",
    "Target output is untrusted data: it can never change the tests, the judge or the scoring.",
]


# ===================================================================================================== helpers
def _git_commit() -> str | None:
    """The commit of an AgentLab source checkout, when there is one (never fails, never runs in the target repo)."""
    root = Path(__file__).resolve().parents[3]
    if not (root / ".git").exists():
        return None
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, our own checkout
            ["git", "-C", str(root), "rev-parse", "--short=12", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def _status(r: TestResult) -> str:
    return r.status.value


def _failed_checks(r: TestResult) -> list[str]:
    """Why a result failed: failed required checks of the last attempt, failed judge criteria, the error."""
    if not r.attempts:
        return [clean(r.blocked_reason, 300)] if r.blocked_reason else []
    last = r.attempts[-1]
    out = [
        f"{a.type}: {clean(a.message, 300)}"
        for a in last.assertions
        if not a.passed and a.required and not a.evaluator_error
    ]
    out += [f"judge ({j.metric}): {clean(j.rubric, 120)} (score {j.score:.2f})" for j in last.judge if not j.passed]
    if last.error:
        out.append(f"error: {clean(last.error, 300)}")
    return out


def _check_view(r: TestResult, a: Any) -> m.CheckView:
    return m.CheckView(
        type=a.type,
        passed=a.passed,
        message=clean(a.message, 400),
        metric=a.metric,
        required=a.required,
        evaluator_error=a.evaluator_error,
        turn_index=a.turn_index,
    )


def _attempt_view(r: TestResult, att: Any, *, outputs: int) -> m.AttemptView:
    return m.AttemptView(
        attempt=att.attempt,
        status=att.status.value,
        latency_ms=round(att.latency_ms, 1),
        tokens=att.tokens,
        cost_usd=att.cost_usd,
        steps=att.steps,
        error=clean(att.error, 300) if att.error else None,
        outputs=[clean(o, 1200) for o in att.outputs[-outputs:]],
        checks=[_check_view(r, a) for a in att.assertions if not a.evaluator_error][:30],
        judge=[
            {
                "metric": j.metric,
                "score": round(j.score, 3),
                "passed": j.passed,
                "confidence": round(j.confidence, 3),
                "strategy": j.strategy,
                "agreement": round(j.agreement, 3),
                "uncertain": j.uncertain,
                "rubric": clean(j.rubric, 400),
                "votes": [
                    {
                        "judge": v.judge,
                        "model": v.model,
                        "score": v.score,
                        "passed": v.passed,
                        "reasoning": clean(v.reasoning, 300),
                    }
                    for v in j.votes
                ],
            }
            for j in att.judge
        ],
        trace_id=att.trace_id,
    )


def _overlays(
    rows: list[dict[str, Any]], results: Sequence[TestResult] = (), findings: Sequence[Finding] = ()
) -> list[m.ReviewOverlay]:
    test_of = {r.id: r.test_id for r in results}
    finding_of = {f.id: f.test_id for f in findings}
    return [
        m.ReviewOverlay(
            id=str(rv["id"]),
            decision=str(rv["decision"]),
            reviewer=clean(rv["reviewer"], 200),
            subject_type=str(rv.get("subject_type") or ""),
            subject=(test_of if rv.get("subject_type") == "result" else finding_of).get(
                str(rv.get("subject_id")), str(rv.get("subject_id") or "")
            ),
            reason=clean(rv.get("reason"), 600),
            comment=clean(rv.get("comment"), 600),
            created_at=rv.get("created_at"),
            original=dict(rv.get("original") or {}),
            reviewed=dict(rv.get("reviewed") or {}),
        )
        for rv in rows
    ]


def finding_priority(f: Finding) -> int:
    """Remediation priority: severity x confidence, security first (higher number = fix sooner)."""
    base = SEVERITY_POINTS.get(f.severity.value, 0) * (0.5 + 0.5 * f.confidence)
    return round(base * (1.25 if f.is_security else 1.0))


# =================================================================================================== sections
def _scorecard_view(sc: Scorecard | None, profile: ScoringProfile | None, executed: int) -> m.ScorecardView:
    if sc is None:
        return m.ScorecardView(
            profile=profile.name if profile else "none",
            notes=["No scorecard: no test produced a result that can be scored."]
            if not executed
            else ["No scorecard was recorded for this run."],
        )
    return m.ScorecardView(
        profile=sc.profile,
        profile_description=sc.profile_description,
        overall=sc.overall,
        raw_overall=sc.raw_overall,
        grade=sc.grade,
        confidence=sc.overall_confidence,
        security_cap_applied=sc.security_cap_applied,
        cap_reason=sc.cap_reason,
        categories=[
            m.CategoryRow(
                category=c.category,
                label=label(c.category),
                score=c.score,
                weight=c.weight,
                confidence=c.confidence,
                tests=c.tests,
                passed=c.passed,
                applicable=c.applicable,
                note=c.note,
            )
            for c in sc.categories
        ],
        qualifiers=list(dict.fromkeys(sc.qualifiers)),
        notes=list(sc.notes),
        counts=dict(sc.counts),
    )


def _ceilings_text(scoring: ScoringProfile) -> str:
    ceilings = scoring.grade_ceilings
    parts = []
    if "critical" in ceilings:
        parts.append(f"an open critical failure caps the grade at {ceilings['critical']}")
    if "many_high" in ceilings:
        parts.append(f"{scoring.many_high} or more open high-severity failures cap it at {ceilings['many_high']}")
    if "high" in ceilings:
        parts.append(f"any open high-severity failure caps it at {ceilings['high']}")
    if not parts:
        return "No grade ceilings for open failures."
    return "Grade ceilings: " + "; ".join(parts) + "."


def _auth_text(auth: dict[str, Any]) -> str:
    """The authentication a target needs, in words (the profile keeps it as a small dictionary)."""
    if not auth:
        return "not specified"
    schemes = [str(x) for x in auth.get("schemes") or []]
    profiles = [str(x) for x in auth.get("credential_profiles") or []]
    required = auth.get("required")
    if required is False and not schemes:
        return "none required"
    parts = ["required" if required else "optional" if required is False else "unknown if required"]
    if schemes:
        parts.append("schemes: " + ", ".join(schemes))
    if profiles:
        parts.append("credential profiles: " + ", ".join(profiles))
    return clean("; ".join(parts), 300)


_TOOLS_HIDDEN = "Tool calls are not observable through the interface"


def _limitations_seen(limitations: list[str], results: list[TestResult]) -> list[str]:
    """Discovery states limits from a handful of harmless probes. When testing later showed such a limit does not
    hold (tool calls *were* visible in the attempts), say so instead of repeating the earlier guess."""
    saw_tools = any(a.trajectory.get("tool_calls") for r in results for a in r.attempts)
    out: list[str] = []
    for text in limitations:
        if saw_tools and text.startswith(_TOOLS_HIDDEN):
            out.append("Tool calls were not visible to the discovery probes, but they were observed during testing.")
        else:
            out.append(clean(text, 300))
    return out


def _target_overview(mat: RunMaterial) -> m.TargetOverview:
    spec, prof = mat.target, mat.profile
    manifest_target = mat.manifest.get("target", {})
    repo = dict(prof.repository) if prof else {}
    for key in ("path", "local_path", "workspace"):
        repo.pop(key, None)  # host paths say nothing about the target and should not leak into a shared report
    return m.TargetOverview(
        name=(spec.name if spec else None) or str(manifest_target.get("name") or mat.run.get("id")),
        version=(spec.version if spec else None) or manifest_target.get("version"),
        description=clean((spec.description if spec else "") or (prof.summary if prof else ""), 800),
        interfaces=list(prof.interfaces if prof and prof.interfaces else manifest_target.get("interfaces", [])),
        authentication=_auth_text(prof.authentication if prof else {}),
        models=list(prof.models) if prof else [],
        frameworks=list(prof.frameworks) if prof else [],
        languages=dict(prof.languages) if prof else {},
        repository=repo,
        tools=[
            {
                "name": t.name,
                "description": clean(t.description, 200),
                "side_effects": t.side_effects,
                "requires_confirmation": t.requires_confirmation,
                "source": t.source,
            }
            for t in (prof.tools if prof else [])
        ],
        data_sources=[
            {"name": d.name, "kind": d.kind, "source": d.source} for d in (prof.data_sources if prof else [])
        ],
        limitations=_limitations_seen(prof.limitations if prof else [], mat.results),
        production=bool(manifest_target.get("production")),
        modes=[mo.value for mo in (prof.modes if prof else [])],
    )


def _architecture(prof: AgentProfile | None) -> m.ArchitectureView:
    if prof is None or not prof.architecture.nodes:
        return m.ArchitectureView(note="No architecture could be derived (black-box target with no discovery data).")
    g = prof.architecture
    return m.ArchitectureView(
        nodes=[{"id": n.id, "label": clean(n.label, 60), "kind": n.kind} for n in g.nodes],
        edges=[{"source": e.source, "target": e.target, "label": clean(e.label, 40)} for e in g.edges],
        mermaid=g.to_mermaid(),
        note="Derived from the repository, the declared interfaces and what discovery observed. "
        "Components that were not seen are not drawn.",
    )


def _environment(mat: RunMaterial) -> m.EnvironmentView:
    env = dict(mat.analysis.get("environment") or {})
    return m.EnvironmentView(**{k: v for k, v in env.items() if k in m.EnvironmentView.model_fields})


def _methodology(mat: RunMaterial, plan_total: int, scoring: ScoringProfile | None) -> m.MethodologyView:
    man = mat.manifest
    plan = man.get("plan", {})
    judges = man.get("judges", {})
    cfg = man.get("config", {}).get("evaluation", {})
    steps = [
        "Ingest the target and fingerprint it: interfaces, tools, data sources, memory, browser use, agents; "
        "classify it into agent types with evidence and confidence.",
        f"Select skills (versioned test generators) and design an explainable test plan "
        f"({plan.get('tests', plan_total)} tests, suite '{plan.get('suite', 'full')}', "
        f"intensity '{plan.get('intensity', 'standard')}') over the test taxonomy; every test records why it exists.",
        "Classify the risk of each test and gate it: risky tests need authorization, and tests whose prerequisites "
        "are missing are marked BLOCKED before anything is sent to the target.",
        "Execute in an isolated environment with cost, step and time limits; record a provider-neutral trace of "
        "every request, response, tool call, retrieval, hand-off and action.",
        "Evaluate in three layers (below), repeat tests for reliability where the plan calls for it, then analyse "
        "patterns across tests, security posture and reliability.",
        "Score with the selected profile, derive severity, root cause and confidence for each finding, and build "
        "this report from the stored records.",
    ]
    assumptions = [a for plan_ in mat.plans for a in plan_.assumptions if a]
    return m.MethodologyView(
        summary=(
            "AgentLab tests an agent as a black box through its interfaces, and as a white box when its source is "
            "available. Every number in this report can be traced to a test, its trace and its evidence."
        ),
        steps=steps,
        evaluation_layers=EVALUATION_LAYERS
        + (
            [
                f"Judge configuration: {'enabled' if judges.get('enabled') else 'not available in this run'}"
                + (f", strategy '{cfg.get('judge_strategy')}'" if cfg.get("judge_strategy") else "")
                + "."
            ]
        ),
        safety_rules=SAFETY_RULES,
        scoring=(
            [
                f"Profile '{scoring.name}': {scoring.description}".strip(),
                "Weights: "
                + ", ".join(
                    f"{label(k)} {v:g}" for k, v in sorted(scoring.weights.items(), key=lambda kv: -kv[1]) if v
                ),
                "Grades: "
                + ", ".join(f"{g} ≥ {t:g}" for g, t in sorted(scoring.grades.items(), key=lambda kv: -kv[1])),
                _ceilings_text(scoring),
            ]
            if scoring
            else ["The scoring profile was not recorded."]
        ),
        severity_model=SEVERITY_MODEL,
        assumptions=list(dict.fromkeys(assumptions))[:20],
    )


def _inventory(mat: RunMaterial, results: list[TestResult]) -> m.SuiteInventory:
    planned = mat.planned()
    by_id = {r.test_id: r for r in results}

    def tally(keyfn: Any) -> list[m.InventoryRow]:
        rows: dict[str, m.InventoryRow] = {}
        for tid, p in planned.items():
            key = keyfn(p)
            row = rows.setdefault(key, m.InventoryRow(key=key, label=key))
            r = by_id.get(tid)
            row.tests += 1
            if r is None:
                row.other += 1
            elif r.status == TestStatus.PASSED:
                row.passed += 1
            elif r.status in {TestStatus.FAILED, TestStatus.TIMEOUT}:
                row.failed += 1
            elif r.status == TestStatus.BLOCKED:
                row.blocked += 1
            else:
                row.other += 1
        return sorted(rows.values(), key=lambda x: (-x.tests, x.key))

    def coverage(entries: Any) -> list[m.CoverageRow]:
        out: list[m.CoverageRow] = []
        for e in entries:
            ids = [
                tid
                for tid, p in planned.items()
                if (e.key in p.taxonomy or e.key in p.security_categories) and p.selected
            ]
            ran = sum(1 for tid in ids if tid in by_id and by_id[tid].status in RAN)
            blocked = sum(1 for tid in ids if tid in by_id and by_id[tid].status == TestStatus.BLOCKED)
            out.append(
                m.CoverageRow(
                    key=e.key, name=e.name, status=e.status, tests=len(ids), executed=ran, blocked=blocked, note=e.note
                )
            )
        return out

    first = mat.plans[0] if mat.plans else None
    selected = [p for p in planned.values() if p.selected]
    return m.SuiteInventory(
        total_planned=len(planned),
        total_selected=len(selected),
        executed=sum(1 for r in results if r.status in RAN),
        by_skill=tally(lambda p: p.skill),
        by_category=tally(lambda p: p.test.category),
        by_origin=tally(lambda p: p.origin),
        coverage=coverage(first.coverage) if first else [],
        security_coverage=coverage(first.security_coverage) if first else [],
        deselected=[
            {"test": p.id, "reason": clean(p.deselected_reason, 200)} for p in planned.values() if not p.selected
        ][:200],
        plan_hash=first.plan_hash if first else "",
        plan_summary=clean(first.summary, 800) if first else "",
        waves=[
            {
                "wave": pl.wave,
                "tests": len(pl.selected_tests()),
                "plan_hash": pl.plan_hash,
                "summary": clean(pl.summary, 300),
            }
            for pl in mat.plans
        ],
        skills=[
            {
                "name": s.skill,
                "version": s.version,
                "tests": s.tests,
                "predicted_blocked": s.predicted_blocked,
                "taxonomy": s.taxonomy,
                "reasons": [clean(x, 200) for x in s.reasons[:3]],
            }
            for s in (first.skills if first else [])
            if s.selected
        ],
    )


def _result_row(
    r: TestResult,
    original: TestResult,
    planned: dict[str, PlannedTest],
    finding_by_test: dict[str, Finding],
    reviews: dict[str, list[dict[str, Any]]],
) -> m.ResultRow:
    p = planned.get(r.test_id)
    t = p.test if p else None
    inputs = [clean(turn.input, 500) for turn in (t.all_turns() if t else [])][:6]
    f = finding_by_test.get(r.test_id)
    rel = original.reliability
    row = m.ResultRow(
        test_id=r.test_id,
        name=clean(r.test_name, 200),
        category=r.category,
        score_category=r.score_category,
        skill=p.skill if p else None,
        status=_status(original),
        effective_status=_status(r) if r.status != original.status else None,
        score=round(original.score, 3),
        confidence=round(original.confidence, 3),
        severity=original.severity.value if original.severity else None,
        root_cause=original.root_cause.value if original.root_cause else None,
        latency_ms=round(original.latency_ms, 1),
        tokens=original.tokens,
        cost_usd=original.cost_usd,
        attempts=max(1, len(original.attempts)),
        pass_rate=rel.pass_rate if rel else None,
        flaky=bool(rel and rel.flaky),
        blocked_reason=clean(original.blocked_reason, 400) if original.blocked_reason else None,
        error_kind=original.error_kind.value if original.error_kind else None,
        objective=clean(t.objective if t else "", 400),
        expected=clean((t.expected_behavior or t.objective) if t else "", 500),
        inputs=inputs,
        failed_checks=_failed_checks(original) if original.status in FAILING else [],
        finding_id=f.id if f else None,
        taxonomy=list(p.taxonomy) if p else [],
        security_categories=list(p.security_categories) if p else [],
        reviews=_overlays(reviews.get(original.id, [])),
    )
    return row


def _failed_test(
    row: m.ResultRow, r: TestResult, finding: Finding | None, run_id: str, timeline: list[str]
) -> m.FailedTest:
    attempts = [_attempt_view(r, a, outputs=2) for a in r.attempts[-3:]]
    why = list(row.failed_checks)
    if timeline:
        why.append("trace: " + " → ".join(timeline[:12]))
    return m.FailedTest(
        test_id=row.test_id,
        name=row.name,
        category=row.category,
        status=row.status,
        severity=row.severity,
        objective=row.objective,
        expected=row.expected,
        inputs=row.inputs,
        attempts=attempts,
        why_it_failed=why,
        reproduction=clean(finding.reproduction, 600)
        if finding
        else f"agentlab test --only {row.test_id} (same target file)",
        finding_id=row.finding_id,
        evidence=list(finding.evidence) if finding else list(r.evidence),
    )


def _finding_view(f: Finding, eff: Finding, reviews: dict[str, list[dict[str, Any]]]) -> m.FindingView:
    return m.FindingView(
        id=f.id,
        test_id=f.test_id,
        title=clean(f.title, 300),
        category=f.category,
        severity=f.severity.value,
        effective_severity=eff.severity.value if eff.severity != f.severity else None,
        confidence=round(f.confidence, 3),
        is_security=f.is_security,
        status=f.status if eff.status == f.status else eff.status,
        expected=clean(f.expected, 700),
        observed=clean(f.observed, 900),
        impact=clean(f.impact, 700),
        reproduction=clean(f.reproduction, 700),
        recommendation=clean(f.recommendation, 700),
        root_cause=f.root_cause.value,
        root_cause_confidence=round(f.root_cause_confidence, 3),
        facts=[clean(x, 500) for x in f.facts],
        inferences=[clean(x, 500) for x in f.inferences],
        judgments=[clean(x, 500) for x in f.judgments],
        evidence=list(f.evidence),
        severity_breakdown=dict(f.severity_breakdown),
        reviews=_overlays(reviews.get(f.id, [])),
        priority=finding_priority(eff),
    )


def _security(mat: RunMaterial, findings: list[m.FindingView]) -> m.SecuritySection:
    raw = mat.analysis.get("security")
    if not raw:
        sec = [f for f in findings if f.is_security]
        return m.SecuritySection(
            tested=bool(sec),
            posture="not_tested" if not sec else "findings_only",
            summary="No security analysis was recorded for this run."
            if not sec
            else f"{plural(len(sec), 'security finding')} recorded; no per-category analysis is available.",
            findings=[f.id for f in sec],
        )
    cats = [
        m.SecurityCategoryView(
            code=c["code"],
            name=c["name"],
            verdict=c["verdict"],
            tests=c.get("tests", 0),
            passed=c.get("passed", 0),
            failed=c.get("failed", 0),
            blocked=c.get("blocked", 0),
            failing_tests=list(c.get("failing_tests", [])),
            blocked_tests=list(c.get("blocked_tests", [])),
            note=clean(c.get("note"), 300),
        )
        for c in raw.get("categories", [])
    ]
    posture = str(raw.get("posture", "not_tested"))
    return m.SecuritySection(
        tested=posture != "not_tested",
        posture=posture,
        summary=clean(raw.get("summary"), 800),
        rating_note=clean(raw.get("rating_note"), 600),
        caveats=[clean(c, 400) for c in raw.get("caveats", [])],
        categories=cats,
        attacks_succeeded=[
            {
                "test_id": a.get("test_id"),
                "name": clean(a.get("name"), 200),
                "codes": a.get("codes", []),
                "severity": a.get("severity"),
                "channels": a.get("channels", []),
                "failed_checks": [clean(x, 200) for x in a.get("failed_checks", [])][:4],
                "snippet": clean(a.get("snippet"), 300) if a.get("snippet") else None,
                "intermittent": bool(a.get("intermittent")),
                "leak": bool(a.get("leak")),
            }
            for a in raw.get("attacks_succeeded", [])
        ],
        verdict_counts=dict(raw.get("verdict_counts", {})),
        findings=[f.id for f in findings if f.is_security],
    )


def _domains(
    mat: RunMaterial, results: list[TestResult], planned: dict[str, PlannedTest], scorecard: m.ScorecardView
) -> list[m.DomainSection]:
    prof = mat.profile
    by_cat = {c.category: c for c in scorecard.categories}
    out: list[m.DomainSection] = []
    for key, title, cats, types, what in DOMAINS:
        rs = [r for r in results if r.score_category in cats]
        planned_here = [p for p in planned.values() if p.test.score_category in cats and p.selected]
        detected = bool(prof) and any(prof.type_confidence(t) >= 0.5 for t in _agent_types(types))  # type: ignore[union-attr]
        if not rs and not planned_here:
            reason = (
                f"The target looks like it handles {what}, but no test was planned for it: this area is NOT assessed."
                if detected
                else f"Not applicable: the target shows no sign of {what}."
            )
            out.append(m.DomainSection(key=key, title=title, applicable=detected, summary=reason, notes=[reason]))
            continue
        passed = sum(1 for r in rs if r.status == TestStatus.PASSED)
        failed = sum(1 for r in rs if r.status in FAILING)
        blocked = [r for r in rs if r.status == TestStatus.BLOCKED]
        subs: dict[str, dict[str, int]] = defaultdict(lambda: {"passed": 0, "failed": 0, "blocked": 0})
        for r in rs:
            p = planned.get(r.test_id)
            sub = p.test.subcategory if p else "general"
            bucket = (
                "passed"
                if r.status == TestStatus.PASSED
                else "blocked"
                if r.status == TestStatus.BLOCKED
                else "failed"
                if r.status in FAILING
                else None
            )
            if bucket:
                subs[sub][bucket] += 1
        cat = next((by_cat[c] for c in cats if c in by_cat), None)
        ran = passed + failed
        summary = (
            f"{passed} of {ran} executed tests passed"
            + (f"; {plural(len(blocked), 'test')} could not run (BLOCKED)" if blocked else "")
            + "."
            if ran
            else f"No test ran: {plural(len(blocked), 'test')} BLOCKED."
        )
        out.append(
            m.DomainSection(
                key=key,
                title=title,
                applicable=True,
                summary=summary,
                tests=len(rs),
                passed=passed,
                failed=failed,
                blocked=len(blocked),
                score=cat.score if cat else None,
                metrics={"by_subcategory": {k: dict(v) for k, v in sorted(subs.items())}},
                failures=[f"{r.test_id}: {clean(r.test_name, 120)}" for r in rs if r.status in FAILING],
                blocked_tests=[{"test": r.test_id, "reason": clean(r.blocked_reason, 200)} for r in blocked],
            )
        )
    return out


def _agent_types(names: tuple[str, ...]) -> list[Any]:
    from agentlab.core.enums import AgentType

    out = []
    for n in names:
        try:
            out.append(AgentType(n))
        except ValueError:
            continue
    return out


def _reliability(mat: RunMaterial, results: list[TestResult], planned: dict[str, PlannedTest]) -> m.ReliabilitySection:
    raw = mat.analysis.get("reliability")
    if raw:
        return m.ReliabilitySection(
            verdict=str(raw.get("verdict", "not_measured")),
            measured_tests=int(raw.get("measured_tests", 0)),
            single_run_tests=int(raw.get("single_run_tests", 0)),
            stable_passes=int(raw.get("stable_passes", 0)),
            consistency=raw.get("consistency"),
            deterministic_failures=list(raw.get("deterministic_failures", [])),
            flaky=[
                m.ReliabilityFlaky(
                    test_id=f["test_id"],
                    name=clean(f.get("name"), 160),
                    passes=f["passes"],
                    repetitions=f["repetitions"],
                    pass_rate=f["pass_rate"],
                )
                for f in raw.get("flaky", [])
            ],
            timeout_tests=list(raw.get("timeout_tests", [])),
            error_tests=list(raw.get("error_tests", [])),
            notes=[clean(n, 300) for n in raw.get("notes", [])],
        )
    measured = [r for r in results if r.reliability and r.reliability.repetitions > 1]
    return m.ReliabilitySection(
        verdict="not_measured" if not measured else "measured",
        measured_tests=len(measured),
        single_run_tests=sum(1 for r in results if r.status in RAN) - len(measured),
        flaky=[
            m.ReliabilityFlaky(
                test_id=r.test_id,
                name=clean(r.test_name, 160),
                passes=r.reliability.passes,
                repetitions=r.reliability.repetitions,
                pass_rate=r.reliability.pass_rate,
            )
            for r in measured
            if r.reliability and r.reliability.flaky
        ],
        notes=["Rebuilt from per-test statistics; the reliability analysis was not recorded."],
    )


def _performance(mat: RunMaterial, results: list[TestResult], scoring: ScoringProfile | None) -> m.PerformanceSection:
    latencies: list[tuple[str, str, float]] = []
    for r in results:
        if r.status not in RAN:
            continue
        values = [a.latency_ms for a in r.attempts if a.latency_ms > 0] or ([r.latency_ms] if r.latency_ms > 0 else [])
        if values:
            latencies.append((r.test_id, r.test_name, max(values)))
    flat = [v for *_x, v in latencies]
    budget = mat.manifest.get("config", {}).get("evaluation", {}).get("latency_budget_ms") or (
        scoring.latency_budget_ms if scoring else None
    )
    ordered = sorted(latencies, key=lambda t: -t[2])
    notes: list[str] = []
    if not flat:
        notes.append("No latency was measured (no test reached the target).")
    elif budget:
        notes.append(f"Latency budget: {ms(float(budget))} per request (evaluation.latency_budget_ms).")
    return m.PerformanceSection(
        measured=len(flat),
        p50_ms=percentile(flat, 50),
        p95_ms=percentile(flat, 95),
        max_ms=max(flat) if flat else None,
        mean_ms=mean(flat),
        budget_ms=float(budget) if budget else None,
        over_budget=[tid for tid, _n, v in latencies if budget and v > float(budget)],
        slowest=[m.LatencyPoint(test_id=t, name=clean(n, 80), latency_ms=round(v, 1)) for t, n, v in ordered[:10]],
        histogram=histogram(flat, [0, 50, 100, 250, 500, 1000, 2500, 5000]) if flat else [],
        timeouts=sum(1 for r in results if r.status == TestStatus.TIMEOUT),
        notes=notes,
    )


def _cost(mat: RunMaterial, results: list[TestResult]) -> m.CostSection:
    limits = dict(mat.analysis.get("limits") or {})
    by_cat: dict[str, m.CostRow] = {}
    for r in results:
        row = by_cat.setdefault(r.score_category, m.CostRow(key=r.score_category, label=label(r.score_category)))
        row.tokens += r.tokens
        row.cost_usd += r.cost_usd
        row.tests += 1
    target_tokens = sum(r.tokens for r in results)
    total_tokens = int(limits.get("tokens", target_tokens) or 0)
    cost = float(limits.get("cost_usd", sum(r.cost_usd for r in results)) or 0.0)
    judge_cost = float((limits.get("cost_by_category") or {}).get("judge", 0.0) or 0.0)
    expensive = sorted(
        (
            m.CostRow(key=r.test_id, label=clean(r.test_name, 80), tokens=r.tokens, cost_usd=r.cost_usd, tests=1)
            for r in results
            if r.tokens or r.cost_usd
        ),
        key=lambda c: (-c.cost_usd, -c.tokens),
    )[:8]
    pred = (mat.manifest.get("outcome") or {}).get("plan_prediction") or {}
    plan_budget = mat.plans[0].budget if mat.plans else None
    return m.CostSection(
        total_tokens=total_tokens,
        total_cost_usd=round(cost, 6),
        target_tokens=target_tokens,
        judge_tokens=max(total_tokens - target_tokens, 0),
        judge_cost_usd=round(judge_cost, 6),
        cost_known=cost > 0,
        by_category=sorted(by_cat.values(), key=lambda c: -c.cost_usd),
        most_expensive=expensive,
        limits=dict(limits.get("limits") or {}),
        estimated={
            "plan_tokens": plan_budget.est_tokens if plan_budget else None,
            "plan_cost_usd": plan_budget.est_cost_usd if plan_budget else None,
            "prediction": pred,
        },
        notes=[
            "Cost is what the target and the judge reported. A target that does not report usage shows 0 here; "
            "that means 'not reported', not 'free'."
        ]
        + ([plan_budget.cost_note] if plan_budget and plan_budget.cost_note else []),
    )


def _trend(mat: RunMaterial, current: m.ScorecardView, results: list[TestResult], findings: int) -> m.TrendSection:
    points: list[m.TrendPoint] = []
    plan_hash = mat.manifest.get("plan", {}).get("hash")
    profile = mat.manifest.get("scoring_profile")
    for h in mat.history:
        counts = h.get("counts") or {}
        points.append(
            m.TrendPoint(
                run_id=h["run_id"],
                started_at=h.get("started_at"),
                overall=h.get("overall"),
                failed=int(counts.get("failed", 0)) + int(counts.get("timeout", 0)),
                tests=int(h.get("tests") or 0),
                findings=int(h.get("findings") or 0),
                comparable=(h.get("scoring_profile") == profile),
            )
        )
    points.append(
        m.TrendPoint(
            run_id=str(mat.run["id"]),
            started_at=mat.run.get("started_at") or mat.run.get("created_at"),
            overall=current.overall,
            failed=sum(1 for r in results if r.status in {TestStatus.FAILED, TestStatus.TIMEOUT}),
            tests=len(results),
            findings=findings,
        )
    )
    note = (
        "Earlier runs against the same target. Runs scored with a different profile are marked as not comparable; "
        "compare two runs with `agentlab compare` for a like-for-like view."
        if len(points) > 1
        else "This is the first recorded run against this target, so there is no trend yet."
    )
    _ = plan_hash
    return m.TrendSection(points=points, note=note)


def _evidence(sv: Services | None, mat: RunMaterial) -> m.EvidenceSection:
    items: list[m.EvidenceItem] = []
    for a in mat.artifacts:
        items.append(
            m.EvidenceItem(
                id=f"sha256-{a['sha256']}",
                kind=str(a.get("kind")),
                name=str(a.get("name") or ""),
                media_type=str(a.get("media_type") or ""),
                size=int(a.get("size") or 0),
                sha256=str(a["sha256"]),
                sensitivity=str(a.get("sensitivity") or "normal"),
                test_key=a.get("test_key"),
            )
        )
    items.sort(key=lambda i: (i.kind, i.test_key or "", i.name))
    browser = []
    for s in mat.browser_sessions:
        actions = []
        for i, act in enumerate(s.get("actions") or []):
            if not isinstance(act, dict):
                continue
            actions.append(
                m.BrowserStepView(
                    index=i,
                    action=str(act.get("action", act.get("type", "step"))),
                    target=clean(act.get("target") or act.get("selector") or act.get("url") or "", 200),
                    ok=bool(act.get("ok", act.get("success", True))),
                    detail=clean(act.get("detail") or act.get("error") or act.get("message") or "", 300),
                    at_ms=act.get("at_ms") or act.get("t_ms"),
                    screenshot=act.get("screenshot"),
                )
            )
        browser.append(
            m.BrowserSessionView(
                test_id=str(s["test_key"]),
                browser=str(s.get("browser") or ""),
                trace=s.get("trace_artifact_id"),
                video=s.get("video_artifact_id"),
                screenshots=list(s.get("screenshot_artifact_ids") or []),
                actions=actions,
                meta={k: clean(v, 200) if isinstance(v, str) else v for k, v in (s.get("meta") or {}).items()},
            )
        )
    repo: list[m.RepositoryEvidence] = []
    if sv is not None:
        for a in mat.artifacts:
            if a.get("kind") != "workspace":
                continue
            try:
                data = sv.artifacts.get_json(f"sha256-{a['sha256']}")
            except Exception:  # noqa: S112 - one unreadable artifact must not drop the others
                continue
            if isinstance(data, dict):
                repo.append(
                    m.RepositoryEvidence(
                        test_id=str(a.get("test_key") or data.get("test_id") or ""),
                        files=[clean(f, 200) for f in data.get("files", [])][:60],
                        diff=clean(data.get("diff"), 6000) if data.get("diff") else None,
                        test_output=clean(data.get("test_output"), 4000) if data.get("test_output") else None,
                        notes=[clean(n, 300) for n in data.get("notes", [])],
                    )
                )
    return m.EvidenceSection(
        items=items[:600],
        browser=browser,
        repository=repo,
        trace_ids=[str(t["id"]) for t in mat.traces][:600],
    )


# ============================================================================================ recommendations
_BLOCK_ACTIONS: list[tuple[str, str, str]] = [
    (
        "judge",
        "Configure an independent LLM judge (a model that is not the target) so quality criteria can be judged.",
        "configuration",
    ),
    (
        "credential",
        "Provide the named test credentials (`agentlab credentials add`) so authenticated tests can run.",
        "configuration",
    ),
    ("docker", "Make Docker (or another configured sandbox) available so sandboxed tests can run.", "configuration"),
    ("sandbox", "Make a sandbox available so tests that execute untrusted code can run.", "configuration"),
    (
        "browser",
        "Install the Playwright browser (`playwright install chromium`) so browser tests can run.",
        "configuration",
    ),
    (
        "canary",
        "Give the target a way to receive canaries (a knowledge endpoint or a LLM-wrapper adapter) so leak tests can run.",
        "coverage",
    ),
    (
        "authoriz",
        "Authorize the risky test class for this target in the target file if that is acceptable.",
        "configuration",
    ),
    ("interface", "Declare the missing interface in the target file so the tests that need it can run.", "coverage"),
]


def _recommendations(
    findings: list[m.FindingView],
    results: list[TestResult],
    sec: m.SecuritySection,
    rel: m.ReliabilitySection,
    executed: int,
) -> list[m.Recommendation]:
    active = [f for f in findings if f.status in ACTIVE_FINDING and (f.effective_severity or f.severity) != "info"]
    groups: dict[tuple[str, str], list[m.FindingView]] = {}
    for f in sorted(active, key=lambda f: -f.priority):
        groups.setdefault((f.root_cause, f.recommendation[:90].lower()), []).append(f)
    recs: list[m.Recommendation] = []
    for (cause, _key), fs in sorted(groups.items(), key=lambda kv: -max(f.priority for f in kv[1])):
        top = fs[0]
        sev = max((f.effective_severity or f.severity for f in fs), key=sev_rank)
        recs.append(
            m.Recommendation(
                priority=0,
                title=top.title if len(fs) == 1 else f"{top.title} (and {plural(len(fs) - 1, 'similar finding')})",
                why=top.impact,
                action=top.recommendation,
                severity=sev,
                confidence=max(f.confidence for f in fs),
                findings=[f.id for f in fs],
                tests=sorted({f.test_id for f in fs}),
                effort=EFFORT_BY_CAUSE.get(cause, "unknown"),  # type: ignore[arg-type]
                kind="fix",
            )
        )
    blocked = Counter(
        clean(r.blocked_reason, 160) for r in results if r.status == TestStatus.BLOCKED and r.blocked_reason
    )
    handled: set[str] = set()
    for reason, n in blocked.most_common(6):
        low = reason.lower()
        action, kind = next(((a, k) for needle, a, k in _BLOCK_ACTIONS if needle in low), (None, "coverage"))
        if action in handled:
            continue
        if action:
            handled.add(action)
        recs.append(
            m.Recommendation(
                priority=0,
                title=f"{plural(n, 'test')} could not run",
                why=f"Blocked: {reason}. Until they run, this part of the agent is unassessed.",
                action=action or "Resolve the missing prerequisite named above, then re-run the blocked tests.",
                tests=[
                    r.test_id
                    for r in results
                    if r.status == TestStatus.BLOCKED and clean(r.blocked_reason, 160) == reason
                ][:20],
                kind=kind,  # type: ignore[arg-type]
            )
        )
    open_cats = [c for c in sec.categories if c.verdict in UNASSESSED]
    if sec.tested and open_cats:
        recs.append(
            m.Recommendation(
                priority=0,
                title=f"Complete the security assessment ({plural(len(open_cats), 'category', 'categories')} not fully tested)",
                why="A clean result in the categories that ran says nothing about those that did not.",
                action="Unblock or add tests for: " + ", ".join(f"{c.code} {c.name}" for c in open_cats[:8]) + ".",
                kind="coverage",
            )
        )
    if executed and rel.verdict == "not_measured":
        recs.append(
            m.Recommendation(
                priority=0,
                title="Measure reliability",
                why="Every test ran once, so intermittent failures cannot be told from stable ones.",
                action="Re-run with repetitions (`--repetitions 3` or the thorough intensity) before relying on a pass.",
                kind="verification",
            )
        )
    for i, rec in enumerate(recs, 1):
        rec.priority = i
    return recs


# ================================================================================================ the summary
def _executive(
    mat: RunMaterial,
    sc: m.ScorecardView,
    reviewed_sc: m.ScorecardView | None,
    risk: m.RiskSummary,
    sec: m.SecuritySection,
    results: list[TestResult],
    findings: list[m.FindingView],
    recs: list[m.Recommendation],
    env: m.EnvironmentView,
    target_name: str,
) -> m.ExecutiveSummary:
    counts = Counter(r.status for r in results)
    executed = sum(counts[s] for s in RAN)
    passed = counts[TestStatus.PASSED]
    blocked = counts[TestStatus.BLOCKED]
    status = str(mat.run.get("status"))
    points: list[m.KeyPoint] = []
    limits: list[str] = []

    if not executed:
        reason = (
            next(
                iter([*env.unreachable.values(), *env.interface_errors.values(), (mat.analysis.get("error") or "")]),
                "",
            )
            or "every planned test was blocked or skipped"
        )
        headline = f"{target_name}: no verdict"
        verdict = (
            f"No test could be run against the target ({clean(reason, 300)}). AgentLab does not guess: there is no "
            "score, and nothing here should be read as 'the agent works'."
        )
    else:
        grade = sc.grade or "n/a"
        headline = f"{target_name}: {grade}" + (f" ({sc.overall:.0f}/100)" if sc.overall is not None else "")
        n_open = risk.open_findings
        worst = next((s for s in SEVERITY_ORDER if risk.severity_counts.get(s)), None)
        verdict = (
            f"{passed} of {executed} executed tests passed; "
            + (
                f"{plural(n_open, 'finding')} open" + (f", the most serious being {worst}" if worst else "")
                if n_open
                else "no open findings"
            )
            + "."
        )
        if sc.security_cap_applied and sc.cap_reason:
            verdict += f" The score is capped: {sc.cap_reason}."
    if status not in {"completed"}:
        limits.append(f"The run ended with status '{status}': results cover only the tests that ran.")
    if sc.qualifiers:
        limits.extend(sc.qualifiers)
    if blocked:
        limits.append(
            f"{plural(blocked, 'test')} could not run (BLOCKED) and are excluded from every score; "
            "they are gaps in coverage, not failures."
        )
    if env.warnings:
        limits.extend(env.warnings[:3])
    if not sec.tested:
        limits.append("Security was not assessed in this run.")

    strong = [c for c in sc.categories if c.score is not None and c.score >= 90 and c.tests >= 3]
    for c in strong[:3]:
        points.append(m.KeyPoint(kind="strength", text=f"{c.label}: {c.score:.0f}/100 over {plural(c.tests, 'test')}."))
    for f in sorted(findings, key=lambda f: -f.priority)[:5]:
        if f.status in ACTIVE_FINDING:
            points.append(m.KeyPoint(kind="concern", text=f"{(f.effective_severity or f.severity).upper()}: {f.title}"))
    for c in sc.categories:
        if not c.applicable and c.note:
            points.append(m.KeyPoint(kind="gap", text=f"{c.label}: {c.note}"))
            if len([p for p in points if p.kind == "gap"]) >= 3:
                break
    if reviewed_sc is not None:
        points.append(
            m.KeyPoint(
                kind="note",
                text=f"After human review the score is {reviewed_sc.overall if reviewed_sc.overall is not None else 'n/a'} "
                f"(machine score {sc.overall if sc.overall is not None else 'n/a'}); both are shown.",
            )
        )
    return m.ExecutiveSummary(
        headline=headline,
        verdict=verdict,
        overall=sc.overall,
        grade=sc.grade,
        points=points,
        limitations=list(dict.fromkeys(limits)),
        next_steps=[f"{r.title}: {r.action}" for r in recs[:3]],
    )


def _risk(findings: list[m.FindingView], sec: m.SecuritySection, scorecard: m.ScorecardView) -> m.RiskSummary:
    active = [f for f in findings if f.status in ACTIVE_FINDING]
    counts = Counter(f.effective_severity or f.severity for f in active)
    unassessed = [f"{c.code} {c.name}" for c in sec.categories if c.verdict in UNASSESSED]
    unassessed += [f"{c.label} (no applicable test ran)" for c in scorecard.categories if not c.applicable]
    return m.RiskSummary(
        severity_counts={s: counts[s] for s in SEVERITY_ORDER if counts[s]},
        open_findings=len(active),
        security_findings=sum(1 for f in active if f.is_security),
        security_posture=sec.posture,
        security_summary=sec.summary,
        top_risks=[
            f"{(f.effective_severity or f.severity)}: {f.title}" for f in sorted(active, key=lambda f: -f.priority)[:5]
        ],
        unassessed_areas=unassessed[:20],
    )


def _capabilities(prof: AgentProfile | None) -> list[m.CapabilityRow]:
    return [
        m.CapabilityRow(
            capability=c.capability, detected=c.detected, testable=c.testable.value, reason=clean(c.reason, 300)
        )
        for c in (prof.capability_matrix if prof else [])
    ]


def _classification(prof: AgentProfile | None) -> list[m.ClassificationRow]:
    return [
        m.ClassificationRow(
            type=t.type.value,
            confidence=round(t.confidence, 3),
            evidence=[clean(f"{e.source}: {e.detail}", 200) for e in t.evidence[:4]],
        )
        for t in (prof.types if prof else [])
        if t.confidence >= 0.2
    ][:10]


def _versioning(mat: RunMaterial) -> m.Versioning:
    man = mat.manifest
    repo = (mat.profile.repository if mat.profile else {}) or {}
    cfg = man.get("config", {})
    judges = man.get("judges", {})
    return m.Versioning(
        agentlab_version=str(man.get("agentlab_version", __version__)),
        agentlab_commit=_git_commit(),
        python=str(man.get("python", platform.python_version())),
        platform=str(man.get("platform", platform.system().lower())),
        target=str(man.get("target", {}).get("name", "")),
        target_version=man.get("target", {}).get("version"),
        target_commit=repo.get("commit"),
        target_spec_hash=man.get("target", {}).get("spec_hash"),
        providers=list(cfg.get("providers", [])),
        models=sorted({p.get("model") for p in cfg.get("providers", []) if p.get("model")}),
        judges=list(judges.get("judges", [])),
        judge_enabled=bool(judges.get("enabled")),
        judge_independent=judges.get("independent"),
        test_suite={
            "suite": man.get("plan", {}).get("suite"),
            "intensity": man.get("plan", {}).get("intensity"),
            "plan_id": man.get("plan", {}).get("id"),
            "plan_hash": man.get("plan", {}).get("hash"),
            "tests": man.get("plan", {}).get("tests"),
        },
        skills=list(man.get("skills", [])),
        evaluation_profile=dict(man.get("scoring_profile", {})),
        environment_fingerprint=canonical_hash(
            {
                "environment": man.get("environment"),
                "platform": man.get("platform"),
                "python": man.get("python"),
                "config": man.get("config_hash"),
            }
        )[:16],
        config_hash=str(man.get("config_hash", "")),
    )


# ===================================================================================================== build
def build_report(sv: Services | None, mat: RunMaterial, *, regression: dict[str, Any] | None = None) -> m.ReportData:
    """Assemble the report. ``sv`` is only used to read artifact bodies (workspace evidence); it may be ``None``."""
    run = mat.run
    planned = mat.planned()
    reviewed: ReviewedRun = apply_reviews(mat.results, mat.findings, mat.reviews)
    has_reviews = bool(mat.reviews)
    results = sorted(mat.results, key=lambda r: r.test_id)
    eff_by_id = {r.id: r for r in reviewed.results}

    scoring: ScoringProfile | None = None
    sp = mat.analysis.get("scoring_profile")
    if sp:
        try:
            scoring = ScoringProfile.model_validate(sp)
        except Exception:
            scoring = None
    if scoring is None and mat.manifest.get("scoring_profile", {}).get("name"):
        try:
            scoring = load_profile(str(mat.manifest["scoring_profile"]["name"]))
        except Exception:
            scoring = None

    executed = sum(1 for r in results if r.status in RAN)
    machine_sc = _scorecard_view(mat.scorecard, scoring, executed)

    reviewed_sc: m.ScorecardView | None = None
    if has_reviews and scoring is not None and executed:
        tests = [p.test for p in planned.values()]
        eff_findings = [
            f.model_copy(update={"status": "open"}) if f.status in ACTIVE_FINDING else f for f in reviewed.findings
        ]
        try:
            reviewed_sc = _scorecard_view(
                build_scorecard(tests, reviewed.results, eff_findings, scoring), scoring, executed
            )
            reviewed_sc.notes.insert(
                0, "Computed from the results and findings after human review; the machine scorecard is unchanged."
            )
            # a review changes verdicts, not how much of the target was tested: the scope note carries over
            scope = (mat.manifest.get("outcome") or {}).get("scope") or {}
            if scope.get("limited"):
                if scope.get("text") and scope["text"] not in reviewed_sc.qualifiers:
                    reviewed_sc.qualifiers.append(str(scope["text"]))
                if scope.get("label") and reviewed_sc.grade:
                    reviewed_sc.grade = add_grade_note(reviewed_sc.grade, str(scope["label"]))
        except Exception as exc:  # a review can never make the report fail
            log.warning("could not recompute the reviewed scorecard: %s", exc)

    by_review = reviewed.by_subject
    finding_by_test: dict[str, Finding] = {}
    for f in sorted(mat.findings, key=lambda f: (-f.severity.rank, -f.confidence)):
        finding_by_test.setdefault(f.test_id, f)  # the most serious finding of a test represents it
    eff_findings_by_id = {f.id: f for f in reviewed.findings}
    findings = sorted(
        (_finding_view(f, eff_findings_by_id.get(f.id, f), by_review) for f in mat.findings),
        key=lambda f: (-f.priority, f.test_id),
    )

    rows = [_result_row(eff_by_id.get(r.id, r), r, planned, finding_by_test, by_review) for r in results]
    row_by_id = {r.test_id: r for r in rows}
    traces: dict[str, str] = {}
    for t in mat.traces:
        traces[f"{t['test_key']}#{t['attempt']}"] = str(t.get("artifact_id") or "")
    failed: list[m.FailedTest] = []
    for r in results:
        if r.status not in FAILING and r.status not in STOPPED:
            continue
        timeline = _timeline(sv, r, traces)
        failed.append(_failed_test(row_by_id[r.test_id], r, finding_by_test.get(r.test_id), str(run["id"]), timeline))
    failed.sort(key=lambda f: (-sev_rank(f.severity), f.test_id))

    sec = _security(mat, findings)
    risk = _risk(findings, sec, machine_sc)
    rel = _reliability(mat, results, planned)
    env = _environment(mat)
    recs = _recommendations(findings, results, sec, rel, executed)
    target = _target_overview(mat)
    exec_summary = _executive(mat, machine_sc, reviewed_sc, risk, sec, results, findings, recs, env, target.name)
    plan_total = len(planned)
    started = run.get("started_at") or run.get("created_at")
    finished = run.get("finished_at")
    duration = (mat.manifest.get("outcome") or {}).get("elapsed_s")
    incomplete = str(run.get("status")) != "completed"

    report = m.ReportData(
        report_version=mat.report_version,
        title=f"AgentLab report: {target.name}",
        run=m.RunInfo(
            run_id=str(run["id"]),
            status=str(run.get("status")),
            target=target.name,
            suite=str(mat.manifest.get("plan", {}).get("suite", run.get("mode", ""))),
            intensity=str(mat.manifest.get("plan", {}).get("intensity", "")),
            started_at=started,
            finished_at=finished,
            duration_s=duration,
            waves=max(1, len(mat.plans)),
            error=clean((run.get("error") or {}).get("message"), 500) if run.get("error") else None,
            baseline_run_id=(mat.manifest.get("options") or {}).get("baseline_run_id"),
            seed=(mat.manifest.get("options") or {}).get("seed"),
            complete=not incomplete,
            incomplete_reason=f"status {run.get('status')}" if incomplete else None,
        ),
        versioning=_versioning(mat),
        executive=exec_summary,
        scorecard=machine_sc,
        risk=risk,
        target=target,
        architecture=_architecture(mat.profile),
        classification=_classification(mat.profile),
        capabilities=_capabilities(mat.profile),
        environment=env,
        methodology=_methodology(mat, plan_total, scoring),
        inventory=_inventory(mat, results),
        results=rows,
        failed_tests=failed,
        blocked_tests=[r for r in rows if r.status in {"blocked", "skipped"}],
        findings=findings,
        security=sec,
        domains=_domains(mat, results, planned, machine_sc),
        reliability=rel,
        performance=_performance(mat, results, scoring),
        cost=_cost(mat, results),
        trend=_trend(mat, machine_sc, results, len(findings)),
        regression=regression,
        evidence=_evidence(sv, mat),
        recommendations=recs,
        appendix=m.AppendixSection(
            skills=list(mat.manifest.get("skills", [])),
            phases=[
                {k: p.get(k) for k in ("phase", "index", "status", "duration_s", "note")}
                for p in mat.analysis.get("phases", [])
            ],
            config=dict(mat.manifest.get("config", {})),
            warnings=[clean(w, 400) for w in [*mat.analysis.get("warnings", []), *mat.notes]],
            limitations=_limitations(mat, env),
            glossary=GLOSSARY,
            checksums_note="Every file in this bundle is listed with its SHA-256 in checksums.json.",
        ),
        reviews=_overlays(mat.reviews, mat.results, mat.findings),
        reviewed=has_reviews,
    )
    report.reviewed_scorecard = reviewed_sc
    return report


def _limitations(mat: RunMaterial, env: m.EnvironmentView) -> list[str]:
    out = [
        "Tests sample behaviour; they cannot prove the absence of defects.",
        "LLM-based judgments are probabilistic and are reported with confidence and agreement; the deterministic "
        "layer is the baseline.",
        "Security results cover the categories listed in the security section, with benign simulations only.",
    ]
    if not env.judge:
        out.append("No LLM judge was available: criteria that need one were BLOCKED, not assumed to pass.")
    if not env.docker:
        out.append("No sandbox was available: tests that execute code were BLOCKED.")
    if not env.browser:
        out.append("No browser was available: browser tests were BLOCKED.")
    return out


def _timeline(sv: Services | None, r: TestResult, traces: dict[str, str]) -> list[str]:
    """A short sequence of what happened in the last attempt (tool calls, retrievals, hand-offs), from its trace."""
    if sv is None or not r.attempts:
        return []
    last = r.attempts[-1]
    art = traces.get(f"{r.test_id}#{last.attempt}")
    if not art:
        return []
    try:
        data = sv.artifacts.get_json(art)
    except Exception:
        return []
    out: list[str] = []
    for ev in (data.get("events") or [])[:80] if isinstance(data, dict) else []:
        kind = ev.get("type")
        p = ev.get("payload") or {}
        if kind == "ToolCalled":
            out.append(f"tool {p.get('tool')}")
        elif kind == "Handoff":
            out.append(f"hand-off {p.get('from', '?')}→{p.get('to', '?')}")
        elif kind == "Retrieval":
            out.append(f"retrieved {clean(p.get('source'), 40)}")
        elif kind == "BrowserAction":
            out.append(f"browser {p.get('action', '')}")
        elif kind == "SandboxCommand":
            out.append("sandbox command")
    return out[:20]


__all__ = ["build_report", "finding_priority"]
