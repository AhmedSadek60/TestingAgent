"""Publishing a submission to an issue tracker: what is available, what would be sent, and sending it on purpose."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from starlette.concurrency import run_in_threadpool

from agentlab.api.routes.common import RESPONSES, client_of, current_principal, require_staff
from agentlab.api.schemas import ErrorResponse
from agentlab.api.state import ApiState, state_of
from agentlab.core.errors import UserError
from agentlab.feedback import Actor, actor_for
from agentlab.feedback.models import (
    IntegrationOut,
    PublicationOut,
    PublicationPreview,
    PublicationPreviewIn,
    PublishIn,
)
from agentlab.feedback.publication import PublicationService

router = APIRouter(
    responses={
        **RESPONSES,
        409: {
            "model": ErrorResponse,
            "description": "What would be published is not what was previewed, or a publication is in progress",
        },
        429: {
            "model": ErrorResponse,
            "description": "The tracker's rate limit was reached; `Retry-After` says when to try again",
        },
    },
    tags=["Feedback & Support"],
)

SUBMISSION = Annotated[
    str,
    Path(
        min_length=3,
        max_length=40,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:\-]*$",
        description="The submission's id, or its number such as `FR-2026-00042` or `IR-2026-00042`",
    ),
]
PROVIDER = Annotated[str, Path(pattern=r"^[a-z]{2,20}$", description="The issue tracker: `github`")]
NOTHING_ELSE = (
    "Only a sanitized summary is sent: never files, diagnostic information, handoff packages, the reporter's name, internal "
    "notes or the maintainers' ratings, and a private security report is never published."
)


def _service(st: ApiState) -> PublicationService:
    if st.publications is None:
        raise UserError("publishing is not available on this server")
    return st.publications


def _actor(request: Request, st: ApiState) -> Actor:
    return actor_for(current_principal(request), st.services.config.feedback)


@router.get(
    "/integrations",
    response_model=list[IntegrationOut],
    summary="The issue trackers a submission can be published to",
    description="Each tracker the server knows about, whether the owner switched it on, why not when it is not ready, and "
    "whether the caller's role may publish. Nothing is ever sent to a tracker that is off. Maintainers only.",
    dependencies=[Depends(require_staff)],
)
def integrations(request: Request) -> list[IntegrationOut]:
    st = state_of(request)
    return _service(st).integrations(_actor(request, st))


@router.post(
    "/submissions/{submission_id}/publications/preview",
    response_model=PublicationPreview,
    summary="Preview a publication",
    description="Exactly the title, text, labels and assignee that would be sent, what is deliberately left out, the "
    "warnings, and the `content_hash` to send back to publish exactly this. Nothing is sent. " + NOTHING_ELSE,
    dependencies=[Depends(require_staff)],
)
async def preview(submission_id: SUBMISSION, body: PublicationPreviewIn, request: Request) -> PublicationPreview:
    st = state_of(request)
    return await run_in_threadpool(
        _service(st).preview, _actor(request, st), submission_id, body.provider, client=client_of(request)
    )


@router.post(
    "/submissions/{submission_id}/publications",
    response_model=PublicationOut,
    status_code=201,
    summary="Publish a submission",
    description="Makes an issue in the configured repository from what was previewed. The `content_hash` of the preview must "
    "come back, so that nothing is sent that was not read. A retry cannot make a second issue: a publication that exists "
    "is returned, and one that failed is retried with the same key. The result carries the issue's address. "
    + NOTHING_ELSE,
    dependencies=[Depends(require_staff)],
)
async def publish(submission_id: SUBMISSION, body: PublishIn, request: Request) -> PublicationOut:
    st = state_of(request)
    return await run_in_threadpool(
        _service(st).publish, _actor(request, st), submission_id, body, client=client_of(request)
    )


@router.get(
    "/submissions/{submission_id}/publications",
    response_model=list[PublicationOut],
    summary="Where a submission was published",
    description="The issues made from this submission, with their state and address. Maintainers only.",
    dependencies=[Depends(require_staff)],
)
async def publications(submission_id: SUBMISSION, request: Request) -> list[PublicationOut]:
    st = state_of(request)
    return await run_in_threadpool(
        _service(st).publications, _actor(request, st), submission_id, client=client_of(request)
    )


@router.post(
    "/submissions/{submission_id}/publications/{provider}/sync",
    response_model=PublicationOut,
    summary="Push a submission's status to its issue",
    description="Comments the submission's status on the issue and closes the issue when the submission is closed. Only when "
    "the owner switched `status_sync` on, only when a maintainer asks, and only with the status's name: no text of the "
    "submission is sent.",
    dependencies=[Depends(require_staff)],
)
async def sync(submission_id: SUBMISSION, provider: PROVIDER, request: Request) -> PublicationOut:
    st = state_of(request)
    return await run_in_threadpool(
        _service(st).sync_status, _actor(request, st), submission_id, provider, client=client_of(request)
    )
