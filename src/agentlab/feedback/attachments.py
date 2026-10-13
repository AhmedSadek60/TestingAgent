"""Attachments: validating, storing, sanitizing, listing, downloading and removing the files of a submission.

The bytes go to the artifact store as *restricted* evidence, under a name made from their hash; this module keeps what is
known about them. A person who may not see a submission may not see its attachments either: every call starts from the
submission's visibility rule. The original is never changed: a text file also gets a sanitized copy, made by the
DiagnosticRedactor, and the two are kept apart.

The slow parts (looking at the file, masking secrets, running a scanner) happen *outside* a database transaction, which holds
the process-wide lock.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select

from agentlab.core.config import FeedbackConfig
from agentlab.core.errors import InfrastructureError, NotFoundError, PolicyBlocked, UserError
from agentlab.core.ids import utcnow
from agentlab.feedback.errors import QuotaExceeded
from agentlab.feedback.files import Inspected, MiB, display_name, inspect_upload
from agentlab.feedback.models import STAFF_NAME, AttachmentOut, AttachmentPreview, ScanSummary, SecretsSummary
from agentlab.feedback.policy import Actor
from agentlab.feedback.scanner import MalwareScanner
from agentlab.feedback.service import SubmissionService
from agentlab.security.diagnostic_redactor import DiagnosticRedactor
from agentlab.storage import orm
from agentlab.storage.artifacts import ArtifactStore

DAY = timedelta(hours=24)
PREVIEW_CHARS = 20_000
KIND_ORIGINAL = "support_attachment"
KIND_SANITIZED = "support_attachment_sanitized"
SECRETS_NOTE = (
    "Masking reduces risk; it does not guarantee that nothing sensitive remains. A file that needs review must be looked at "
    "by a person before it is shared outside AgentLab."
)


def _aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


class AttachmentService:
    def __init__(
        self,
        submissions: SubmissionService,
        store: ArtifactStore,
        config: FeedbackConfig,
        *,
        scanner: MalwareScanner | None = None,
        redactor: DiagnosticRedactor | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.submissions = submissions
        self.db = submissions.db
        self.store = store
        self.config = config
        self.scanner = scanner or MalwareScanner(
            config.attachments.scanner_command, config.attachments.scanner_timeout_seconds
        )
        self.redactor = redactor or DiagnosticRedactor(
            custom_rules=[(r.name, r.pattern) for r in config.redaction.rules],
            redact_ip_addresses=config.redaction.redact_ip_addresses,
        )
        self._now = clock

    # ----------------------------------------------------------------------------------------------------- views
    @staticmethod
    def _secrets(row: orm.SubmissionAttachmentRow) -> SecretsSummary:
        report = row.secrets_report or {}
        blocked = row.secrets_status == "needs_review"
        return SecretsSummary(
            status=row.secrets_status,  # type: ignore[arg-type]
            counts=dict(report.get("counts", {})),
            external_sharing_blocked=blocked,
            note=SECRETS_NOTE,
        )

    def _out(self, actor: Actor, row: orm.SubmissionAttachmentRow) -> AttachmentOut:
        if actor.user_id is not None:
            mine = row.uploader_user_id == actor.user_id
        else:
            mine = row.uploader_user_id is None and row.uploader_label == actor.label
        uploader = row.uploader_label if actor.is_staff or mine or not row.uploader_is_staff else STAFF_NAME
        return AttachmentOut(
            id=row.id,
            name=row.original_name,
            extension=row.extension,
            type=row.detected_type,
            declared_type=row.declared_type,
            size=row.size,
            sha256=row.sha256,
            visibility=row.visibility,  # type: ignore[arg-type]
            uploader=uploader,
            uploader_is_staff=bool(row.uploader_is_staff),
            mine=mine,
            created_at=_aware(row.created_at),  # type: ignore[arg-type]
            text_like=bool(row.text_like),
            secrets=self._secrets(row),
            scan=ScanSummary(status=row.scan_status, detail=row.scan_detail),  # type: ignore[arg-type]
            archive=dict(row.archive_summary) or None,
            sanitized_available=bool(row.text_like and row.sanitized_artifact_id),
        )

    @staticmethod
    def _visible(actor: Actor):  # type: ignore[no-untyped-def]
        A = orm.SubmissionAttachmentRow
        clause = [A.status == "available"]
        if not actor.is_staff:
            clause.append(A.visibility == "public")
        return clause

    # -------------------------------------------------------------------------------------------------- reading
    def of_submission(self, actor: Actor, ident: str, *, client: str = "") -> list[AttachmentOut]:
        A = orm.SubmissionAttachmentRow
        with self.submissions.visible_submission(actor, ident, client=client) as (s, row):
            rows = s.scalars(
                select(A).where(A.submission_id == row.id, *self._visible(actor)).order_by(A.created_at, A.id)
            )
            return [self._out(actor, r) for r in rows]

    def _find(self, s: Any, actor: Actor, submission_id: str, attachment_id: str) -> orm.SubmissionAttachmentRow:
        A = orm.SubmissionAttachmentRow
        row = s.scalar(select(A).where(A.id == attachment_id, A.submission_id == submission_id, *self._visible(actor)))
        if row is None:
            raise NotFoundError("no such attachment")
        return row

    def get(self, actor: Actor, ident: str, attachment_id: str, *, client: str = "") -> AttachmentOut:
        with self.submissions.visible_submission(actor, ident, client=client) as (s, sub):
            return self._out(actor, self._find(s, actor, sub.id, attachment_id))

    def download(
        self, actor: Actor, ident: str, attachment_id: str, *, sanitized: bool = False, client: str = ""
    ) -> tuple[bytes, AttachmentOut]:
        """The bytes of an attachment, as uploaded or (a text file) sanitized. Every download is recorded."""
        with self.submissions.visible_submission(actor, ident, client=client) as (s, sub):
            att = self._find(s, actor, sub.id, attachment_id)
            if sanitized and not (att.text_like and att.sanitized_artifact_id):
                raise NotFoundError("this attachment has no sanitized copy")
            artifact = att.sanitized_artifact_id if sanitized else att.artifact_id
            self.submissions.audit(
                s,
                "attachment_downloaded",
                actor,
                sub.id,
                client=client,
                number=sub.human_id,
                attachment=att.id,
                version="sanitized" if sanitized else "original",
            )
            out = self._out(actor, att)
        try:
            return self.store.get(str(artifact)), out
        except InfrastructureError as exc:
            raise NotFoundError("the stored file is no longer available") from exc

    def preview(self, actor: Actor, ident: str, attachment_id: str, *, client: str = "") -> AttachmentPreview:
        with self.submissions.visible_submission(actor, ident, client=client) as (s, sub):
            att = self._find(s, actor, sub.id, attachment_id)
            if att.detected_type.startswith("image/"):
                return AttachmentPreview(
                    kind="image",
                    text=None,
                    truncated=False,
                    sanitized=False,
                    message="An image: download it to look at it.",
                )
            if not att.text_like:
                return AttachmentPreview(
                    kind="none",
                    text=None,
                    truncated=False,
                    sanitized=False,
                    message="This type of file has no preview.",
                )
            artifact = att.sanitized_artifact_id or att.artifact_id
            status = att.secrets_status
        try:
            text = self.store.get(artifact).decode("utf-8", errors="replace")
        except InfrastructureError as exc:
            raise NotFoundError("the stored file is no longer available") from exc
        shown = text[:PREVIEW_CHARS]
        if status == "not_inspected":
            note = "This file was too large to check for secrets, so the preview shows it as it was uploaded."
        elif status == "needs_review":
            note = "Secrets were masked, and a second inspection found more: the file needs a person's review."
        else:
            note = "Secrets that were recognised are masked in this preview."
        return AttachmentPreview(
            kind="text",
            text=shown,
            truncated=len(text) > PREVIEW_CHARS,
            sanitized=status in ("redacted", "needs_review", "none"),
            message=note,
        )

    # -------------------------------------------------------------------------------------------------- writing
    def add(
        self,
        actor: Actor,
        ident: str,
        *,
        filename: str,
        declared_type: str,
        data: bytes,
        visibility: str = "public",
        client: str = "",
    ) -> AttachmentOut:
        cfg = self.config.attachments
        if visibility not in ("public", "internal"):
            raise UserError("visibility is public or internal")
        if visibility == "internal" and not actor.is_staff:
            raise PolicyBlocked("only maintainers may attach a file that the reporter cannot see")
        if len(data) > cfg.max_file_mb * MiB:
            raise UserError(f"the file is larger than {cfg.max_file_mb} MB (feedback.attachments.max_file_mb)")
        name = display_name(filename)
        # 1. may the caller attach to this submission, and is there room? (a short transaction)
        with self.submissions.visible_submission(actor, ident, client=client) as (s, sub):
            self._room(s, actor, sub.id, len(data), digest=None)
            submission_id, number = sub.id, sub.human_id
        # 2. what is it? (nothing is run, opened or extracted)
        inspected = inspect_upload(name, data, cfg)
        digest = hashlib.sha256(data).hexdigest()
        # 3. is it clean, and what secrets does it hold?
        scan = self.scanner.scan(data)
        if scan.status == "infected":
            with self.submissions.visible_submission(actor, ident, client=client) as (s, sub):
                self.submissions.audit(
                    s,
                    "attachment_rejected",
                    actor,
                    sub.id,
                    client=client,
                    number=number,
                    reason="malware",
                    size=len(data),
                )
            raise PolicyBlocked("the malware scanner flagged this file, so it was not kept")
        if scan.status == "error" and cfg.scanner_required:
            raise PolicyBlocked(f"the file could not be scanned, and this server requires a scan: {scan.detail}")
        secrets_status, report, sanitized_bytes = self._secrets_check(inspected, data)
        # 4. keep it
        original = self.store.put(
            data,
            kind=KIND_ORIGINAL,
            media_type=inspected.media_type,
            name=f"{number}-{digest[:12]}.{inspected.extension}",
            sensitivity="restricted",
            redact=False,
            meta={"submission": number},
        )
        sanitized_id = None
        if sanitized_bytes is not None:
            sanitized_id = self.store.put(
                sanitized_bytes,
                kind=KIND_SANITIZED,
                media_type=inspected.media_type,
                name=f"{number}-{digest[:12]}.sanitized.{inspected.extension}",
                sensitivity="restricted",
                redact=False,
                meta={"submission": number},
            ).id
        with self.submissions.visible_submission(actor, ident, client=client) as (s, sub):
            self._room(s, actor, sub.id, len(data), digest=digest)  # again: another upload may have taken the room
            row = orm.SubmissionAttachmentRow(
                submission_id=submission_id,
                uploader_user_id=actor.user_id,
                uploader_label=actor.stored_label[:80],
                uploader_is_staff=actor.is_staff,
                visibility=visibility,
                original_name=name,
                extension=inspected.extension,
                declared_type=(declared_type or "")[:120],
                detected_type=inspected.media_type,
                size=len(data),
                sha256=digest,
                artifact_id=original.id,
                sanitized_artifact_id=sanitized_id,
                text_like=inspected.text_like,
                secrets_status=secrets_status,
                secrets_report=report,
                scan_status=scan.status,
                scan_detail=scan.detail[:200],
                archive_summary=inspected.archive,
                created_at=self._now(),
                status="available",
            )
            s.add(row)
            sub.last_activity_at = self._now()
            self.submissions.audit(
                s,
                "attachment_added",
                actor,
                sub.id,
                client=client,
                number=sub.human_id,
                attachment=row.id,
                type=inspected.media_type,
                size=len(data),
                scan=scan.status,
                secrets=secrets_status,
            )
            s.flush()
            s.expire_all()
            return self._out(actor, row)

    def _room(self, s: Any, actor: Actor, submission_id: str, size: int, *, digest: str | None) -> None:
        cfg = self.config.attachments
        A = orm.SubmissionAttachmentRow
        live = list(
            s.execute(select(A.size, A.sha256).where(A.submission_id == submission_id, A.status == "available"))
        )
        if len(live) >= cfg.max_files:
            raise PolicyBlocked(
                f"this submission already has {len(live)} attachments, which is the most allowed "
                "(feedback.attachments.max_files)"
            )
        if sum(n for n, _ in live) + size > cfg.max_total_mb * MiB:
            raise PolicyBlocked(
                f"the attachments of one submission may add up to {cfg.max_total_mb} MB "
                "(feedback.attachments.max_total_mb)"
            )
        if digest is not None and any(d == digest for _, d in live):
            raise UserError("this file is already attached to the submission")
        if not actor.is_staff:
            now = self._now()
            made = s.scalar(select(func.count()).where(A.uploader_user_id == actor.user_id, A.created_at >= now - DAY))
            if int(made or 0) >= cfg.max_uploads_per_day:
                raise QuotaExceeded(
                    f"you have attached {made} files in the last 24 hours, which is the most this server allows; "
                    "try again later",
                    retry_after_seconds=3600,
                )

    def _secrets_check(self, inspected: Inspected, data: bytes) -> tuple[str, dict[str, Any], bytes | None]:
        """Mask what a text file holds. Returns the status, the report (kinds and locations) and the sanitized bytes."""
        if not inspected.text_like or inspected.text is None:
            return "not_applicable", {}, None
        limit = self.config.attachments.sanitize_max_mb * MiB
        if len(data) > limit:
            return "not_inspected", {"reason": f"larger than {self.config.attachments.sanitize_max_mb} MB"}, None
        result = self.redactor.redact_text(inspected.text)
        report = result.report()
        if result.status == "clean":
            return "none", report, None
        return result.status, report, result.text.encode("utf-8")

    def remove(self, actor: Actor, ident: str, attachment_id: str, *, client: str = "") -> bool:
        """Take an attachment away. The row stays (the audit trail refers to it); the bytes go when nothing else uses
        them. Returns whether the bytes were deleted. A reporter may remove what they attached; a maintainer anything."""
        with self.submissions.visible_submission(actor, ident, client=client) as (s, sub):
            att = self._find(s, actor, sub.id, attachment_id)
            out = self._out(actor, att)
            if not actor.is_staff and not out.mine:
                raise PolicyBlocked("only the person who attached a file, or a maintainer, may remove it")
            att.status = "removed"
            att.removed_at = self._now()
            att.removed_by_label = actor.stored_label[:80]
            sub.last_activity_at = self._now()
            self.submissions.audit(
                s,
                "attachment_removed",
                actor,
                sub.id,
                client=client,
                number=sub.human_id,
                attachment=att.id,
                size=att.size,
            )
            original, sanitized = att.artifact_id, att.sanitized_artifact_id
            s.flush()
            original_used = self._used(s, original)
            sanitized_used = bool(sanitized) and self._used(s, str(sanitized))
        deleted = False if original_used else self.store.delete(original, kind=KIND_ORIGINAL)
        if sanitized and not sanitized_used:
            deleted = self.store.delete(sanitized, kind=KIND_SANITIZED) and deleted
        return deleted

    @staticmethod
    def _used(s: Any, artifact_id: str) -> bool:
        """Whether an attachment that is still there refers to these bytes (the same file may be attached twice)."""
        A = orm.SubmissionAttachmentRow
        found = s.scalar(
            select(func.count()).where(
                A.status == "available", or_(A.artifact_id == artifact_id, A.sanitized_artifact_id == artifact_id)
            )
        )
        return int(found or 0) > 0
