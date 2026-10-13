"""Accounts and sessions, without a server: registration, sign-in, locking, sessions, roles and the audit trail."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text

from agentlab.accounts import AccountConflict, AccountLocked, AccountService, SignInFailed
from agentlab.core.config import AccountsConfig
from agentlab.core.errors import NotFoundError, PolicyBlocked, UserError
from agentlab.storage import orm
from agentlab.storage.db import Database

PASSWORD = "correct-horse-battery-7"  # made up
OTHER = "another-long-secret-phrase-9"  # made up


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def make(tmp_path: Path, clock: Clock):
    def build(**config) -> AccountService:  # type: ignore[no-untyped-def]
        db = Database(f"sqlite:///{tmp_path}/accounts.db")
        db.migrate()
        return AccountService(db, AccountsConfig(**config), clock=clock)

    return build


def test_the_migration_creates_the_account_tables_and_can_be_undone(tmp_path: Path) -> None:
    from sqlalchemy import inspect

    from agentlab.storage.migrate import current_head, downgrade

    db = Database(f"sqlite:///{tmp_path}/m.db")
    db.migrate()
    mine = {"users", "user_sessions", "account_events"}
    assert current_head() >= "0002" and mine <= set(inspect(db.engine).get_table_names())
    downgrade(db.url, "0001")
    assert not mine & set(inspect(db.engine).get_table_names())
    db.migrate()  # and forward again
    assert mine <= set(inspect(db.engine).get_table_names())


def test_registration_makes_a_reporter_and_normalises_the_name(make) -> None:  # type: ignore[no-untyped-def]
    svc = make()
    user = svc.register("  Alice.Smith ", PASSWORD, display_name="Alice", client="10.0.0.1")
    assert (user.username, user.role, user.status, user.display_name) == ("alice.smith", "reporter", "active", "Alice")
    assert svc.by_username("ALICE.SMITH") is not None
    with pytest.raises(AccountConflict):
        svc.register("alice.smith", OTHER, client="10.0.0.2")
    for bad in ("ab", "has space", "../etc", "", "x" * 65, "-leading"):
        with pytest.raises(UserError):
            svc.register(bad, PASSWORD, client="10.0.0.3")


def test_weak_passwords_are_refused_with_the_reason(make) -> None:  # type: ignore[no-untyped-def]
    svc = make(min_password_length=14)
    with pytest.raises(UserError, match="at least 14"):
        svc.register("bob", PASSWORD[:10])
    with pytest.raises(UserError, match="user name"):
        svc.register("bobbobbob", "bobbobbob-and-more-9")


def test_registration_follows_the_configured_mode(make) -> None:  # type: ignore[no-untyped-def]
    closed = make(registration="closed")
    with pytest.raises(PolicyBlocked, match="closed"):
        closed.register("carol", PASSWORD)
    approval = make(registration="approval")
    pending = approval.register("dave", PASSWORD, client="1.1.1.1")
    assert pending.status == "pending"
    with pytest.raises(AccountConflict, match="approve"):
        approval.authenticate("dave", PASSWORD, client="1.1.1.1")
    approval.update("admin", pending.id, status="active")
    assert approval.authenticate("dave", PASSWORD, client="1.1.1.1").username == "dave"


def test_the_default_role_of_a_registration_is_configuration(make) -> None:  # type: ignore[no-untyped-def]
    assert make(default_role="maintainer").register("erin", PASSWORD).role == "maintainer"


def test_registrations_are_limited_per_client_address(make) -> None:  # type: ignore[no-untyped-def]
    svc = make(registrations_per_hour=2)
    svc.register("user-one", PASSWORD, client="9.9.9.9")
    svc.register("user-two", PASSWORD, client="9.9.9.9")
    with pytest.raises(AccountLocked) as caught:
        svc.register("user-three", PASSWORD, client="9.9.9.9")
    assert caught.value.retry_after_seconds > 0
    assert svc.register("user-four", PASSWORD, client="8.8.8.8").username == "user-four"


def test_sign_in_says_the_same_thing_for_a_wrong_password_and_an_unknown_user(make) -> None:  # type: ignore[no-untyped-def]
    svc = make()
    svc.register("frank", PASSWORD, client="c")
    messages = set()
    for name, password in (("frank", "wrong-password-123"), ("nobody", PASSWORD)):
        with pytest.raises(SignInFailed) as caught:
            svc.authenticate(name, password, client="c")
        messages.add(str(caught.value))
    assert len(messages) == 1
    assert svc.authenticate("FRANK", PASSWORD, client="c").username == "frank"


def test_repeated_wrong_passwords_lock_the_account_for_a_while_and_the_count_survives_the_failure(make, clock) -> None:  # type: ignore[no-untyped-def]
    svc = make(max_failed_attempts=3, lock_minutes=10)
    user = svc.register("gina", PASSWORD, client="c")
    for _ in range(3):
        with pytest.raises(SignInFailed):
            svc.authenticate("gina", "wrong-password-123", client="c")
    with pytest.raises(AccountLocked) as caught:
        svc.authenticate("gina", PASSWORD, client="c")  # even the right password is refused while locked
    assert 0 < caught.value.retry_after_seconds <= 601
    clock.advance(minutes=11)
    assert svc.authenticate("gina", PASSWORD, client="c").id == user.id
    # an administrator can end the lock early
    for _ in range(3):
        with pytest.raises(SignInFailed):
            svc.authenticate("gina", "wrong-password-123", client="c")
    svc.unlock("admin", user.id)
    assert svc.authenticate("gina", PASSWORD, client="c").username == "gina"


def test_one_address_cannot_try_without_limit_whoever_it_tries_to_be(make) -> None:  # type: ignore[no-untyped-def]
    svc = make()
    for i in range(30):
        with pytest.raises(SignInFailed):
            svc.authenticate(f"nobody-{i}", "wrong-password-123", client="attacker")
    with pytest.raises(AccountLocked):
        svc.authenticate("nobody-x", "wrong-password-123", client="attacker")


def test_a_session_lasts_while_it_is_used_and_ends_when_idle_or_too_old(make, clock) -> None:  # type: ignore[no-untyped-def]
    svc = make(session_idle_minutes=30, session_absolute_hours=2)
    user = svc.register("hank", PASSWORD, client="c")
    token, csrf, _ = svc.create_session(user.id, client="c", user_agent="test")
    principal = svc.principal_for_session(token)
    assert principal and principal.username == "hank" and principal.csrf_token == csrf and principal.kind == "user"
    for _ in range(4):  # used every 25 minutes (100 minutes in all): never idle for 30, and inside the 2 hours
        clock.advance(minutes=25)
        assert svc.principal_for_session(token) is not None
    clock.advance(hours=3)  # past the absolute limit
    assert svc.principal_for_session(token) is None
    token2, _, _ = svc.create_session(user.id, client="c")
    clock.advance(minutes=31)
    assert svc.principal_for_session(token2) is None, "idle for more than 30 minutes"


def test_the_cookie_value_is_not_stored_and_a_made_up_one_is_refused(make) -> None:  # type: ignore[no-untyped-def]
    svc = make()
    user = svc.register("iris", PASSWORD, client="c")
    token, _, _ = svc.create_session(user.id, client="c")
    with svc.db.session() as s:
        stored = s.scalars(select(orm.UserSessionRow.token_hash)).all()
    assert token not in stored and len(stored[0]) == 64
    assert svc.principal_for_session("x" * 43) is None and svc.principal_for_session("") is None


def test_sign_out_disabling_and_a_role_change_end_the_sessions(make) -> None:  # type: ignore[no-untyped-def]
    svc = make()
    admin, _ = svc.create_user("cli", "root-admin", role="admin", password=PASSWORD)
    user = svc.register("jack", PASSWORD, client="c")
    first, _, _ = svc.create_session(user.id, client="c")
    svc.revoke_session(first)
    assert svc.principal_for_session(first) is None
    second, _, _ = svc.create_session(user.id, client="c")
    svc.update(admin.username, user.id, role="maintainer")
    assert svc.principal_for_session(second) is None, "a new role needs a new sign-in"
    third, _, _ = svc.create_session(user.id, client="c")
    svc.update(admin.username, user.id, status="disabled")
    assert svc.principal_for_session(third) is None
    with pytest.raises(SignInFailed):
        svc.authenticate("jack", PASSWORD, client="c")


def test_the_last_active_administrator_cannot_be_demoted_or_disabled(make) -> None:  # type: ignore[no-untyped-def]
    svc = make()
    only, _ = svc.create_user("cli", "the-admin", role="admin", password=PASSWORD)
    with pytest.raises(AccountConflict, match="last active administrator"):
        svc.update("x", only.id, role="maintainer")
    with pytest.raises(AccountConflict):
        svc.update("x", only.id, status="disabled")
    second, _ = svc.create_user("cli", "second-admin", role="admin", password=OTHER)
    assert svc.update("x", only.id, role="maintainer").role == "maintainer"
    with pytest.raises(AccountConflict):
        svc.update("x", second.id, status="disabled")


def test_a_temporary_password_has_to_be_changed_and_the_change_ends_the_other_sessions(make) -> None:  # type: ignore[no-untyped-def]
    svc = make()
    user, temporary = svc.create_user("cli", "kate", role="reporter")
    assert temporary and user.must_change_password
    assert svc.authenticate("kate", temporary, client="c").must_change_password
    a, _, _ = svc.create_session(user.id, client="c")
    b, _, _ = svc.create_session(user.id, client="c")
    keep = svc.principal_for_session(a)
    assert keep and keep.must_change_password
    with pytest.raises(SignInFailed):
        svc.change_password(user.id, "not-the-current-one-1", OTHER, client="c")
    with pytest.raises(UserError, match="differ"):
        svc.change_password(user.id, temporary, temporary, client="c")
    svc.change_password(user.id, temporary, OTHER, keep_session=keep.session_id, client="c")
    assert svc.principal_for_session(a) is not None and svc.principal_for_session(b) is None
    assert not svc.get(user.id).must_change_password
    assert svc.authenticate("kate", OTHER, client="c").username == "kate"


def test_an_administrator_reset_gives_a_temporary_password_and_ends_sessions_and_locks(make) -> None:  # type: ignore[no-untyped-def]
    svc = make(max_failed_attempts=3)
    user = svc.register("liam", PASSWORD, client="c")
    token, _, _ = svc.create_session(user.id, client="c")
    for _ in range(3):
        with pytest.raises(SignInFailed):
            svc.authenticate("liam", "wrong-password-123", client="c")
    temporary = svc.reset_password("root", user.id)
    assert svc.principal_for_session(token) is None
    signed = svc.authenticate("liam", temporary, client="c")
    assert signed.must_change_password
    with pytest.raises(NotFoundError):
        svc.reset_password("root", "no-such-id")


def test_no_password_or_hash_or_token_reaches_the_audit_trail_or_any_view(make) -> None:  # type: ignore[no-untyped-def]
    svc = make(max_failed_attempts=3)
    user = svc.register("mona", PASSWORD, client="c")
    token, csrf, _ = svc.create_session(user.id, client="c")
    for _ in range(3):
        with pytest.raises(SignInFailed):
            svc.authenticate("mona", "wrong-password-123", client="c")
    svc.reset_password("root", user.id)
    audit = json.dumps(svc.events(500), default=str)
    for secret in (PASSWORD, "wrong-password-123", token, csrf):
        assert secret not in audit
    with svc.db.session() as s:
        user_row = s.get(orm.UserRow, user.id)
        assert user_row is not None and PASSWORD not in user_row.password_hash
        dump = "".join(str(r) for r in s.execute(text("select * from users")).all())
    assert PASSWORD not in dump
    assert {e["event"] for e in svc.events(500)} >= {"registered", "sign_in_failed", "locked", "password_reset"}


def test_a_name_with_a_nul_byte_is_an_unknown_name_and_never_reaches_the_database(make) -> None:  # type: ignore[no-untyped-def]
    """PostgreSQL refuses a NUL byte in text, so looking one up would be a server error: it is answered like any unknown name."""
    from sqlalchemy import event

    svc = make()
    svc.create_user("test", "alice", role="reporter", password=PASSWORD)
    sent: list[object] = []

    @event.listens_for(svc.db.engine, "before_cursor_execute")
    def spy(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        sent.append(parameters)

    for name in ("ali\x00ce", "\x00", "alice\x00"):
        with pytest.raises(SignInFailed, match="wrong user name or password"):
            svc.authenticate(name, PASSWORD)
        assert svc.by_username(name) is None
    assert sent and not any("\x00" in repr(p) for p in sent), "no statement carried the NUL byte"
    events = [e for e in svc.events() if e["event"] == "sign_in_failed"]
    assert len(events) == 3 and all("\x00" not in e["actor"] for e in events)


def test_a_display_name_and_an_address_are_one_line_without_control_characters(make) -> None:  # type: ignore[no-untyped-def]
    """PostgreSQL refuses a NUL byte in text, and no name needs a line break or an escape character."""
    svc = make()
    user, _ = svc.create_user(
        "test",
        "bruno",
        role="reporter",
        password=PASSWORD,
        display_name="Bru\x00no\n\x1b[31mDias\x7f",
        email="b\x00@example.org",
    )
    assert user.display_name == "Bruno [31mDias" and user.email == "b@example.org"
    changed = svc.update("test", user.id, display_name="Br\x00uno\r\nD")
    assert changed.display_name == "Bruno D"
