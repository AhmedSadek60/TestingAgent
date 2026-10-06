"""Helpers every router uses: authentication, lookups that answer 404, the documented error responses and the views of a run."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from agentlab.api.schemas import ErrorResponse, RunDetail, RunProgress, RunSummary
from agentlab.api.state import ApiState, state_of
from agentlab.core.enums import EventType, RunStatus, TestStatus

RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "The API token is missing or wrong"},
    403: {"model": ErrorResponse, "description": "The request is not allowed (policy, origin or restricted evidence)"},
    404: {"model": ErrorResponse, "description": "The thing does not exist"},
    422: {"model": ErrorResponse, "description": "The request is invalid; the message says what to change"},
    429: {"model": ErrorResponse, "description": "Too many failed authentication attempts"},
}


def require_auth(request: Request) -> None:
    """Dependency of every endpoint that needs the token."""
    st = state_of(request)
    if not st.guard.required:
        return
    client = request.client.host if request.client else "unknown"
    if st.guard.throttled(client):
        raise HTTPException(status_code=429, detail="too many failed attempts; wait a minute")
    from agentlab.api.security import presented_token

    if not st.guard.check(client, presented_token(request.headers)):
        raise HTTPException(
            status_code=401,
            detail="missing or wrong API token (send Authorization: Bearer <token>)",
            headers={"WWW-Authenticate": "Bearer"},
        )


def run_row(st: ApiState, ident: str) -> dict[str, Any]:
    """The run named by a full id or an unambiguous prefix, or 404."""
    return st.store.get_run(st.store.resolve_run_id(ident))


def links(run_id: str) -> dict[str, str]:
    base = f"/test-runs/{run_id}"
    return {
        "run": base,
        "events": f"{base}/events",
        "stream": f"{base}/stream",
        "plan": f"{base}/plan",
        "results": f"{base}/results",
        "findings": f"{base}/findings",
        "scorecard": f"{base}/scorecard",
        "traces": f"{base}/traces",
        "artifacts": f"{base}/artifacts",
        "reports": f"{base}/reports",
        "reviews": f"{base}/reviews",
        "cancel": f"{base}/cancel",
    }


# ====================================================================================================== views
def run_kind(row: dict[str, Any]) -> str:
    totals = row.get("totals") or {}
    return "plan" if totals.get("plan_only") or totals.get("queued_kind") == "plan" else "run"


def run_summary(
    row: dict[str, Any], *, projects: dict[str, str] | None = None, targets: dict[str, str] | None = None
) -> RunSummary:
    totals = {k: v for k, v in (row.get("totals") or {}).items() if k != "queued_kind"}
    manifest = row.get("manifest") or {}
    target_name = (targets or {}).get(str(row.get("target_id"))) or (manifest.get("target") or {}).get("name")
    return RunSummary(
        id=row["id"],
        project_id=row["project_id"],
        project=(projects or {}).get(row["project_id"]),
        target_id=row.get("target_id"),
        target=target_name,
        kind=run_kind(row),  # type: ignore[arg-type]
        suite=str(row.get("mode") or "full"),
        status=row["status"],
        created_at=row["created_at"],
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
        error=row.get("error"),
        overall=totals.get("overall"),
        grade=totals.get("grade"),
        totals=totals,
    )


def run_progress(st: ApiState, row: dict[str, Any]) -> RunProgress:
    """What the live view shows, read from what the run has recorded so far (nothing is recomputed or guessed)."""
    run_id = row["id"]
    store = st.store
    counts = store.event_counts(run_id)
    results = store.list_results(run_id)
    by = {s: 0 for s in TestStatus}
    for r in results:
        by[r.status] += 1
    planned = 0
    for ev in store.list_events(run_id, types=[EventType.TEST_PLAN_GENERATED.value], limit=50):
        planned += int((ev.get("payload") or {}).get("runnable", 0))
    started = {
        ev["test_id"]
        for ev in store.list_events(run_id, types=[EventType.TEST_STARTED.value], limit=5000)
        if ev.get("test_id")
    }
    done = {r.test_id for r in results}
    phases = store.list_events(
        run_id, types=[EventType.PHASE_STARTED.value, EventType.PHASE_COMPLETED.value], limit=200
    )
    phase = None
    finished_phases = 0
    for ev in phases:
        payload = ev.get("payload") or {}
        if ev["type"] == EventType.PHASE_STARTED.value:
            phase = payload.get("phase")
        elif payload.get("phase") == phase:
            phase = None
        if ev["type"] == EventType.PHASE_COMPLETED.value:
            finished_phases += 1
    terminal = row["status"] not in {RunStatus.RUNNING.value, RunStatus.PENDING.value}
    latencies = [r.latency_ms for r in results if r.latency_ms]
    stopped = sum(1 for r in results if r.status.is_stopped)
    return RunProgress(
        tests_total=max(planned, len(results), len(started)),
        tests_done=len(results),
        passed=by[TestStatus.PASSED],
        failed=by[TestStatus.FAILED] + by[TestStatus.TIMEOUT],
        blocked=by[TestStatus.BLOCKED],
        errors=by[TestStatus.ERROR],
        skipped=by[TestStatus.SKIPPED],
        stopped=stopped,
        findings=counts.get(EventType.FINDING_CREATED.value, 0),
        security_alerts=counts.get(EventType.SECURITY_ALERT.value, 0),
        phase=None if terminal else phase,
        phases_done=finished_phases,
        running_tests=[] if terminal else sorted(started - done),
        tool_calls=counts.get(EventType.TOOL_CALLED.value, 0),
        browser_actions=counts.get(EventType.BROWSER_ACTION.value, 0),
        llm_calls=counts.get(EventType.LLM_CALLED.value, 0),
        tokens=sum(r.tokens for r in results),
        cost_usd=round(sum(r.cost_usd for r in results), 6),
        latency_ms_avg=round(sum(latencies) / len(latencies), 1) if latencies else None,
    )


def run_detail(st: ApiState, row: dict[str, Any]) -> RunDetail:
    projects = {p["id"]: p["name"] for p in st.store.list_projects()}
    summary = run_summary(row, projects=projects)
    return RunDetail(
        **summary.model_dump(),
        progress=run_progress(st, row),
        limits=row.get("limits") or {},
        manifest=row.get("manifest") or {},
        suite_id=row.get("suite_id"),
        links=links(row["id"]),
    )
