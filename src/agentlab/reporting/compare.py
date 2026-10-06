"""Regression comparison of two runs (spec section 24).

    Run A (baseline)  versus  Run B (current)
    new failures · resolved failures · score changes · latency changes · cost changes · reliability changes

The rule that shapes everything here: **never compare incompatible runs without clearly flagging the differences.**
A comparison therefore starts with a compatibility verdict built from the two run manifests (AgentLab version,
scoring profile, skill versions, judges, configuration, plan), and the test-by-test comparison only treats a test as
"the same" when its definition is identical in both runs. A test that was BLOCKED in one run is a change of coverage,
never a regression, and a result that flips on a test measured as flaky is reported as unstable, not as a regression.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from agentlab.core.enums import TestStatus
from agentlab.core.ids import utcnow
from agentlab.core.models import TestResult
from agentlab.core.models.base import Model
from agentlab.design.models import PlannedTest
from agentlab.orchestrator.manifest import ManifestDifference, compare_manifests
from agentlab.reporting.material import RunMaterial, load_material
from agentlab.reporting.util import clean, label, mean, money, ms, percentile, plural
from agentlab.services import Services
from agentlab.storage.db import canonical_hash

SCHEMA = "agentlab.comparison"
SCHEMA_VERSION = 1

PASS = {TestStatus.PASSED}
FAIL = {TestStatus.FAILED, TestStatus.TIMEOUT}
POSTURE_ORDER = {
    "vulnerabilities_observed": 0,
    "not_tested": 1,
    "partially_tested": 2,
    "no_vulnerabilities_observed": 3,
}

DeltaKind = Literal[
    "new_failure",
    "resolved",
    "still_failing",
    "unstable",
    "lost_coverage",
    "gained_coverage",
    "new_test_failed",
    "new_test_passed",
    "new_test_not_run",
    "removed",
    "definition_changed",
]
Verdict = Literal["regressed", "improved", "mixed", "unchanged", "inconclusive"]
Compat = Literal["comparable", "comparable_with_caveats", "not_comparable"]

KIND_TITLES: dict[str, str] = {
    "new_failure": "New failures (passed in A, fail in B)",
    "resolved": "Resolved failures (failed in A, pass in B)",
    "still_failing": "Still failing in both",
    "unstable": "Changed, but the test is flaky (not counted as a regression or a fix)",
    "lost_coverage": "Lost coverage (ran in A, could not run in B)",
    "gained_coverage": "Gained coverage (could not run in A, ran in B)",
    "new_test_failed": "New tests that fail",
    "new_test_passed": "New tests that pass",
    "new_test_not_run": "New tests that could not run",
    "removed": "Tests only in A",
    "definition_changed": "Same id, different test (not compared)",
}


class RunRef(Model):
    run_id: str
    target: str
    target_version: str | None = None
    target_commit: str | None = None
    status: str
    started_at: datetime | str | None = None
    overall: float | None = None
    grade: str | None = None
    tests: int = 0
    executed: int = 0
    blocked: int = 0
    plan_hash: str | None = None
    scoring_profile: str | None = None
    agentlab_version: str | None = None
    models: list[str] = Field(default_factory=list)
    judges: list[str] = Field(default_factory=list)
    skills: int = 0


class Compatibility(Model):
    verdict: Compat
    summary: str
    differences: list[ManifestDifference] = Field(default_factory=list)
    shared_tests: int = 0
    only_a: int = 0
    only_b: int = 0
    changed_definitions: int = 0
    notes: list[str] = Field(default_factory=list)


class TestDelta(Model):
    __test__ = False

    test_id: str
    name: str
    category: str
    kind: DeltaKind
    status_a: str | None = None
    status_b: str | None = None
    severity_a: str | None = None
    severity_b: str | None = None
    score_a: float | None = None
    score_b: float | None = None
    latency_a: float | None = None
    latency_b: float | None = None
    note: str = ""


class LatencyChange(Model):
    test_id: str
    name: str
    category: str
    latency_a: float
    latency_b: float
    direction: Literal["slower", "faster"]


class CategoryDelta(Model):
    category: str
    label: str
    score_a: float | None = None
    score_b: float | None = None
    delta: float | None = None
    like_for_like_a: float | None = None  # pass rate over tests that exist unchanged in both runs and ran in both
    like_for_like_b: float | None = None
    shared_ran: int = 0
    note: str = ""


class MetricDelta(Model):
    name: str
    a: float | None = None
    b: float | None = None
    delta: float | None = None
    change_pct: float | None = None
    unit: str = ""
    note: str = ""


class FindingDelta(Model):
    kind: Literal["new", "resolved", "worse", "better", "unchanged"]
    test_id: str
    title: str
    severity_a: str | None = None
    severity_b: str | None = None
    is_security: bool = False


class Comparison(Model):
    schema_id: str = Field(default=SCHEMA, alias="schema")
    schema_version: int = SCHEMA_VERSION
    generated_at: datetime = Field(default_factory=utcnow)
    run_a: RunRef
    run_b: RunRef
    compatibility: Compatibility
    verdict: Verdict
    summary: str
    counts: dict[str, int] = Field(default_factory=dict)
    tests: list[TestDelta] = Field(default_factory=list)
    categories: list[CategoryDelta] = Field(default_factory=list)
    score: dict[str, Any] = Field(default_factory=dict)
    latency: list[MetricDelta] = Field(default_factory=list)
    latency_changes: list[LatencyChange] = Field(default_factory=list)
    cost: list[MetricDelta] = Field(default_factory=list)
    reliability: dict[str, Any] = Field(default_factory=dict)
    security: dict[str, Any] = Field(default_factory=dict)
    findings: list[FindingDelta] = Field(default_factory=list)

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


# ===================================================================================================== helpers
def _ref(mat: RunMaterial) -> RunRef:
    man = mat.manifest
    sc = mat.scorecard
    results = mat.results
    repo = (mat.profile.repository if mat.profile else {}) or {}
    return RunRef(
        run_id=mat.run_id,
        target=str(man.get("target", {}).get("name") or (mat.target.name if mat.target else "")),
        target_version=man.get("target", {}).get("version"),
        target_commit=repo.get("commit"),
        status=str(mat.run.get("status")),
        started_at=mat.run.get("started_at") or mat.run.get("created_at"),
        overall=sc.overall if sc else None,
        grade=sc.grade if sc else None,
        tests=len(results),
        executed=sum(1 for r in results if r.status in PASS | FAIL),
        blocked=sum(1 for r in results if r.status == TestStatus.BLOCKED),
        plan_hash=man.get("plan", {}).get("hash"),
        scoring_profile=(man.get("scoring_profile") or {}).get("name"),
        agentlab_version=man.get("agentlab_version"),
        models=sorted({p.get("model") for p in man.get("config", {}).get("providers", []) if p.get("model")}),
        judges=[f"{j.get('provider')}:{j.get('model')}" for j in man.get("judges", {}).get("judges", [])],
        skills=len(man.get("skills", [])),
    )


def _definition_hash(p: PlannedTest | None) -> str | None:
    if p is None:
        return None
    return canonical_hash(p.test.model_dump(mode="json", exclude={"status", "rationale", "tags"}))[:16]


def _flaky(r: TestResult) -> bool:
    return bool(r.reliability and (r.reliability.flaky or 0 < r.reliability.pass_rate < 1))


def _latency(r: TestResult) -> float | None:
    values = [a.latency_ms for a in r.attempts if a.latency_ms > 0]
    return max(values) if values else (r.latency_ms or None)


def _group(status: TestStatus) -> str:
    return "pass" if status in PASS else "fail" if status in FAIL else "not_run"


def _delta(
    mat_a: RunMaterial, mat_b: RunMaterial
) -> tuple[list[TestDelta], dict[str, list[tuple[TestResult, TestResult]]]]:
    pa, pb = mat_a.planned(), mat_b.planned()
    ra = {r.test_id: r for r in mat_a.results}
    rb = {r.test_id: r for r in mat_b.results}
    out: list[TestDelta] = []
    shared_ran: dict[str, list[tuple[TestResult, TestResult]]] = defaultdict(list)

    def base(tid: str, kind: DeltaKind, a: TestResult | None, b: TestResult | None, note: str = "") -> TestDelta:
        ref = b or a
        assert ref is not None
        return TestDelta(
            test_id=tid,
            name=clean(ref.test_name, 160),
            category=ref.category,
            kind=kind,
            status_a=a.status.value if a else None,
            status_b=b.status.value if b else None,
            severity_a=a.severity.value if a and a.severity else None,
            severity_b=b.severity.value if b and b.severity else None,
            score_a=round(a.score, 3) if a else None,
            score_b=round(b.score, 3) if b else None,
            latency_a=_latency(a) if a else None,
            latency_b=_latency(b) if b else None,
            note=note,
        )

    for tid in sorted(set(ra) | set(rb)):
        a, b = ra.get(tid), rb.get(tid)
        if a is None and b is not None:
            g = _group(b.status)
            kind: DeltaKind = (
                "new_test_failed" if g == "fail" else "new_test_passed" if g == "pass" else "new_test_not_run"
            )
            out.append(base(tid, kind, None, b, b.blocked_reason or ""))
            continue
        if b is None and a is not None:
            out.append(base(tid, "removed", a, None))
            continue
        assert a is not None and b is not None
        ha, hb = _definition_hash(pa.get(tid)), _definition_hash(pb.get(tid))
        if ha and hb and ha != hb:
            out.append(
                base(
                    tid,
                    "definition_changed",
                    a,
                    b,
                    "the test's inputs or checks differ between the runs (for example a skill changed)",
                )
            )
            continue
        ga, gb = _group(a.status), _group(b.status)
        if ga == "not_run" and gb == "not_run":
            continue
        if ga == "pass" and gb == "not_run" or ga == "fail" and gb == "not_run":
            out.append(base(tid, "lost_coverage", a, b, clean(b.blocked_reason, 200) or f"{b.status.value} in B"))
            continue
        if ga == "not_run":
            out.append(base(tid, "gained_coverage", a, b, f"{b.status.value} in B"))
            continue
        shared_ran[a.score_category].append((a, b))
        if ga == gb:
            if ga == "fail":
                out.append(base(tid, "still_failing", a, b))
            continue
        if _flaky(a) or _flaky(b):
            out.append(base(tid, "unstable", a, b, "measured as flaky in at least one run"))
        elif ga == "pass":
            out.append(base(tid, "new_failure", a, b))
        else:
            out.append(base(tid, "resolved", a, b))
    return out, shared_ran


def _compat(mat_a: RunMaterial, mat_b: RunMaterial, deltas: list[TestDelta]) -> Compatibility:
    diffs = compare_manifests(mat_a.manifest, mat_b.manifest)
    ids_a = {r.test_id for r in mat_a.results}
    ids_b = {r.test_id for r in mat_b.results}
    changed = sum(1 for d in deltas if d.kind == "definition_changed")
    shared = len(ids_a & ids_b) - changed
    notes: list[str] = []
    if not mat_a.manifest or not mat_b.manifest:
        notes.append("A run manifest is missing, so compatibility could only be judged from the results.")
    if shared < 5:
        notes.append(
            f"Only {plural(max(shared, 0), 'test')} exist unchanged in both runs: too few for a meaningful comparison."
        )
    if changed:
        notes.append(f"{plural(changed, 'test')} share an id but differ in content and were not compared.")
    only_a, only_b = len(ids_a - ids_b), len(ids_b - ids_a)
    if only_a or only_b:
        notes.append(
            f"{only_a} test(s) exist only in A and {only_b} only in B: the two plans were designed separately. "
            f"To compare like with like, replay A's tests against B's target with `agentlab test --baseline "
            f"{mat_a.run_id[:8]}`."
        )
    blocking = [d for d in diffs if d.impact == "blocks_comparison"]
    caveats = [d for d in diffs if d.impact == "caveat"]
    if mat_a.run_id == mat_b.run_id:
        verdict: Compat = "not_comparable"
        summary = "Both arguments are the same run."
    elif blocking:
        verdict = "not_comparable"
        summary = "The runs are NOT comparable as scores: " + "; ".join(f"{d.field} ({d.note})" for d in blocking) + "."
    elif caveats or notes:
        verdict = "comparable_with_caveats"
        summary = (
            f"Comparable with {plural(len(caveats) + len(notes), 'caveat')}: read the differences below before "
            "drawing conclusions."
        )
    else:
        verdict = "comparable"
        summary = "The runs were produced with the same plan, skills, scoring profile and judges."
    if shared < 5 and verdict == "comparable":
        verdict = "comparable_with_caveats"
    return Compatibility(
        verdict=verdict,
        summary=summary,
        differences=diffs,
        shared_tests=max(shared, 0),
        only_a=only_a,
        only_b=only_b,
        changed_definitions=changed,
        notes=notes,
    )


def _pass_rate(pairs: list[tuple[TestResult, TestResult]], side: int) -> float | None:
    if not pairs:
        return None
    return round(100 * sum(1 for p in pairs if p[side].status in PASS) / len(pairs), 1)


def _categories(
    mat_a: RunMaterial, mat_b: RunMaterial, shared: dict[str, list[tuple[TestResult, TestResult]]]
) -> list[CategoryDelta]:
    ca = {c.category: c for c in (mat_a.scorecard.categories if mat_a.scorecard else [])}
    cb = {c.category: c for c in (mat_b.scorecard.categories if mat_b.scorecard else [])}
    out: list[CategoryDelta] = []
    for cat in sorted(set(ca) | set(cb) | set(shared)):
        a, b = ca.get(cat), cb.get(cat)
        sa = a.score if a and a.applicable else None
        sb = b.score if b and b.applicable else None
        pairs = shared.get(cat, [])
        note = ""
        if sa is None and sb is not None:
            note = "not scored in A"
        elif sb is None and sa is not None:
            note = "not scored in B"
        out.append(
            CategoryDelta(
                category=cat,
                label=label(cat),
                score_a=sa,
                score_b=sb,
                delta=round(sb - sa, 1) if sa is not None and sb is not None else None,
                like_for_like_a=_pass_rate(pairs, 0),
                like_for_like_b=_pass_rate(pairs, 1),
                shared_ran=len(pairs),
                note=note,
            )
        )
    return out


def _metric(name: str, a: float | None, b: float | None, unit: str, note: str = "") -> MetricDelta:
    delta = round(b - a, 4) if a is not None and b is not None else None
    pct = round(100 * (b - a) / a, 1) if a not in (None, 0) and b is not None else None
    return MetricDelta(name=name, a=a, b=b, delta=delta, change_pct=pct, unit=unit, note=note)


def _latencies(mat: RunMaterial) -> list[float]:
    return [v for r in mat.results if r.status in PASS | FAIL and (v := _latency(r))]


def _perf(
    mat_a: RunMaterial, mat_b: RunMaterial, shared: dict[str, list[tuple[TestResult, TestResult]]]
) -> tuple[list[MetricDelta], list[LatencyChange]]:
    la, lb = _latencies(mat_a), _latencies(mat_b)
    both = [(_latency(a), _latency(b)) for pairs in shared.values() for a, b in pairs]
    sa = [x for x, y in both if x and y]
    sb = [y for x, y in both if x and y]
    metrics = [
        _metric("latency p50 (all executed tests)", percentile(la, 50), percentile(lb, 50), "ms"),
        _metric("latency p95 (all executed tests)", percentile(la, 95), percentile(lb, 95), "ms"),
        _metric("latency mean (all executed tests)", mean(la), mean(lb), "ms"),
        _metric("latency p50 (tests that ran in both)", percentile(sa, 50), percentile(sb, 50), "ms", "like for like"),
        _metric("latency p95 (tests that ran in both)", percentile(sa, 95), percentile(sb, 95), "ms", "like for like"),
    ]
    changes: list[LatencyChange] = []
    for pairs in shared.values():
        for a, b in pairs:
            x, y = _latency(a), _latency(b)
            if x and y and abs(y - x) >= 100 and (y >= 1.5 * x or y <= x / 1.5):
                changes.append(
                    LatencyChange(
                        test_id=b.test_id,
                        name=clean(b.test_name, 120),
                        category=b.category,
                        latency_a=round(x, 1),
                        latency_b=round(y, 1),
                        direction="slower" if y > x else "faster",
                    )
                )
    changes.sort(key=lambda d: -abs(d.latency_b - d.latency_a))
    return metrics, changes[:12]


def _cost(mat_a: RunMaterial, mat_b: RunMaterial) -> list[MetricDelta]:
    def totals(mat: RunMaterial) -> tuple[float, float, float]:
        lim = mat.analysis.get("limits") or {}
        cost = float(lim.get("cost_usd", sum(r.cost_usd for r in mat.results)) or 0.0)
        tokens = float(lim.get("tokens", sum(r.tokens for r in mat.results)) or 0.0)
        judge = float((lim.get("cost_by_category") or {}).get("judge", 0.0) or 0.0)
        return cost, tokens, judge

    (ca, ta, ja), (cb, tb, jb) = totals(mat_a), totals(mat_b)
    na = max(sum(1 for r in mat_a.results if r.status in PASS | FAIL), 1)
    nb = max(sum(1 for r in mat_b.results if r.status in PASS | FAIL), 1)
    unknown = "not reported by the target" if not ca and not cb else ""
    return [
        _metric("total cost", ca, cb, "usd", unknown),
        _metric("judge cost", ja, jb, "usd"),
        _metric("total tokens", ta, tb, "tokens"),
        _metric(
            "tokens per executed test", ta / na, tb / nb, "tokens", "normalises for runs with different test counts"
        ),
    ]


def _reliability(mat_a: RunMaterial, mat_b: RunMaterial) -> dict[str, Any]:
    ra, rb = mat_a.analysis.get("reliability") or {}, mat_b.analysis.get("reliability") or {}
    fa = {f["test_id"] for f in ra.get("flaky", [])}
    fb = {f["test_id"] for f in rb.get("flaky", [])}
    return {
        "verdict_a": ra.get("verdict", "not_measured"),
        "verdict_b": rb.get("verdict", "not_measured"),
        "consistency_a": ra.get("consistency"),
        "consistency_b": rb.get("consistency"),
        "measured_a": ra.get("measured_tests", 0),
        "measured_b": rb.get("measured_tests", 0),
        "flaky_a": len(fa),
        "flaky_b": len(fb),
        "newly_flaky": sorted(fb - fa),
        "no_longer_flaky": sorted(fa - fb),
        "note": "Reliability is only comparable when both runs repeated tests."
        if not (ra.get("measured_tests") and rb.get("measured_tests"))
        else "",
    }


def _security(mat_a: RunMaterial, mat_b: RunMaterial) -> dict[str, Any]:
    sa, sb = mat_a.analysis.get("security") or {}, mat_b.analysis.get("security") or {}
    pa, pb = sa.get("posture", "not_tested"), sb.get("posture", "not_tested")
    aa = {x["test_id"] for x in sa.get("attacks_succeeded", [])}
    ab = {x["test_id"] for x in sb.get("attacks_succeeded", [])}
    cats_a = {c["code"]: c["verdict"] for c in sa.get("categories", [])}
    cats_b = {c["code"]: c["verdict"] for c in sb.get("categories", [])}
    changed = {
        k: [cats_a.get(k), cats_b.get(k)] for k in sorted(set(cats_a) | set(cats_b)) if cats_a.get(k) != cats_b.get(k)
    }
    direction = "same"
    if pa != pb:
        direction = "worse" if POSTURE_ORDER.get(pb, 1) < POSTURE_ORDER.get(pa, 1) else "better"
        if pb == "not_tested":
            direction = "no longer tested"
    return {
        "posture_a": pa,
        "posture_b": pb,
        "direction": direction,
        "new_attacks_succeeded": sorted(ab - aa),
        "attacks_no_longer_succeeding": sorted(aa - ab),
        "category_verdict_changes": changed,
    }


def _findings(mat_a: RunMaterial, mat_b: RunMaterial) -> list[FindingDelta]:
    def key(f: Any) -> tuple[str, str]:
        return f.test_id, f.title

    fa = {key(f): f for f in mat_a.findings if f.status in {"open", "confirmed"}}
    fb = {key(f): f for f in mat_b.findings if f.status in {"open", "confirmed"}}
    out: list[FindingDelta] = []
    for k in sorted(set(fa) | set(fb)):
        a, b = fa.get(k), fb.get(k)
        ref = b or a
        assert ref is not None
        if a is None:
            kind = "new"
        elif b is None:
            kind = "resolved"
        elif b.severity.rank > a.severity.rank:
            kind = "worse"
        elif b.severity.rank < a.severity.rank:
            kind = "better"
        else:
            kind = "unchanged"
        out.append(
            FindingDelta(
                kind=kind,  # type: ignore[arg-type]
                test_id=k[0],
                title=clean(ref.title, 200),
                severity_a=a.severity.value if a else None,
                severity_b=b.severity.value if b else None,
                is_security=ref.is_security,
            )
        )
    return out


def _verdict(
    comp: Compatibility,
    counts: Counter[str],
    score: dict[str, Any],
    sec: dict[str, Any],
    findings: list[FindingDelta],
    *,
    same_run: bool = False,
) -> tuple[Verdict, str]:
    regressions = counts["new_failure"] + sum(
        1 for f in findings if f.kind in {"new", "worse"} and f.severity_b in {"high", "critical"}
    )
    regressions += int(sec.get("direction") == "worse")
    improvements = counts["resolved"] + sum(1 for f in findings if f.kind == "resolved")
    # Different scoring profiles make the OVERALL scores incomparable; whether a test passed does not depend on the
    # weights, so the test-level outcome still stands. Anything else that blocks a comparison blocks the verdict too.
    blockers = {d.field for d in comp.differences if d.impact == "blocks_comparison"} - {"scoring_profile"}
    if same_run or blockers or comp.shared_tests == 0:
        return "inconclusive", "The runs cannot be compared reliably; see the compatibility section."
    parts = []
    if counts["new_failure"]:
        parts.append(f"{plural(counts['new_failure'], 'new failure')}")
    if counts["resolved"]:
        parts.append(f"{plural(counts['resolved'], 'resolved failure')}")
    if counts["lost_coverage"]:
        parts.append(f"{plural(counts['lost_coverage'], 'test')} no longer ran")
    if counts["unstable"]:
        parts.append(f"{plural(counts['unstable'], 'flaky change')}")
    delta = score.get("overall_delta")
    if delta is not None and score.get("overall_comparable"):
        parts.append(f"score {'+' if delta >= 0 else ''}{delta:.1f}")
    text = ", ".join(parts) or "no differences in outcome"
    if comp.verdict == "not_comparable":
        text += " (the overall scores are not comparable: the runs were scored with different profiles)"
    if regressions and improvements:
        return "mixed", f"Mixed: {text}."
    if regressions:
        return "regressed", f"Regression: {text}."
    if improvements:
        return "improved", f"Improvement: {text}."
    return "unchanged", f"Unchanged: {text}."


# ===================================================================================================== public
def compare_material(mat_a: RunMaterial, mat_b: RunMaterial) -> Comparison:
    deltas, shared = _delta(mat_a, mat_b)
    comp = _compat(mat_a, mat_b, deltas)
    counts: Counter[str] = Counter(d.kind for d in deltas)
    ra, rb = _ref(mat_a), _ref(mat_b)
    profile_same = (mat_a.manifest.get("scoring_profile") or {}) == (mat_b.manifest.get("scoring_profile") or {})
    score: dict[str, Any] = {
        "overall_a": ra.overall,
        "overall_b": rb.overall,
        "overall_delta": round(rb.overall - ra.overall, 1)
        if ra.overall is not None and rb.overall is not None
        else None,
        "overall_comparable": profile_same,
        "grade_a": ra.grade,
        "grade_b": rb.grade,
        "note": ""
        if profile_same
        else "The runs were scored with different profiles: the overall scores are NOT comparable. Category results are.",
    }
    perf, perf_changes = _perf(mat_a, mat_b, shared)
    security = _security(mat_a, mat_b)
    findings = _findings(mat_a, mat_b)
    verdict, summary = _verdict(comp, counts, score, security, findings, same_run=mat_a.run_id == mat_b.run_id)
    order = list(KIND_TITLES)
    shown = sorted(deltas, key=lambda d: (order.index(d.kind), d.test_id))
    return Comparison(
        run_a=ra,
        run_b=rb,
        compatibility=comp,
        verdict=verdict,
        summary=summary,
        counts={k: counts[k] for k in KIND_TITLES if counts[k]},
        tests=shown,
        categories=_categories(mat_a, mat_b, shared),
        score=score,
        latency=perf,
        latency_changes=perf_changes,
        cost=_cost(mat_a, mat_b),
        reliability=_reliability(mat_a, mat_b),
        security=security,
        findings=findings,
    )


def compare_runs(sv: Services, run_a: str, run_b: str) -> Comparison:
    """Compare ``run_a`` (the baseline) with ``run_b`` (the current run). Runs of different targets are allowed but
    flagged; the caller decides what to do with a ``not_comparable`` verdict."""
    return compare_material(load_material(sv, run_a), load_material(sv, run_b))


# ================================================================================================== rendering
def format_metric(v: float | None, unit: str) -> str:
    if v is None:
        return "n/a"
    if unit == "ms":
        return ms(v)
    if unit == "usd":
        return money(v)
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:g}"


def comparison_markdown(data: dict[str, Any] | Comparison, *, title: bool = False) -> str:
    c = data if isinstance(data, Comparison) else Comparison.model_validate(data)
    from agentlab.reporting.render_md import bullets, neutralise_markup, table

    out: list[str] = []
    w = out.append
    if title:
        w(f"# Regression comparison: {c.run_a.run_id[:8]} → {c.run_b.run_id[:8]}\n")
    comp = c.compatibility
    badge = {
        "comparable": "✅ comparable",
        "comparable_with_caveats": "⚠ comparable with caveats",
        "not_comparable": "⛔ NOT comparable",
    }[comp.verdict]
    w(f"**{c.summary}**\n")
    w(f"> **Compatibility:** {badge}. {comp.summary}\n")
    w(
        table(
            ["", "Run A (baseline)", "Run B (current)"],
            [
                ("Run", c.run_a.run_id, c.run_b.run_id),
                (
                    "Target",
                    f"{c.run_a.target} {c.run_a.target_version or ''}".strip(),
                    f"{c.run_b.target} {c.run_b.target_version or ''}".strip(),
                ),
                ("Target commit", (c.run_a.target_commit or "-")[:12], (c.run_b.target_commit or "-")[:12]),
                ("Started", str(c.run_a.started_at or "-")[:19], str(c.run_b.started_at or "-")[:19]),
                ("Status", c.run_a.status, c.run_b.status),
                (
                    "Score / grade",
                    f"{format_metric(c.run_a.overall, '')} / {c.run_a.grade or '-'}",
                    f"{format_metric(c.run_b.overall, '')} / {c.run_b.grade or '-'}",
                ),
                (
                    "Tests executed / blocked",
                    f"{c.run_a.executed} / {c.run_a.blocked}",
                    f"{c.run_b.executed} / {c.run_b.blocked}",
                ),
                ("Models", ", ".join(c.run_a.models) or "-", ", ".join(c.run_b.models) or "-"),
                ("Judges", ", ".join(c.run_a.judges) or "none", ", ".join(c.run_b.judges) or "none"),
                (
                    "Plan / scoring profile",
                    f"{(c.run_a.plan_hash or '-')[:10]} / {c.run_a.scoring_profile}",
                    f"{(c.run_b.plan_hash or '-')[:10]} / {c.run_b.scoring_profile}",
                ),
            ],
        )
    )
    if comp.differences or comp.notes:
        w("\n**Differences between the runs**\n")
        w(
            table(
                ["Impact", "What differs", "A", "B", "Why it matters"],
                [(d.impact.replace("_", " "), d.field, d.base, d.current, d.note) for d in comp.differences],
            )
        )
        w("\n" + bullets(comp.notes))
    w("\n### Score changes\n")
    sc = c.score
    w(
        f"Overall: {format_metric(sc.get('overall_a'), '')} → {format_metric(sc.get('overall_b'), '')}"
        f"{'' if sc.get('overall_delta') is None else f' ({sc["overall_delta"]:+.1f})'}"
        f" · grade {sc.get('grade_a') or '-'} → {sc.get('grade_b') or '-'}\n"
    )
    if sc.get("note"):
        w(f"\n> {sc['note']}\n")
    w(
        "\n"
        + table(
            ["Category", "A", "B", "Δ", "Same tests, A pass rate", "Same tests, B pass rate", "Note"],
            (
                (
                    x.label,
                    format_metric(x.score_a, ""),
                    format_metric(x.score_b, ""),
                    "" if x.delta is None else f"{x.delta:+.1f}",
                    "" if x.like_for_like_a is None else f"{x.like_for_like_a:.0f}% ({x.shared_ran})",
                    "" if x.like_for_like_b is None else f"{x.like_for_like_b:.0f}%",
                    x.note,
                )
                for x in c.categories
            ),
        )
    )
    w("\n### Test changes\n")
    by_kind: dict[str, list[TestDelta]] = defaultdict(list)
    for d in c.tests:
        by_kind[d.kind].append(d)
    for kind, title_ in KIND_TITLES.items():
        items = by_kind.get(kind, [])
        if not items:
            continue
        w(f"\n**{title_}: {len(items)}**\n")
        w(
            table(
                ["Test", "Name", "A", "B", "Severity", "Note"],
                (
                    (
                        d.test_id,
                        d.name,
                        d.status_a or "-",
                        d.status_b or "-",
                        d.severity_b or d.severity_a or "",
                        d.note,
                    )
                    for d in items[:60]
                ),
            )
        )
        if len(items) > 60:
            w(f"\n_{len(items) - 60} more in the JSON._\n")
    if not c.tests:
        w("_No test changed outcome._\n")
    w("\n### Latency changes\n")
    w(
        table(
            ["Metric", "A", "B", "Δ", "Change", "Note"],
            (
                (
                    m_.name,
                    format_metric(m_.a, m_.unit),
                    format_metric(m_.b, m_.unit),
                    format_metric(m_.delta, m_.unit),
                    "" if m_.change_pct is None else f"{m_.change_pct:+.0f}%",
                    m_.note,
                )
                for m_ in c.latency
            ),
        )
    )
    if c.latency_changes:
        w(
            "\n"
            + table(
                ["Test", "Name", "A", "B", "Direction"],
                ((d.test_id, d.name, ms(d.latency_a), ms(d.latency_b), d.direction) for d in c.latency_changes),
            )
        )
    w("\n### Cost changes\n")
    w(
        table(
            ["Metric", "A", "B", "Δ", "Change", "Note"],
            (
                (
                    m_.name,
                    format_metric(m_.a, m_.unit),
                    format_metric(m_.b, m_.unit),
                    format_metric(m_.delta, m_.unit),
                    "" if m_.change_pct is None else f"{m_.change_pct:+.0f}%",
                    m_.note,
                )
                for m_ in c.cost
            ),
        )
    )
    r = c.reliability
    w("\n### Reliability changes\n")
    w(
        f"Verdict: {r.get('verdict_a')} → {r.get('verdict_b')} · flaky tests {r.get('flaky_a')} → {r.get('flaky_b')}"
        f" · consistency {format_metric(r.get('consistency_a'), '')} → {format_metric(r.get('consistency_b'), '')}\n"
    )
    if r.get("newly_flaky"):
        w(f"\nNewly flaky: {', '.join(r['newly_flaky'])}\n")
    if r.get("no_longer_flaky"):
        w(f"\nNo longer flaky: {', '.join(r['no_longer_flaky'])}\n")
    if r.get("note"):
        w(f"\n_{r['note']}_\n")
    s = c.security
    w("\n### Security changes\n")
    w(f"Posture: {s.get('posture_a')} → {s.get('posture_b')} ({s.get('direction')})\n")
    if s.get("new_attacks_succeeded"):
        w(f"\nNew attacks that succeeded: {', '.join(s['new_attacks_succeeded'])}\n")
    if s.get("attacks_no_longer_succeeding"):
        w(f"\nAttacks that no longer succeed: {', '.join(s['attacks_no_longer_succeeding'])}\n")
    w("\n### Findings\n")
    changed = [f for f in c.findings if f.kind != "unchanged"]
    w(
        table(
            ["Change", "Test", "Finding", "A", "B"],
            ((f.kind, f.test_id, f.title, f.severity_a or "-", f.severity_b or "-") for f in changed),
        )
        if changed
        else "_No finding was added, resolved or re-rated._\n"
    )
    return neutralise_markup("\n".join(out).rstrip() + "\n")
