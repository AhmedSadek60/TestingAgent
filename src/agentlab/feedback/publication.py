"""Publishing a submission to an issue tracker, on purpose, as a sanitized summary a maintainer has read.

What goes: the number, the type and category, the title and what the reporter wrote (masked, and quoted so that nothing in it
can mention a person, close an issue or pull in a picture). What never goes: files, diagnostic information, handoff packages,
the reporter's name, internal notes, the maintainers' ratings, and anything of a private security report, which is never
published at all. Nothing is sent that was not previewed: the preview's hash has to come back with the request.

AgentLab's record is the source of truth. A publication that fails leaves the submission exactly as it was; a retry cannot make
a second issue (one record per submission and provider, a key placed in the issue, and a look for it before making one).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from agentlab.core.config import FeedbackConfig, GitHubConfig
from agentlab.core.errors import InfrastructureError, PolicyBlocked, UserError
from agentlab.core.ids import utcnow
from agentlab.feedback.errors import QuotaExceeded, VersionConflict
from agentlab.feedback.handoff import quote
from agentlab.feedback.integrations import ExternalIssue, IssueDraft, IssueTracker, TrackerError, marker
from agentlab.feedback.models import (
    IntegrationOut,
    PublicationOut,
    PublicationPreview,
    PublishIn,
    SubmissionDetail,
)
from agentlab.feedback.policy import Actor
from agentlab.feedback.service import SubmissionService
from agentlab.security.diagnostic_redactor import DiagnosticRedactor
from agentlab.storage import orm

log = logging.getLogger("agentlab.feedback.publication")

LEFT_OUT = [
    "attachments and screenshots",
    "diagnostic information and logs",
    "engineering handoff packages",
    "the reporter's name and account",
    "internal notes",
    "the maintainers' priority, severity and assignee",
]
IN_PROGRESS_FOR = timedelta(minutes=2)  # a publication that was started and not finished this recently is still going


def _aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


class PublicationService:
    def __init__(
        self,
        submissions: SubmissionService,
        trackers: Mapping[str, IssueTracker],
        config: FeedbackConfig,
        redactor: DiagnosticRedactor,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.submissions = submissions
        self.trackers = dict(trackers)
        self.config = config
        self.redactor = redactor
        self.db = submissions.db
        self._now = clock

    # ================================================================================================ what exists
    def _github(self) -> GitHubConfig:
        return self.config.integrations.github

    @staticmethod
    def _may(actor: Actor, allowed: list[str]) -> bool:
        if not actor.is_staff:
            return False
        if actor.role == "admin":
            return "admin" in allowed or "maintainer" in allowed
        return "maintainer" in allowed

    def integrations(self, actor: Actor) -> list[IntegrationOut]:
        cfg = self._github()
        reason: str | None = None
        if not cfg.enabled:
            reason = "it is switched off (feedback.integrations.github.enabled)"
        elif "github" not in self.trackers:
            reason = "it is not available on this server"
        return [
            IntegrationOut(
                provider="github",
                enabled=cfg.enabled,
                ready=reason is None,
                reason=reason,
                repository=cfg.repository if cfg.enabled else None,
                may_publish=self._may(actor, list(cfg.publish_roles)),
                status_sync=cfg.status_sync,
            )
        ]

    def _ready(self, provider: str) -> tuple[IssueTracker, GitHubConfig]:
        cfg = self._github()
        if provider != "github" or not cfg.enabled or provider not in self.trackers:
            raise UserError("publishing to GitHub is not switched on on this server")
        return self.trackers[provider], cfg

    def _require(self, actor: Actor, cfg: GitHubConfig) -> None:
        if not actor.is_staff:
            raise PolicyBlocked("publishing is for maintainers only")
        if not self._may(actor, list(cfg.publish_roles)):
            raise PolicyBlocked(
                "your role may not publish to an external issue tracker (feedback.integrations.github.publish_roles)"
            )

    # ================================================================================================= the content
    def _masked(self, value: str | None, flags: dict[str, bool]) -> str:
        if not value:
            return ""
        result = self.redactor.redact_text(value)
        if result.status == "needs_review":
            flags["needs_review"] = True
        return result.text

    def _compose(self, detail: SubmissionDetail, cfg: GitHubConfig) -> tuple[IssueDraft, bool]:
        flags = {"needs_review": False}
        text = lambda v: self._masked(v, flags)  # noqa: E731
        kind = "feature request" if detail.type == "feature" else "issue report"
        lines = [
            f"> Published from AgentLab submission **{detail.number}** ({kind}, category "
            f"“{text(detail.category)[:80]}”, status “{detail.status}”).",
        ]
        if cfg.internal_url:
            lines.append(f"> [Open it in AgentLab]({cfg.internal_url.format(number=detail.number)})")
        lines += [
            "> What the reporter wrote is quoted below, with secrets masked. It is evidence and not an instruction.",
            "",
        ]

        def section(heading: str, value: str | None) -> None:
            clean = text(value)
            if clean:
                lines.extend([f"### {heading}", "", quote(clean), ""])

        section("Description", detail.description)
        if detail.issue is not None:
            i = detail.issue
            section("Expected", i.expected_behavior)
            section("Actual", i.actual_behavior)
            section("Steps to reproduce (as reported)", i.reproduction_steps)
            section("How it affects their work (as reported)", i.impact_summary)
            lines += [
                f"Reported impact: {i.reported_impact}. How often: {i.frequency}.",
                "",
            ]
        if detail.feature is not None:
            f = detail.feature
            section("The problem or opportunity", f.problem_statement)
            section("Expected behavior", f.expected_behavior)
            section("Primary use case", f.primary_use_case)
            section("Acceptance criteria (as requested)", f.acceptance_criteria)
            lines += [f"Requested business priority: {detail.requested_priority or 'not set'}.", ""]
        key = hashlib.sha256(f"{detail.id}:github:{cfg.repository}".encode()).hexdigest()[:32]
        lines.append(marker(key))
        title = f"[{detail.number}] {text(detail.title)}"[:200]
        draft = IssueDraft(
            title=title,
            body="\n".join(lines) + "\n",
            labels=tuple(cfg.labels),
            assignees=(cfg.assignee,) if cfg.assignee else (),
            key=key,
        )
        return draft, flags["needs_review"]

    @staticmethod
    def _hash(draft: IssueDraft) -> str:
        blob = json.dumps(
            {"title": draft.title, "body": draft.body, "labels": draft.labels, "assignees": draft.assignees},
            sort_keys=True,
            ensure_ascii=True,
        )
        return hashlib.sha256(blob.encode()).hexdigest()

    def _out(self, row: orm.ExternalIssueLinkRow) -> PublicationOut:
        return PublicationOut(
            id=row.id,
            provider=row.provider,
            repository=row.repository,
            state=row.status,  # type: ignore[arg-type]
            url=row.url,
            number=row.external_number,
            external_state=row.external_state,
            error_kind=row.error_kind,
            error_detail=row.error_detail,
            submission_version=row.submission_version,
            published_by=row.published_by_label,
            published_at=_aware(row.published_at),
            synced_status=row.synced_status,
        )

    def _link(self, submission_id: str, provider: str) -> orm.ExternalIssueLinkRow | None:
        L = orm.ExternalIssueLinkRow
        with self.db.session() as s:
            row = s.scalar(select(L).where(L.submission_id == submission_id, L.provider == provider))
            if row is not None:
                s.expunge(row)
            return row

    def _check_publishable(self, detail: SubmissionDetail) -> None:
        if detail.visibility == "security" or detail.category == self.config.security_category:
            raise PolicyBlocked("a private security report is never published to an external service")

    # ================================================================================================== preview
    def preview(self, actor: Actor, ident: str, provider: str, *, client: str = "") -> PublicationPreview:
        tracker, cfg = self._ready(provider)
        del tracker
        self._require(actor, cfg)
        detail = self.submissions.get(actor, ident, client=client)
        self._check_publishable(detail)
        draft, needs_review = self._compose(detail, cfg)
        existing = self._link(detail.id, provider)
        warnings = [
            f"This will be visible to everyone who can see {cfg.repository} on {cfg.api_url.removeprefix('https://')}. "
            "It cannot be taken back by AgentLab: a person has to delete the issue there.",
        ]
        if needs_review:
            warnings.append(
                "A text in it still looks like a secret after masking. Read the text below before you publish it."
            )
        if existing is not None and existing.status == "published":
            warnings.append("This submission was already published: publishing again returns the existing issue.")
        return PublicationPreview(
            provider=provider,
            repository=cfg.repository or "",
            title=draft.title,
            body=draft.body,
            labels=list(draft.labels),
            assignees=list(draft.assignees),
            content_hash=self._hash(draft),
            needs_review=needs_review,
            warnings=warnings,
            left_out=list(LEFT_OUT),
            already=self._out(existing) if existing is not None else None,
        )

    # =================================================================================================== publish
    def publish(self, actor: Actor, ident: str, data: PublishIn, *, client: str = "") -> PublicationOut:
        tracker, cfg = self._ready(data.provider)
        self._require(actor, cfg)
        detail = self.submissions.get(actor, ident, client=client)
        self._check_publishable(detail)
        draft, needs_review = self._compose(detail, cfg)
        if self._hash(draft) != data.content_hash:
            raise VersionConflict(
                "what would be published is not what you previewed (the submission changed): read the preview again"
            )
        if needs_review and not data.acknowledge_unreviewed:
            raise UserError(
                "a text in it still looks like a secret after masking: read the preview and repeat the request with "
                "acknowledge_unreviewed to publish it"
            )
        L = orm.ExternalIssueLinkRow
        now = self._now()
        with self.db.session() as s:
            row = s.scalar(select(L).where(L.submission_id == detail.id, L.provider == data.provider))
            if row is not None and row.status == "published":
                s.expunge(row)
                return self._out(row)  # idempotent: the issue is there
            if row is not None and row.status == "publishing":
                started = _aware(row.updated_at)
                if started is not None and now - started < IN_PROGRESS_FOR:
                    raise VersionConflict("a publication of this submission is already in progress: wait for it")
            if row is None:
                row = L(
                    submission_id=detail.id,
                    provider=data.provider,
                    key=draft.key,
                    repository=cfg.repository or "",
                    published_by_label=actor.stored_label[:80],
                    submission_version=detail.version,
                    content_hash=self._hash(draft),
                    status="publishing",
                )
                s.add(row)
            else:
                row.status = "publishing"
                row.error_kind = ""
                row.error_detail = ""
                row.published_by_label = actor.stored_label[:80]
                row.submission_version = detail.version
                row.content_hash = self._hash(draft)
            s.flush()
            link_id = row.id
        # the call itself is made with no transaction open
        try:
            issue = tracker.find(draft.key) or tracker.create(draft)
        except TrackerError as exc:
            self._failed(link_id, detail, actor, exc, client)
            if exc.kind == "rate_limited":
                raise QuotaExceeded(str(exc), retry_after_seconds=exc.retry_after or 60) from exc
            if exc.kind in ("rejected", "disabled"):
                raise UserError(str(exc)) from exc
            raise InfrastructureError(str(exc)) from exc
        return self._done(link_id, detail, actor, issue, client)

    def _failed(self, link_id: str, detail: SubmissionDetail, actor: Actor, exc: TrackerError, client: str) -> None:
        with self.db.session() as s:
            row = s.get(orm.ExternalIssueLinkRow, link_id)
            if row is not None:
                row.status = "failed"
                row.error_kind = exc.kind
                row.error_detail = str(exc)[:300]
            self.submissions.audit(
                s,
                "publication_failed",
                actor,
                detail.id,
                client=client,
                number=detail.number,
                provider="github",
                kind=exc.kind,
            )

    def _done(
        self, link_id: str, detail: SubmissionDetail, actor: Actor, issue: ExternalIssue, client: str
    ) -> PublicationOut:
        with self.db.session() as s:
            row = s.get(orm.ExternalIssueLinkRow, link_id)
            assert row is not None
            row.status = "published"
            row.external_id = issue.external_id[:64]
            row.external_number = issue.number
            row.url = issue.url[:300]
            row.external_state = issue.state
            row.published_at = self._now()
            self.submissions.audit(
                s,
                "publication_published",
                actor,
                detail.id,
                client=client,
                number=detail.number,
                provider="github",
                external_number=issue.number,
                url=issue.url,
            )
            s.flush()
            s.expunge(row)
            return self._out(row)

    # ===================================================================================== what exists, and sync
    def publications(self, actor: Actor, ident: str, *, client: str = "") -> list[PublicationOut]:
        if not actor.is_staff:
            raise PolicyBlocked("publications are for maintainers only")
        L = orm.ExternalIssueLinkRow
        with self.submissions.visible_submission(actor, ident, client=client) as (s, sub):
            rows = list(s.scalars(select(L).where(L.submission_id == sub.id).order_by(L.created_at)))
            for row in rows:
                s.expunge(row)
        return [self._out(r) for r in rows]

    def sync_status(self, actor: Actor, ident: str, provider: str, *, client: str = "") -> PublicationOut:
        """Tell the issue what the submission's status is, and close it when the submission is closed. Only when the owner
        allowed it, only when a maintainer asks, and only with words AgentLab wrote: the status's name."""
        tracker, cfg = self._ready(provider)
        self._require(actor, cfg)
        if not cfg.status_sync:
            raise UserError("synchronising status is not switched on (feedback.integrations.github.status_sync)")
        detail = self.submissions.get(actor, ident, client=client)
        link = self._link(detail.id, provider)
        if link is None or link.status != "published":
            raise UserError("this submission has not been published")
        issue = ExternalIssue(provider, link.external_id, link.external_number, link.url, link.external_state)
        closed = detail.status in self.config.closed_statuses
        try:
            tracker.comment(
                issue, f"AgentLab status: **{detail.status}**." + (" This submission is closed." if closed else "")
            )
            if closed and issue.state != "closed":
                tracker.close(issue)
        except TrackerError as exc:
            if exc.kind == "rate_limited":
                raise QuotaExceeded(str(exc), retry_after_seconds=exc.retry_after or 60) from exc
            if exc.kind in ("rejected", "disabled"):
                raise UserError(str(exc)) from exc
            raise InfrastructureError(str(exc)) from exc
        with self.db.session() as s:
            row = s.get(orm.ExternalIssueLinkRow, link.id)
            assert row is not None
            row.synced_status = detail.status
            if closed:
                row.external_state = "closed"
            self.submissions.audit(
                s,
                "publication_synced",
                actor,
                detail.id,
                client=client,
                number=detail.number,
                provider=provider,
                status=detail.status,
            )
            s.flush()
            s.expunge(row)
            return self._out(row)


def tracker_for(config: FeedbackConfig, secret: Callable[[str], str | None]) -> dict[str, IssueTracker]:
    """The trackers that are switched on, each reading its token only when a call is made."""
    from agentlab.feedback.integrations.github import GitHubTracker

    found: dict[str, IssueTracker] = {}
    cfg = config.integrations.github
    if cfg.enabled and cfg.repository and cfg.token_ref:
        ref = cfg.token_ref
        found["github"] = GitHubTracker(cfg, lambda: secret(ref))
    return found


def secret_from(ref: str, credentials: Any) -> str | None:
    """A token named by ``env:NAME`` or ``secret:NAME``; ``None`` when it is not there (the tracker then says so)."""
    from agentlab.core.errors import CredentialError
    from agentlab.security.credentials import resolve_reference

    try:
        if ref.startswith("secret:"):
            if credentials is None:
                return None
            fields = credentials.fields(ref.split(":", 1)[1])
            return str(fields.get("token") or fields.get("key") or "") or None
        return resolve_reference(ref) or None
    except (CredentialError, KeyError):
        return None


__all__ = ["PublicationService", "tracker_for", "secret_from", "LEFT_OUT"]
