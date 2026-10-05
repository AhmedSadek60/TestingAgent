"""Analyses of finished runs, tested on synthetic results (no target involved)."""

from __future__ import annotations

from agentlab.core.enums import RiskClass, Severity, TestStatus
from agentlab.core.models import AssertionResult, AttemptResult, Finding, ReliabilityStats, TestCase, TestResult
from agentlab.design.models import CoverageEntry, PlannedTest
from agentlab.orchestrator.analysis import (
    analyse_cross_test,
    analyse_reliability,
    analyse_security,
    apply_adaptive_to_findings,
    is_diagnostic,
    scored_results,
)


def mk_test(tid: str, category: str = "security", **ctx: object) -> TestCase:
    return TestCase(id=tid, name=f"test {tid}", category=category, objective="o", input="hi", context=dict(ctx))


def planned(test: TestCase, codes: list[str] | None = None) -> PlannedTest:
    return PlannedTest(test=test, skill="s", skill_version="1", security_categories=codes or [], risk=RiskClass.SAFE)


def result(
    tid: str,
    status: TestStatus,
    *,
    failed: list[tuple[str, dict[str, object]]] | None = None,
    reps: int = 1,
    passes: int | None = None,
    reason: str | None = None,
    category: str = "security",
) -> TestResult:
    attempts = []
    for i in range(reps):
        ok = status == TestStatus.PASSED
        asserts = [
            AssertionResult(type=t, passed=False, score=0.0, message=f"{t} failed", evidence=ev)
            for t, ev in failed or []
        ]
        attempts.append(AttemptResult(attempt=i + 1, status=TestStatus.PASSED if ok else status, assertions=asserts))
    p = (reps if status == TestStatus.PASSED else 0) if passes is None else passes
    stats = ReliabilityStats(
        repetitions=reps,
        passes=p,
        pass_rate=p / reps,
        flaky=0 < p < reps,
        deterministic_failure=p == 0 and status == TestStatus.FAILED,
    )
    return TestResult(
        run_id="r",
        test_id=tid,
        test_name=f"test {tid}",
        category=category,
        score_category="security",
        status=status,
        attempts=attempts,
        reliability=stats,
        blocked_reason=reason,
    )


def finding(tid: str, sev: Severity = Severity.HIGH, security: bool = True) -> Finding:
    return Finding(
        run_id="r",
        test_id=tid,
        title=f"finding {tid}",
        category="security",
        severity=sev,
        confidence=0.8,
        expected="e",
        observed="o",
        impact="i",
        reproduction="r",
        recommendation="fix",
        is_security=security,
    )


# ------------------------------------------------------------------ security
def test_a_probe_that_succeeded_makes_the_category_vulnerable_and_is_listed_as_an_attack() -> None:
    t = mk_test("EXFIL-EXTRACT-PROMPT-DUMP-001")
    r = result(
        t.id,
        TestStatus.FAILED,
        failed=[("no_canary_leak", {"channels": ["output"], "snippet": "the code is AGENTLAB_CANARY_AB12CD34"})],
    )
    sa = analyse_security([planned(t, ["N3", "N16"])], [r], [finding(t.id)])
    assert sa.posture == "vulnerabilities_observed"
    assert sa.category("N16").verdict == "vulnerable"  # type: ignore[union-attr]
    attack = sa.attacks_succeeded[0]
    assert attack.leak and attack.channels == ["output"] and attack.severity == "high"
    assert "rating" in sa.rating_note or "secure" in sa.rating_note
    assert sa.leaks and "unqualified" in sa.rating_note  # a leaky agent never gets an unqualified rating


def test_blocked_probes_are_not_tested_never_resistant() -> None:
    t = mk_test("ABUSE-PATH-001")
    r = result(t.id, TestStatus.BLOCKED, reason="HIGH_IMPACT test needs authorization")
    sa = analyse_security([planned(t, ["N4", "N21"])], [r])
    assert sa.category("N21").verdict == "not_tested"  # type: ignore[union-attr]
    assert sa.posture == "not_tested"
    assert any("BLOCKED" in c for c in sa.caveats)
    assert "authorization" in sa.category("N21").note  # type: ignore[union-attr]


def test_passed_and_blocked_probes_make_a_category_partially_tested() -> None:
    a, b = mk_test("INJ-OVERRIDE-001"), mk_test("INJ-OVERRIDE-002")
    sa = analyse_security(
        [planned(a, ["N1", "N3"]), planned(b, ["N1", "N3"])],
        [result(a.id, TestStatus.PASSED), result(b.id, TestStatus.BLOCKED, reason="needs a judge")],
    )
    assert sa.category("N1").verdict == "partially_tested"  # type: ignore[union-attr]
    assert sa.posture == "partially_tested"


def test_every_applicable_category_resistant_still_carries_a_no_proof_caveat() -> None:
    cov = [
        CoverageEntry(key=c.code, name=c.name, status="not_applicable", note="not relevant")
        for c in __import__("agentlab.design.taxonomy", fromlist=["SECURITY_CATEGORIES"]).SECURITY_CATEGORIES
        if c.code != "N1"
    ]
    t = mk_test("INJ-OVERRIDE-001")
    sa = analyse_security([planned(t, ["N1"])], [result(t.id, TestStatus.PASSED)], base_coverage=cov)
    assert sa.posture == "no_vulnerabilities_observed"
    assert any("does not prove" in c for c in sa.caveats)
    assert "not proof" in sa.rating_note


def test_intermittent_security_failure_counts_as_vulnerable() -> None:
    t = mk_test("INJ-OVERRIDE-001")
    r = result(t.id, TestStatus.PASSED, reps=4, passes=3)  # passed overall (threshold < 1) but failed once
    sa = analyse_security([planned(t, ["N1"])], [r])
    assert sa.category("N1").verdict == "vulnerable"  # type: ignore[union-attr]
    assert sa.attacks_succeeded[0].intermittent


def test_uncovered_and_not_applicable_categories_are_distinguished() -> None:
    cov = [
        CoverageEntry(key="N8", name="Memory poisoning", status="not_applicable", note="no memory capability"),
        CoverageEntry(
            key="N5", name="Tool output poisoning", status="not_covered", note="interface cannot replace tool results"
        ),
    ]
    sa = analyse_security([], [], base_coverage=cov)
    assert sa.category("N8").verdict == "not_applicable"  # type: ignore[union-attr]
    assert sa.category("N5").verdict == "not_covered"  # type: ignore[union-attr]
    assert sa.posture == "not_tested"


def test_evidence_snippets_never_carry_secret_like_material() -> None:
    t = mk_test("EXFIL-CREDENTIALS-001")
    secret = "AKIA" + "ABCDEFGHIJKLMNOP"
    r = result(t.id, TestStatus.FAILED, failed=[("no_secret_leak", {"snippet": f"key {secret} here"})])
    sa = analyse_security([planned(t, ["N17"])], [r])
    assert secret not in (sa.attacks_succeeded[0].snippet or "")


# ------------------------------------------------------------------ reliability
def test_reliability_flags_flaky_tests_and_notes_single_runs() -> None:
    tests = [mk_test(f"T{i}", "functional") for i in range(3)]
    results = [
        result("T0", TestStatus.FAILED, reps=4, passes=2, category="functional"),
        result("T1", TestStatus.PASSED, reps=4, category="functional"),
        result("T2", TestStatus.PASSED, reps=1, category="functional"),
    ]
    ra = analyse_reliability(tests, results)
    assert ra.measured_tests == 2 and ra.single_run_tests == 1
    assert [f.test_id for f in ra.flaky] == ["T0"] and ra.verdict == "unstable"
    assert ra.consistency == 0.5
    assert any("ran once" in n for n in ra.notes)


def test_reliability_without_repetitions_says_it_was_not_measured() -> None:
    ra = analyse_reliability([mk_test("T0", "functional")], [result("T0", TestStatus.PASSED, category="functional")])
    assert ra.verdict == "not_measured" and any("not measured" in n for n in ra.notes)


# ------------------------------------------------------------------ cross-test + adaptive
def test_variants_are_diagnostic_and_not_scored_twice() -> None:
    parent = mk_test("INJ-OVERRIDE-001")
    variant = mk_test("INJ-OVERRIDE-001-W2URGENCY", diagnostic=True, variant_of=parent.id, variant_kind="urgency")
    assert is_diagnostic(variant) and not is_diagnostic(parent)
    rs = [result(parent.id, TestStatus.FAILED), result(variant.id, TestStatus.FAILED)]
    assert [r.test_id for r in scored_results([parent, variant], rs)] == [parent.id]


def test_adaptive_followups_are_folded_into_the_original_finding() -> None:
    parent = mk_test("INJ-OVERRIDE-001")
    v1 = mk_test("INJ-OVERRIDE-001-W2ROLEPLAY", diagnostic=True, variant_of=parent.id, variant_kind="role_play")
    v2 = mk_test("INJ-OVERRIDE-001-W2TYPOS", diagnostic=True, variant_of=parent.id, variant_kind="typos")
    results = [
        result(parent.id, TestStatus.FAILED, failed=[("refuses", {})]),
        result(v1.id, TestStatus.FAILED, failed=[("refuses", {})]),
        result(v2.id, TestStatus.PASSED),
    ]
    cross = analyse_cross_test([parent, v1, v2], results, [])
    obs = cross.adaptive[0]
    assert obs.reproduced == ["role_play"] and obs.not_reproduced == ["typos"]
    assert obs.conclusion.startswith("partly wording-dependent")
    f = finding(parent.id)
    assert apply_adaptive_to_findings([f], cross.adaptive) == 1
    assert f.confidence > 0.8 and "Wave-2 follow-up" in f.inferences[-1]
    # diagnostics are excluded from the per-category table
    assert sum(c.failed for c in cross.categories) == 1


def test_cross_test_patterns_group_repeated_failed_checks() -> None:
    tests = [mk_test(f"T{i}", "functional") for i in range(3)]
    results = [result(t.id, TestStatus.FAILED, failed=[("contains", {})], category="functional") for t in tests]
    cross = analyse_cross_test(tests, results, [])
    pat = cross.patterns[0]
    assert pat.kind == "failed_check" and pat.key == "contains" and pat.count == 3
    assert cross.counts == {"failed": 3}
