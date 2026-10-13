"""What a person sends when they make or change a submission, what they get back, and the fixed choices of the forms.

Every field is documented: these models are the API's contract (``web/openapi.json`` is generated from them)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from agentlab.core.models.base import Model

FEATURE_PRIORITIES = ("low", "normal", "high", "critical_business_need")
PRIORITIES = ("low", "normal", "high", "critical")
SEVERITIES = ("info", "low", "medium", "high", "critical")
FREQUENCIES = ("every_time", "frequently", "occasionally", "once", "unknown")
IMPACTS = (
    "cosmetic",
    "slows_work",
    "prevents_workflow",
    "blocks_project",
    "incorrect_results",
    "data_loss_or_exposure",
    "unavailable",
    "other",
)
RELATION_KINDS = ("duplicate_of", "related", "caused_by", "implements")
COMMENT_KINDS = ("comment", "info_request")
VISIBILITIES = ("normal", "security")

ID = r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,63}$"
#: The longest any one free-text field may be. A fixed limit, not a setting: it is in the API description.
TEXT = 20_000
STAFF_NAME = "AgentLab maintainer"

# C0 control characters other than tab and newline (a NUL byte makes PostgreSQL refuse the whole row), and DEL.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(value: Any) -> Any:
    """A person's text without control characters, with line ends as ``\\n`` and without leading or trailing blanks.

    The words are not changed. Nothing is HTML-escaped here: the text is data, and whatever shows it is responsible for
    showing it as text (the web interface does not render it as markup)."""
    if not isinstance(value, str):
        return value
    return _CONTROL.sub("", value.replace("\r\n", "\n").replace("\r", "\n")).strip()


class _Clean(Model):
    @field_validator("*", mode="before")
    @classmethod
    def _clean(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [clean_text(v) for v in value]
        return clean_text(value)


# ============================================================================================================== input
class FeatureDetailsIn(_Clean):
    """What a feature request says beyond its title and description."""

    problem_statement: str = Field(
        min_length=1, max_length=TEXT, description="The problem or opportunity this addresses"
    )
    expected_behavior: str = Field(
        default="", max_length=TEXT, description="What the person should be able to do afterwards"
    )
    primary_use_case: str = Field(
        default="", max_length=TEXT, description="The main situation in which it would be used"
    )
    why_important: str = Field(default="", max_length=TEXT, description="Why it matters")
    users_affected: str = Field(default="", max_length=TEXT, description="Who is affected: people, teams")
    business_impact: str = Field(default="", max_length=TEXT, description="What it is worth, or what its absence costs")
    use_frequency: str = Field(default="", max_length=120, description="How often the use case comes up")
    current_workaround: str = Field(default="", max_length=TEXT, description="What is done today instead")
    proposed_solution: str = Field(default="", max_length=TEXT, description="An idea of how it could work")
    acceptance_criteria: str = Field(
        default="", max_length=TEXT, description="How to tell it is done, as measurable statements when possible"
    )
    related_area: str = Field(default="", max_length=200, description="The page or feature of AgentLab it concerns")
    dependencies: str = Field(default="", max_length=TEXT, description="What it depends on")
    requested_priority: Literal["low", "normal", "high", "critical_business_need"] = Field(
        default="normal",
        description="The business importance the person asks for. The maintainers set their own priority when they triage",
    )


class IssueDetailsIn(_Clean):
    """What an issue report says beyond its title and description."""

    impact_summary: str = Field(
        min_length=1, max_length=TEXT, description="A brief summary of how the issue affects the work"
    )
    expected_behavior: str = Field(default="", max_length=TEXT, description="What AgentLab should have done")
    actual_behavior: str = Field(default="", max_length=TEXT, description="What AgentLab did instead")
    reproduction_steps: str = Field(default="", max_length=TEXT, description="Steps to reproduce, if known")
    not_reproducible: bool = Field(
        default=False, description="True when the person cannot say how to make it happen again"
    )
    frequency: Literal["every_time", "frequently", "occasionally", "once", "unknown"] = Field(
        default="unknown", description="How often it happens"
    )
    first_observed_at: datetime | None = Field(default=None, description="Approximately when it was first seen")
    consistent: bool | None = Field(default=None, description="Whether it happens consistently")
    blocks_work: bool | None = Field(default=None, description="Whether it blocks the person's work")
    workaround: str = Field(default="", max_length=TEXT, description="A way around it, if there is one")
    reported_impact: Literal[
        "cosmetic",
        "slows_work",
        "prevents_workflow",
        "blocks_project",
        "incorrect_results",
        "data_loss_or_exposure",
        "unavailable",
        "other",
    ] = Field(
        default="other",
        description="How the person says it affects their work. The maintainers decide the severity when they triage",
    )
    ui_screen: str = Field(default="", max_length=200, description="The screen it happened on")
    worker_job_id: str | None = Field(default=None, pattern=ID, description="The background job it concerns")
    provider_config_id: str | None = Field(
        default=None, pattern=ID, description="The provider configuration it concerns"
    )


class SubmissionIn(_Clean):
    """A feature request or an issue report."""

    type: Literal["feature", "issue"] = Field(description="feature: a request for something new; issue: a problem")
    title: str = Field(min_length=3, max_length=200, description="A short title")
    description: str = Field(default="", max_length=TEXT, description="The description, in the person's own words")
    category: str = Field(
        min_length=1, max_length=80, description="One of the categories the server offers for this type"
    )
    feature: FeatureDetailsIn | None = Field(default=None, description="The details of a feature request")
    issue: IssueDetailsIn | None = Field(default=None, description="The details of an issue report")
    project_id: str | None = Field(default=None, pattern=ID, description="The AgentLab project it concerns")
    related_run_id: str | None = Field(default=None, pattern=ID, description="The evaluation run it concerns")
    related_test_id: str | None = Field(default=None, pattern=ID, description="The test case it concerns")
    related_trace_id: str | None = Field(default=None, pattern=ID, description="The trace it concerns")
    related_result_id: str | None = Field(default=None, pattern=ID, description="The test result it concerns")
    related_report_id: str | None = Field(default=None, pattern=ID, description="The report it concerns")
    related_target_id: str | None = Field(default=None, pattern=ID, description="The target it concerns")
    app_version: str | None = Field(
        default=None, max_length=40, description="The AgentLab version the person was using"
    )
    build: str | None = Field(default=None, max_length=80, description="The build or commit identifier, when known")
    suspected_security: bool = Field(
        default=False, description="True when the person believes this is a security problem: it is then kept private"
    )

    @model_validator(mode="after")
    def _details_match_the_type(self) -> SubmissionIn:
        if self.type == "feature" and (self.feature is None or self.issue is not None):
            raise ValueError("a feature request carries `feature` details and no `issue` details")
        if self.type == "issue" and (self.issue is None or self.feature is not None):
            raise ValueError("an issue report carries `issue` details and no `feature` details")
        if self.type == "issue" and not self.description:
            raise ValueError("an issue report needs a description of the problem")
        return self


LABEL = re.compile(r"^[^\W_][\w .:/+-]{0,58}[^\W_]$|^[^\W_]$")


class SubmissionPatch(_Clean):
    """What a maintainer may change when triaging. Everything but ``version`` is optional; what is sent is applied together."""

    version: int = Field(
        ge=1, description="The version of the submission the caller read: a stale one is refused with 409"
    )
    status: str | None = Field(default=None, max_length=40, description="The new status, one of the configured ones")
    status_comment: str = Field(
        default="", max_length=TEXT, description="A comment kept with the status change; the reporter sees it"
    )
    assigned_priority: Literal["low", "normal", "high", "critical"] | None = Field(
        default=None,
        description="The maintainers' priority, separate from the one the person requested; null clears it",
    )
    severity: Literal["info", "low", "medium", "high", "critical"] | None = Field(
        default=None, description="The severity the maintainers decided on; null clears it"
    )
    assignee_id: str | None = Field(
        default=None,
        max_length=36,
        description="The id of the account to assign it to; null or an empty string unassigns",
    )
    category: str | None = Field(default=None, max_length=80, description="A different category")
    labels: list[str] | None = Field(
        default=None, max_length=30, description="The complete set of labels (replaces the old set)"
    )
    visibility: Literal["normal", "security"] | None = Field(
        default=None, description="security: only the reporter and authorised maintainers see it"
    )

    @field_validator("labels")
    @classmethod
    def _labels(cls, labels: list[str] | None) -> list[str] | None:
        if labels is None:
            return None
        seen: dict[str, str] = {}
        for label in labels:
            if not LABEL.match(label):
                raise ValueError(
                    f"label {label!r} is not allowed: 1 to 60 letters, digits, spaces and . : / + - _, "
                    "starting and ending with a letter or digit"
                )
            seen.setdefault(label.casefold(), label)
        return list(seen.values())


class CommentIn(_Clean):
    """A line of the discussion."""

    body: str = Field(min_length=1, max_length=TEXT, description="The comment")
    visibility: Literal["public", "internal"] = Field(
        default="public",
        description="public: the reporter sees it; internal: maintainers only (a reporter may not write one)",
    )
    kind: Literal["comment", "info_request", "reproduction_request"] = Field(
        default="comment",
        description="info_request asks the reporter for more information; reproduction_request asks them to try again "
        "and report what happens (both maintainers only, and always public)",
    )

    @model_validator(mode="after")
    def _a_request_is_public(self) -> CommentIn:
        if self.kind != "comment" and self.visibility != "public":
            raise ValueError("a request is addressed to the reporter, so it is public")
        return self


class RelationIn(_Clean):
    """A link between two submissions."""

    to: str = Field(
        min_length=3, max_length=40, description="The other submission: its id or its number (FR-2026-00042)"
    )
    kind: Literal["duplicate_of", "related", "caused_by", "implements"] = Field(
        description="duplicate_of: this repeats the other; related; caused_by: the other is the cause; implements: this "
        "feature request is delivered by the other"
    )


# ============================================================================================================= output
class AssigneeOut(Model):
    """A person a submission is assigned to."""

    id: str = Field(description="The account's id")
    name: str = Field(description="The account's display name")


class FeatureDetailsOut(Model):
    """The details of a feature request, as stored."""

    problem_statement: str = Field(description="The problem or opportunity this addresses")
    expected_behavior: str = Field(description="What the person should be able to do afterwards")
    primary_use_case: str = Field(description="The main situation in which it would be used")
    why_important: str = Field(description="Why it matters")
    users_affected: str = Field(description="Who is affected")
    business_impact: str = Field(description="What it is worth, or what its absence costs")
    use_frequency: str = Field(description="How often the use case comes up")
    current_workaround: str = Field(description="What is done today instead")
    proposed_solution: str = Field(description="An idea of how it could work")
    acceptance_criteria: str = Field(description="How to tell it is done")
    related_area: str = Field(description="The page or feature of AgentLab it concerns")
    dependencies: str = Field(description="What it depends on")


class IssueDetailsOut(Model):
    """The details of an issue report, as stored."""

    impact_summary: str = Field(description="A brief summary of how the issue affects the work")
    expected_behavior: str = Field(description="What AgentLab should have done")
    actual_behavior: str = Field(description="What AgentLab did instead")
    reproduction_steps: str = Field(description="Steps to reproduce, if known")
    not_reproducible: bool = Field(description="True when the person cannot say how to make it happen again")
    frequency: str = Field(description="How often it happens")
    first_observed_at: datetime | None = Field(description="Approximately when it was first seen")
    consistent: bool | None = Field(description="Whether it happens consistently")
    blocks_work: bool | None = Field(description="Whether it blocks the person's work")
    workaround: str = Field(description="A way around it, if there is one")
    reported_impact: str = Field(description="How the person says it affects their work")
    ui_screen: str = Field(description="The screen it happened on")
    worker_job_id: str | None = Field(description="The background job it concerns")
    provider_config_id: str | None = Field(description="The provider configuration it concerns")


class CommentOut(Model):
    """One line of the discussion, as the caller may see it."""

    id: str = Field(description="The comment's id")
    author: str = Field(
        description=f'Who wrote it. A reporter is never told which maintainer wrote something: it reads "{STAFF_NAME}"'
    )
    author_is_staff: bool = Field(description="True when a maintainer or the shared token wrote it")
    mine: bool = Field(description="True when the caller wrote it")
    visibility: Literal["public", "internal"] = Field(
        description="public: the reporter sees it; internal: maintainers only"
    )
    kind: Literal["comment", "info_request", "reproduction_request"] = Field(description="What sort of line it is")
    body: str = Field(description="The text")
    created_at: datetime = Field(description="When it was written")


class StatusChangeOut(Model):
    """One change of status, kept for good."""

    id: str = Field(description="The change's id")
    old_status: str | None = Field(description="The status before; null for the first one")
    new_status: str = Field(description="The status after")
    changed_by: str = Field(description="Who changed it (a reporter sees the role of a maintainer, not their name)")
    by_staff: bool = Field(description="True when a maintainer or the shared token changed it")
    comment: str = Field(description="The comment kept with the change; the reporter sees it")
    at: datetime = Field(description="When it changed")


class RelationOut(Model):
    """A link from or to another submission the caller may see."""

    id: str = Field(description="The link's id")
    kind: str = Field(description="duplicate_of, related, caused_by or implements")
    direction: Literal["from", "to"] = Field(
        description="from: this submission is the source of the link; to: the other one is"
    )
    other_id: str = Field(description="The other submission's id")
    other_number: str = Field(description="The other submission's number, such as FR-2026-00042")
    other_title: str = Field(description="The other submission's title")
    other_status: str = Field(description="The other submission's status")


class SubmissionSummary(Model):
    """A submission as a list shows it. What the caller is not told is null or empty."""

    id: str = Field(description="The submission's id")
    number: str = Field(description="The submission's number, such as FR-2026-00042 (feature) or IR-2026-00042 (issue)")
    type: Literal["feature", "issue"] = Field(description="feature or issue")
    title: str = Field(description="The title")
    category: str = Field(description="The category")
    status: str = Field(description="The status; the workflow is `GET /submissions/options`")
    visibility: Literal["normal", "security"] = Field(
        description="security: only the reporter and authorised maintainers see it"
    )
    requested_priority: str | None = Field(description="The priority the reporter asked for (feature requests)")
    assigned_priority: str | None = Field(description="The maintainers' priority; null for a reporter, who is not told")
    severity: str | None = Field(description="The maintainers' severity; null for a reporter, who is not told")
    reporter: str = Field(description="Who made it")
    reporter_id: str | None = Field(
        description="The reporter's account id; null for a reporter's own view, and when the shared token made it"
    )
    mine: bool = Field(description="True when the caller made it")
    assignee: AssigneeOut | None = Field(description="Who it is assigned to; null for a reporter, who is not told")
    labels: list[str] = Field(description="The labels; empty for a reporter, who is not told")
    created_at: datetime = Field(description="When it was made")
    updated_at: datetime = Field(description="When it was last changed")
    last_activity_at: datetime = Field(description="The last change, comment or status change")
    unread: bool = Field(
        description="True when a maintainer answered or changed the status since the reporter last looked"
    )
    comments: int = Field(description="How many comments the caller can see")


class SubmissionDetail(SubmissionSummary):
    """A submission with its details, discussion and status history."""

    version: int = Field(description="Send this back with a change; a stale one is refused with 409")
    description: str = Field(description="The description")
    feature: FeatureDetailsOut | None = Field(description="The details of a feature request")
    issue: IssueDetailsOut | None = Field(description="The details of an issue report")
    project_id: str | None = Field(description="The project it concerns")
    related_run_id: str | None = Field(description="The run it concerns")
    related_test_id: str | None = Field(description="The test it concerns")
    related_trace_id: str | None = Field(description="The trace it concerns")
    related_result_id: str | None = Field(description="The test result it concerns")
    related_report_id: str | None = Field(description="The report it concerns")
    related_target_id: str | None = Field(description="The target it concerns")
    app_version: str | None = Field(description="The AgentLab version the person was using")
    build: str | None = Field(description="The build or commit identifier")
    resolved_at: datetime | None = Field(description="When it entered a resolved status")
    closed_at: datetime | None = Field(description="When it entered a closed status")
    discussion: list[CommentOut] = Field(description="The comments, oldest first; a reporter gets the public ones only")
    history: list[StatusChangeOut] = Field(description="Every status change, oldest first")
    relations: list[RelationOut] = Field(
        description="Links to other submissions the caller may see; empty for a reporter"
    )
    statuses: list[str] = Field(description="The statuses this kind of submission moves through")


class SubmissionPage(Model):
    """One page of a list of submissions."""

    items: list[SubmissionSummary] = Field(description="The submissions on this page")
    total: int = Field(description="How many match, over all pages")
    limit: int = Field(description="The page size")
    offset: int = Field(description="How many were skipped")


class SubmissionCounts(Model):
    """Counts of the submissions the caller can see."""

    total: int = Field(description="How many submissions the caller can see")
    open: int = Field(description="How many of them are not in a closed status")
    unread: int = Field(description="How many of the caller's own have an answer they have not seen")
    unassigned: int | None = Field(description="Open ones nobody is assigned to; null for a reporter")
    by_status: dict[str, int] = Field(description="Count per status")
    by_type: dict[str, int] = Field(description="Count per type")


class SimilarOut(Model):
    """A submission whose title shares words with the one being typed."""

    id: str = Field(description="The submission's id")
    number: str = Field(description="The submission's number")
    title: str = Field(description="The title")
    status: str = Field(description="The status")
    score: float = Field(description="0 to 1: how much of the title they share")


class AuditEventOut(Model):
    """One line of a submission's audit trail. It holds fields and statuses, never text."""

    event: str = Field(description="What happened: created, status_changed, updated, commented, related, viewed, ...")
    actor: str = Field(description="Who did it")
    at: datetime = Field(description="When")
    detail: dict[str, Any] = Field(description="Which fields or statuses; never the text itself")


class FeedbackOptions(Model):
    """The choices of the forms and the workflow, as this server is configured."""

    feature_categories: list[str] = Field(description="Categories of a feature request")
    issue_categories: list[str] = Field(description="Categories of an issue report")
    feature_statuses: list[str] = Field(description="The workflow of a feature request, in order")
    issue_statuses: list[str] = Field(description="The workflow of an issue report, in order")
    closed_statuses: list[str] = Field(description="Statuses that close a submission")
    feature_priorities: list[str] = Field(description="What a person can ask for on a feature request")
    priorities: list[str] = Field(description="Priorities a maintainer assigns")
    severities: list[str] = Field(description="Severities a maintainer assigns")
    frequencies: list[str] = Field(description="How often an issue happens")
    impacts: list[str] = Field(description="How an issue can affect the work")
    relation_kinds: list[str] = Field(description="Kinds of link between submissions")
    security_category: str = Field(description="An issue in this category is kept private")
    max_text_chars: int = Field(description="The longest any text field may be")
    can_triage: bool = Field(description="True when the caller is a maintainer and may change status and priority")


# =========================================================================================================== attachments
class SecretsSummary(Model):
    """What the check for secrets in an attached text file found. Values are never shown."""

    status: Literal["not_applicable", "none", "redacted", "needs_review", "not_inspected"] = Field(
        description="not_applicable: not a text file; none: nothing found; redacted: patterns matched and a sanitized copy "
        "was made; needs_review: the second inspection found more, so the file must be reviewed before it is shared outside "
        "AgentLab; not_inspected: the file is too large to inspect"
    )
    counts: dict[str, int] = Field(description="How many of each kind of thing were masked")
    external_sharing_blocked: bool = Field(
        description="True when the file may not leave AgentLab until it has been reviewed"
    )
    note: str = Field(description="What this does and does not tell you")


class ScanSummary(Model):
    """Whether a malware scanner looked at the file. 'not_scanned' means exactly that: no scanner ran."""

    status: Literal["not_scanned", "clean", "infected", "error"] = Field(
        description="not_scanned: no scanner is configured or it did not run; clean: the scanner found nothing; error: "
        "the scanner failed, so the file is not known to be clean"
    )
    detail: str = Field(description="What the scanner said, or why it did not run")


class AttachmentOut(Model):
    """A file attached to a submission."""

    id: str = Field(description="The attachment's id")
    name: str = Field(description="The file name the person gave it, made safe to show")
    extension: str = Field(description="Its type by extension")
    type: str = Field(description="What it was found to be by looking at its content")
    declared_type: str = Field(description="What the browser said it was; not trusted")
    size: int = Field(description="Its size in bytes")
    sha256: str = Field(description="The SHA-256 of the file as it was uploaded")
    visibility: Literal["public", "internal"] = Field(description="internal: maintainers only")
    uploader: str = Field(description="Who attached it (a reporter is not told which maintainer)")
    uploader_is_staff: bool = Field(description="True when a maintainer or the shared token attached it")
    mine: bool = Field(description="True when the caller attached it")
    created_at: datetime = Field(description="When it was attached")
    text_like: bool = Field(description="True for text files, which can be previewed and have a sanitized copy")
    secrets: SecretsSummary = Field(description="The check for secrets in a text file")
    scan: ScanSummary = Field(description="The malware scan")
    archive: dict[str, Any] | None = Field(description="For a ZIP archive or a Word document: what its directory lists")
    sanitized_available: bool = Field(description="True when a sanitized copy can be downloaded with version=sanitized")


class AttachmentPreview(Model):
    """A preview of an attachment, as the caller may see it."""

    kind: Literal["text", "image", "none"] = Field(
        description="text: the sanitized text is in `text`; image: download it and show it; none: no preview exists"
    )
    text: str | None = Field(description="The sanitized text, up to a limit")
    truncated: bool = Field(description="True when the text was cut at the limit")
    sanitized: bool = Field(description="True when secrets were masked in the text shown")
    message: str = Field(description="What to know about this preview")
