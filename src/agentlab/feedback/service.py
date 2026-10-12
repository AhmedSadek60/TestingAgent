"""Submissions: making them, finding them and triaging them.

Who may see what is decided in ``policy.py`` and applied here, in the query, so a caller never receives a row it may not
see. Anything a caller may not see is answered as "not found", the same as something that does not exist, and the attempt
is recorded."""

from __future__ import annotations

import math
import re
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agentlab.core.config import FeedbackConfig
from agentlab.core.errors import InfrastructureError, NotFoundError, PolicyBlocked, UserError
from agentlab.core.ids import utcnow
from agentlab.feedback.errors import QuotaExceeded, VersionConflict
from agentlab.feedback.models import (
    FEATURE_PRIORITIES,
    FREQUENCIES,
    IMPACTS,
    PRIORITIES,
    RELATION_KINDS,
    SEVERITIES,
    STAFF_NAME,
    TEXT,
    AssigneeOut,
    AuditEventOut,
    CommentIn,
    CommentOut,
    FeatureDetailsOut,
    FeedbackOptions,
    IssueDetailsOut,
    RelationIn,
    RelationOut,
    SimilarOut,
    StatusChangeOut,
    SubmissionCounts,
    SubmissionDetail,
    SubmissionIn,
    SubmissionPage,
    SubmissionPatch,
    SubmissionSummary,
    clean_text,
)
from agentlab.feedback.policy import Actor, owns, visible
from agentlab.storage import orm
from agentlab.storage.db import Database

HUMAN = re.compile(r"^(FR|IR)-\d{4}-\d{5,}$")
WORD = re.compile(r"[^\W_]{3,}")
STOPWORDS = frozenset(
    [
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "have",
        "has",
        "not",
        "are",
        "but",
        "was",
        "were",
        "can",
        "could",
        "should",
        "would",
        "when",
        "what",
        "why",
        "how",
        "into",
        "about",
        "does",
        "did",
        "its",
        "our",
        "your",
        "you",
        "they",
        "them",
        "their",
        "there",
        "then",
        "than",
        "also",
        "just",
        "only",
        "over",
        "under",
    ]
)
SIMILAR_MIN_SCORE = 0.25
SIMILAR_CANDIDATES = 500
DENIED_NOTE_EVERY = 600.0  # seconds: a denied attempt on one submission by one caller is recorded at most this often
DAY = timedelta(hours=24)
PRIORITY_RANK = {"critical": 4, "high": 3, "normal": 2, "low": 1}


def _aware(moment: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; everything here is UTC."""
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _words(text: str) -> set[str]:
    return {w for w in WORD.findall(text.casefold()) if w not in STOPWORDS}


@dataclass
class SubmissionFilter:
    """What a list asks for. The fields marked (staff) are refused to a reporter: a filter on something they are not told
    would tell them."""

    type: str | None = None
    statuses: list[str] = field(default_factory=list)
    category: str | None = None
    priority: str | None = None  # (staff) the assigned priority
    severity: str | None = None  # (staff)
    assignee: str | None = None  # (staff) an account id, "me" or "none"
    reporter: str | None = None  # (staff) an account id
    label: str | None = None  # (staff)
    visibility: str | None = None  # (staff)
    q: str | None = None
    mine: bool = False
    unread: bool = False
    created_from: datetime | None = None
    created_to: datetime | None = None
    sort: str = "activity"  # activity | created | priority (staff)
    descending: bool = True
    limit: int = 50
    offset: int = 0


def _clean_filter(f: SubmissionFilter) -> SubmissionFilter:
    """The text a caller put in a filter, without control characters: what is not text reaches no query."""
    out = replace(f)
    for name in ("category", "priority", "severity", "assignee", "reporter", "label", "visibility", "q"):
        value = getattr(out, name)
        if isinstance(value, str):
            setattr(out, name, clean_text(value) or None)
    out.statuses = [v for v in (clean_text(status) for status in f.statuses) if v]
    return out


class SubmissionService:
    def __init__(self, db: Database, config: FeedbackConfig, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.db = db
        self.config = config
        self._now = clock
        self._lock = threading.Lock()
        self._denied: dict[tuple[str, str], float] = {}

    # ----------------------------------------------------------------------------------------------- the workflow
    def workflow(self, type_: str) -> list[str]:
        return list(self.config.feature_statuses if type_ == "feature" else self.config.issue_statuses)

    def categories(self, type_: str) -> list[str]:
        return list(self.config.feature_categories if type_ == "feature" else self.config.issue_categories)

    def options(self, actor: Actor) -> FeedbackOptions:
        cfg = self.config
        return FeedbackOptions(
            feature_categories=list(cfg.feature_categories),
            issue_categories=list(cfg.issue_categories),
            feature_statuses=list(cfg.feature_statuses),
            issue_statuses=list(cfg.issue_statuses),
            closed_statuses=list(cfg.closed_statuses),
            feature_priorities=list(FEATURE_PRIORITIES),
            priorities=list(PRIORITIES),
            severities=list(SEVERITIES),
            frequencies=list(FREQUENCIES),
            impacts=list(IMPACTS),
            relation_kinds=list(RELATION_KINDS),
            security_category=cfg.security_category,
            max_text_chars=TEXT,
            can_triage=actor.is_staff,
        )

    # ---------------------------------------------------------------------------------------------------- helpers
    @staticmethod
    def _mine(actor: Actor, row: orm.SubmissionRow) -> bool:
        if actor.user_id is not None:
            return row.reporter_user_id == actor.user_id
        return row.reporter_user_id is None and row.reporter_label == actor.label

    @staticmethod
    def _require_staff(actor: Actor, what: str = "this") -> None:
        if not actor.is_staff:
            raise PolicyBlocked(f"{what} is for maintainers only")

    def _audit(
        self, s: Session, event: str, actor: Actor, submission_id: str | None, *, client: str = "", **detail: Any
    ) -> None:
        s.add(
            orm.SubmissionAuditRow(
                submission_id=submission_id,
                event=event,
                actor_label=actor.label[:80],
                actor_user_id=actor.user_id,
                client=client[:64],
                detail=detail,
                status="recorded",
            )
        )

    def _find(self, s: Session, actor: Actor, ident: str) -> tuple[orm.SubmissionRow | None, str | None]:
        """The row this actor may see, or (None, id of a row that exists but is hidden from them)."""
        S = orm.SubmissionRow
        ident = ident.strip()
        key = ident.upper()
        cond = S.human_id == key if HUMAN.match(key) else S.id == ident
        row = s.scalar(select(S).where(cond, visible(actor)))
        if row is not None:
            return row, None
        return None, s.scalar(select(S.id).where(cond))

    @contextmanager
    def _open(self, actor: Actor, ident: str, *, client: str = "") -> Iterator[tuple[Session, orm.SubmissionRow]]:
        """A transaction and the submission in it, or "not found". A submission that exists but is hidden from the actor
        is answered the same way, and the attempt is recorded once the transaction has ended (an exception rolls it back)."""
        hidden: str | None = None
        with self.db.session() as s:
            row, hidden = self._find(s, actor, ident)
            if row is not None:
                yield s, row
                return
        self._note_denied(actor, ident, hidden, client)
        raise NotFoundError("no such submission")

    def _note_denied(self, actor: Actor, ident: str, hidden: str | None, client: str) -> None:
        if hidden is None:
            return
        now = time.monotonic()
        with self._lock:
            last = self._denied.get((actor.key, hidden))
            if last is not None and now - last < DENIED_NOTE_EVERY:
                return
            if len(self._denied) > 4096:
                self._denied.pop(next(iter(self._denied)))
            self._denied[(actor.key, hidden)] = now
        with self.db.session() as s:
            self._audit(s, "access_denied", actor, hidden, client=client)

    # ---------------------------------------------------------------------------------------------------- views
    def _summaries(self, s: Session, actor: Actor, rows: list[orm.SubmissionRow]) -> list[SubmissionSummary]:
        if not rows:
            return []
        ids = [r.id for r in rows]
        C, L, U = orm.SubmissionCommentRow, orm.SubmissionLabelRow, orm.UserRow
        count_q = select(C.submission_id, func.count()).where(C.submission_id.in_(ids))
        if not actor.is_staff:
            count_q = count_q.where(C.visibility == "public")
        counts = {sid: n for sid, n in s.execute(count_q.group_by(C.submission_id))}
        labels: dict[str, list[str]] = defaultdict(list)
        names: dict[str, str] = {}
        if actor.is_staff:
            for sid, label in s.execute(
                select(L.submission_id, L.label).where(L.submission_id.in_(ids)).order_by(L.label)
            ):
                labels[sid].append(label)
            wanted = {r.assignee_user_id for r in rows if r.assignee_user_id}
            if wanted:
                for user in s.scalars(select(U).where(U.id.in_(wanted))):
                    names[user.id] = user.display_name or user.username
        out = []
        for r in rows:
            mine = self._mine(actor, r)
            seen = _aware(r.reporter_seen_at)
            staff_at = _aware(r.last_staff_activity_at)
            out.append(
                SubmissionSummary(
                    id=r.id,
                    number=r.human_id,
                    type=r.type,  # type: ignore[arg-type]
                    title=r.title,
                    category=r.category,
                    status=r.status,
                    visibility=r.visibility,  # type: ignore[arg-type]
                    requested_priority=r.requested_priority,
                    assigned_priority=r.assigned_priority if actor.is_staff else None,
                    severity=r.severity if actor.is_staff else None,
                    reporter=r.reporter_label,
                    reporter_id=r.reporter_user_id if actor.is_staff else None,
                    mine=mine,
                    assignee=(
                        AssigneeOut(id=r.assignee_user_id, name=names.get(r.assignee_user_id, "(unknown)"))
                        if actor.is_staff and r.assignee_user_id
                        else None
                    ),
                    labels=labels.get(r.id, []) if actor.is_staff else [],
                    created_at=_aware(r.created_at),  # type: ignore[arg-type]
                    updated_at=_aware(r.updated_at),  # type: ignore[arg-type]
                    last_activity_at=_aware(r.last_activity_at),  # type: ignore[arg-type]
                    unread=bool(mine and staff_at is not None and (seen is None or seen < staff_at)),
                    comments=int(counts.get(r.id, 0)),
                )
            )
        return out

    @staticmethod
    def _comment_out(actor: Actor, c: orm.SubmissionCommentRow) -> CommentOut:
        if actor.user_id is not None:
            mine = c.author_user_id == actor.user_id
        else:
            mine = c.author_user_id is None and c.author_label == actor.label and c.author_is_staff == actor.is_staff
        # a reporter is not told which maintainer wrote something
        author = c.author_label if actor.is_staff or mine or not c.author_is_staff else STAFF_NAME
        return CommentOut(
            id=c.id,
            author=author,
            author_is_staff=bool(c.author_is_staff),
            mine=mine,
            visibility=c.visibility,  # type: ignore[arg-type]
            kind=c.kind,  # type: ignore[arg-type]
            body=c.body,
            created_at=_aware(c.created_at),  # type: ignore[arg-type]
        )

    def _detail(self, s: Session, actor: Actor, row: orm.SubmissionRow) -> SubmissionDetail:
        base = self._summaries(s, actor, [row])[0]
        Feat, Iss = orm.FeatureDetailsRow, orm.IssueDetailsRow
        C, H = orm.SubmissionCommentRow, orm.SubmissionHistoryRow
        feature = s.scalar(select(Feat).where(Feat.submission_id == row.id))
        issue = s.scalar(select(Iss).where(Iss.submission_id == row.id))
        comments = select(C).where(C.submission_id == row.id)
        if not actor.is_staff:
            comments = comments.where(C.visibility == "public")
        discussion = [self._comment_out(actor, c) for c in s.scalars(comments.order_by(C.created_at, C.id))]
        history = [
            StatusChangeOut(
                id=h.id,
                old_status=h.old_status,
                new_status=h.new_status,
                changed_by=(h.changed_by_label if actor.is_staff or not h.by_staff else STAFF_NAME),
                by_staff=bool(h.by_staff),
                comment=h.comment,
                at=_aware(h.created_at),  # type: ignore[arg-type]
            )
            for h in s.scalars(select(H).where(H.submission_id == row.id).order_by(H.created_at, H.id))
        ]
        relations = self._relations(s, actor, row) if actor.is_staff else []
        return SubmissionDetail(
            **base.model_dump(),
            version=row.version,
            description=row.description,
            feature=(
                FeatureDetailsOut(**{k: getattr(feature, k) for k in FeatureDetailsOut.model_fields})
                if feature
                else None
            ),
            issue=(IssueDetailsOut(**{k: getattr(issue, k) for k in IssueDetailsOut.model_fields}) if issue else None),
            project_id=row.project_id,
            related_run_id=row.related_run_id,
            related_test_id=row.related_test_id,
            related_trace_id=row.related_trace_id,
            related_report_id=row.related_report_id,
            related_target_id=row.related_target_id,
            app_version=row.app_version,
            build=row.build,
            resolved_at=_aware(row.resolved_at),
            closed_at=_aware(row.closed_at),
            discussion=discussion,
            history=history,
            relations=relations,
            statuses=self.workflow(row.type),
        )

    @staticmethod
    def _relations(s: Session, actor: Actor, row: orm.SubmissionRow) -> list[RelationOut]:
        R, S = orm.SubmissionRelationRow, orm.SubmissionRow
        found: list[RelationOut] = []
        for direction, near, far in (("from", R.from_id, R.to_id), ("to", R.to_id, R.from_id)):
            query = select(R, S).join(S, S.id == far).where(near == row.id, visible(actor)).order_by(R.created_at)
            for rel, other in s.execute(query):
                found.append(
                    RelationOut(
                        id=rel.id,
                        kind=rel.kind,
                        direction=direction,  # type: ignore[arg-type]
                        other_id=other.id,
                        other_number=other.human_id,
                        other_title=other.title,
                        other_status=other.status,
                    )
                )
        return found

    # ----------------------------------------------------------------------------------------------------- create
    def _check_quota(self, actor: Actor, now: datetime) -> None:
        """Open registration makes anyone able to file: a person with the role reporter has a daily allowance."""
        if actor.is_staff:
            return
        S = orm.SubmissionRow
        since = now - DAY
        with self.db.session() as s:
            made = [
                _aware(t)
                for t in s.scalars(
                    select(S.created_at)
                    .where(S.reporter_user_id == actor.user_id, S.created_at >= since)
                    .order_by(S.created_at)
                )
            ]
        if len(made) >= self.config.max_submissions_per_day:
            wait = max(60, int((made[0] + DAY - now).total_seconds())) if made[0] else 3600  # type: ignore[operator]
            raise QuotaExceeded(
                f"you have made {len(made)} submissions in the last 24 hours, which is the most this server allows; "
                f"try again in about {math.ceil(wait / 3600)} hour(s)",
                retry_after_seconds=wait,
            )

    def create(self, actor: Actor, data: SubmissionIn, *, client: str = "") -> SubmissionDetail:
        cfg = self.config
        allowed = self.categories(data.type)
        if data.category not in allowed:
            raise UserError(f"category must be one of: {', '.join(allowed)}")
        now = self._now()
        self._check_quota(actor, now)
        visibility = "security" if data.suspected_security or data.category == cfg.security_category else "normal"
        initial = self.workflow(data.type)[0]
        last: IntegrityError | None = None
        for _ in range(
            5
        ):  # another process may take the next number between the read and the write: take the one after
            try:
                with self.db.session() as s:
                    row = self._insert(s, actor, data, now=now, visibility=visibility, initial=initial, client=client)
                    return self._detail(s, actor, row)
            except IntegrityError as exc:
                last = exc
        assert last is not None
        raise InfrastructureError("could not give the submission a number; try again") from last

    def _insert(
        self,
        s: Session,
        actor: Actor,
        data: SubmissionIn,
        *,
        now: datetime,
        visibility: str,
        initial: str,
        client: str,
    ) -> orm.SubmissionRow:
        S = orm.SubmissionRow
        feature = data.type == "feature"
        year = now.year
        seq = (s.scalar(select(func.max(S.seq)).where(S.type == data.type, S.year == year)) or 0) + 1
        row = S(
            human_id=f"{'FR' if feature else 'IR'}-{year}-{seq:05d}",
            type=data.type,
            year=year,
            seq=seq,
            title=data.title,
            description=data.description,
            category=data.category,
            status=initial,
            requested_priority=data.feature.requested_priority if data.feature else None,
            visibility=visibility,
            reporter_user_id=actor.user_id,
            reporter_label=actor.stored_label[:80],
            project_id=data.project_id,
            related_run_id=data.related_run_id,
            related_test_id=data.related_test_id,
            related_trace_id=data.related_trace_id,
            related_report_id=data.related_report_id,
            related_target_id=data.related_target_id,
            app_version=data.app_version,
            build=data.build,
            created_at=now,
            updated_at=now,
            last_activity_at=now,
        )
        s.add(row)
        s.flush()  # the number is unique: a clash surfaces here, before anything else is written
        if data.feature is not None:
            s.add(
                orm.FeatureDetailsRow(
                    submission_id=row.id, **data.feature.model_dump(exclude={"requested_priority"}), status="active"
                )
            )
        if data.issue is not None:
            s.add(orm.IssueDetailsRow(submission_id=row.id, **data.issue.model_dump(), status="active"))
        s.add(
            orm.SubmissionHistoryRow(
                submission_id=row.id,
                old_status=None,
                new_status=initial,
                changed_by_user_id=actor.user_id,
                changed_by_label=actor.stored_label[:80],
                by_staff=actor.is_staff,
                comment="",
                created_at=now,
                status="recorded",
            )
        )
        self._audit(
            s,
            "created",
            actor,
            row.id,
            client=client,
            number=row.human_id,
            type=data.type,
            category=data.category,
            visibility=visibility,
        )
        s.flush()
        s.expire_all()  # answer with what was stored (secrets masked), not with the text that was sent
        return row

    # ------------------------------------------------------------------------------------------------------ read
    def get(self, actor: Actor, ident: str, *, client: str = "") -> SubmissionDetail:
        with self._open(actor, ident, client=client) as (s, row):
            if row.visibility == "security" and not self._mine(actor, row):
                self._audit(s, "viewed", actor, row.id, client=client, number=row.human_id)
            return self._detail(s, actor, row)

    def search(self, actor: Actor, f: SubmissionFilter) -> SubmissionPage:
        S, L = orm.SubmissionRow, orm.SubmissionLabelRow
        if not actor.is_staff:
            used = [
                name
                for name, value in (
                    ("priority", f.priority),
                    ("severity", f.severity),
                    ("assignee", f.assignee),
                    ("reporter", f.reporter),
                    ("label", f.label),
                    ("visibility", f.visibility),
                )
                if value
            ]
            if used or f.sort == "priority":
                raise PolicyBlocked(
                    f"filtering or sorting by {', '.join(used or ['priority'])} is for maintainers only"
                )
        f = _clean_filter(f)
        stmt = select(S).where(visible(actor))
        if f.type:
            stmt = stmt.where(S.type == f.type)
        if f.statuses:
            stmt = stmt.where(S.status.in_(f.statuses))
        if f.category:
            stmt = stmt.where(S.category == f.category)
        if f.priority:
            stmt = stmt.where(S.assigned_priority == f.priority)
        if f.severity:
            stmt = stmt.where(S.severity == f.severity)
        if f.visibility:
            stmt = stmt.where(S.visibility == f.visibility)
        if f.assignee == "none":
            stmt = stmt.where(S.assignee_user_id.is_(None))
        elif f.assignee == "me":
            stmt = stmt.where(S.assignee_user_id == actor.user_id) if actor.user_id else stmt.where(S.id.is_(None))
        elif f.assignee:
            stmt = stmt.where(S.assignee_user_id == f.assignee)
        if f.reporter:
            stmt = stmt.where(S.reporter_user_id == f.reporter)
        if f.label:
            stmt = stmt.where(S.id.in_(select(L.submission_id).where(L.label == f.label)))
        if f.mine:
            stmt = stmt.where(owns(actor))
        if f.unread:
            stmt = stmt.where(
                owns(actor),
                S.last_staff_activity_at.is_not(None),
                or_(S.reporter_seen_at.is_(None), S.reporter_seen_at < S.last_staff_activity_at),
            )
        if f.created_from:
            stmt = stmt.where(S.created_at >= f.created_from)
        if f.created_to:
            stmt = stmt.where(S.created_at <= f.created_to)
        term = (f.q or "").strip()
        if term:
            terms = [S.title.icontains(term, autoescape=True), S.description.icontains(term, autoescape=True)]
            if HUMAN.match(term.upper()):
                terms.append(S.human_id == term.upper())
            stmt = stmt.where(or_(*terms))
        total_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())

        def direction(column: Any) -> Any:
            return column.desc() if f.descending else column.asc()

        if f.sort == "created":
            ordered = [direction(S.created_at), S.id]
        elif f.sort == "priority":
            rank = case(*[(S.assigned_priority == k, v) for k, v in PRIORITY_RANK.items()], else_=0)
            ordered = [direction(rank), S.last_activity_at.desc(), S.id]
        else:
            ordered = [direction(S.last_activity_at), S.id]
        with self.db.session() as s:
            total = int(s.scalar(total_stmt) or 0)
            rows = list(s.scalars(stmt.order_by(*ordered).limit(f.limit).offset(f.offset)))
            return SubmissionPage(items=self._summaries(s, actor, rows), total=total, limit=f.limit, offset=f.offset)

    def summary(self, actor: Actor) -> SubmissionCounts:
        S = orm.SubmissionRow
        closed = set(self.config.closed_statuses)
        with self.db.session() as s:
            by_status: dict[str, int] = defaultdict(int)
            by_type: dict[str, int] = defaultdict(int)
            for type_, status, n in s.execute(
                select(S.type, S.status, func.count()).where(visible(actor)).group_by(S.type, S.status)
            ):
                by_status[status] += n
                by_type[type_] += n
            total = sum(by_type.values())
            open_ = sum(n for status, n in by_status.items() if status not in closed)
            unread = int(
                s.scalar(
                    select(func.count()).where(
                        owns(actor),
                        S.last_staff_activity_at.is_not(None),
                        or_(S.reporter_seen_at.is_(None), S.reporter_seen_at < S.last_staff_activity_at),
                    )
                )
                or 0
            )
            unassigned: int | None = None
            if actor.is_staff:
                unassigned = int(
                    s.scalar(
                        select(func.count()).where(
                            visible(actor), S.assignee_user_id.is_(None), S.status.not_in(closed)
                        )
                    )
                    or 0
                )
        return SubmissionCounts(
            total=total,
            open=open_,
            unread=unread,
            unassigned=unassigned,
            by_status=dict(by_status),
            by_type=dict(by_type),
        )

    def similar(self, actor: Actor, type_: str, title: str, *, category: str | None = None) -> list[SimilarOut]:
        """Submissions the caller can see that share words with a title. A suggestion, never a block; and only of what the
        caller may see, so for a reporter it is their own."""
        wanted = _words(clean_text(title))
        if not wanted:
            return []
        S = orm.SubmissionRow
        with self.db.session() as s:
            candidates = s.execute(
                select(S.id, S.human_id, S.title, S.status, S.category)
                .where(visible(actor), S.type == type_)
                .order_by(S.last_activity_at.desc())
                .limit(SIMILAR_CANDIDATES)
            ).all()
        scored = []
        for ident, number, title_, status, cat in candidates:
            theirs = _words(title_)
            if not theirs:
                continue
            score = len(wanted & theirs) / len(wanted | theirs)
            if category and cat == category:
                score = min(1.0, score + 0.1)
            if score >= SIMILAR_MIN_SCORE:
                scored.append(SimilarOut(id=ident, number=number, title=title_, status=status, score=round(score, 3)))
        scored.sort(key=lambda r: (-r.score, r.number))
        return scored[: self.config.similar_limit]

    def assignees(self, actor: Actor) -> list[AssigneeOut]:
        self._require_staff(actor, "the list of people a submission can be assigned to")
        U = orm.UserRow
        with self.db.session() as s:
            users = s.scalars(
                select(U).where(U.role.in_(("maintainer", "admin")), U.status == "active").order_by(U.username)
            )
            return [AssigneeOut(id=u.id, name=u.display_name or u.username) for u in users]

    def audit_trail(self, actor: Actor, ident: str, *, limit: int = 200) -> list[AuditEventOut]:
        self._require_staff(actor, "the audit trail")
        A = orm.SubmissionAuditRow
        with self._open(actor, ident) as (s, row):
            events = s.scalars(
                select(A).where(A.submission_id == row.id).order_by(A.created_at.desc(), A.id).limit(limit)
            )
            return [
                AuditEventOut(event=e.event, actor=e.actor_label, at=_aware(e.created_at), detail=e.detail)  # type: ignore[arg-type]
                for e in events
            ]

    # ------------------------------------------------------------------------------------------------------ write
    def _validated_assignee(self, s: Session, user_id: str) -> str:
        U = orm.UserRow
        user = s.get(U, user_id)
        if user is None or user.status != "active" or user.role not in ("maintainer", "admin"):
            raise UserError("a submission can be assigned to an active maintainer or admin account only")
        return user.id

    def patch(self, actor: Actor, ident: str, patch: SubmissionPatch, *, client: str = "") -> SubmissionDetail:
        self._require_staff(actor, "changing a submission")
        sent = patch.model_fields_set
        S, L, H = orm.SubmissionRow, orm.SubmissionLabelRow, orm.SubmissionHistoryRow
        with self._open(actor, ident, client=client) as (s, row):
            now = self._now()
            mine = self._mine(actor, row)
            fields: dict[str, Any] = {}
            changed: dict[str, Any] = {}
            status_change: tuple[str, str] | None = None

            if patch.status is not None and patch.status != row.status:
                workflow = self.workflow(row.type)
                if patch.status not in workflow:
                    raise UserError(f"status must be one of: {', '.join(workflow)}")
                status_change = (row.status, patch.status)
                fields["status"] = patch.status
                cfg = self.config
                resolved = patch.status in cfg.resolved_statuses
                closed = patch.status in cfg.closed_statuses
                if resolved:
                    fields["resolved_at"] = _aware(row.resolved_at) or now
                if closed:
                    fields["closed_at"] = now
                if not resolved and not closed:
                    fields["resolved_at"] = None
                    fields["closed_at"] = None
                elif resolved and not closed:
                    fields["closed_at"] = None
            elif patch.status_comment:
                raise UserError("a status comment goes with a status change: send `status` too")

            for name in ("assigned_priority", "severity"):
                if name in sent and getattr(patch, name) != getattr(row, name):
                    fields[name] = getattr(patch, name)
                    changed[name] = [getattr(row, name), getattr(patch, name)]
            if "assignee_id" in sent:
                wanted = patch.assignee_id or None
                if wanted is not None:
                    wanted = self._validated_assignee(s, wanted)
                if wanted != row.assignee_user_id:
                    fields["assignee_user_id"] = wanted
                    changed["assignee"] = [row.assignee_user_id, wanted]
            visibility = row.visibility
            if patch.category is not None and patch.category != row.category:
                allowed = self.categories(row.type)
                if patch.category not in allowed:
                    raise UserError(f"category must be one of: {', '.join(allowed)}")
                fields["category"] = patch.category
                changed["category"] = [row.category, patch.category]
                if patch.category == self.config.security_category:
                    visibility = "security"  # moving into the security category never makes it more public
            if patch.visibility is not None:
                visibility = patch.visibility
            if visibility != row.visibility:
                fields["visibility"] = visibility
                changed["visibility"] = [row.visibility, visibility]

            labels_added: list[str] = []
            labels_removed: list[str] = []
            current: dict[str, str] = {}
            if patch.labels is not None:
                for lab in s.scalars(select(L).where(L.submission_id == row.id)):
                    current[lab.label.casefold()] = lab.label
                wanted_labels = {label.casefold(): label for label in patch.labels}
                labels_added = [v for k, v in wanted_labels.items() if k not in current]
                labels_removed = [v for k, v in current.items() if k not in wanted_labels]
                if labels_added or labels_removed:
                    changed["labels"] = {"added": labels_added, "removed": labels_removed}

            if not fields and not labels_added and not labels_removed:
                return self._detail(s, actor, row)  # nothing to change: not a conflict either

            # claim the row: one statement both checks the version the caller read and bumps it, so two maintainers
            # triaging the same submission at once cannot both win
            claim: CursorResult[Any] = s.execute(  # type: ignore[assignment]
                update(S).where(S.id == row.id, S.version == patch.version).values(version=S.version + 1)
            )
            if claim.rowcount != 1:
                raise VersionConflict(
                    "the submission was changed by someone else since you opened it: reload it and apply your change again"
                )
            s.refresh(row)
            for key, value in fields.items():
                setattr(row, key, value)
            row.last_activity_at = now
            if status_change is not None:
                s.add(
                    H(
                        submission_id=row.id,
                        old_status=status_change[0],
                        new_status=status_change[1],
                        changed_by_user_id=actor.user_id,
                        changed_by_label=actor.stored_label[:80],
                        by_staff=True,
                        comment=patch.status_comment,
                        created_at=now,
                        status="recorded",
                    )
                )
                if not mine:
                    row.last_staff_activity_at = now  # something the reporter can see changed
            for label in labels_removed:
                s.execute(delete(L).where(L.submission_id == row.id, L.label == label))
            for label in labels_added:
                s.add(orm.SubmissionLabelRow(submission_id=row.id, label=label, status="active"))
            if status_change is not None:  # its own event, so the trail can be filtered for status changes
                self._audit(
                    s,
                    "status_changed",
                    actor,
                    row.id,
                    client=client,
                    number=row.human_id,
                    old=status_change[0],
                    new=status_change[1],
                )
            if changed:
                self._audit(s, "updated", actor, row.id, client=client, number=row.human_id, changes=changed)
            s.flush()
            s.expire_all()  # answer with what was stored
            return self._detail(s, actor, row)

    def comment(self, actor: Actor, ident: str, data: CommentIn, *, client: str = "") -> CommentOut:
        if not actor.is_staff and (data.visibility != "public" or data.kind != "comment"):
            raise PolicyBlocked("only maintainers may write an internal note or ask for information")
        C = orm.SubmissionCommentRow
        with self._open(actor, ident, client=client) as (s, row):
            now = self._now()
            if not actor.is_staff:
                written = s.scalar(
                    select(func.count()).where(C.author_user_id == actor.user_id, C.created_at >= now - DAY)
                )
                if int(written or 0) >= self.config.max_comments_per_day:
                    raise QuotaExceeded(
                        "you have written as many comments in the last 24 hours as this server allows; try again later",
                        retry_after_seconds=3600,
                    )
            comment = C(
                submission_id=row.id,
                author_user_id=actor.user_id,
                author_label=actor.stored_label[:80],
                author_is_staff=actor.is_staff,
                visibility=data.visibility,
                kind=data.kind,
                body=data.body,
                created_at=now,
                status="active",
            )
            s.add(comment)
            row.last_activity_at = now
            if actor.is_staff and data.visibility == "public" and not self._mine(actor, row):
                row.last_staff_activity_at = now
            if self._mine(actor, row):
                row.reporter_seen_at = now  # whoever wrote it has read the thread
            self._audit(
                s,
                "commented",
                actor,
                row.id,
                client=client,
                number=row.human_id,
                visibility=data.visibility,
                kind=data.kind,
            )
            s.flush()
            s.expire_all()  # answer with what was stored
            return self._comment_out(actor, comment)

    def mark_seen(self, actor: Actor, ident: str, *, client: str = "") -> None:
        with self._open(actor, ident, client=client) as (_s, row):
            if self._mine(actor, row):
                row.reporter_seen_at = self._now()

    # ---------------------------------------------------------------------------------------------- relations
    def relate(self, actor: Actor, ident: str, data: RelationIn, *, client: str = "") -> SubmissionDetail:
        self._require_staff(actor, "linking submissions")
        R = orm.SubmissionRelationRow
        with self._open(actor, ident, client=client) as (s, row):
            other, _hidden = self._find(s, actor, data.to)
            if other is None:  # missing and hidden are answered alike
                raise NotFoundError("no such submission to link to")
            if other.id == row.id:
                raise UserError("a submission cannot be linked to itself")
            if data.kind == "duplicate_of" and other.type != row.type:
                raise UserError("a feature request and an issue report are not duplicates of each other")
            if s.scalar(select(R.id).where(R.from_id == row.id, R.to_id == other.id, R.kind == data.kind)):
                raise UserError("these two are already linked in that way")
            if data.kind == "duplicate_of" and self._reaches(s, other.id, row.id):
                raise UserError("that would make two submissions duplicates of each other")
            s.add(
                R(
                    from_id=row.id,
                    to_id=other.id,
                    kind=data.kind,
                    created_by_label=actor.label[:80],
                    status="active",
                )
            )
            row.last_activity_at = self._now()
            self._audit(
                s, "related", actor, row.id, client=client, number=row.human_id, kind=data.kind, other=other.human_id
            )
            s.flush()
            s.expire_all()  # answer with what was stored
            return self._detail(s, actor, row)

    @staticmethod
    def _reaches(s: Session, start: str, goal: str) -> bool:
        """Whether following ``duplicate_of`` links from ``start`` arrives at ``goal``."""
        R = orm.SubmissionRelationRow
        seen: set[str] = set()
        frontier = [start]
        while frontier:
            here = frontier.pop()
            if here == goal:
                return True
            if here in seen:
                continue
            seen.add(here)
            frontier.extend(s.scalars(select(R.to_id).where(R.from_id == here, R.kind == "duplicate_of")))
        return False

    def unrelate(self, actor: Actor, ident: str, relation_id: str, *, client: str = "") -> SubmissionDetail:
        self._require_staff(actor, "unlinking submissions")
        R = orm.SubmissionRelationRow
        with self._open(actor, ident, client=client) as (s, row):
            rel = s.scalar(select(R).where(R.id == relation_id, or_(R.from_id == row.id, R.to_id == row.id)))
            if rel is None:
                raise NotFoundError("no such link on this submission")
            s.delete(rel)
            row.last_activity_at = self._now()
            self._audit(s, "unrelated", actor, row.id, client=client, number=row.human_id, kind=rel.kind)
            s.flush()
            s.expire_all()  # answer with what was stored
            return self._detail(s, actor, row)
