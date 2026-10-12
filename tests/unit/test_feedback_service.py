"""Feedback & Support without a server: who sees what, the workflow, concurrent changes, allowances, links and the audit trail.

Every secret below is invented and assembled from fragments so no scanner mistakes this file for a leak."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from agentlab.accounts import AccountService
from agentlab.accounts.models import Principal
from agentlab.core.config import AccountsConfig, FeedbackConfig
from agentlab.core.errors import InfrastructureError, NotFoundError, PolicyBlocked, UserError
from agentlab.feedback import (
    Actor,
    CommentIn,
    QuotaExceeded,
    RelationIn,
    SubmissionFilter,
    SubmissionIn,
    SubmissionPatch,
    SubmissionService,
    VersionConflict,
    actor_for,
)
from agentlab.feedback.models import SubmissionDetail
from agentlab.storage.db import Database

PASSWORD = "correct-horse-battery-7"  # made up
SECRET = "sk-" + "proj" + "-" + "Zq81LmN4xW7sR2pK9vB3cD6e"  # looks like a provider key, is not one


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


FEATURE: dict[str, Any] = {
    "type": "feature",
    "title": "Export reports as CSV",
    "category": "Reporting and Analytics",
    "description": "A spreadsheet would help.",
    "feature": {"problem_statement": "JSON cannot be opened in a spreadsheet", "requested_priority": "high"},
}
ISSUE: dict[str, Any] = {
    "type": "issue",
    "title": "A run stays on Processing forever",
    "category": "Test Execution Failure",
    "description": "It never finishes.",
    "issue": {"impact_summary": "blocks my work", "frequency": "every_time", "reported_impact": "blocks_project"},
}


class World:
    """A database with a few people, and a service that tells the time the test says it is."""

    def __init__(self, tmp_path: Path, clock: Clock, **feedback: Any) -> None:
        self.clock = clock
        self.db = Database(f"sqlite:///{tmp_path}/feedback.db")
        self.db.migrate()
        self.config = FeedbackConfig(**feedback)
        self.accounts = AccountService(self.db, AccountsConfig(), clock=clock)
        self.svc = SubmissionService(self.db, self.config, clock=clock)
        self._people: dict[str, Actor] = {}

    def person(self, name: str, role: str = "reporter") -> Actor:
        if name not in self._people:
            user, _ = self.accounts.create_user("test", name, role=role, password=PASSWORD)  # type: ignore[arg-type]
            principal = Principal(
                kind="user", role=user.role, user_id=user.id, username=user.username, display_name=user.display_name
            )
            self._people[name] = actor_for(principal, self.config)
        return self._people[name]

    def maintainer(self, name: str = "maria") -> Actor:
        return self.person(name, "maintainer")

    @property
    def token(self) -> Actor:
        return actor_for(Principal(kind="service", role="maintainer"), self.config)

    @property
    def local(self) -> Actor:
        return actor_for(Principal(kind="local", role="admin"), self.config)

    def file(self, who: Actor, body: dict[str, Any] | None = None, **over: Any) -> SubmissionDetail:
        return self.svc.create(who, SubmissionIn(**{**(body or FEATURE), **over}), client="10.0.0.1")

    def triage(self, who: Actor, ident: str, **change: Any) -> SubmissionDetail:
        version = self.svc.get(who, ident).version
        return self.svc.patch(who, ident, SubmissionPatch(version=version, **change))


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def make(tmp_path: Path, clock: Clock) -> Callable[..., World]:
    return lambda **feedback: World(tmp_path, clock, **feedback)


@pytest.fixture
def world(make: Callable[..., World]) -> World:
    return make()


# ======================================================================================================== numbering
def test_numbers_count_per_type_and_restart_with_the_year(world: World) -> None:
    alice = world.person("alice")
    assert world.file(alice).number == "FR-2026-00001"
    assert world.file(alice).number == "FR-2026-00002"
    assert world.file(alice, ISSUE).number == "IR-2026-00001"
    world.clock.advance(days=100)  # into 2027
    assert world.file(alice).number == "FR-2027-00001"
    again = world.svc.get(alice, "fr-2026-00002")  # looked up by number, in any case
    assert again.number == "FR-2026-00002" and again.type == "feature"


def test_people_filing_at_the_same_time_never_share_a_number(world: World) -> None:
    people = [world.person(f"person{i}") for i in range(6)]
    numbers: list[str] = []
    failures: list[BaseException] = []

    def file_five(who: Actor) -> None:
        try:
            for _ in range(5):
                numbers.append(world.file(who).number)
        except BaseException as exc:  # noqa: BLE001 - reported by the assertion below
            failures.append(exc)

    threads = [threading.Thread(target=file_five, args=(p,)) for p in people]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not failures, failures
    assert sorted(numbers) == [f"FR-2026-{i:05d}" for i in range(1, 31)]


def test_a_number_taken_by_another_process_is_retried_and_a_persistent_clash_is_reported(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = world.person("alice")
    real = SubmissionService._insert
    calls = {"n": 0}

    def clash_twice(self: SubmissionService, *a: Any, **k: Any) -> Any:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise IntegrityError("insert", {}, Exception("uq_submission_number"))
        return real(self, *a, **k)

    monkeypatch.setattr(SubmissionService, "_insert", clash_twice)
    assert world.file(alice).number == "FR-2026-00001" and calls["n"] == 3

    def always_clash(self: SubmissionService, *a: Any, **k: Any) -> Any:
        raise IntegrityError("insert", {}, Exception("uq_submission_number"))

    monkeypatch.setattr(SubmissionService, "_insert", always_clash)
    with pytest.raises(InfrastructureError, match="number"):
        world.file(alice)
    assert world.svc.search(alice, SubmissionFilter()).total == 1, "nothing half-written was left behind"


# ================================================================================================ what is stored
def test_a_feature_request_and_an_issue_report_keep_their_details_and_start_at_the_first_status(world: World) -> None:
    alice = world.person("alice")
    f = world.file(alice)
    assert (f.status, f.type, f.requested_priority, f.visibility) == ("Submitted", "feature", "high", "normal")
    assert f.feature is not None and f.feature.problem_statement.startswith("JSON cannot")
    assert f.issue is None and f.history[0].old_status is None and f.history[0].new_status == "Submitted"
    i = world.file(alice, ISSUE, related_run_id="run-123", app_version="0.4.0")
    assert i.issue is not None and i.issue.frequency == "every_time" and i.issue.reported_impact == "blocks_project"
    assert i.related_run_id == "run-123" and i.app_version == "0.4.0" and i.requested_priority is None
    assert i.statuses[0] == "Submitted" and "Cannot Reproduce" in i.statuses and "Planned" not in i.statuses


def test_the_category_must_be_one_the_server_offers_for_that_type(world: World) -> None:
    alice = world.person("alice")
    with pytest.raises(UserError, match="category must be one of"):
        world.file(alice, category="Test Execution Failure")  # an issue category on a feature request
    assert world.file(alice, category="Other").category == "Other"


def test_details_must_match_the_type_and_ids_must_be_ids() -> None:
    with pytest.raises(ValidationError):
        SubmissionIn(**{**FEATURE, "feature": None})
    with pytest.raises(ValidationError):
        SubmissionIn(**{**FEATURE, "issue": ISSUE["issue"]})
    with pytest.raises(ValidationError):
        SubmissionIn(**{**ISSUE, "feature": FEATURE["feature"]})
    for bad in ("../../etc/passwd", "a b", "x" * 80, "'; drop table submissions;--", ""):
        with pytest.raises(ValidationError):
            SubmissionIn(**{**ISSUE, "related_run_id": bad})
    for extra in ("status", "assigned_priority", "visibility", "reporter_user_id", "version", "id"):
        with pytest.raises(ValidationError):
            SubmissionIn(**{**FEATURE, extra: "x"})


def test_control_characters_are_removed_and_a_secret_is_masked_before_it_is_stored(world: World) -> None:
    alice = world.person("alice")
    made = world.file(
        alice,
        title="Bad\x00 title\x07 here",
        description=f"line one\r\nline two\x00\nmy key is {SECRET}\n",
    )
    assert made.title == "Bad title here" and "\x00" not in made.description and "\r" not in made.description
    assert SECRET not in made.description, "the answer is the stored text, not what was sent"
    with world.db.session() as s:
        stored = s.execute(text("select title, description from submissions")).one()
    assert SECRET not in stored[1] and "Zq81LmN4xW7sR2pK9vB3cD6e" not in stored[1]
    for blank in ("   ", "ab", "\x00\x00\x00"):
        with pytest.raises(ValidationError):
            SubmissionIn(**{**FEATURE, "title": blank})


# ===================================================================================== who sees what
def test_a_reporter_sees_only_their_own_and_cannot_tell_that_others_exist(world: World) -> None:
    alice, bob, maria = world.person("alice"), world.person("bob"), world.maintainer()
    mine = world.file(alice)
    world.file(bob, title="Bob's own idea", category="Other")
    svc = world.svc
    assert [i.number for i in svc.search(alice, SubmissionFilter()).items] == [mine.number]
    assert svc.search(bob, SubmissionFilter()).total == 1 and svc.search(maria, SubmissionFilter()).total == 2

    with pytest.raises(NotFoundError) as hidden:
        svc.get(bob, mine.id)
    with pytest.raises(NotFoundError) as missing:
        svc.get(bob, "00000000-0000-0000-0000-000000000000")
    assert str(hidden.value) == str(missing.value), "a hidden submission is answered exactly like one that is not there"
    with pytest.raises(NotFoundError):
        svc.get(bob, mine.number)  # by number too
    with pytest.raises(NotFoundError):
        svc.comment(bob, mine.id, CommentIn(body="hello"))
    with pytest.raises(NotFoundError):
        svc.mark_seen(bob, mine.id)
    assert svc.similar(bob, "feature", "export reports as csv") == [], "suggestions are of what the caller may see"
    assert svc.summary(bob).total == 1 and svc.summary(maria).total == 2


def test_security_submissions_are_seen_by_the_reporter_and_maintainers_and_not_by_the_shared_token(
    world: World, make: Callable[..., World]
) -> None:
    alice, bob, maria = world.person("alice"), world.person("bob"), world.maintainer()
    private = world.file(alice, ISSUE, category="Suspected Security Issue", title="Tokens appear in the logs")
    flagged = world.file(alice, ISSUE, suspected_security=True, title="Login page has an injection")
    assert private.visibility == flagged.visibility == "security"
    svc = world.svc
    for who in (alice, maria, world.local):
        assert svc.get(who, private.id).visibility == "security"
    for who in (bob, world.token):
        with pytest.raises(NotFoundError):
            svc.get(who, private.id)
        assert svc.search(who, SubmissionFilter()).total == 0
    assert svc.summary(world.token).total == 0

    # the owner may let the shared token see them
    open_world = make(service_token_sees_security=True)
    carol = open_world.person("carol")
    s = open_world.file(carol, ISSUE, suspected_security=True)
    assert open_world.svc.get(open_world.token, s.id).number == s.number


def test_a_reporter_is_not_told_what_the_maintainers_decided_or_who_they_are(world: World) -> None:
    alice, maria, nina = world.person("alice"), world.maintainer("maria"), world.maintainer("nina")
    made = world.file(alice)
    world.triage(
        maria,
        made.id,
        assigned_priority="critical",
        severity="high",
        labels=["csv", "reports"],
        assignee_id=nina.user_id,
    )
    world.clock.advance(seconds=1)
    world.svc.comment(maria, made.id, CommentIn(body="Could you attach a sample?", kind="info_request"))
    world.clock.advance(seconds=1)
    world.svc.comment(maria, made.id, CommentIn(body="Customer is on the free plan", visibility="internal"))
    world.clock.advance(seconds=1)
    world.svc.comment(alice, made.id, CommentIn(body="Sure, one moment."))

    theirs = world.svc.get(alice, made.id)
    assert (theirs.assigned_priority, theirs.severity, theirs.assignee, theirs.labels, theirs.relations) == (
        None,
        None,
        None,
        [],
        [],
    )
    assert theirs.reporter_id is None and theirs.mine is True
    assert [(c.author, c.visibility, c.kind, c.mine) for c in theirs.discussion] == [
        ("AgentLab maintainer", "public", "info_request", False),
        ("alice", "public", "comment", True),
    ]
    assert theirs.comments == 2, "the count is of what they can see"
    assert "maria" not in json.dumps(theirs.model_dump(mode="json"))
    assert "nina" not in json.dumps(theirs.model_dump(mode="json"))

    ours = world.svc.get(maria, made.id)
    assert (ours.assigned_priority, ours.severity, ours.labels) == ("critical", "high", ["csv", "reports"])
    assert ours.assignee is not None and ours.assignee.name == "nina" and ours.reporter_id == alice.user_id
    assert [c.visibility for c in ours.discussion] == ["public", "internal", "public"] and ours.comments == 3
    assert ours.discussion[0].author == "maria"

    for field, value in (
        ("priority", "high"),
        ("severity", "low"),
        ("label", "csv"),
        ("assignee", "me"),
        ("reporter", "x"),
    ):
        with pytest.raises(PolicyBlocked):
            world.svc.search(alice, SubmissionFilter(**{field: value}))
    with pytest.raises(PolicyBlocked):
        world.svc.search(alice, SubmissionFilter(sort="priority"))
    assert world.svc.search(maria, SubmissionFilter(priority="critical", label="csv")).total == 1
    assert world.svc.search(maria, SubmissionFilter(priority="low")).total == 0


def test_only_maintainers_triage_write_internal_notes_ask_for_information_or_link(world: World) -> None:
    alice, bob = world.person("alice"), world.person("bob")
    one, two = world.file(alice), world.file(alice, title="Dark mode please", category="Other")
    patch = SubmissionPatch(version=1, status="Planned")
    with pytest.raises(PolicyBlocked):
        world.svc.patch(alice, one.id, patch)
    for body in (
        CommentIn(body="note", visibility="internal"),
        CommentIn(body="please send more", kind="info_request"),
    ):
        with pytest.raises(PolicyBlocked):
            world.svc.comment(alice, one.id, body)
    with pytest.raises(PolicyBlocked):
        world.svc.relate(alice, one.id, RelationIn(to=two.id, kind="related"))
    with pytest.raises(PolicyBlocked):
        world.svc.audit_trail(alice, one.id)
    with pytest.raises(PolicyBlocked):
        world.svc.assignees(alice)
    with pytest.raises(
        PolicyBlocked
    ):  # another reporter is refused the change itself, before anything about the submission
        world.svc.patch(bob, one.id, patch)


# ===================================================================================================== workflow
def test_status_changes_follow_the_workflow_keep_their_history_and_set_the_times(world: World) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    made = world.file(alice, ISSUE)
    with pytest.raises(UserError, match="status must be one of"):
        world.triage(maria, made.id, status="Planned")  # a feature status
    with pytest.raises(UserError, match="goes with a status change"):
        world.triage(maria, made.id, status_comment="why?")

    world.clock.advance(hours=1)
    d = world.triage(maria, made.id, status="Investigating", status_comment="Looking now")
    assert d.status == "Investigating" and d.resolved_at is None and d.closed_at is None
    world.clock.advance(hours=1)
    d = world.triage(maria, made.id, status="Fix Available")  # resolved, not closed
    assert d.resolved_at is not None and d.closed_at is None
    resolved_at = d.resolved_at
    world.clock.advance(hours=1)
    d = world.triage(maria, made.id, status="Closed")  # closed; the resolution time is kept
    assert d.closed_at is not None and d.resolved_at == resolved_at
    world.clock.advance(hours=1)
    d = world.triage(maria, made.id, status="Investigating")  # re-opened: both are cleared
    assert d.resolved_at is None and d.closed_at is None

    assert [(h.old_status, h.new_status) for h in d.history] == [
        (None, "Submitted"),
        ("Submitted", "Investigating"),
        ("Investigating", "Fix Available"),
        ("Fix Available", "Closed"),
        ("Closed", "Investigating"),
    ]
    assert d.history[1].comment == "Looking now" and d.history[1].changed_by == "maria"
    assert d.history[0].changed_by == "alice" and d.history[0].by_staff is False


def test_a_change_made_from_a_stale_version_is_refused_and_changes_nothing(world: World) -> None:
    alice, maria, nina = world.person("alice"), world.maintainer("maria"), world.maintainer("nina")
    made = world.file(alice)
    v = made.version
    world.clock.advance(minutes=1)
    first = world.svc.patch(maria, made.id, SubmissionPatch(version=v, status="Under Review", assigned_priority="high"))
    assert first.version == v + 1
    with pytest.raises(VersionConflict):
        world.svc.patch(nina, made.id, SubmissionPatch(version=v, status="Declined", labels=["x"]))
    now = world.svc.get(nina, made.id)
    assert (now.status, now.labels, now.version) == ("Under Review", [], v + 1), "the refused change left no trace"
    assert [h.new_status for h in now.history] == ["Submitted", "Under Review"]
    ok = world.svc.patch(nina, made.id, SubmissionPatch(version=now.version, status="Planned"))
    assert ok.status == "Planned" and ok.version == v + 2


def test_a_change_that_changes_nothing_is_not_a_conflict_and_keeps_the_version(world: World) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    made = world.file(alice)
    same = world.svc.patch(maria, made.id, SubmissionPatch(version=999, status="Submitted"))
    assert same.version == made.version and same.status == "Submitted"
    assert len(same.history) == 1


def test_clearing_and_assigning(world: World) -> None:
    alice, maria, root = world.person("alice"), world.maintainer("maria"), world.person("root", "admin")
    made = world.file(alice)
    d = world.triage(maria, made.id, assigned_priority="high", severity="low", assignee_id=maria.user_id)
    assert (d.assigned_priority, d.severity, d.assignee.name) == ("high", "low", "maria")  # type: ignore[union-attr]
    d = world.triage(maria, made.id, assigned_priority=None, assignee_id="")
    assert (d.assigned_priority, d.severity, d.assignee) == (None, "low", None), "null clears; what is not sent stays"
    assert world.svc.search(maria, SubmissionFilter(assignee="none")).total == 1
    assert world.svc.search(maria, SubmissionFilter(assignee="me")).total == 0
    assert {a.name for a in world.svc.assignees(maria)} == {"maria", "root"}
    for not_assignable in (alice.user_id, "no-such-account"):
        with pytest.raises(UserError, match="maintainer or admin"):
            world.triage(maria, made.id, assignee_id=not_assignable)
    world.accounts.update("test", maria.user_id, status="disabled")  # type: ignore[arg-type]
    with pytest.raises(UserError, match="maintainer or admin"):
        world.triage(root, made.id, assignee_id=maria.user_id)


def test_labels_are_replaced_whole_and_kept_tidy(world: World) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    made = world.file(alice)
    assert world.triage(maria, made.id, labels=["CSV", "csv", "reports", "needs design"]).labels == [
        "CSV",
        "needs design",
        "reports",
    ]
    assert world.triage(maria, made.id, labels=["reports"]).labels == ["reports"]
    assert world.triage(maria, made.id, labels=[]).labels == []
    for bad in ("", " ", "-leading", "a" * 61, "semi;colon", "<script>"):
        with pytest.raises(ValidationError):
            SubmissionPatch(version=1, labels=[bad])


def test_moving_into_the_security_category_makes_it_private_and_never_the_other_way(world: World) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    made = world.file(alice, ISSUE)
    assert made.visibility == "normal"
    d = world.triage(maria, made.id, category="Suspected Security Issue")
    assert d.visibility == "security"
    d = world.triage(maria, made.id, category="Application Bug")
    assert d.visibility == "security", "changing the category back does not publish it"
    assert world.triage(maria, made.id, visibility="normal").visibility == "normal"
    with pytest.raises(UserError, match="category must be one of"):
        world.triage(maria, made.id, category="Export reports")


# ===================================================================================== the unread marker
def test_unread_follows_what_the_reporter_can_see_and_clears_when_they_look(world: World) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    made = world.file(alice)
    assert world.svc.get(alice, made.id).unread is False
    world.clock.advance(minutes=5)
    world.triage(maria, made.id, assigned_priority="high", labels=["x"])
    world.svc.comment(maria, made.id, CommentIn(body="private thought", visibility="internal"))
    assert world.svc.get(alice, made.id).unread is False, "nothing the reporter can see has changed"
    world.clock.advance(minutes=5)
    world.svc.comment(maria, made.id, CommentIn(body="We are on it"))
    assert world.svc.get(alice, made.id).unread is True
    assert world.svc.summary(alice).unread == 1 and world.svc.search(alice, SubmissionFilter(unread=True)).total == 1
    world.svc.mark_seen(alice, made.id)
    assert world.svc.get(alice, made.id).unread is False and world.svc.summary(alice).unread == 0
    world.clock.advance(minutes=5)
    world.triage(maria, made.id, status="Under Review")
    assert world.svc.get(alice, made.id).unread is True
    world.svc.comment(alice, made.id, CommentIn(body="thanks"))  # writing in the thread means they have read it
    assert world.svc.get(alice, made.id).unread is False


def test_a_maintainers_own_submission_is_not_unread_because_of_their_own_actions(world: World) -> None:
    maria = world.maintainer()
    made = world.file(maria)
    world.clock.advance(minutes=1)
    world.triage(maria, made.id, status="Under Review")
    world.svc.comment(maria, made.id, CommentIn(body="note to self"))
    assert world.svc.get(maria, made.id).unread is False and world.svc.summary(maria).unread == 0


# ======================================================================================== allowances
def test_a_reporter_has_a_daily_allowance_of_submissions_that_slides_and_a_maintainer_has_none(
    make: Callable[..., World],
) -> None:
    w = make(max_submissions_per_day=2)
    alice, maria = w.person("alice"), w.maintainer()
    w.file(alice)
    w.clock.advance(hours=1)
    w.file(alice, title="Second idea here", category="Other")
    with pytest.raises(QuotaExceeded) as refused:
        w.file(alice, title="Third idea here", category="Other")
    assert 22 * 3600 < refused.value.retry_after_seconds <= 23 * 3600 + 60 and "2 submissions" in str(refused.value)
    w.clock.advance(hours=23, minutes=1)  # the first one has left the window
    assert w.file(alice, title="Third idea here", category="Other").number == "FR-2026-00003"
    for i in range(5):
        w.file(maria, title=f"Maintainer idea {i}", category="Other")  # no allowance


def test_a_reporter_has_a_daily_allowance_of_comments(make: Callable[..., World]) -> None:
    w = make(max_comments_per_day=3)
    alice = w.person("alice")
    made = w.file(alice)
    for i in range(3):
        w.svc.comment(alice, made.id, CommentIn(body=f"comment {i}"))
    with pytest.raises(QuotaExceeded):
        w.svc.comment(alice, made.id, CommentIn(body="one more"))
    w.clock.advance(hours=25)
    assert w.svc.comment(alice, made.id, CommentIn(body="a new day")).body == "a new day"


# =========================================================================== finding and suggesting
def test_search_treats_wildcards_and_quotes_as_text(world: World) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    world.file(alice, title="100% of runs fail", category="Other")
    world.file(alice, title="Use snake_case names", category="Other")
    world.file(alice, title="Plain title here", category="Other")
    count = lambda q: world.svc.search(maria, SubmissionFilter(q=q)).total  # noqa: E731
    assert (count("%"), count("_"), count("100%"), count("snake_case")) == (1, 1, 1, 1)
    assert count("' OR '1'='1") == 0 and count("\\") == 0 and count('"; drop table submissions; --') == 0
    assert count("FR-2026-00003") == 1, "a number finds the submission"
    assert count("PLAIN") == 1, "case does not matter"


def test_paging_and_ordering(world: World) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    made = []
    for i in range(5):
        world.clock.advance(minutes=1)
        made.append(world.file(alice, title=f"Idea number {i}", category="Other"))
    world.clock.advance(minutes=1)
    world.triage(maria, made[0].id, assigned_priority="critical")
    world.clock.advance(minutes=1)
    world.triage(maria, made[3].id, assigned_priority="low")
    page = world.svc.search(maria, SubmissionFilter(limit=2))
    assert page.total == 5 and [i.title for i in page.items] == ["Idea number 3", "Idea number 0"], (
        "the latest activity first"
    )
    by_priority = world.svc.search(maria, SubmissionFilter(sort="priority")).items
    assert [i.title for i in by_priority][:2] == ["Idea number 0", "Idea number 3"]
    oldest = world.svc.search(maria, SubmissionFilter(sort="created", descending=False)).items
    assert [i.title for i in oldest] == [f"Idea number {i}" for i in range(5)]
    tail = world.svc.search(maria, SubmissionFilter(sort="created", descending=False, limit=2, offset=4))
    assert [i.title for i in tail.items] == ["Idea number 4"] and tail.total == 5


def test_similar_suggestions_share_words_with_the_title_and_come_from_what_the_caller_sees(world: World) -> None:
    alice, bob, maria = world.person("alice"), world.person("bob"), world.maintainer()
    csv = world.file(alice)
    world.file(alice, title="Dark mode for the dashboard", category="User Interface / UX")
    world.file(bob, title="Export every report to a CSV file", category="Reporting and Analytics")
    found = world.svc.similar(maria, "feature", "export reports as csv")
    assert [s.title for s in found][0] in {"Export reports as CSV", "Export every report to a CSV file"}
    assert len(found) == 2 and all(0 < s.score <= 1 for s in found)
    assert [s.id for s in world.svc.similar(alice, "feature", "export reports as csv")] == [csv.id]
    assert world.svc.similar(maria, "feature", "xyzzy plugh") == []
    assert world.svc.similar(maria, "issue", "export reports as csv") == [], "a different type is not suggested"
    assert world.svc.similar(maria, "feature", "the and for") == [], "stop words alone match nothing"


# ================================================================================================ links
def test_links_between_submissions(world: World) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    a, b = world.file(alice), world.file(alice, title="Export results to a spreadsheet", category="Other")
    issue = world.file(alice, ISSUE)
    d = world.svc.relate(maria, b.id, RelationIn(to=a.number, kind="duplicate_of"))
    assert [(r.kind, r.direction, r.other_number) for r in d.relations] == [("duplicate_of", "from", a.number)]
    seen_from_a = world.svc.get(maria, a.id)
    assert [(r.direction, r.other_number, r.other_title) for r in seen_from_a.relations] == [
        ("to", b.number, "Export results to a spreadsheet")
    ]
    for body, message in (
        (RelationIn(to=b.id, kind="related"), "itself"),
        (RelationIn(to=a.id, kind="duplicate_of"), "already linked"),
    ):
        with pytest.raises(UserError, match=message):
            world.svc.relate(maria, b.id, body)
    with pytest.raises(UserError, match="duplicates of each other"):
        world.svc.relate(maria, a.id, RelationIn(to=b.id, kind="duplicate_of"))
    with pytest.raises(UserError, match="not duplicates"):
        world.svc.relate(maria, issue.id, RelationIn(to=a.id, kind="duplicate_of"))
    with pytest.raises(NotFoundError):
        world.svc.relate(maria, a.id, RelationIn(to="FR-2026-99999", kind="related"))
    gone = world.svc.unrelate(maria, b.id, d.relations[0].id)
    assert gone.relations == []
    with pytest.raises(NotFoundError):
        world.svc.unrelate(maria, b.id, d.relations[0].id)


def test_a_link_to_something_the_caller_may_not_see_is_neither_made_nor_shown(world: World) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    secret = world.file(alice, ISSUE, suspected_security=True)
    open_one = world.file(alice, ISSUE, title="A different issue here")
    token = world.token
    with pytest.raises(NotFoundError):
        world.svc.relate(token, open_one.id, RelationIn(to=secret.id, kind="related"))
    world.svc.relate(maria, open_one.id, RelationIn(to=secret.id, kind="related"))  # a maintainer may
    assert [r.other_number for r in world.svc.get(maria, open_one.id).relations] == [secret.number]
    assert world.svc.get(token, open_one.id).relations == [], "the token is not shown a private one's number or title"


# ==================================================================================================== audit
def test_the_audit_trail_says_what_happened_and_never_holds_the_text(world: World) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    made = world.file(alice, description=f"{SECRET} is in my log, and my phone is 555-0100", title="Leaky log file")
    world.triage(maria, made.id, status="Under Review", assigned_priority="high", labels=["urgent"])
    world.svc.comment(maria, made.id, CommentIn(body="TOP SECRET WORDS", visibility="internal"))
    events = world.svc.audit_trail(maria, made.id)
    assert sorted(e.event for e in events) == ["commented", "created", "status_changed", "updated"]
    assert all(e.actor in {"alice", "maria"} for e in events)
    blob = json.dumps([e.model_dump(mode="json") for e in events])
    for text_ in ("TOP SECRET WORDS", "555-0100", "Leaky log file", "Zq81LmN4xW7sR2pK9vB3cD6e"):
        assert text_ not in blob, f"the audit trail held {text_!r}"
    created = next(e for e in events if e.event == "created")
    assert created.detail["number"] == made.number and created.detail["visibility"] == "normal"
    status = next(e for e in events if e.event == "status_changed")
    assert (status.detail["old"], status.detail["new"]) == ("Submitted", "Under Review")


def test_opening_someone_elses_private_submission_and_probing_for_one_are_recorded(world: World) -> None:
    alice, bob, maria = world.person("alice"), world.person("bob"), world.maintainer()
    private = world.file(alice, ISSUE, suspected_security=True)
    world.svc.get(alice, private.id)  # the reporter reading their own is not an event
    world.svc.get(maria, private.id)
    for _ in range(4):  # a probe repeated within the window is recorded once
        with pytest.raises(NotFoundError):
            world.svc.get(bob, private.id)
    events = world.svc.audit_trail(maria, private.id)
    kinds = [(e.event, e.actor) for e in events]
    assert kinds.count(("viewed", "maria")) == 1 and kinds.count(("access_denied", "bob")) == 1
    assert ("viewed", "alice") not in kinds
    with pytest.raises(NotFoundError):
        world.svc.get(bob, "00000000-0000-0000-0000-000000000000")
    with world.db.session() as s:
        total = s.execute(text("select count(*) from submission_audit_events where event='access_denied'")).scalar()
    assert total == 1, "a probe for something that does not exist leaves no record"


# ================================================================================================= storage
def test_the_migration_creates_the_feedback_tables_and_can_be_undone(tmp_path: Path) -> None:
    from agentlab.storage.migrate import current_head, downgrade

    db = Database(f"sqlite:///{tmp_path}/m.db")
    db.migrate()
    mine = {
        "submissions",
        "feature_request_details",
        "issue_report_details",
        "submission_comments",
        "submission_status_history",
        "submission_labels",
        "submission_relations",
        "submission_audit_events",
    }
    assert current_head() >= "0003" and mine <= set(inspect(db.engine).get_table_names())
    downgrade(db.url, "0002")
    after = set(inspect(db.engine).get_table_names())
    assert not mine & after and {"users", "user_sessions"} <= after, "the accounts tables are untouched"
    db.migrate()
    assert mine <= set(inspect(db.engine).get_table_names())


def test_deleting_a_submission_takes_its_children_and_deleting_an_account_keeps_what_they_made(world: World) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    made = world.file(alice)
    kept = world.file(alice, title="Stays when the account goes", category="Other")
    world.svc.comment(maria, made.id, CommentIn(body="hi"))
    with world.db.session() as s:
        s.execute(text("delete from submissions where id = :i"), {"i": made.id})
    with world.db.session() as s:
        count = lambda table: s.execute(text(f"select count(*) from {table}")).scalar()  # noqa: E731
        assert count("feature_request_details") == 1, "only the other submission's details are left"
        assert count("submission_comments") == 0, "the comment went with the submission it was on"
        assert count("submission_status_history") == 1
        assert s.execute(text("select count(*) from submission_audit_events")).scalar() >= 2, "the record outlives it"
        s.execute(text("delete from users where id = :i"), {"i": alice.user_id})
    gone = world.svc.get(maria, kept.id)
    assert gone.title == "Stays when the account goes" and gone.reporter == "alice" and gone.reporter_id is None


# ======================================================================================== configuration
def test_the_lists_in_the_configuration_must_agree() -> None:
    assert FeedbackConfig().security_category in FeedbackConfig().issue_categories
    for bad, match in (
        ({"closed_statuses": ["Nonexistent"]}, "no workflow has"),
        ({"resolved_statuses": ["Nonexistent"]}, "no workflow has"),
        ({"security_category": "Other things"}, "security_category"),
        ({"issue_statuses": []}, "issue_statuses"),
        ({"feature_categories": ["A", "A"]}, "feature_categories"),
        ({"max_submissions_per_day": 0}, "greater than or equal"),
    ):
        with pytest.raises(ValidationError, match=match):
            FeedbackConfig(**bad)
    custom = FeedbackConfig(
        feature_statuses=["New", "Done"],
        closed_statuses=["Done"],
        resolved_statuses=["Done"],
        issue_statuses=["New", "Done"],
    )
    assert custom.feature_statuses[0] == "New"
