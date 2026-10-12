"""Diagnostic information for an issue report: what can be collected, collecting it, reviewing it, and attaching it."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from agentlab.api.routes.common import RESPONSES, client_of, current_principal, require_user
from agentlab.api.state import ApiState, state_of
from agentlab.core.config import FeedbackConfig
from agentlab.core.errors import UserError
from agentlab.feedback import Actor, actor_for
from agentlab.feedback.diagnostics import DiagnosticsService
from agentlab.feedback.models import (
    ID,
    AttachDiagnosticsIn,
    DiagnosticCollectionOut,
    DiagnosticsAvailable,
    DiagnosticsRequest,
    RedactPreviewIn,
    RedactPreviewOut,
)

router = APIRouter(responses=RESPONSES, tags=["Feedback & Support"])

SUBMISSION = Annotated[
    str,
    Path(
        min_length=3,
        max_length=40,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:\-]*$",
        description="The submission's id, or its number such as `FR-2026-00042` or `IR-2026-00042`",
    ),
]
COLLECTION = Annotated[
    str, Path(min_length=8, max_length=40, pattern=r"^[A-Za-z0-9\-]+$", description="Id of a collection")
]


def _service(st: ApiState) -> DiagnosticsService:
    if st.diagnostics is None:
        raise UserError("diagnostics are not available on this server")
    return st.diagnostics


def _actor(request: Request, st: ApiState) -> Actor:
    config: FeedbackConfig = st.services.config.feedback
    return actor_for(current_principal(request), config)


@router.get(
    "/diagnostics/available",
    response_model=DiagnosticsAvailable,
    summary="What diagnostic information can be collected",
    description="Every category AgentLab knows about, whether it can be collected here and now, and why not when it cannot "
    "(a log that is switched off, evaluation data that only maintainers may see, a screenshot AgentLab cannot take). Also "
    "the time windows, the limits, and the text the person agrees to before attaching anything.",
    dependencies=[Depends(require_user)],
)
def available(
    request: Request,
    run_id: Annotated[str | None, Query(pattern=ID, description="The evaluation run it concerns")] = None,
    test_id: Annotated[str | None, Query(pattern=ID, description="The test case it concerns")] = None,
    trace_id: Annotated[str | None, Query(pattern=ID, description="The trace it concerns")] = None,
) -> DiagnosticsAvailable:
    st = state_of(request)
    return _service(st).available(_actor(request, st), run_id=run_id, test_id=test_id, trace_id=trace_id)


@router.post(
    "/diagnostics/preview",
    response_model=DiagnosticCollectionOut,
    summary="Preview what would be collected",
    description="Collects the chosen categories, masks them and shows the result, item by item, with what was masked and "
    "where, and keeps nothing. A category that cannot be collected is listed with the reason; it never stops the others.",
    dependencies=[Depends(require_user)],
)
def preview(body: DiagnosticsRequest, request: Request) -> DiagnosticCollectionOut:
    st = state_of(request)
    return _service(st).preview(_actor(request, st), body)


@router.post(
    "/diagnostics/collect",
    response_model=DiagnosticCollectionOut,
    status_code=201,
    summary="Collect diagnostic information",
    description="Collects and masks the chosen categories and keeps the masked result as a draft for a limited time, so it "
    "can be reviewed and then attached to a submission. Only the masked text is kept. What is collected is limited to the "
    "caller's own requests, system-level warnings and, for a maintainer, the named run.",
    dependencies=[Depends(require_user)],
)
def collect(body: DiagnosticsRequest, request: Request) -> DiagnosticCollectionOut:
    st = state_of(request)
    return _service(st).collect(_actor(request, st), body, client=client_of(request))


@router.post(
    "/diagnostics/redact-preview",
    response_model=RedactPreviewOut,
    summary="See text masked before pasting it",
    description="Masks the text the way a collection would and says what was masked and where, without keeping anything. "
    "Use it on a snippet before including it in a report.",
    dependencies=[Depends(require_user)],
)
def redact_preview(body: RedactPreviewIn, request: Request) -> RedactPreviewOut:
    st = state_of(request)
    return _service(st).redact_preview(body.text)


@router.get(
    "/diagnostics/{collection_id}",
    response_model=DiagnosticCollectionOut,
    summary="Read a collection",
    description="A draft the caller collected, or a collection attached to a submission the caller may see.",
    dependencies=[Depends(require_user)],
)
def read(collection_id: COLLECTION, request: Request) -> DiagnosticCollectionOut:
    st = state_of(request)
    return _service(st).get(_actor(request, st), collection_id)


@router.get(
    "/diagnostics/{collection_id}/download",
    summary="Download a collection as an archive",
    description="A ZIP of the included items under the names of the specification, with a manifest that says what was "
    "collected, what was not available, what was left out and what was masked, and a README. Every file is a generated, "
    "masked copy. Downloads of an attached collection are recorded.",
    response_class=Response,
    responses={200: {"content": {"application/zip": {}}, "description": "The archive"}},
    dependencies=[Depends(require_user)],
)
def download(collection_id: COLLECTION, request: Request) -> Response:
    st = state_of(request)
    data, name = _service(st).bundle(_actor(request, st), collection_id, client=client_of(request))
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/submissions/{submission_id}/diagnostics",
    response_model=DiagnosticCollectionOut,
    status_code=201,
    summary="Attach diagnostics to a submission",
    description="Attaches a draft collection to a submission the caller may see, with their explicit consent (`consent` "
    "must be true). Items left out of `include` are deleted, not kept. It is refused when the collection is not the "
    "caller's, or has expired.",
    dependencies=[Depends(require_user)],
)
def attach(submission_id: SUBMISSION, body: AttachDiagnosticsIn, request: Request) -> DiagnosticCollectionOut:
    st = state_of(request)
    return _service(st).attach(_actor(request, st), submission_id, body, client=client_of(request))


@router.get(
    "/submissions/{submission_id}/diagnostics",
    response_model=list[DiagnosticCollectionOut],
    summary="The diagnostics attached to a submission",
    description="The collections attached to the submission, with what each holds and what was masked. A reporter sees "
    "what they attached; a maintainer sees what was attached to any submission they may see.",
    dependencies=[Depends(require_user)],
)
def attached(submission_id: SUBMISSION, request: Request) -> list[DiagnosticCollectionOut]:
    st = state_of(request)
    return _service(st).of_submission(_actor(request, st), submission_id, client=client_of(request))
