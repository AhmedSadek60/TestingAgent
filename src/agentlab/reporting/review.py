"""Human review (spec section 59).

A reviewer can confirm or reject a finding, mark a false positive or a false negative, override a score, change a
severity and comment. Each review is stored as a *new row* next to the original evaluation, which is never touched:

    original result, reviewer result, reason, timestamp, reviewer

Reports show both. The machine scorecard is kept as it was; when reviews exist a second, clearly labelled scorecard
is computed from the reviewed values so that nobody mistakes an opinion for a measurement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentlab.core.enums import ReviewDecision, Severity, TestStatus
from agentlab.core.errors import UserError
from agentlab.core.models import Finding, TestResult
from agentlab.services import Services

REQUIRE_REASON = {
    ReviewDecision.FALSE_POSITIVE,
    ReviewDecision.FALSE_NEGATIVE,
    ReviewDecision.OVERRIDE_SCORE,
    ReviewDecision.CHANGE_SEVERITY,
}
ACTIVE_FINDING = {"open", "confirmed"}


class ReviewError(UserError):
    """The review request is not valid (unknown subject, missing reason, out-of-range value)."""


def _decision(value: str | ReviewDecision) -> ReviewDecision:
    try:
        return ReviewDecision(value)
    except ValueError as exc:
        raise ReviewError(
            f"unknown review decision '{value}'; use one of: {', '.join(d.value for d in ReviewDecision)}"
        ) from exc


def _check(reviewer: str, decision: ReviewDecision, reason: str) -> None:
    if not reviewer.strip():
        raise ReviewError("a review needs the name of the reviewer")
    if decision in REQUIRE_REASON and not reason.strip():
        raise ReviewError(f"'{decision.value}' needs a reason, so the next reader can follow it")


def _severity(value: str | Severity | None) -> Severity | None:
    if value is None or value == "":
        return None
    try:
        return Severity(value)
    except ValueError as exc:
        raise ReviewError(f"unknown severity '{value}'; use one of: {', '.join(s.value for s in Severity)}") from exc


def review_result(
    sv: Services,
    run_id: str,
    test_id: str,
    *,
    decision: str | ReviewDecision,
    reviewer: str,
    reason: str = "",
    comment: str = "",
    score: float | None = None,
    severity: str | Severity | None = None,
) -> dict[str, Any]:
    """Review one test result (looked up by test id or result id)."""
    d = _decision(decision)
    _check(reviewer, d, reason)
    sv.store.get_run(run_id)
    found = next((r for r in sv.store.list_results(run_id) if test_id in {r.test_id, r.id}), None)
    if found is None:
        raise ReviewError(f"run '{run_id}' has no result for test '{test_id}'")
    original = {
        "status": found.status.value,
        "score": found.score,
        "severity": found.severity.value if found.severity else None,
    }
    reviewed: dict[str, Any] = {}
    sev = _severity(severity)
    if d == ReviewDecision.OVERRIDE_SCORE:
        if score is None or not 0.0 <= float(score) <= 1.0:
            raise ReviewError("'override_score' needs --score between 0 and 1")
        reviewed["score"] = round(float(score), 4)
    elif d == ReviewDecision.CHANGE_SEVERITY:
        if sev is None:
            raise ReviewError("'change_severity' needs a severity")
        reviewed["severity"] = sev.value
    elif d == ReviewDecision.FALSE_POSITIVE:
        if found.status not in {TestStatus.FAILED, TestStatus.TIMEOUT, TestStatus.ERROR}:
            raise ReviewError(
                f"'false_positive' applies to a failed test; '{test_id}' is {found.status.value}. "
                "Use 'false_negative' if a passing test should have failed."
            )
        reviewed = {"status": TestStatus.PASSED.value, "score": 1.0}
    elif d == ReviewDecision.FALSE_NEGATIVE:
        if found.status != TestStatus.PASSED:
            raise ReviewError(f"'false_negative' applies to a passed test; '{test_id}' is {found.status.value}")
        reviewed = {"status": TestStatus.FAILED.value, "score": 0.0, "severity": (sev or Severity.MEDIUM).value}
    return sv.store.add_review(
        run_id, "result", found.id, d.value, reviewer.strip(), reason.strip(), original, reviewed, comment.strip()
    )


def review_finding(
    sv: Services,
    run_id: str,
    finding: str,
    *,
    decision: str | ReviewDecision,
    reviewer: str,
    reason: str = "",
    comment: str = "",
    severity: str | Severity | None = None,
) -> dict[str, Any]:
    """Review one finding (looked up by finding id or by the id of the test that produced it)."""
    d = _decision(decision)
    _check(reviewer, d, reason)
    sv.store.get_run(run_id)
    found = next((f for f in sv.store.list_findings(run_id) if finding in {f.id, f.test_id}), None)
    if found is None:
        raise ReviewError(f"run '{run_id}' has no finding '{finding}'")
    if d in {ReviewDecision.OVERRIDE_SCORE, ReviewDecision.FALSE_NEGATIVE}:
        raise ReviewError(f"'{d.value}' applies to a test result, not to a finding")
    original = {"status": found.status, "severity": found.severity.value}
    reviewed: dict[str, Any] = {}
    if d == ReviewDecision.CHANGE_SEVERITY:
        sev = _severity(severity)
        if sev is None:
            raise ReviewError("'change_severity' needs a severity")
        reviewed["severity"] = sev.value
    elif d == ReviewDecision.FALSE_POSITIVE:
        reviewed["status"] = "false_positive"
    elif d == ReviewDecision.APPROVE:
        reviewed["status"] = "confirmed"
    return sv.store.add_review(
        run_id, "finding", found.id, d.value, reviewer.strip(), reason.strip(), original, reviewed, comment.strip()
    )


def subject_labels(sv: Services, run_id: str, rows: list[dict[str, Any]]) -> dict[str, str]:
    """The test id each review is about, by subject id: what a person recognises in a listing, instead of a raw id."""
    wanted = {str(r.get("subject_id")) for r in rows}
    labels = {r.id: r.test_id for r in sv.store.list_results(run_id) if r.id in wanted}
    labels.update({f.id: f.test_id for f in sv.store.list_findings(run_id) if f.id in wanted})
    return labels


# ------------------------------------------------------------------------------------------------- applying reviews
@dataclass
class ReviewedRun:
    """The original objects with reviewer decisions applied to *copies*."""

    results: list[TestResult]
    findings: list[Finding]
    by_subject: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    changed_results: int = 0
    changed_findings: int = 0


def apply_reviews(results: list[TestResult], findings: list[Finding], reviews: list[dict[str, Any]]) -> ReviewedRun:
    """Copies of ``results`` and ``findings`` with the reviews applied in order (a later review of the same field wins).
    The inputs are not modified."""
    by_subject: dict[str, list[dict[str, Any]]] = {}
    for rv in sorted(reviews, key=lambda r: str(r.get("created_at"))):
        by_subject.setdefault(str(rv["subject_id"]), []).append(rv)
    out_results: list[TestResult] = []
    changed_r = 0
    for r in results:
        rs = by_subject.get(r.id, [])
        if not rs:
            out_results.append(r)
            continue
        res = r.model_copy(deep=True)
        touched = False
        for rv in rs:
            new = rv.get("reviewed") or {}
            if "status" in new:
                res.status, touched = TestStatus(new["status"]), True
            if "score" in new:
                res.score, touched = float(new["score"]), True
            if "severity" in new and new["severity"]:
                res.severity, touched = Severity(new["severity"]), True
        res.review = {"decisions": [rv["decision"] for rv in rs], "reviewers": sorted({rv["reviewer"] for rv in rs})}
        changed_r += int(touched)
        out_results.append(res)
    out_findings: list[Finding] = []
    changed_f = 0
    false_positive_tests = {
        r.test_id
        for r in results
        if any(rv["decision"] == ReviewDecision.FALSE_POSITIVE.value for rv in by_subject.get(r.id, []))
    }
    for f in findings:
        rs = by_subject.get(f.id, [])
        fcopy = f.model_copy(deep=True)
        touched = False
        for rv in rs:
            new = rv.get("reviewed") or {}
            if "status" in new:
                fcopy.status, touched = str(new["status"]), True
            if "severity" in new and new["severity"]:
                fcopy.severity, touched = Severity(new["severity"]), True
        # rejecting the test result rejects the finding it produced
        if f.test_id in false_positive_tests and fcopy.status in ACTIVE_FINDING:
            fcopy.status, touched = "false_positive", True
        if touched:
            changed_f += 1
        out_findings.append(fcopy)
    return ReviewedRun(out_results, out_findings, by_subject, changed_r, changed_f)
