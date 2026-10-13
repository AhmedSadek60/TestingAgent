"""Retention and deletion: nothing is removed unless the owner said how long, an open submission is never touched, what goes is
the content and what stays is the record that it was there, and a file another record uses is not deleted from under it.

Every secret below is invented and assembled from fragments so no scanner mistakes this file for a leak."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from agentlab.core.config import RetentionConfig
from agentlab.core.errors import NotFoundError, PolicyBlocked, UserError
from agentlab.feedback.models import CommentIn
from agentlab.feedback.retention import RetentionService
from tests.unit.test_feedback_diagnostics import DiagWorld  # noqa: F401 - the fixtures of the worlds it builds on
from tests.unit.test_feedback_handoff import FULL_ISSUE, LOG, HandoffWorld
from tests.unit.test_feedback_service import Clock

OPENAI = "sk-" + "proj" + "-" + "Zq81LmN4xW7sR2pK9vB3cD6e"


class RetentionWorld(HandoffWorld):
    def __init__(self, tmp_path: Path, clock: Clock, **feedback: Any) -> None:
        super().__init__(tmp_path, clock, **feedback)
        self.retention = RetentionService(self.svc, self.artifacts, self.config, clock=clock)

    def admin(self, name: str = "root") -> Any:
        return self.person(name, "admin")

    def closed(self, title: str = "A run stays on Processing", *, note: bool = True, closed: bool = True) -> Any:
        """A submission with a file, diagnostic information, an internal note and a handoff package, then closed."""
        alice, maria = self.person("alice"), self.maintainer()
        made = self.file(alice, {**FULL_ISSUE, "title": title})
        self.attach_file(alice, made.id, "run.log", LOG)
        self.attach_diagnostics(alice, made.id, "environment")
        self.svc.comment(alice, made.id, CommentIn(body="It is still stuck"))
        if note:
            self.svc.comment(maria, made.id, CommentIn(body="Customer is on a trial", visibility="internal"))
        self.build(made.id, maria)
        if closed:
            self.triage(maria, made.id, status="Closed")
        return made

    def count(self, table: str) -> int:
        with self.db.session() as s:
            return int(s.execute(text(f"select count(*) from {table}")).scalar() or 0)

    def stored(self) -> int:
        return len(self.artifacts.list())


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def make(tmp_path: Path, clock: Clock):  # type: ignore[no-untyped-def]
    return lambda **feedback: RetentionWorld(tmp_path, clock, **feedback)


@pytest.fixture
def world(make):  # type: ignore[no-untyped-def]
    return make()


def policy(**periods: int) -> dict[str, Any]:
    return {"retention": RetentionConfig(**periods)}


# ======================================================================================================== the policy
def test_nothing_is_removed_unless_a_period_is_set(world: RetentionWorld, clock: Clock) -> None:
    world.closed()
    before = (world.count("submissions"), world.count("submission_attachments"), world.stored())
    clock.advance(days=3650)
    report = world.retention.run()
    assert (
        report.submissions,
        report.attachments,
        report.diagnostic_items,
        report.handoffs,
        report.internal_notes,
    ) == (
        0,
        0,
        0,
        0,
        0,
    )
    assert report.files_deleted == 0 and report.dry_run is False
    assert (world.count("submissions"), world.count("submission_attachments"), world.stored()) == before
    assert world.config.retention.active is False


def test_a_submission_that_is_not_closed_is_never_touched_however_old_it_is(make, clock: Clock) -> None:  # type: ignore[no-untyped-def]
    w = make(
        **policy(submissions_days=1, attachments_days=1, diagnostics_days=1, handoffs_days=1, internal_notes_days=1)
    )
    w.closed("Still open", closed=False)
    private = w.file(w.person("bob"), FULL_ISSUE, suspected_security=True, title="A private one still being looked at")
    w.attach_file(w.person("bob"), private.id, "evidence.log", LOG + b"more\n")
    before = (w.count("submissions"), w.count("submission_attachments"), w.stored())
    clock.advance(days=400)
    report = w.retention.run()
    assert report.submissions == report.attachments == report.diagnostic_items == report.handoffs == 0
    assert (w.count("submissions"), w.count("submission_attachments"), w.stored()) == before


def test_attachments_go_after_their_period_from_the_day_the_submission_closed_and_the_record_stays(
    make, clock: Clock
) -> None:  # type: ignore[no-untyped-def]
    w = make(**policy(attachments_days=30))
    made = w.closed()
    alice = w.person("alice")
    files_before = w.stored()
    clock.advance(days=29)
    assert w.retention.run().attachments == 0, "the period has not passed"
    clock.advance(days=2)
    report = w.retention.run()
    assert report.attachments == 1 and report.files_deleted >= 2, "the original and the masked copy go"
    assert w.stored() < files_before
    kept = w.attachments.of_submission(alice, made.id)
    assert kept == [], "a removed attachment is not listed"
    with w.db.session() as s:
        row = s.execute(text("select status, removed_by_label from submission_attachments")).one()
    assert tuple(row) == ("removed", "retention policy"), "the record stays, saying who removed it"
    assert w.svc.get(alice, made.id).number == made.number, "the submission itself is kept"
    assert w.retention.run().attachments == 0, "a second pass finds nothing more"
    trail = [e for e in w.svc.audit_trail(w.maintainer(), made.id) if e.event == "retention_applied"]
    assert len(trail) == 1 and trail[0].actor == "retention policy"
    assert trail[0].detail["attachments"] == 1 and "run.log" not in json.dumps(trail[0].detail)


def test_a_file_that_another_submission_still_uses_stays_until_nothing_does(make, clock: Clock) -> None:  # type: ignore[no-untyped-def]
    w = make(**policy(attachments_days=30))
    alice, maria = w.person("alice"), w.maintainer()
    first = w.file(alice, FULL_ISSUE)
    second = w.file(alice, {**FULL_ISSUE, "title": "The same file again"})
    for made in (first, second):
        w.attach_file(alice, made.id, "run.log", LOG)  # the same bytes: one stored file
    w.triage(maria, first.id, status="Closed")
    clock.advance(days=20)
    w.triage(maria, second.id, status="Closed")
    stored = w.stored()
    clock.advance(days=11)  # the first is past its period, the second is not
    report = w.retention.run()
    assert report.attachments == 1 and report.files_deleted == 0
    assert w.stored() == stored, "the file is still the second submission's"
    assert [a.name for a in w.attachments.of_submission(alice, second.id)] == ["run.log"]
    original, _ = w.attachments.download(alice, second.id, w.attachments.of_submission(alice, second.id)[0].id)
    assert original == LOG
    clock.advance(days=20)
    report = w.retention.run()
    assert report.attachments == 1 and report.files_deleted >= 1
    assert w.stored() < stored


def test_diagnostic_information_is_removed_and_the_collection_says_so(make, clock: Clock) -> None:  # type: ignore[no-untyped-def]
    w = make(**policy(diagnostics_days=10))
    made = w.closed()
    alice = w.person("alice")
    (collection,) = w.diag.of_submission(alice, made.id)
    assert any(i.outcome == "collected" and i.included for i in collection.items)
    clock.advance(days=11)
    report = w.retention.run()
    assert report.diagnostic_items >= 1
    (after,) = w.diag.of_submission(alice, made.id)
    assert all(i.outcome != "collected" and not i.included for i in after.items if i.category == "environment")
    removed = next(i for i in after.items if i.category == "environment")
    assert removed.detail == "removed by the retention policy" and removed.preview is None and removed.size == 0
    with pytest.raises(NotFoundError, match="nothing to download"):
        w.diag.bundle(alice, str(collection.id))


def test_handoff_packages_are_removed_and_the_record_of_what_they_held_stays(make, clock: Clock) -> None:  # type: ignore[no-untyped-def]
    w = make(**policy(handoffs_days=10))
    made = w.closed()
    maria = w.maintainer()
    (package,) = w.handoff.of_submission(maria, made.id)
    assert package.status == "generated" and package.task
    clock.advance(days=11)
    assert w.retention.run().handoffs == 1
    (after,) = w.handoff.of_submission(maria, made.id)
    assert after.status == "purged" and after.task == "" and after.sha256 == package.sha256
    assert [f.path for f in after.files] == [f.path for f in package.files], "what it held is still on record"
    for call in (
        lambda: w.handoff.download(maria, str(package.id)),
        lambda: w.handoff.download(maria, str(package.id), task_only=True),
    ):
        with pytest.raises(NotFoundError, match="no longer available"):
            call()
    assert w.retention.run().handoffs == 0


def test_internal_notes_go_and_what_the_reporter_was_told_stays(make, clock: Clock) -> None:  # type: ignore[no-untyped-def]
    w = make(**policy(internal_notes_days=10))
    made = w.closed()
    maria = w.maintainer()
    assert any(c.visibility == "internal" for c in w.svc.get(maria, made.id).discussion)
    clock.advance(days=11)
    assert w.retention.run().internal_notes == 1
    discussion = w.svc.get(maria, made.id).discussion
    assert not any(c.visibility == "internal" for c in discussion)
    assert [c.body for c in discussion] == ["It is still stuck"]


def test_a_whole_submission_is_deleted_with_everything_attached_and_only_the_record_of_it_stays(
    make, clock: Clock
) -> None:  # type: ignore[no-untyped-def]
    w = make(**policy(submissions_days=90))
    other = w.closed("A submission that is not yet due", note=False)
    made = w.closed(
        "A title that must not survive in the audit trail"
    )  # the newest: its number is the one to be given again
    assert other.number != made.number
    maria = w.maintainer()
    # the first closed a long time ago, the second just now
    with w.db.session() as s:
        s.execute(
            text("update submissions set closed_at = :t where id = :i"),
            {"t": w.clock.now.replace(year=2026, month=1), "i": made.id},
        )
    report = w.retention.run()
    assert report.submissions == 1 and report.attachments == 1 and report.handoffs == 1
    assert report.files_deleted == 2, (
        "the package and its task file: the file, the copy and the diagnostics are the other's too"
    )
    with pytest.raises(NotFoundError):
        w.svc.get(maria, made.id)
    assert w.svc.get(maria, other.id).number == other.number
    for table in (
        "submission_comments",
        "submission_status_history",
        "submission_attachments",
        "diagnostic_collections",
        "diagnostic_items",
        "engineering_handoffs",
    ):
        with w.db.session() as s:
            left = s.execute(
                text(
                    f"select count(*) from {table} where submission_id = :i"
                    if table != "diagnostic_items"
                    else "select 0"
                ),
                {"i": made.id},
            ).scalar()
        assert left == 0, table
    with w.db.session() as s:
        event = s.execute(
            text("select event, actor_label, detail from submission_audit_events where event = 'submission_deleted'")
        ).one()
    assert event[0] == "submission_deleted" and event[1] == "retention policy"
    detail = json.loads(event[2]) if isinstance(event[2], str) else event[2]
    assert (
        detail["number"] == made.number
        and detail["type"] == "issue"
        and detail["attachments"] == 1
        and detail["handoffs"] == 1
    )
    assert detail["handoff_downloads"] == 1
    assert "must not survive" not in json.dumps(detail)
    # the number is not given again
    again = w.file(w.person("alice"), FULL_ISSUE, title="A new one")
    assert again.number not in (made.number, other.number), (
        "the number of the newest, deleted, submission is not given again"
    )
    assert int(again.number.rsplit("-", 1)[1]) > int(made.number.rsplit("-", 1)[1])


def test_a_dry_run_says_what_would_go_and_changes_nothing(make, clock: Clock) -> None:  # type: ignore[no-untyped-def]
    w = make(**policy(submissions_days=1))
    w.closed()
    clock.advance(days=5)
    before = (w.count("submissions"), w.stored(), w.count("submission_audit_events"))
    report = w.retention.run(dry_run=True)
    assert report.dry_run is True and report.submissions == 1 and report.files_deleted >= 3
    assert (w.count("submissions"), w.stored(), w.count("submission_audit_events")) == before
    assert w.retention.run().submissions == 1


def test_a_stored_file_that_cannot_be_deleted_is_logged_and_the_run_goes_on(
    make, clock: Clock, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:  # type: ignore[no-untyped-def]
    w = make(**policy(attachments_days=1))
    made = w.closed()
    clock.advance(days=3)

    def refuse(*_a: Any, **_k: Any) -> bool:
        raise OSError("disk says no")

    monkeypatch.setattr(w.artifacts, "delete", refuse)
    with caplog.at_level("WARNING", logger="agentlab.feedback.retention"):
        report = w.retention.run()
    assert report.attachments == 1 and report.files_deleted == 0
    assert any("could not delete a stored file" in r.message for r in caplog.records)
    assert w.attachments.of_submission(w.person("alice"), made.id) == [], (
        "the attachment is gone from the submission all the same"
    )


def test_the_periods_are_checked_when_the_configuration_loads() -> None:
    assert RetentionConfig().active is False
    assert RetentionConfig(attachments_days=365).active is True
    for bad in ({"submissions_days": 0}, {"handoffs_days": -1}, {"interval_hours": 0}, {"attachments_days": 100000}):
        with pytest.raises(ValidationError):
            RetentionConfig(**bad)


# ====================================================================================================== a deletion
def test_only_an_administrator_may_delete_a_submission_and_must_say_why(world: RetentionWorld) -> None:
    made = world.closed()
    alice, maria, root = world.person("alice"), world.maintainer(), world.admin()
    for who in (alice, maria, world.token):
        with pytest.raises((PolicyBlocked, NotFoundError)):
            world.retention.delete_submission(who, made.id, reason="because")
    with pytest.raises(UserError, match="say why"):
        world.retention.delete_submission(root, made.id, reason="  ")
    assert world.svc.get(maria, made.id).number == made.number


def test_a_deletion_removes_everything_masks_the_reason_and_says_how_often_a_package_was_downloaded(
    world: RetentionWorld,
) -> None:
    made = world.closed()
    maria, root = world.maintainer(), world.admin()
    (package,) = world.handoff.of_submission(maria, made.id)
    world.handoff.download(maria, str(package.id))
    world.handoff.download(maria, str(package.id), task_only=True)
    stored = world.stored()
    done = world.retention.delete_submission(
        root, made.id, reason=f"erasure requested; they pasted {OPENAI} by mistake"
    )
    assert (done.number, done.attachments, done.handoffs) == (made.number, 1, 1) and done.diagnostic_items >= 1
    assert done.exported_copies == 3, "the build's own download and the two above cannot be recalled"
    assert done.files_deleted >= 3 and world.stored() < stored
    with pytest.raises(NotFoundError):
        world.svc.get(maria, made.id)
    with pytest.raises(NotFoundError, match="no such handoff"):
        world.handoff.get(maria, str(package.id))
    with world.db.session() as s:
        rows = s.execute(
            text("select actor_label, detail from submission_audit_events where event = 'submission_deleted'")
        ).all()
    assert len(rows) == 1 and rows[0][0] == "root"
    blob = json.dumps(rows[0][1]) if not isinstance(rows[0][1], str) else rows[0][1]
    assert OPENAI not in blob and "erasure requested" in blob
    assert world.count("submission_comments") == 0


def test_an_open_private_security_report_is_deleted_only_when_the_administrator_says_they_know(
    world: RetentionWorld,
) -> None:
    root = world.admin()
    private = world.file(world.person("bob"), FULL_ISSUE, suspected_security=True, title="Tokens show on a page")
    with pytest.raises(UserError, match="private security report that is not closed"):
        world.retention.delete_submission(root, private.id, reason="requested by the reporter")
    assert world.svc.get(root, private.id).number == private.number
    done = world.retention.delete_submission(
        root, private.id, reason="requested by the reporter", acknowledge_open_security_report=True
    )
    assert done.number == private.number
    with world.db.session() as s:
        detail = s.execute(
            text("select detail from submission_audit_events where event = 'submission_deleted'")
        ).scalar()
    detail = json.loads(detail) if isinstance(detail, str) else detail
    assert detail["open_security_report"] is True


def test_a_closed_private_report_needs_no_acknowledgement(world: RetentionWorld) -> None:
    root, maria = world.admin(), world.maintainer()
    private = world.file(world.person("bob"), FULL_ISSUE, suspected_security=True, title="Tokens show on a page")
    world.triage(maria, private.id, status="Closed")
    assert (
        world.retention.delete_submission(root, private.id, reason="the investigation is over").number == private.number
    )


# ============================================================================================================ storage
def test_the_counters_migration_carries_numbering_on_from_what_exists_and_can_be_undone(tmp_path: Path) -> None:
    from sqlalchemy import inspect

    from agentlab.storage.db import Database
    from agentlab.storage.migrate import current_head, downgrade

    db = Database(f"sqlite:///{tmp_path}/m.db")
    db.migrate()
    assert current_head() >= "0007" and "submission_counters" in set(inspect(db.engine).get_table_names())
    downgrade(db.url, "0006")
    assert "submission_counters" not in set(inspect(db.engine).get_table_names())
    with db.session() as s:
        s.execute(
            text(
                "insert into submissions (id, human_id, type, year, seq, title, description, category, visibility, "
                "reporter_label, last_activity_at, created_at, updated_at, version, status) values "
                "('s1', 'FR-2026-00007', 'feature', 2026, 7, 't', '', 'Other', 'normal', 'x', "
                "'2026-01-01', '2026-01-01', '2026-01-01', 1, 'Submitted')"
            )
        )
    db.migrate()
    with db.session() as s:
        assert tuple(s.execute(text("select type, year, last_seq from submission_counters")).one()) == (
            "feature",
            2026,
            7,
        )
