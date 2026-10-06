"""Reports, regression comparison and evidence downloads."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response

from agentlab.api.routes.common import RESPONSES, require_auth, run_row
from agentlab.api.schemas import (
    ArtifactOut,
    ExportRequest,
    ExportResponse,
    ReportCreate,
    ReportFileOut,
    ReportOut,
    ViewLink,
)
from agentlab.api.security import FRAMED_CSP, SANDBOX_CSP, safe_filename
from agentlab.api.state import ApiState, state_of
from agentlab.core.errors import InfrastructureError, NotFoundError, PolicyBlocked, UserError
from agentlab.reporting.bundle import MEDIA_TYPES, generate_report
from agentlab.reporting.compare import Comparison, compare_runs
from agentlab.storage.artifacts import ArtifactRef

log = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_auth)], responses=RESPONSES)
public = APIRouter()  # reached with a signed link, not a token

INLINE_TYPES = (
    "application/json",
    "text/plain",
    "text/markdown",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "application/pdf",
)


# ================================================================================================== reports
def _file(st: ApiState, report_id: str, fmt: str, artifact_id: str) -> ReportFileOut:
    try:
        ref = st.services.artifacts.ref(artifact_id)
        size, media = ref.size, ref.media_type
    except InfrastructureError:
        size, media = 0, MEDIA_TYPES.get(fmt, "application/octet-stream")
    return ReportFileOut(
        format=fmt,  # type: ignore[arg-type]
        artifact_id=artifact_id,
        media_type=media,
        size=size,
        url=f"/reports/{report_id}/files/{fmt}",
    )


def _report_out(st: ApiState, row: dict[str, Any]) -> ReportOut:
    manifest = row.get("manifest") or {}
    return ReportOut(
        id=row["id"],
        run_id=row["run_id"],
        report_version=row["report_version"],
        created_at=row["created_at"],
        formats=[_file(st, row["id"], fmt, aid) for fmt, aid in sorted((row.get("formats") or {}).items())],
        bundle_id=manifest.get("bundle_id"),
        generated_at=manifest.get("generated_at"),
        baseline_run_id=manifest.get("baseline_run_id"),
        redactions=manifest.get("redactions") or {},
        warnings=list(manifest.get("warnings") or []),
    )


def _reports_of(st: ApiState, run_id: str) -> list[dict[str, Any]]:
    return st.store.list_reports(run_id)


@router.get(
    "/test-runs/{run_id}/reports",
    response_model=list[ReportOut],
    tags=["Reports"],
    summary="List the reports of a run",
    description="Every report version generated for the run (a report is never overwritten; a new one is a new version).",
)
def list_run_reports(run_id: str, request: Request) -> Any:
    st = state_of(request)
    rid = run_row(st, run_id)["id"]
    return [_report_out(st, r) for r in _reports_of(st, rid)]


@router.post(
    "/test-runs/{run_id}/reports",
    response_model=ReportOut,
    status_code=201,
    tags=["Reports"],
    summary="Generate a report",
    description=(
        "Build a new, versioned report of a finished run in the formats asked for: JSON, Markdown, interactive HTML and "
        "PDF, with the 27 sections of the report specification, checksums and secrets masked. Evidence taken while signed in "
        "is left out unless `include_sensitive` is true."
    ),
)
async def create_report(run_id: str, body: ReportCreate, request: Request) -> Any:
    st = state_of(request)
    rid = run_row(st, run_id)["id"]
    return await _generate(st, rid, body.formats, body.include_sensitive, body.baseline_run_id)


async def _generate(
    st: ApiState, run_id: str, formats: Sequence[str], include_sensitive: bool, baseline: str | None
) -> ReportOut:
    row = st.store.get_run(run_id)
    if row["status"] in {"pending", "running"}:
        raise UserError("the run has not finished; a report describes a finished run")
    base = st.store.resolve_run_id(baseline) if baseline else None
    bundle = await asyncio.to_thread(
        generate_report, st.services, run_id, formats=formats, baseline=base, include_sensitive=include_sensitive
    )
    stored = st.store.get_report(str(bundle.report_id))
    return _report_out(st, stored)


@router.get(
    "/reports/{report_id}",
    response_model=ReportOut,
    tags=["Reports"],
    summary="Get a report",
    description="A report version: which formats exist, their sizes and download links, its checksum and what was masked.",
)
def get_report(report_id: str, request: Request) -> Any:
    st = state_of(request)
    return _report_out(st, st.store.get_report(report_id))


@router.post(
    "/reports/{report_id}/export",
    response_model=ExportResponse,
    tags=["Reports"],
    summary="Export a report in a format",
    description=(
        "Get the report in one format. If that format was not generated for this report version (or the evidence it would "
        "embed was not), a new report version is generated with it; the earlier version stays as it was."
    ),
)
async def export_report(report_id: str, body: ExportRequest, request: Request) -> Any:
    st = state_of(request)
    row = st.store.get_report(report_id)
    manifest = row.get("manifest") or {}
    fmt_ids = row.get("formats") or {}
    have = fmt_ids
    needs_new = body.format not in have or (body.include_sensitive and not manifest.get("include_sensitive"))
    if not needs_new:
        out = _report_out(st, row)
        return ExportResponse(
            report=out, file=_file(st, row["id"], body.format, fmt_ids[body.format]), created_new_version=False
        )
    out = await _generate(st, row["run_id"], [body.format], body.include_sensitive, manifest.get("baseline_run_id"))
    chosen = next(f for f in out.formats if f.format == body.format)
    return ExportResponse(report=out, file=chosen, created_new_version=True)


@router.get(
    "/reports/{report_id}/files/{format}",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}, "description": "The report file"}},
    tags=["Reports"],
    summary="Download a report file",
    description=(
        "The report in one format. The HTML report is served with a content security policy that gives it no access to "
        "this server (it is a document, not a page of the interface)."
    ),
)
def download_report(report_id: str, format: str, request: Request) -> Response:
    st = state_of(request)
    row = st.store.get_report(report_id)
    artifact_id = (row.get("formats") or {}).get(format)
    if artifact_id is None:
        raise NotFoundError(f"report '{report_id}' has no {format} file (POST /reports/{report_id}/export creates it)")
    return _send(
        st, artifact_id, name=f"agentlab-report-{row['run_id'][:8]}-v{row['report_version']}.{format}", inline=True
    )


@router.post(
    "/reports/{report_id}/view-link",
    response_model=ViewLink,
    tags=["Reports"],
    summary="Get a link that shows the HTML report",
    description=(
        "A short-lived link (it works for five minutes, and until this server restarts) that opens the HTML report on its own, "
        "with no token, so it can be shown in a frame. The report is served inside a sandbox: it has no origin of its own and "
        "cannot read the page that frames it or call this API. The link opens that one file and nothing else."
    ),
)
def report_view_link(report_id: str, request: Request) -> ViewLink:
    st = state_of(request)
    row = st.store.get_report(report_id)
    if (row.get("formats") or {}).get("html") is None:
        raise NotFoundError(f"report '{report_id}' has no html file (POST /reports/{report_id}/export creates it)")
    token = st.links.sign(row["id"], "html")
    return ViewLink(url=f"/view/{token}", expires_in=st.links.ttl_seconds)


@public.get("/view/{token}", include_in_schema=False, response_class=Response)
def view_report(token: str, request: Request) -> Response:
    """What a link from ``POST /reports/{id}/view-link`` opens. Anything wrong with the link is the same plain 404."""
    st = state_of(request)
    claim = st.links.verify(token)
    gone = NotFoundError("this link does not work (it has expired, or this server was restarted); ask for a new one")
    if claim is None:
        raise gone
    report_id, fmt = claim
    try:
        row = st.store.get_report(report_id)
    except NotFoundError:
        raise gone from None
    artifact_id = (row.get("formats") or {}).get(fmt)
    if artifact_id is None:
        raise gone
    response = _send(st, artifact_id, name=f"report.{fmt}", inline=True)
    response.headers["Content-Security-Policy"] = FRAMED_CSP
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


# =============================================================================================== comparison
@router.get(
    "/comparisons",
    response_model=Comparison,
    response_model_by_alias=True,
    tags=["Reports"],
    summary="Compare two runs",
    description=(
        "Regression comparison of `run_a` (the baseline) with `run_b` (the current run): tests that regressed, were fixed, "
        "are new or are gone; score, latency, cost, reliability and security deltas; and whether the runs are comparable "
        "(same target, same plan, same scoring profile) with the reason when they are not."
    ),
)
def compare(run_a: str, run_b: str, request: Request) -> Any:
    st = state_of(request)
    return compare_runs(st.services, run_row(st, run_a)["id"], run_row(st, run_b)["id"])


# ================================================================================================ artifacts
@router.get(
    "/test-runs/{run_id}/artifacts",
    response_model=list[ArtifactOut],
    tags=["Artifacts"],
    summary="List the evidence of a run",
    description="Plans, traces, screenshots, browser traces, reports and other files the run stored, with their sensitivity.",
)
def list_artifacts(run_id: str, request: Request, kind: str | None = None) -> Any:
    st = state_of(request)
    rid = run_row(st, run_id)["id"]
    out: dict[tuple[str, str | None, str | None], ArtifactOut] = {}
    for row in st.store.list_artifacts(rid):
        if kind and row["kind"] != kind:
            continue
        art = ArtifactOut(
            id=f"sha256-{row['sha256']}",
            sha256=row["sha256"],
            kind=row["kind"],
            media_type=row["media_type"],
            size=row["size"],
            sensitivity=row["sensitivity"],
            name=row.get("name"),
            run_id=row.get("run_id"),
            test_id=row.get("test_key"),
            url=f"/artifacts/sha256-{row['sha256']}",
        )
        out[(art.sha256, art.name, art.test_id)] = art
    return list(out.values())


@router.get(
    "/artifacts/{artifact_id}",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}, "description": "The stored bytes"}},
    tags=["Artifacts"],
    summary="Download an artifact",
    description=(
        "The stored bytes of one piece of evidence. Restricted evidence (traces and screenshots taken while signed in) is "
        "refused unless `include_restricted=true` is given. Content that a browser could execute is served so that it "
        "cannot (as an attachment, with a sandboxing content security policy)."
    ),
)
def download_artifact(
    artifact_id: str,
    request: Request,
    include_restricted: bool = Query(default=False, description="Confirm that you want restricted evidence"),
    inline: bool = Query(default=False, description="Show instead of download, for types that are safe to show"),
) -> Response:
    st = state_of(request)
    try:
        ref = st.services.artifacts.ref(artifact_id)
    except InfrastructureError as exc:
        raise NotFoundError(str(exc)) from exc
    if ref.sensitivity == "restricted" and not include_restricted:
        raise PolicyBlocked(
            "this evidence is restricted (it may show a signed-in session); add include_restricted=true to get it"
        )
    if ref.sensitivity == "restricted":
        log.warning("restricted artifact %s of run %s was downloaded", ref.id[:19], ref.run_id)
    return _send(st, artifact_id, name=ref.name or ref.id, inline=inline, ref=ref)


def _send(st: ApiState, artifact_id: str, *, name: str, inline: bool, ref: ArtifactRef | None = None) -> Response:
    try:
        ref = ref or st.services.artifacts.ref(artifact_id)
        data = st.services.artifacts.get(artifact_id)
    except InfrastructureError as exc:
        raise NotFoundError(str(exc)) from exc
    media = ref.media_type
    html_like = media in {"text/html", "image/svg+xml", "application/xhtml+xml", "application/xml", "text/xml"}
    disposition = "inline" if inline and (media.startswith(INLINE_TYPES) or html_like) else "attachment"
    headers = {
        "Content-Disposition": f'{disposition}; filename="{safe_filename(name, fallback="download")}"',
        "Cache-Control": "no-store",
    }
    if html_like:
        headers["Content-Security-Policy"] = SANDBOX_CSP  # a document of a run is never a page of this server
    return Response(content=data, media_type=media, headers=headers)
