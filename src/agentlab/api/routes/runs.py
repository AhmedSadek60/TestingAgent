"""Plans and runs: design a plan, approve it, start a run, follow it live, cancel it, read what it found and review it."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from agentlab.api.errors import ConflictError
from agentlab.api.routes.common import (
    RESPONSES,
    links,
    require_auth,
    run_detail,
    run_kind,
    run_row,
    run_summary,
)
from agentlab.api.schemas import (
    CancelRequest,
    CancelResponse,
    EventOut,
    PlanOut,
    PlanRequest,
    ReviewOut,
    ReviewRequest,
    RunAccepted,
    RunDetail,
    RunRequest,
    RunSummary,
    TraceOut,
    TraceSummary,
)
from agentlab.api.state import ApiState, state_of
from agentlab.api.targets import prepare_target, server_path
from agentlab.core.enums import RunStatus, Severity, TestStatus
from agentlab.core.errors import InfrastructureError, NotFoundError, UserError
from agentlab.core.ids import as_utc, new_id, utcnow
from agentlab.core.models import Finding, Scorecard, TargetSpec, TestResult
from agentlab.design import SUITES
from agentlab.design.models import TestPlan
from agentlab.evaluation.scoring import list_profiles
from agentlab.jobs.models import JobOptions, JobOverrides, JobSpec
from agentlab.reporting.review import apply_reviews, review_finding, review_result, subject_labels
from agentlab.skills.context import INTENSITIES

router = APIRouter(dependencies=[Depends(require_auth)], responses=RESPONSES)

TERMINAL = {
    RunStatus.COMPLETED.value,
    RunStatus.FAILED.value,
    RunStatus.CANCELLED.value,
    RunStatus.STOPPED_DUE_TO_COST.value,
    RunStatus.STOPPED_DUE_TO_TIMEOUT.value,
    RunStatus.STOPPED_DUE_TO_STEP_LIMIT.value,
}
POLL_SECONDS = 0.4
KEEPALIVE_EVERY = 30  # polls without an event: about twelve seconds


# =================================================================================================== submit
def _options(st: ApiState, options: JobOptions) -> JobOptions:
    """The request's options, checked against what exists, with uploaded files turned into server paths."""
    if options.suite not in SUITES:
        raise UserError(f"suite must be one of {', '.join(SUITES)}")
    if options.intensity not in INTENSITIES:
        raise UserError(f"intensity must be one of {', '.join(INTENSITIES)}")
    if options.scoring_profile and options.scoring_profile not in list_profiles():
        raise UserError(f"unknown scoring profile '{options.scoring_profile}' (known: {', '.join(list_profiles())})")
    out = options.model_copy(deep=True)
    out.user_test_files = [server_path(st, p, what="a file of test cases") for p in options.user_test_files]
    if options.baseline_run_id:
        out.baseline_run_id = st.store.resolve_run_id(options.baseline_run_id)
    return out


def _target_of(
    st: ApiState, target_id: str | None, target: TargetSpec | None, plan_row: dict[str, Any] | None
) -> TargetSpec:
    if target_id and target is not None:
        raise UserError("give at most one of `target_id` and `target`")
    if target_id:
        return st.store.get_target(target_id)[1]
    if target is not None:
        return target
    if plan_row is not None and plan_row.get("target_id"):
        return st.store.get_target(plan_row["target_id"])[1]
    raise UserError("give `target_id` or `target` (or `plan_id`, whose target is used)")


def _approved_plan(st: ApiState, plan_id: str, deselect: list[str]) -> tuple[dict[str, Any], TestPlan]:
    row = run_row(st, plan_id)
    if run_kind(row) != "plan" or row["status"] != RunStatus.COMPLETED.value or not row.get("suite_id"):
        raise UserError(f"'{plan_id}' is not a finished plan (create one with POST /test-plans and wait for it)")
    suite, _ = st.store.get_suite(row["suite_id"])
    plan = TestPlan.model_validate(suite["plan"])
    if deselect:
        known = {t.id for t in plan.tests}
        unknown = [t for t in deselect if t not in known]
        if unknown:
            raise UserError(f"the plan has no test(s) {', '.join(unknown[:5])}")
        plan.deselect(deselect, "deselected when the plan was approved")
    return row, plan


async def _submit(
    st: ApiState,
    *,
    kind: Literal["plan", "run"],
    project: str,
    target_id: str | None,
    target: TargetSpec | None,
    options: JobOptions,
    overrides: JobOverrides,
    plan_id: str | None = None,
    deselect: list[str] | None = None,
) -> RunAccepted:
    plan_row: dict[str, Any] | None = None
    plan: TestPlan | None = None
    if plan_id:
        plan_row, plan = _approved_plan(st, plan_id, deselect or [])
    elif deselect:
        raise UserError("`deselect` applies to an approved plan: give `plan_id` too")
    spec = prepare_target(st, _target_of(st, target_id, target, plan_row))
    opts = _options(st, options)
    st.services.credentials.reload()

    proj = st.store.ensure_project(project)
    target_row = st.store.add_target(proj["id"], spec)
    run_id = new_id()
    mode = "regression" if opts.baseline_run_id else opts.suite
    st.store.create_run(
        proj["id"],
        target_row["id"],
        None,
        mode,
        {"queued": {"at": utcnow().isoformat(), "kind": kind, "plan_id": plan_id}},
        st.services.config.limits.model_dump(mode="json"),
        run_id=run_id,
    )
    st.store.update_run(run_id, totals={"queued_kind": kind})
    job = JobSpec(
        kind=kind, run_id=run_id, project=proj["name"], target=spec, options=opts, overrides=overrides, plan=plan
    )  # type: ignore[arg-type]
    try:
        await st.queue.submit(job)
    except InfrastructureError as exc:
        st.store.update_run(
            run_id,
            status=RunStatus.FAILED.value,
            finished_at=utcnow(),
            error={"kind": "INFRASTRUCTURE_ERROR", "message": str(exc)},
        )
        raise
    return RunAccepted(run_id=run_id, kind=kind, status="pending", queue=st.queue.name, links=links(run_id))


@router.post(
    "/test-plans",
    response_model=PlanOut,
    status_code=202,
    responses={
        200: {"model": PlanOut, "description": "The plan, when `wait_seconds` was given and it was ready in time"}
    },
    tags=["Test plans"],
    summary="Design a test plan",
    description=(
        "Discover the target and design an explainable test plan: which tests, why, what each needs, what cannot run and "
        "why, and what the run is expected to cost. Only harmless discovery probes are sent to the target. The plan is "
        "designed in the background; poll `GET /test-plans/{id}` (or pass `wait_seconds`). Run it exactly as approved with "
        "`POST /test-runs` and `plan_id`."
    ),
)
async def create_plan(body: PlanRequest, request: Request) -> Any:
    st = state_of(request)
    if (body.target_id is None) == (body.target is None):
        raise UserError("give exactly one of `target_id` and `target`")
    accepted = await _submit(
        st,
        kind="plan",
        project=body.project,
        target_id=body.target_id,
        target=body.target,
        options=body.options,
        overrides=body.overrides,
    )
    deadline = asyncio.get_running_loop().time() + body.wait_seconds
    while body.wait_seconds and asyncio.get_running_loop().time() < deadline:
        row = await asyncio.to_thread(st.store.get_run, accepted.run_id)
        if row["status"] in TERMINAL:
            break
        await asyncio.sleep(POLL_SECONDS)
    out = await asyncio.to_thread(_plan_out, st, accepted.run_id)
    return JSONResponse(out.model_dump(mode="json"), status_code=200 if out.status in TERMINAL else 202)


def _plan_out(st: ApiState, run_id: str) -> PlanOut:
    row = st.store.get_run(run_id)
    plan = None
    if row.get("suite_id"):
        suite, _ = st.store.get_suite(row["suite_id"])
        if suite.get("plan"):
            plan = TestPlan.model_validate(suite["plan"])
    warnings = list((row.get("totals") or {}).get("warnings") or [])
    return PlanOut(
        run_id=run_id,
        status=row["status"],
        plan=plan,
        profile=st.store.profile_for_run(run_id),
        warnings=warnings,
        error=row.get("error"),
    )


@router.get(
    "/test-plans/{plan_id}",
    response_model=PlanOut,
    tags=["Test plans"],
    summary="Get a test plan",
    description="The plan of a `POST /test-plans` request (`plan` is null until it has been designed), with what discovery learned.",
)
def get_plan(plan_id: str, request: Request) -> Any:
    st = state_of(request)
    return _plan_out(st, st.store.resolve_run_id(plan_id))


@router.post(
    "/test-runs",
    response_model=RunAccepted,
    status_code=202,
    tags=["Test runs"],
    summary="Start a test run",
    description=(
        "Queue a run: discover the target, design (or take the approved) plan, execute it safely, evaluate, score and "
        "report. Follow it with `GET /test-runs/{id}` and the live stream; stop it with `POST /test-runs/{id}/cancel`. "
        "A run never widens what the target's owner authorised, and tests that cannot run (a credential that is not stored, "
        "no Docker) are reported BLOCKED, not failed."
    ),
)
async def create_run(body: RunRequest, request: Request) -> Any:
    st = state_of(request)
    if body.target_id is not None and body.target is not None:
        raise UserError("give at most one of `target_id` and `target`")
    return await _submit(
        st,
        kind="run",
        project=body.project,
        target_id=body.target_id,
        target=body.target,
        options=body.options,
        overrides=body.overrides,
        plan_id=body.plan_id,
        deselect=body.deselect,
    )


# ================================================================================================= reading
@router.get(
    "/test-runs",
    response_model=list[RunSummary],
    tags=["Test runs"],
    summary="List runs",
    description="Recent runs, newest first. Plans created with `POST /test-plans` are included (`kind: plan`).",
)
def list_runs(
    request: Request,
    project: str | None = None,
    status: str | None = None,
    target: str | None = Query(default=None, description="Only runs of the target with this id"),
    kind: str | None = Query(default=None, pattern="^(plan|run)$"),
    limit: int = Query(default=50, ge=1, le=500),
) -> Any:
    st = state_of(request)
    project_id = st.store.get_project(project)["id"] if project else None
    projects = {p["id"]: p["name"] for p in st.store.list_projects()}
    targets = {t["id"]: t["name"] for t in st.store.list_targets()}
    out = []
    for row in st.store.list_runs(project_id=project_id, limit=limit * 3 if (status or target or kind) else limit):
        if status and row["status"] != status:
            continue
        if target and row.get("target_id") != target:
            continue
        if kind and run_kind(row) != kind:
            continue
        out.append(run_summary(row, projects=projects, targets=targets))
        if len(out) >= limit:
            break
    return out


@router.get(
    "/test-runs/{run_id}",
    response_model=RunDetail,
    tags=["Test runs"],
    summary="Get a run",
    description=(
        "The state of a run: status, progress (tests done, passed, failed, blocked, tool calls, tokens, cost), the limits it "
        "runs under and the manifest that makes it reproducible. An unambiguous id prefix is enough."
    ),
)
def get_run(run_id: str, request: Request) -> Any:
    st = state_of(request)
    return run_detail(st, run_row(st, run_id))


@router.get(
    "/test-runs/{run_id}/plan",
    response_model=PlanOut,
    tags=["Test runs"],
    summary="Get the plan a run executes",
    description="The first-wave plan of the run. Follow-up tests added later by the adaptive second wave appear in the results.",
)
def get_run_plan(run_id: str, request: Request) -> Any:
    st = state_of(request)
    return _plan_out(st, run_row(st, run_id)["id"])


def _effective(st: ApiState, run_id: str) -> tuple[list[TestResult], list[Finding]]:
    """Results and findings with human reviews applied (to copies: what was originally concluded is never changed)."""
    reviewed = apply_reviews(
        st.store.list_results(run_id), st.store.list_findings(run_id), st.store.list_reviews(run_id)
    )
    return reviewed.results, reviewed.findings


@router.get(
    "/test-runs/{run_id}/results",
    response_model=list[TestResult],
    tags=["Test runs"],
    summary="List test results",
    description=(
        "Every test result with its attempts, assertions, judge verdicts, reliability statistics, root cause and severity. "
        "Human reviews are applied to a copy (`review` says so); the original evaluation is kept and shown by `/reviews`."
    ),
)
def list_results(
    run_id: str,
    request: Request,
    status: TestStatus | None = None,
    category: str | None = None,
    severity: Severity | None = None,
    q: str | None = Query(default=None, description="Text in the test id or name"),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> Any:
    st = state_of(request)
    rid = run_row(st, run_id)["id"]
    results, _ = _effective(st, rid)
    needle = (q or "").lower()
    picked = [
        r
        for r in results
        if (status is None or r.status == status)
        and (category is None or r.category == category)
        and (severity is None or r.severity == severity)
        and (not needle or needle in r.test_id.lower() or needle in r.test_name.lower())
    ]
    return picked[offset : offset + limit]


@router.get(
    "/test-runs/{run_id}/results/{test_id}",
    response_model=TestResult,
    tags=["Test runs"],
    summary="Get one test result",
    description="One result by test id (or result id).",
)
def get_result(run_id: str, test_id: str, request: Request) -> Any:
    st = state_of(request)
    rid = run_row(st, run_id)["id"]
    results, _ = _effective(st, rid)
    found = next((r for r in results if test_id in {r.test_id, r.id}), None)
    if found is None:
        raise NotFoundError(f"run has no result for test '{test_id}'")
    return found


@router.get(
    "/test-runs/{run_id}/findings",
    response_model=list[Finding],
    tags=["Test runs"],
    summary="List findings",
    description=(
        "Findings in the report format: title, severity, confidence, expected, observed, impact, evidence, reproduction, "
        "recommendation and root cause, with observed facts kept apart from inferences and judgments."
    ),
)
def list_findings(
    run_id: str,
    request: Request,
    severity: Severity | None = None,
    security: bool | None = None,
    status: str | None = Query(default=None, description="open, confirmed or false_positive"),
) -> Any:
    st = state_of(request)
    rid = run_row(st, run_id)["id"]
    _, findings = _effective(st, rid)
    order = {s: i for i, s in enumerate(reversed(list(Severity)))}
    picked = [
        f
        for f in findings
        if (severity is None or f.severity == severity)
        and (security is None or f.is_security == security)
        and (status is None or f.status == status)
    ]
    return sorted(picked, key=lambda f: (order.get(f.severity, 99), f.test_id))


@router.get(
    "/test-runs/{run_id}/scorecard",
    response_model=Scorecard,
    tags=["Test runs"],
    summary="Get the scorecard",
    description="Scores per category with the confidence of each, the overall score and grade, and what limits them (a security cap, thin coverage).",
)
def get_scorecard(run_id: str, request: Request) -> Any:
    st = state_of(request)
    rid = run_row(st, run_id)["id"]
    card = st.store.latest_scorecard(rid)
    if card is None:
        raise NotFoundError(
            "this run has no scorecard (it was not scored: still running, plan only, or nothing could be tested)"
        )
    return card


# ============================================================================================ events & traces
def _event(row: dict[str, Any]) -> EventOut:
    return EventOut(
        event_id=row["event_id"],
        run_id=row["run_id"],
        test_id=row.get("test_id"),
        timestamp=row["timestamp"],
        type=row["type"],
        payload=row.get("payload") or {},
        redaction_status=row.get("redaction_status") or "not_scanned",
    )


def _naive(value: str | datetime) -> datetime:
    """A timestamp as UTC wall-clock time. Stored rows hand timestamps back as ISO text, with an offset or without one
    depending on the database, and these have to be comparable with each other and with a cursor."""
    return as_utc(value).replace(tzinfo=None)


def _position(event: dict[str, Any]) -> tuple[datetime, str]:
    return _naive(event["timestamp"]), str(event["event_id"])


def _cursor(value: str | None) -> tuple[datetime, str] | None:
    """``<iso timestamp>|<event id>``: the place in a run's events a reader has reached."""
    if not value:
        return None
    stamp, _, ident = value.partition("|")
    try:
        return _naive(stamp), ident
    except ValueError as exc:
        raise UserError("`after` must be the cursor of an earlier event (<timestamp>|<id>)") from exc


def _cursor_of(event: dict[str, Any]) -> str:
    return f"{_naive(event['timestamp']).isoformat()}|{event['event_id']}"


@router.get(
    "/test-runs/{run_id}/events",
    response_model=list[EventOut],
    tags=["Test runs"],
    summary="List a run's events",
    description=(
        "The structured events of a run in order (phases, tests, requests, tool calls, assertions, judge decisions, findings, "
        "security alerts). Payloads were redacted when the events were created. Pass the `X-Next-Cursor` of a response as "
        "`after` to continue from there."
    ),
)
def list_events(
    run_id: str,
    request: Request,
    after: str | None = None,
    type: Annotated[list[str] | None, Query(description="Only these event types")] = None,
    test_id: str | None = None,
    limit: int = Query(default=500, ge=1, le=5000),
) -> Any:
    st = state_of(request)
    rid = run_row(st, run_id)["id"]
    cur = _cursor(after)
    rows = st.store.list_events(rid, after_ts=cur[0] if cur else None, inclusive=True, types=type, limit=limit + 50)
    if cur:
        rows = [r for r in rows if _position(r) > cur]
    if test_id:
        rows = [r for r in rows if r.get("test_id") == test_id]
    rows = rows[:limit]
    headers = {"X-Next-Cursor": _cursor_of(rows[-1])} if rows else {}
    body = [_event(r).model_dump(mode="json") for r in rows]
    return JSONResponse(body, headers=headers)


def _sse(event: dict[str, Any]) -> bytes:
    data = _event(event).model_dump(mode="json")
    return (
        f"id: {_cursor_of(event)}\nevent: {event['type']}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()
    )


@router.get(
    "/test-runs/{run_id}/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "Server-sent events"}},
    tags=["Test runs"],
    summary="Follow a run live",
    description=(
        "Server-sent events: one `event:` per run event (`PhaseStarted`, `TestStarted`, `ToolCalled`, `FindingCreated`, "
        "`SecurityAlert`, ...), starting from the beginning of the run (or from `after` / `Last-Event-ID`), then new ones as "
        "they happen, and a final `end` event with the run's status. Browsers that cannot send the Authorization header "
        "can read it with `fetch` instead of `EventSource`."
    ),
)
async def stream_run(
    run_id: str,
    request: Request,
    after: str | None = None,
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    st = state_of(request)
    rid = run_row(st, run_id)["id"]
    cursor = _cursor(after or last_event_id)

    async def feed() -> AsyncIterator[bytes]:
        nonlocal cursor
        idle = 0
        yield b"retry: 2000\n\n"
        while True:
            rows = await asyncio.to_thread(
                st.store.list_events, rid, after_ts=cursor[0] if cursor else None, inclusive=True, limit=500
            )
            if cursor:
                rows = [r for r in rows if _position(r) > cursor]
            if rows:
                idle = 0
                for r in rows:
                    yield _sse(r)
                cursor = _position(rows[-1])
                continue  # there may be more waiting
            row = await asyncio.to_thread(st.store.get_run, rid)
            if row["status"] in TERMINAL:
                last = await asyncio.to_thread(
                    st.store.list_events, rid, after_ts=cursor[0] if cursor else None, inclusive=True, limit=500
                )
                if not [r for r in last if not cursor or _position(r) > cursor]:
                    done = {"run_id": rid, "status": row["status"], "finished_at": row.get("finished_at")}
                    yield f"event: end\ndata: {json.dumps(done, default=str)}\n\n".encode()
                    return
                continue
            idle += 1
            if idle % KEEPALIVE_EVERY == 0:
                yield b": keep-alive\n\n"
            if await request.is_disconnected():
                return
            await asyncio.sleep(POLL_SECONDS)

    return StreamingResponse(
        feed(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.get(
    "/test-runs/{run_id}/traces",
    response_model=list[TraceSummary],
    tags=["Test runs"],
    summary="List traces",
    description="One trace per attempt of each test: the provider-neutral record of what was sent, answered, called and decided.",
)
def list_traces(run_id: str, request: Request, test_id: str | None = None) -> Any:
    st = state_of(request)
    rid = run_row(st, run_id)["id"]
    out = []
    for row in st.store.list_traces(rid):
        if test_id and row["test_key"] != test_id:
            continue
        out.append(
            TraceSummary(
                id=row["id"],
                run_id=rid,
                test_id=row["test_key"],
                attempt=row["attempt"],
                event_count=row["event_count"],
                artifact_id=row.get("artifact_id"),
                event_types=list((row.get("summary") or {}).get("types") or []),
            )
        )
    return out


@router.get(
    "/test-runs/{run_id}/traces/{trace_id}",
    response_model=TraceOut,
    tags=["Test runs"],
    summary="Get a trace",
    description="The ordered events of one attempt of one test (requests, responses, tool calls, retrievals, assertions, judge decisions).",
)
def get_trace(run_id: str, trace_id: str, request: Request) -> Any:
    st = state_of(request)
    rid = run_row(st, run_id)["id"]
    row = next((t for t in st.store.list_traces(rid) if t["id"] == trace_id), None)
    if row is None or not row.get("artifact_id"):
        raise NotFoundError(f"trace '{trace_id}' not found in this run")
    try:
        doc = st.services.artifacts.get_json(row["artifact_id"])
    except InfrastructureError as exc:
        raise NotFoundError("the stored trace is no longer available") from exc
    events = [
        EventOut(
            event_id=e["event_id"],
            run_id=e["run_id"],
            test_id=e.get("test_id"),
            timestamp=e["timestamp"],
            type=e["type"],
            payload=e.get("payload") or {},
            redaction_status=e.get("redaction_status") or "not_scanned",
        )
        for e in doc.get("events", [])
    ]
    return TraceOut(
        id=row["id"],
        run_id=rid,
        test_id=row["test_key"],
        attempt=row["attempt"],
        timestamp=doc.get("timestamp") or row["created_at"],
        events=events,
    )


# ================================================================================================== control
@router.post(
    "/test-runs/{run_id}/cancel",
    response_model=CancelResponse,
    status_code=202,
    tags=["Test runs"],
    summary="Cancel a run",
    description=(
        "Stop a run safely. A run that has not started is cancelled at once. A running one finishes the step in progress, "
        "skips the rest, and what ran is still analysed and reported (status `cancelled`). A finished run answers 409."
    ),
)
async def cancel_run(run_id: str, request: Request, body: CancelRequest | None = None) -> Any:
    st = state_of(request)
    row = run_row(st, run_id)
    rid = row["id"]
    reason = (body.reason if body else None) or "cancelled from the API"
    if row["status"] in TERMINAL:
        raise ConflictError(f"the run has already finished ({row['status']})")
    if row["status"] == RunStatus.PENDING.value and await st.queue.withdraw(rid):
        st.store.update_run(rid, status=RunStatus.CANCELLED.value, finished_at=utcnow(), error=None)
        await st.queue.release(rid)
        return CancelResponse(run_id=rid, status="cancelled", message="the run had not started and will not run")
    await st.queue.request_cancel(rid, reason)
    if st.worker is not None:
        st.worker.runner.cancel_local(rid, reason)
    return CancelResponse(
        run_id=rid, status="cancelling", message="the run stops after the step in progress; what ran is still reported"
    )


# =================================================================================================== reviews
def _review_out(st: ApiState, rid: str, rows: list[dict[str, Any]]) -> list[ReviewOut]:
    labels = subject_labels(st.services, rid, rows)
    return [
        ReviewOut(
            id=r["id"],
            run_id=r["run_id"],
            subject_type=r["subject_type"],
            subject_id=r["subject_id"],
            subject_label=labels.get(str(r["subject_id"])),
            decision=r["decision"],
            reviewer=r["reviewer"],
            reason=r.get("reason") or "",
            comment=r.get("comment") or "",
            original=r.get("original") or {},
            reviewed=r.get("reviewed") or {},
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.post(
    "/test-runs/{run_id}/reviews",
    response_model=ReviewOut,
    status_code=201,
    tags=["Human review"],
    summary="Review a result or a finding",
    description=(
        "Record a human decision: approve, false_positive, false_negative, override_score or change_severity, with who "
        "decided and why. The original evaluation is never changed or deleted; the review is kept beside it and applied "
        "to what the API, the scorecard and new reports show."
    ),
)
def create_review(run_id: str, body: ReviewRequest, request: Request) -> Any:
    st = state_of(request)
    rid = run_row(st, run_id)["id"]
    if body.subject == "result":
        row = review_result(
            st.services, rid, body.subject_id, decision=body.decision, reviewer=body.reviewer, reason=body.reason,
            comment=body.comment, score=body.score, severity=body.severity,
        )  # fmt: skip
    else:
        row = review_finding(
            st.services, rid, body.subject_id, decision=body.decision, reviewer=body.reviewer, reason=body.reason,
            comment=body.comment, severity=body.severity,
        )  # fmt: skip
    return _review_out(st, rid, [row])[0]


@router.get(
    "/test-runs/{run_id}/reviews",
    response_model=list[ReviewOut],
    tags=["Human review"],
    summary="List reviews",
    description="Every human decision on the run, oldest first, with the original values it changed.",
)
def list_reviews(run_id: str, request: Request) -> Any:
    st = state_of(request)
    rid = run_row(st, run_id)["id"]
    return _review_out(st, rid, st.store.list_reviews(rid))
