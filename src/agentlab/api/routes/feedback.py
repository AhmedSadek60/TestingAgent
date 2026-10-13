"""Feedback & Support: feature requests and issue reports.

Who may see what is decided in ``agentlab.feedback.policy`` and applied to the query; a route only says who the caller is."""

from __future__ import annotations

import re
import urllib.parse
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, Request, Response, UploadFile
from starlette.concurrency import run_in_threadpool

from agentlab.api.routes.common import RESPONSES, client_of, current_principal, require_staff, require_user
from agentlab.api.schemas import ErrorResponse
from agentlab.api.state import ApiState, state_of
from agentlab.core.errors import UserError
from agentlab.feedback import (
    Actor,
    CommentIn,
    RelationIn,
    SubmissionFilter,
    SubmissionIn,
    SubmissionPatch,
    SubmissionService,
    actor_for,
)
from agentlab.feedback.attachments import AttachmentService
from agentlab.feedback.models import (
    AssigneeOut,
    AttachmentOut,
    AttachmentPreview,
    AuditEventOut,
    CommentOut,
    DeletionOut,
    FeedbackOptions,
    SimilarOut,
    StatusChangeOut,
    SubmissionCounts,
    SubmissionDetail,
    SubmissionPage,
)

router = APIRouter(
    responses={
        **RESPONSES,
        409: {"model": ErrorResponse, "description": "The submission was changed by someone else since it was read"},
    },
    tags=["Feedback & Support"],
)

SUBMISSION = Annotated[
    str,
    Path(
        min_length=3,
        max_length=40,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:\-]*$",
        description="The submission's id, or its number such as `FR-2026-00042` (feature) or `IR-2026-00042` (issue)",
    ),
]


def _service(st: ApiState) -> SubmissionService:
    if st.feedback is None:  # only a server built without a database could be here
        raise UserError("feedback is not available on this server")
    return st.feedback


def _actor(request: Request, st: ApiState) -> Actor:
    return actor_for(current_principal(request), st.services.config.feedback)


@router.get(
    "/submissions/options",
    response_model=FeedbackOptions,
    summary="The choices of the forms",
    description="Categories, workflow statuses, priorities and limits, as this server is configured, and whether the "
    "caller may triage. The forms and the maintainer queue read this instead of hard-coding the lists.",
    dependencies=[Depends(require_user)],
)
def options(request: Request) -> FeedbackOptions:
    st = state_of(request)
    return _service(st).options(_actor(request, st))


@router.get(
    "/submissions/summary",
    response_model=SubmissionCounts,
    summary="Counts of the submissions the caller can see",
    description="Totals by status and type, how many are open, and how many of the caller's own have an answer they have "
    "not seen. A reporter counts their own; a maintainer counts every submission they may see.",
    dependencies=[Depends(require_user)],
)
def summary(request: Request) -> SubmissionCounts:
    st = state_of(request)
    return _service(st).summary(_actor(request, st))


@router.get(
    "/submissions/similar",
    response_model=list[SimilarOut],
    summary="Submissions that look like a title",
    description="Suggests submissions with similar words in the title, to avoid filing the same thing twice. It is a "
    "suggestion and never blocks anything. Only submissions the caller may see are considered: for a reporter, their own.",
    dependencies=[Depends(require_user)],
)
def similar(
    request: Request,
    title: Annotated[str, Query(min_length=3, max_length=200, description="The title being typed")],
    type_: Annotated[
        Literal["feature", "issue"], Query(alias="type", description="Compare with feature requests or issue reports")
    ],
    category: Annotated[str | None, Query(max_length=80, description="Prefer the same category")] = None,
) -> list[SimilarOut]:
    st = state_of(request)
    return _service(st).similar(_actor(request, st), type_, title, category=category)


@router.get(
    "/submissions/assignees",
    response_model=list[AssigneeOut],
    summary="People a submission can be assigned to",
    description="Active maintainer and admin accounts. Maintainers only.",
    dependencies=[Depends(require_staff)],
)
def assignees(request: Request) -> list[AssigneeOut]:
    st = state_of(request)
    return _service(st).assignees(_actor(request, st))


@router.post(
    "/submissions",
    response_model=SubmissionDetail,
    status_code=201,
    summary="Make a feature request or an issue report",
    description="Records the submission with a number (`FR-2026-00042` or `IR-2026-00042`) and the first status of its "
    "workflow, and answers with it only after it has been stored. A person with the role reporter may make a limited number "
    "a day (`feedback.max_submissions_per_day`); a category of suspected security issues, or `suspected_security`, keeps "
    "it private to the reporter and the maintainers. Anything that looks like a secret in the text is masked before it is "
    "stored.",
    dependencies=[Depends(require_user)],
)
def create(body: SubmissionIn, request: Request) -> SubmissionDetail:
    st = state_of(request)
    return _service(st).create(_actor(request, st), body, client=client_of(request))


@router.get(
    "/submissions",
    response_model=SubmissionPage,
    summary="List submissions",
    description="The submissions the caller may see, newest activity first. A reporter's list is their own; filtering or "
    "sorting by what a reporter is not told (priority, severity, assignee, reporter, label, visibility) is refused to "
    "them. Text search looks at the title and description.",
    dependencies=[Depends(require_user)],
)
def search(
    request: Request,
    type_: Annotated[Literal["feature", "issue"] | None, Query(alias="type", description="Only this type")] = None,
    status: Annotated[
        list[str] | None, Query(max_length=20, description="Only these statuses (repeat the parameter)")
    ] = None,
    category: Annotated[str | None, Query(max_length=80, description="Only this category")] = None,
    requested_priority: Annotated[
        Literal["low", "normal", "high", "critical_business_need"] | None,
        Query(description="Only this priority as the reporter asked for it (feature requests)"),
    ] = None,
    project: Annotated[str | None, Query(max_length=64, description="Only this project (id)")] = None,
    version: Annotated[str | None, Query(max_length=40, description="Only reports of this AgentLab version")] = None,
    run: Annotated[str | None, Query(max_length=64, description="Only submissions about this evaluation run")] = None,
    priority: Annotated[
        Literal["low", "normal", "high", "critical"] | None,
        Query(description="Only this assigned priority (maintainers)"),
    ] = None,
    severity: Annotated[
        Literal["info", "low", "medium", "high", "critical"] | None,
        Query(description="Only this severity (maintainers)"),
    ] = None,
    assignee: Annotated[
        str | None,
        Query(max_length=36, description="An account id, `me` or `none` (maintainers)"),
    ] = None,
    reporter: Annotated[str | None, Query(max_length=36, description="An account id (maintainers)")] = None,
    label: Annotated[str | None, Query(max_length=60, description="Only this label (maintainers)")] = None,
    visibility: Annotated[
        Literal["normal", "security"] | None, Query(description="Only this visibility (maintainers)")
    ] = None,
    q: Annotated[
        str | None,
        Query(
            max_length=200,
            description="Text to find: in the title, the description, what an issue says happened, or an exact run, test, trace, result or report id",
        ),
    ] = None,
    mine: Annotated[bool, Query(description="Only the submissions the caller made")] = False,
    unread: Annotated[bool, Query(description="Only the caller's own that have an answer they have not seen")] = False,
    created_from: Annotated[datetime | None, Query(description="Made at or after this time")] = None,
    created_to: Annotated[datetime | None, Query(description="Made at or before this time")] = None,
    sort: Annotated[
        Literal["activity", "created", "priority"],
        Query(description="activity: last change; created: when made; priority: assigned priority (maintainers)"),
    ] = "activity",
    order: Annotated[
        Literal["desc", "asc"], Query(description="Newest or highest first (desc) or last (asc)")
    ] = "desc",
    limit: Annotated[int, Query(ge=1, le=200, description="Page size")] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000, description="How many to skip")] = 0,
) -> SubmissionPage:
    st = state_of(request)
    return _service(st).search(
        _actor(request, st),
        SubmissionFilter(
            type=type_,
            statuses=status or [],
            category=category,
            requested_priority=requested_priority,
            project=project,
            version=version,
            run=run,
            priority=priority,
            severity=severity,
            assignee=assignee,
            reporter=reporter,
            label=label,
            visibility=visibility,
            q=q,
            mine=mine,
            unread=unread,
            created_from=created_from,
            created_to=created_to,
            sort=sort,
            descending=order == "desc",
            limit=limit,
            offset=offset,
        ),
    )


@router.get(
    "/submissions/{submission_id}",
    response_model=SubmissionDetail,
    summary="Read a submission",
    description="The submission with its discussion and status history. A reporter gets the public discussion and nothing "
    "that says who the maintainers are; a maintainer also gets the internal notes, labels, priority and links. A "
    "submission the caller may not see is answered as not found. A maintainer opening someone else's private "
    "submission is recorded.",
    dependencies=[Depends(require_user)],
)
def read(submission_id: SUBMISSION, request: Request) -> SubmissionDetail:
    st = state_of(request)
    return _service(st).get(_actor(request, st), submission_id, client=client_of(request))


@router.patch(
    "/submissions/{submission_id}",
    response_model=SubmissionDetail,
    summary="Triage a submission",
    description="Changes the status (the reporter sees the change and its comment), priority, severity, assignee, category, "
    "labels or visibility; what is sent is applied together. Send back the `version` that was read: if someone else "
    "changed it since, this answers 409 and changes nothing. Maintainers only.",
    dependencies=[Depends(require_staff)],
)
def triage(submission_id: SUBMISSION, body: SubmissionPatch, request: Request) -> SubmissionDetail:
    st = state_of(request)
    return _service(st).patch(_actor(request, st), submission_id, body, client=client_of(request))


@router.post(
    "/submissions/{submission_id}/comments",
    response_model=CommentOut,
    status_code=201,
    summary="Comment on a submission",
    description="Adds a line to the discussion. A reporter may comment on their own submission, publicly. A maintainer may "
    "also write an internal note (never shown to the reporter) or ask the reporter for information.",
    dependencies=[Depends(require_user)],
)
def comment(submission_id: SUBMISSION, body: CommentIn, request: Request) -> CommentOut:
    st = state_of(request)
    return _service(st).comment(_actor(request, st), submission_id, body, client=client_of(request))


@router.get(
    "/submissions/{submission_id}/comments",
    response_model=list[CommentOut],
    summary="Read the discussion of a submission",
    description="The comments, oldest first. A reporter gets the public ones only, and a maintainer who wrote one reads as "
    '"AgentLab maintainer" to them.',
    dependencies=[Depends(require_user)],
)
def comments(
    submission_id: SUBMISSION,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500, description="How many comments")] = 200,
    offset: Annotated[int, Query(ge=0, le=100_000, description="How many to skip")] = 0,
) -> list[CommentOut]:
    st = state_of(request)
    return _service(st).comments(
        _actor(request, st), submission_id, limit=limit, offset=offset, client=client_of(request)
    )


@router.get(
    "/submissions/{submission_id}/history",
    response_model=list[StatusChangeOut],
    summary="Read the status history of a submission",
    description="Every status change, oldest first, with who made it, when, and the comment that went with it. Nothing is "
    "ever overwritten.",
    dependencies=[Depends(require_user)],
)
def history(submission_id: SUBMISSION, request: Request) -> list[StatusChangeOut]:
    st = state_of(request)
    return _service(st).history(_actor(request, st), submission_id, client=client_of(request))


@router.post(
    "/submissions/{submission_id}/seen",
    status_code=204,
    summary="Mark the answers on a submission as seen",
    description="Records that the reporter has read what the maintainers wrote, which clears `unread`. For anyone but "
    "the reporter it does nothing.",
    dependencies=[Depends(require_user)],
)
def seen(submission_id: SUBMISSION, request: Request) -> Response:
    st = state_of(request)
    _service(st).mark_seen(_actor(request, st), submission_id, client=client_of(request))
    return Response(status_code=204)


@router.post(
    "/submissions/{submission_id}/relations",
    response_model=SubmissionDetail,
    status_code=201,
    summary="Link two submissions",
    description="Says that this submission duplicates, relates to, is caused by or implements another. Maintainers only; "
    "both must be ones the caller may see.",
    dependencies=[Depends(require_staff)],
)
def relate(submission_id: SUBMISSION, body: RelationIn, request: Request) -> SubmissionDetail:
    st = state_of(request)
    return _service(st).relate(_actor(request, st), submission_id, body, client=client_of(request))


@router.delete(
    "/submissions/{submission_id}/relations/{relation_id}",
    response_model=SubmissionDetail,
    summary="Remove a link",
    description="Removes a link between two submissions. Maintainers only.",
    dependencies=[Depends(require_staff)],
)
def unrelate(
    submission_id: SUBMISSION,
    relation_id: Annotated[str, Path(min_length=3, max_length=36, description="Id of a link, from the submission")],
    request: Request,
) -> SubmissionDetail:
    st = state_of(request)
    return _service(st).unrelate(_actor(request, st), submission_id, relation_id, client=client_of(request))


@router.get(
    "/submissions/{submission_id}/audit",
    response_model=list[AuditEventOut],
    summary="The audit trail of a submission",
    description="What was done to a submission and by whom, newest first: made, status changes, edits, comments, links, "
    "and who opened it when it is private. It never holds the text of anything. Maintainers only.",
    dependencies=[Depends(require_staff)],
)
def audit(
    submission_id: SUBMISSION,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500, description="How many events")] = 200,
) -> list[AuditEventOut]:
    st = state_of(request)
    return _service(st).audit_trail(_actor(request, st), submission_id, limit=limit)


# ================================================================================================= attachments
ATTACHMENT = Annotated[
    str,
    Path(
        min_length=8, max_length=40, pattern=r"^[A-Za-z0-9\-]+$", description="Id of an attachment, from the submission"
    ),
]


def _attachments(st: ApiState) -> AttachmentService:
    if st.attachments is None:
        raise UserError("attachments are not available on this server")
    return st.attachments


def _content_disposition(name: str) -> str:
    """An attachment header that cannot be broken out of: an ASCII fallback, and the real name percent-encoded."""
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name.encode("ascii", "ignore").decode()).strip("._") or "attachment"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{urllib.parse.quote(name, safe='')}"


@router.post(
    "/submissions/{submission_id}/attachments",
    response_model=AttachmentOut,
    status_code=201,
    summary="Attach a file to a submission",
    description="One file per request, as `multipart/form-data`. The type is decided by looking at the file (its signature, "
    "its text, an archive's directory), not by its name or by what the browser says; the file is kept privately under a name "
    "made from its hash; a text file is also given a sanitized copy with secrets masked, and the original is kept as it "
    "was. A malware scan runs only if the owner configured a scanner, and the answer says which. Limits are in "
    "`feedback.attachments`.",
    dependencies=[Depends(require_user)],
)
async def attach(
    submission_id: SUBMISSION,
    request: Request,
    file: Annotated[UploadFile, File(description="The file to attach")],
    visibility: Annotated[
        Literal["public", "internal"],
        Form(description="internal: only maintainers can see the file (a reporter may not choose it)"),
    ] = "public",
) -> AttachmentOut:
    st = state_of(request)
    cfg = st.services.config.feedback.attachments
    limit = cfg.max_file_mb * 1024 * 1024
    received = bytearray()
    while chunk := await file.read(1024 * 1024):
        received += chunk
        if len(received) > limit:
            raise HTTPException(413, f"the file is larger than {cfg.max_file_mb} MB (feedback.attachments.max_file_mb)")
    return await run_in_threadpool(
        _attachments(st).add,
        _actor(request, st),
        submission_id,
        filename=file.filename or "",
        declared_type=file.content_type or "",
        data=bytes(received),
        visibility=visibility,
        client=client_of(request),
    )


@router.get(
    "/submissions/{submission_id}/attachments",
    response_model=list[AttachmentOut],
    summary="List the attachments of a submission",
    description="What is attached, with what each file was found to be, whether it was scanned, and what the check for "
    "secrets found. A reporter does not see files that maintainers attached for their own use.",
    dependencies=[Depends(require_user)],
)
def attachments(submission_id: SUBMISSION, request: Request) -> list[AttachmentOut]:
    st = state_of(request)
    return _attachments(st).of_submission(_actor(request, st), submission_id, client=client_of(request))


@router.get(
    "/submissions/{submission_id}/attachments/{attachment_id}",
    response_model=AttachmentOut,
    summary="Read the details of an attachment",
    description="The same record the list gives, for one file.",
    dependencies=[Depends(require_user)],
)
def attachment(submission_id: SUBMISSION, attachment_id: ATTACHMENT, request: Request) -> AttachmentOut:
    st = state_of(request)
    return _attachments(st).get(_actor(request, st), submission_id, attachment_id, client=client_of(request))


@router.get(
    "/submissions/{submission_id}/attachments/{attachment_id}/preview",
    response_model=AttachmentPreview,
    summary="Preview an attachment",
    description="For a text file, its first characters with secrets masked. For an image, say so (download it to look at "
    "it). Nothing is rendered by the server.",
    dependencies=[Depends(require_user)],
)
def preview(submission_id: SUBMISSION, attachment_id: ATTACHMENT, request: Request) -> AttachmentPreview:
    st = state_of(request)
    return _attachments(st).preview(_actor(request, st), submission_id, attachment_id, client=client_of(request))


@router.get(
    "/submissions/{submission_id}/attachments/{attachment_id}/download",
    summary="Download an attachment",
    description="The file as it was uploaded, or with `version=sanitized` (a text file) the copy with secrets masked. It "
    "is always sent as a download of unknown type, never to be shown by the browser, and every download is recorded.",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}, "description": "The file's bytes"}},
    dependencies=[Depends(require_user)],
)
def download(
    submission_id: SUBMISSION,
    attachment_id: ATTACHMENT,
    request: Request,
    version: Annotated[
        Literal["original", "sanitized"], Query(description="original: as uploaded; sanitized: secrets masked (text)")
    ] = "original",
) -> Response:
    st = state_of(request)
    data, meta = _attachments(st).download(
        _actor(request, st),
        submission_id,
        attachment_id,
        sanitized=version == "sanitized",
        client=client_of(request),
    )
    name = meta.name if version == "original" else f"{meta.name}.sanitized.txt"
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": _content_disposition(name),
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete(
    "/submissions/{submission_id}/attachments/{attachment_id}",
    status_code=204,
    summary="Remove an attachment",
    description="Takes the file away: it can no longer be listed or downloaded, and its stored bytes are deleted when "
    "nothing else uses them. The record stays in the audit trail. A reporter may remove what they attached; a maintainer "
    "anything.",
    dependencies=[Depends(require_user)],
)
def remove_attachment(submission_id: SUBMISSION, attachment_id: ATTACHMENT, request: Request) -> Response:
    st = state_of(request)
    _attachments(st).remove(_actor(request, st), submission_id, attachment_id, client=client_of(request))
    return Response(status_code=204)


@router.delete(
    "/submissions/{submission_id}",
    response_model=DeletionOut,
    summary="Delete a submission",
    description="Deletes the submission and everything attached to it: its discussion, history, files, diagnostic information "
    "and handoff packages, and the stored files nothing else uses. Administrators only, and the reason is kept in the audit "
    "trail (masked), with counts and never text. A private security report that is not closed is deleted only when "
    "`acknowledge_open_security_report` is true, because its evidence may still be needed. A package that was downloaded "
    "before cannot be recalled: the answer says how many times that happened.",
    dependencies=[Depends(require_staff)],
)
async def delete_submission(
    submission_id: SUBMISSION,
    request: Request,
    reason: Annotated[
        str, Query(min_length=3, max_length=300, description="Why it is being deleted; kept in the audit trail")
    ],
    acknowledge_open_security_report: Annotated[
        bool, Query(description="Required to delete a private security report that is not closed")
    ] = False,
) -> DeletionOut:
    st = state_of(request)
    if st.retention is None:  # only a server built without a database could be here
        raise UserError("deleting submissions is not available on this server")
    return await run_in_threadpool(
        st.retention.delete_submission,
        _actor(request, st),
        submission_id,
        reason=reason,
        acknowledge_open_security_report=acknowledge_open_security_report,
        client=client_of(request),
    )
