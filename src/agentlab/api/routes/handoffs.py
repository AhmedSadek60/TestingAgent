"""Engineering handoff: a sanitized package of a submission, for an authorised engineer to take."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, Response

from agentlab.api.routes.common import RESPONSES, client_of, current_principal, require_staff
from agentlab.api.state import ApiState, state_of
from agentlab.core.errors import UserError
from agentlab.feedback import Actor, actor_for
from agentlab.feedback.handoff import HandoffService
from agentlab.feedback.models import HandoffIn, HandoffOut

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
HANDOFF = Annotated[
    str, Path(min_length=8, max_length=40, pattern=r"^[A-Za-z0-9\-]+$", description="Id of a handoff package")
]
NOTHING_RUNS = "Nothing here runs Claude Code, changes a repository or sends the package anywhere: it only builds a file for a person to take."


def _service(st: ApiState) -> HandoffService:
    if st.handoffs is None:
        raise UserError("engineering handoffs are not available on this server")
    return st.handoffs


def _actor(request: Request, st: ApiState) -> Actor:
    return actor_for(current_principal(request), st.services.config.feedback)


@router.post(
    "/submissions/{submission_id}/handoffs/preview",
    response_model=HandoffOut,
    summary="Preview an engineering handoff",
    description="Every file the package would hold with its hash, what is left out and why, the warnings, and the text of "
    "`CLAUDE_CODE_TASK.md`, without building or keeping anything. Maintainers only. " + NOTHING_RUNS,
    dependencies=[Depends(require_staff)],
)
def preview(submission_id: SUBMISSION, body: HandoffIn, request: Request) -> HandoffOut:
    st = state_of(request)
    return _service(st).preview(_actor(request, st), submission_id, body, client=client_of(request))


@router.post(
    "/submissions/{submission_id}/handoffs",
    response_model=HandoffOut,
    status_code=201,
    summary="Build an engineering handoff",
    description="Builds the package: the structured record, the reproduction, the masked diagnostics, the attachments that "
    "may go in, context from the run the report names, a masking report and a privacy summary, a README, a manifest of "
    "SHA-256 hashes, and `CLAUDE_CODE_TASK.md` for Claude Code. Only what is available and authorised is put in, and what is "
    "not is listed with the reason. The export is recorded. Maintainers only. " + NOTHING_RUNS,
    dependencies=[Depends(require_staff)],
)
def generate(submission_id: SUBMISSION, body: HandoffIn, request: Request) -> HandoffOut:
    st = state_of(request)
    return _service(st).generate(_actor(request, st), submission_id, body, client=client_of(request))


@router.get(
    "/submissions/{submission_id}/handoffs",
    response_model=list[HandoffOut],
    summary="The handoffs built from a submission",
    description="The packages built from this submission, newest first, each with what it holds, what was left out and its "
    "task file. Maintainers only.",
    dependencies=[Depends(require_staff)],
)
def of_submission(submission_id: SUBMISSION, request: Request) -> list[HandoffOut]:
    st = state_of(request)
    return _service(st).of_submission(_actor(request, st), submission_id, client=client_of(request))


@router.get(
    "/handoffs/{handoff_id}",
    response_model=HandoffOut,
    summary="Read a handoff",
    description="What a built package holds, what was left out, and the task text. Maintainers only, and only for a "
    "submission they may see.",
    dependencies=[Depends(require_staff)],
)
def read(handoff_id: HANDOFF, request: Request) -> HandoffOut:
    st = state_of(request)
    return _service(st).get(_actor(request, st), handoff_id)


def _download(request: Request, handoff_id: str, *, task_only: bool) -> Response:
    st = state_of(request)
    data, name, media = _service(st).download(
        _actor(request, st), handoff_id, task_only=task_only, client=client_of(request)
    )
    return Response(
        content=data,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/handoffs/{handoff_id}/download",
    summary="Download an engineering handoff as an archive",
    description="The ZIP. Every download is recorded in the submission's audit trail. Maintainers only.",
    response_class=Response,
    responses={200: {"content": {"application/zip": {}}, "description": "The archive"}},
    dependencies=[Depends(require_staff)],
)
def download(handoff_id: HANDOFF, request: Request) -> Response:
    return _download(request, handoff_id, task_only=False)


@router.get(
    "/handoffs/{handoff_id}/task",
    summary="Download CLAUDE_CODE_TASK.md on its own",
    description="The task file as Markdown, without the rest of the package. Recorded like the archive. Maintainers only.",
    response_class=Response,
    responses={200: {"content": {"text/markdown": {}}, "description": "The task file"}},
    dependencies=[Depends(require_staff)],
)
def task(handoff_id: HANDOFF, request: Request) -> Response:
    return _download(request, handoff_id, task_only=True)
