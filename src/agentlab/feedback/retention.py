"""Retention and deletion: how long what people sent is kept, and what is left when something is removed.

Two things remove data and both go through here:

* **The policy** (``feedback.retention``): periods, counted from the day a submission *closed*, for the whole submission, its
  files, its diagnostic information, its handoff packages and its internal notes. Every period is empty by default: nothing is
  removed until the owner says how long. A submission that is not closed is never touched, so the evidence of an open
  investigation (security reports above all) is never removed by the clock.
* **A deletion** (an administrator, with a reason): one submission and everything attached to it. An open private security
  report is deleted only when the administrator says in so many words that they know it is one.

What is removed is the content. What stays is what the audit trail needs and nothing a person wrote: a record that a
submission with that number existed, of what type, when it was removed, by whom (the policy, or an account), why (the reason
is masked like any text), and how many files and packages it had. A package that was downloaded before cannot be recalled:
the number of times is recorded, and the documentation says so.

Stored files are content addressed, so the same bytes may be used by more than one record. A file is deleted only when no
record that is still there refers to it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from agentlab.core.config import FeedbackConfig
from agentlab.core.errors import PolicyBlocked, UserError
from agentlab.core.ids import utcnow
from agentlab.feedback.attachments import KIND_ORIGINAL, KIND_SANITIZED
from agentlab.feedback.diagnostics import KIND as KIND_DIAGNOSTIC
from agentlab.feedback.handoff import KIND_TASK, KIND_ZIP
from agentlab.feedback.models import DeletionOut, RetentionReport, clean_text
from agentlab.feedback.policy import Actor
from agentlab.feedback.service import SubmissionService
from agentlab.security.redactor import get_redactor
from agentlab.storage import orm
from agentlab.storage.artifacts import ArtifactStore

log = logging.getLogger("agentlab.feedback.retention")

POLICY_LABEL = "retention policy"


@dataclass
class _Counts:
    submissions: int = 0
    attachments: int = 0
    items: int = 0
    handoffs: int = 0
    notes: int = 0
    downloads: int = 0  # times a package of this submission was downloaded
    candidates: set[tuple[str, str]] = field(default_factory=set)  # (artifact id, kind) that may be deleted
    free: set[tuple[str, str]] = field(default_factory=set)  # of those, the ones nothing that is left refers to


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


class RetentionService:
    def __init__(
        self,
        submissions: SubmissionService,
        artifacts: ArtifactStore,
        config: FeedbackConfig,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.submissions = submissions
        self.artifacts = artifacts
        self.config = config
        self.db = submissions.db
        self._now = clock

    # ================================================================================================== the policy
    def run(self, *, dry_run: bool = False, now: datetime | None = None) -> RetentionReport:
        """Apply the policy. With ``dry_run`` nothing is changed and the report says what would be."""
        moment = now or self._now()
        cfg = self.config.retention
        total = _Counts()
        files = 0
        if cfg.submissions_days is not None:
            for submission_id in self._due_submissions(moment - timedelta(days=cfg.submissions_days)):
                counts = self._delete(submission_id, by=POLICY_LABEL, reason="retention policy", dry_run=dry_run)
                total.submissions += 1
                total.attachments += counts.attachments
                total.items += counts.items
                total.handoffs += counts.handoffs
                files += len(counts.free) if dry_run else self._delete_files(counts.candidates)
        if cfg.attachments_days is not None:
            counts = self._partial("attachments", moment - timedelta(days=cfg.attachments_days), dry_run=dry_run)
            total.attachments += counts.attachments
            files += len(counts.free) if dry_run else self._delete_files(counts.candidates)
        if cfg.diagnostics_days is not None:
            counts = self._partial("diagnostics", moment - timedelta(days=cfg.diagnostics_days), dry_run=dry_run)
            total.items += counts.items
            files += len(counts.free) if dry_run else self._delete_files(counts.candidates)
        if cfg.handoffs_days is not None:
            counts = self._partial("handoffs", moment - timedelta(days=cfg.handoffs_days), dry_run=dry_run)
            total.handoffs += counts.handoffs
            files += len(counts.free) if dry_run else self._delete_files(counts.candidates)
        if cfg.internal_notes_days is not None:
            counts = self._partial("notes", moment - timedelta(days=cfg.internal_notes_days), dry_run=dry_run)
            total.notes += counts.notes
        return RetentionReport(
            ran_at=moment,
            dry_run=dry_run,
            submissions=total.submissions,
            attachments=total.attachments,
            diagnostic_items=total.items,
            handoffs=total.handoffs,
            internal_notes=total.notes,
            files_deleted=files,
        )

    def _due_submissions(self, cutoff: datetime) -> list[str]:
        S = orm.SubmissionRow
        with self.db.session() as s:
            return list(
                s.scalars(select(S.id).where(S.closed_at.is_not(None), S.closed_at <= cutoff).order_by(S.closed_at))
            )

    def _partial(self, what: str, cutoff: datetime, *, dry_run: bool) -> _Counts:
        """Remove one kind of thing from closed submissions that are kept. One transaction per submission, so a failure
        costs one submission's pass and not the whole run."""
        S = orm.SubmissionRow
        with self.db.session() as s:
            ids = list(s.scalars(select(S.id).where(S.closed_at.is_not(None), S.closed_at <= cutoff)))
        total = _Counts()
        for submission_id in ids:
            with self.db.session() as s:
                row = s.get(S, submission_id)
                if row is None:
                    continue
                counts = self._remove_part(s, row, what)
                counts.free = {c for c in counts.candidates if not self._referenced(s, *c)}
                if dry_run:
                    s.rollback()  # what would be removed has been counted, as it would be left: now put it back
                elif counts.attachments or counts.items or counts.handoffs or counts.notes:
                    self.submissions.audit(
                        s,
                        "retention_applied",
                        _policy_actor(),
                        row.id,
                        number=row.human_id,
                        what=what,
                        attachments=counts.attachments,
                        diagnostic_items=counts.items,
                        handoffs=counts.handoffs,
                        internal_notes=counts.notes,
                    )
            total.attachments += counts.attachments
            total.items += counts.items
            total.handoffs += counts.handoffs
            total.notes += counts.notes
            total.candidates |= counts.candidates
            total.free |= counts.free
        return total

    def _remove_part(self, s: Session, row: orm.SubmissionRow, what: str) -> _Counts:
        A, D, C, H, M = (
            orm.SubmissionAttachmentRow,
            orm.DiagnosticItemRow,
            orm.DiagnosticCollectionRow,
            orm.EngineeringHandoffRow,
            orm.SubmissionCommentRow,
        )
        counts = _Counts()
        now = self._now()
        if what == "attachments":
            for att in s.scalars(select(A).where(A.submission_id == row.id, A.status == "available")):
                counts.attachments += 1
                counts.candidates.add((att.artifact_id, KIND_ORIGINAL))
                if att.sanitized_artifact_id:
                    counts.candidates.add((att.sanitized_artifact_id, KIND_SANITIZED))
                att.status = "removed"
                att.removed_at = now
                att.removed_by_label = POLICY_LABEL
        elif what == "diagnostics":
            for item in s.scalars(
                select(D).join(C, C.id == D.collection_id).where(C.submission_id == row.id, D.artifact_id.is_not(None))
            ):
                counts.items += 1
                counts.candidates.add((str(item.artifact_id), KIND_DIAGNOSTIC))
                item.artifact_id = None
                item.included = False
                item.outcome = "excluded"
                item.detail = "removed by the retention policy"
                item.size = 0
        elif what == "handoffs":
            for package in s.scalars(select(H).where(H.submission_id == row.id, H.status == "generated")):
                counts.handoffs += 1
                counts.candidates.add((package.artifact_id, KIND_ZIP))
                counts.candidates.add((package.task_artifact_id, KIND_TASK))
                package.status = "purged"
        elif what == "notes":
            for note in s.scalars(select(M).where(M.submission_id == row.id, M.visibility == "internal")):
                counts.notes += 1
                s.delete(note)
        s.flush()
        return counts

    # ================================================================================================= a deletion
    def delete_submission(
        self,
        actor: Actor,
        ident: str,
        *,
        reason: str,
        acknowledge_open_security_report: bool = False,
        client: str = "",
    ) -> DeletionOut:
        """Delete one submission and everything attached to it. Administrators only."""
        if actor.role != "admin" or not actor.is_staff:
            raise PolicyBlocked("only an administrator may delete a submission")
        reason = clean_text(reason)  # no NUL byte reaches the database: PostgreSQL refuses it
        if len(reason) < 3:
            raise UserError(
                "say why it is being deleted (at least 3 characters): the reason is kept in the audit trail"
            )
        with self.submissions.visible_submission(actor, ident, client=client) as (s, row):
            submission_id, number = row.id, row.human_id
            open_security = row.visibility == "security" and row.closed_at is None
        if open_security and not acknowledge_open_security_report:
            raise UserError(
                "this is a private security report that is not closed, so its evidence may still be needed: close it "
                "first, or repeat the request with acknowledge_open_security_report to delete it anyway"
            )
        masked = get_redactor().redact_text(reason)[0][:300]
        counts = self._delete(
            submission_id,
            by=actor.label,
            by_id=actor.user_id,
            reason=masked,
            client=client,
            open_security=open_security,
        )
        files = self._delete_files(counts.candidates)
        return DeletionOut(
            number=number,
            attachments=counts.attachments,
            diagnostic_items=counts.items,
            handoffs=counts.handoffs,
            files_deleted=files,
            exported_copies=counts.downloads,
        )

    def _delete(
        self,
        submission_id: str,
        *,
        by: str,
        reason: str,
        by_id: str | None = None,
        client: str = "",
        open_security: bool = False,
        dry_run: bool = False,
    ) -> _Counts:
        """One transaction: note what the submission held, record that it was removed, delete it (the rest follows by
        cascade). The files are deleted afterwards, once no record refers to them."""
        S, A, D, C, H, E = (
            orm.SubmissionRow,
            orm.SubmissionAttachmentRow,
            orm.DiagnosticItemRow,
            orm.DiagnosticCollectionRow,
            orm.EngineeringHandoffRow,
            orm.SubmissionAuditRow,
        )
        counts = _Counts(submissions=1)
        with self.db.session() as s:
            row = s.get(S, submission_id)
            if row is None:
                return counts
            for att in s.scalars(select(A).where(A.submission_id == submission_id, A.status == "available")):
                counts.attachments += 1
                counts.candidates.add((att.artifact_id, KIND_ORIGINAL))
                if att.sanitized_artifact_id:
                    counts.candidates.add((att.sanitized_artifact_id, KIND_SANITIZED))
            for item in s.scalars(
                select(D)
                .join(C, C.id == D.collection_id)
                .where(C.submission_id == submission_id, D.artifact_id.is_not(None))
            ):
                counts.items += 1
                counts.candidates.add((str(item.artifact_id), KIND_DIAGNOSTIC))
            for package in s.scalars(select(H).where(H.submission_id == submission_id, H.status == "generated")):
                counts.handoffs += 1
                counts.candidates.add((package.artifact_id, KIND_ZIP))
                counts.candidates.add((package.task_artifact_id, KIND_TASK))
            counts.downloads = int(
                s.scalar(select(func.count()).where(E.submission_id == submission_id, E.event == "handoff_downloaded"))
                or 0
            )
            number, kind = row.human_id, row.type
            s.add(
                orm.SubmissionAuditRow(
                    submission_id=submission_id,
                    event="submission_deleted",
                    actor_label=by[:80],
                    actor_user_id=by_id,
                    client=client[:64],
                    detail={
                        "number": number,
                        "type": kind,
                        "reason": reason,
                        "attachments": counts.attachments,
                        "diagnostic_items": counts.items,
                        "handoffs": counts.handoffs,
                        "handoff_downloads": counts.downloads,
                        "open_security_report": open_security,
                    },
                    status="recorded",
                )
            )
            s.delete(row)
            s.flush()
            counts.free = {c for c in counts.candidates if not self._referenced(s, *c)}
            if dry_run:
                s.rollback()  # counted as it would be left: now put it back
        return counts

    # =============================================================================================== the files
    def _delete_files(self, candidates: set[tuple[str, str]]) -> int:
        """Delete each stored file nothing that is still there refers to. Returns how many went."""
        gone = 0
        for artifact_id, kind in sorted(candidates):
            with self.db.session() as s:
                if self._referenced(s, artifact_id, kind):
                    continue
            try:
                if self.artifacts.delete(artifact_id, kind=kind):
                    gone += 1
            except Exception:  # noqa: BLE001 - a file that cannot be deleted is reported, and the run goes on
                log.warning("could not delete a stored file of kind %s", kind, exc_info=True)
        return gone

    @staticmethod
    def _referenced(s: Session, artifact_id: str, kind: str) -> bool:
        A, D, H = orm.SubmissionAttachmentRow, orm.DiagnosticItemRow, orm.EngineeringHandoffRow
        if kind == KIND_ORIGINAL:
            found = s.scalar(select(func.count()).where(A.status == "available", A.artifact_id == artifact_id))
        elif kind == KIND_SANITIZED:
            found = s.scalar(
                select(func.count()).where(A.status == "available", A.sanitized_artifact_id == artifact_id)
            )
        elif kind == KIND_DIAGNOSTIC:
            found = s.scalar(select(func.count()).where(D.artifact_id == artifact_id))
        else:
            found = s.scalar(
                select(func.count()).where(
                    H.status == "generated", or_(H.artifact_id == artifact_id, H.task_artifact_id == artifact_id)
                )
            )
        return int(found or 0) > 0


def _policy_actor() -> Actor:
    return Actor(
        kind="local",
        role="admin",
        user_id=None,
        label=POLICY_LABEL,
        display_name=POLICY_LABEL,
        is_staff=True,
        sees_security=True,
    )


__all__ = ["RetentionService", "RetentionReport", "DeletionOut", "POLICY_LABEL"]
