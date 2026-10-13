"""Accounts, sign-in and sessions. Everything that decides who someone is lives here, so it can be tested without a server."""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult

from agentlab.accounts import passwords
from agentlab.accounts.errors import AccountConflict, AccountLocked, SignInFailed
from agentlab.accounts.models import ROLES, Principal, Role, UserView
from agentlab.core.config import AccountsConfig
from agentlab.core.errors import NotFoundError, PolicyBlocked, UserError
from agentlab.core.ids import utcnow
from agentlab.storage import orm
from agentlab.storage.db import Database

USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
TEMP_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
TOUCH_EVERY = timedelta(seconds=60)  # a session's last-seen time is written at most this often
SIGN_IN_WINDOW_SECONDS = 600.0
SIGN_IN_PER_CLIENT = 30  # sign-in attempts one address may make in the window, whoever it tries to be
STATUSES = ("active", "pending", "disabled")


def _one_line(text: str) -> str:
    """A name or an address on one line, with no control characters: PostgreSQL refuses a NUL byte in text, and no name
    needs a line break. A line break or a tab becomes a space; any other control character is dropped."""
    spaced = "".join(" " if c in "\t\n\r" else c for c in text)
    kept = "".join(c for c in spaced if c >= " " and c != "\x7f")
    return " ".join(kept.split())


def _aware(moment: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; everything here is UTC."""
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalise_username(raw: str) -> str:
    name = (raw or "").strip().casefold()
    if not USERNAME.match(name):
        raise UserError(
            "a user name is 3 to 64 characters: letters, digits, dot, dash and underscore, starting with a letter or a digit"
        )
    return name


def temporary_password() -> str:
    return "".join(secrets.choice(TEMP_ALPHABET) for _ in range(20))


class AccountService:
    def __init__(self, db: Database, config: AccountsConfig, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.db = db
        self.config = config
        self._now = clock
        self._lock = threading.Lock()
        self._registrations: dict[str, deque[float]] = defaultdict(deque)
        self._sign_ins: dict[str, deque[float]] = defaultdict(deque)

    # ------------------------------------------------------------------------------------------------- views
    @staticmethod
    def _view(row: orm.UserRow) -> UserView:
        return UserView(
            id=row.id,
            username=row.username,
            display_name=row.display_name or row.username,
            email=row.email,
            role=row.role,  # type: ignore[arg-type]
            status=row.status,
            must_change_password=bool(row.must_change_password),
            created_at=_aware(row.created_at),  # type: ignore[arg-type]
            last_login_at=_aware(row.last_login_at),
            locked_until=_aware(row.locked_until),
        )

    def _audit(
        self, session: Any, event: str, *, actor: str = "", subject: str = "", client: str = "", **detail: Any
    ) -> None:
        session.add(
            orm.AccountEventRow(event=event, actor=actor[:80], subject=subject[:80], client=client[:64], detail=detail)
        )

    def record(self, event: str, *, actor: str = "", subject: str = "", client: str = "", **detail: Any) -> None:
        with self.db.session() as s:
            self._audit(s, event, actor=actor, subject=subject, client=client, **detail)

    def events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.db.session() as s:
            rows = s.scalars(select(orm.AccountEventRow).order_by(orm.AccountEventRow.created_at.desc()).limit(limit))
            return [
                {
                    "event": r.event,
                    "actor": r.actor,
                    "subject": r.subject,
                    "client": r.client,
                    "detail": r.detail,
                    "at": _aware(r.created_at),
                }
                for r in rows
            ]

    # --------------------------------------------------------------------------------------------- rate limits
    def _throttle(self, table: dict[str, deque[float]], client: str, limit: int, window: float) -> int:
        """How many seconds until ``client`` may try again (0: it may now). Counts the attempt when it may."""
        now = time.monotonic()
        with self._lock:
            hits = table[client]
            while hits and hits[0] < now - window:
                hits.popleft()
            if len(hits) >= limit:
                return max(1, int(window - (now - hits[0])))
            hits.append(now)
            if len(table) > 4096:  # many addresses must not grow this without bound
                table.pop(next(iter(table)))
        return 0

    # ----------------------------------------------------------------------------------------------- queries
    def get(self, user_id: str) -> UserView:
        with self.db.session() as s:
            row = s.get(orm.UserRow, user_id)
            if row is None:
                raise NotFoundError("no such account")
            return self._view(row)

    def by_username(self, username: str) -> UserView | None:
        name = username.strip().casefold()
        if "\x00" in name:
            return None  # PostgreSQL text cannot hold a NUL byte, and no account has one in its name
        with self.db.session() as s:
            row = s.scalar(select(orm.UserRow).where(orm.UserRow.username == name))
            return self._view(row) if row else None

    def list_users(self, *, limit: int = 200) -> list[UserView]:
        with self.db.session() as s:
            rows = s.scalars(select(orm.UserRow).order_by(orm.UserRow.created_at).limit(limit))
            return [self._view(r) for r in rows]

    def count(self) -> int:
        with self.db.session() as s:
            return int(s.scalar(select(func.count()).select_from(orm.UserRow)) or 0)

    def _active_admins(self, s: Any) -> int:
        return int(
            s.scalar(
                select(func.count())
                .select_from(orm.UserRow)
                .where(orm.UserRow.role == "admin", orm.UserRow.status == "active")
            )
            or 0
        )

    # -------------------------------------------------------------------------------------------- creation
    def _check_password(self, password: str, username: str) -> None:
        problems = passwords.problems_with(password, username=username, min_length=self.config.min_password_length)
        if problems:
            raise UserError("that password is not accepted: " + "; ".join(problems))

    def _insert(
        self,
        s: Any,
        *,
        username: str,
        password: str,
        role: Role,
        status: str,
        display_name: str,
        email: str | None,
        must_change: bool,
        created_by: str | None,
    ) -> orm.UserRow:
        if s.scalar(select(orm.UserRow.id).where(orm.UserRow.username == username)):
            raise AccountConflict("that user name is taken")
        row = orm.UserRow(
            username=username,
            display_name=_one_line(display_name or username)[:120],
            email=(email or None) and _one_line(email)[:254],
            role=role,
            status=status,
            password_hash=passwords.hash_password(password),
            must_change_password=must_change,
            created_by=created_by,
        )
        s.add(row)
        s.flush()
        return row

    def register(
        self, username: str, password: str, *, display_name: str = "", email: str | None = None, client: str = ""
    ) -> UserView:
        """Open registration: a person makes their own account, as the configured default role (a reporter unless the
        owner chose otherwise), active at once or waiting for an admin, depending on ``accounts.registration``."""
        if self.config.registration == "closed":
            raise PolicyBlocked("registration is closed on this server: ask an administrator for an account")
        wait = self._throttle(self._registrations, client or "unknown", self.config.registrations_per_hour, 3600.0)
        if wait:
            raise AccountLocked(
                "too many accounts were registered from this address; try again later", retry_after_seconds=wait
            )
        name = normalise_username(username)
        self._check_password(password, name)
        status = "active" if self.config.registration == "open" else "pending"
        with self.db.session() as s:
            row = self._insert(
                s,
                username=name,
                password=password,
                role=self.config.default_role,
                status=status,
                display_name=display_name,
                email=email,
                must_change=False,
                created_by=None,
            )
            self._audit(s, "registered", actor=name, subject=name, client=client, role=row.role, status=status)
            return self._view(row)

    def create_user(
        self,
        actor: str,
        username: str,
        *,
        role: Role,
        password: str | None = None,
        display_name: str = "",
        email: str | None = None,
        client: str = "",
    ) -> tuple[UserView, str | None]:
        """An administrator (or the command line) makes an account. With no password given, a temporary one is made and
        returned once; either way the person must change it at their first sign-in."""
        if role not in ROLES:
            raise UserError(f"the role is one of {', '.join(ROLES)}")
        name = normalise_username(username)
        generated = None
        if password is None:
            generated = password = temporary_password()
        else:
            self._check_password(password, name)
        with self.db.session() as s:
            row = self._insert(
                s,
                username=name,
                password=password,
                role=role,
                status="active",
                display_name=display_name,
                email=email,
                must_change=True,
                created_by=None,
            )
            self._audit(s, "created", actor=actor, subject=name, client=client, role=role)
            return self._view(row), generated

    # -------------------------------------------------------------------------------------------- sign-in
    def authenticate(self, username: str, password: str, *, client: str = "") -> UserView:
        wait = self._throttle(self._sign_ins, client or "unknown", SIGN_IN_PER_CLIENT, SIGN_IN_WINDOW_SECONDS)
        if wait:
            raise AccountLocked(
                "too many sign-in attempts from this address; wait a few minutes", retry_after_seconds=wait
            )
        name = (username or "").strip().casefold()
        # a name with a NUL byte is no one's (and PostgreSQL would refuse to look for it): answered like any unknown name
        hostile = "\x00" in name
        name = name.replace("\x00", "")
        now = self._now()
        with self.db.session() as s:
            row = None if hostile else s.scalar(select(orm.UserRow).where(orm.UserRow.username == name))
            if row is None:
                passwords.verify_password(password, passwords.DUMMY_HASH)  # the same time as a real account
                self._audit(s, "sign_in_failed", actor=name[:64], subject="", client=client, why="unknown user")
                s.commit()  # the audit record must survive the failure that follows
                raise SignInFailed("wrong user name or password")
            locked = _aware(row.locked_until)
            if locked and locked > now:
                self._audit(s, "sign_in_refused", actor=name, subject=name, client=client, why="locked")
                s.commit()
                raise AccountLocked(
                    "this account is locked after repeated wrong passwords; try again later or ask an administrator",
                    retry_after_seconds=int((locked - now).total_seconds()) + 1,
                )
            if not passwords.verify_password(password, row.password_hash):
                row.failed_attempts = int(row.failed_attempts or 0) + 1
                detail: dict[str, Any] = {"why": "wrong password", "failures": row.failed_attempts}
                if row.failed_attempts >= self.config.max_failed_attempts:
                    row.locked_until = now + timedelta(minutes=self.config.lock_minutes)
                    row.failed_attempts = 0
                    detail["locked_minutes"] = self.config.lock_minutes
                    self._audit(s, "locked", actor=name, subject=name, client=client, **detail)
                else:
                    self._audit(s, "sign_in_failed", actor=name, subject=name, client=client, **detail)
                s.commit()  # keep the count even though the request is about to fail
                raise SignInFailed("wrong user name or password")
            if row.status == "pending":
                raise AccountConflict("this account is waiting for an administrator to approve it")
            if row.status != "active":
                self._audit(s, "sign_in_refused", actor=name, subject=name, client=client, why="disabled")
                s.commit()
                raise SignInFailed("wrong user name or password")
            row.failed_attempts = 0
            row.locked_until = None
            row.last_login_at = now
            if passwords.needs_rehash(row.password_hash):
                row.password_hash = passwords.hash_password(password)
            self._audit(s, "signed_in", actor=name, subject=name, client=client)
            return self._view(row)

    # --------------------------------------------------------------------------------------------- sessions
    def create_session(self, user_id: str, *, client: str = "", user_agent: str = "") -> tuple[str, str, datetime]:
        """``(cookie value, CSRF token, expiry)``. Only the SHA-256 of the cookie value is stored."""
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        now = self._now()
        expires = now + timedelta(hours=self.config.session_absolute_hours)
        with self.db.session() as s:
            s.execute(delete(orm.UserSessionRow).where(orm.UserSessionRow.expires_at < now))  # housekeeping
            s.add(
                orm.UserSessionRow(
                    user_id=user_id,
                    token_hash=_hash_token(token),
                    csrf_token=csrf,
                    expires_at=expires,
                    last_seen_at=now,
                    client=client[:64],
                    user_agent=user_agent[:200],
                )
            )
        return token, csrf, expires

    def principal_for_session(self, token: str) -> Principal | None:
        if not token or len(token) > 200:
            return None
        now = self._now()
        with self.db.session() as s:
            row = s.scalar(select(orm.UserSessionRow).where(orm.UserSessionRow.token_hash == _hash_token(token)))
            if row is None:
                return None
            expires = _aware(row.expires_at)
            seen = _aware(row.last_seen_at) or now
            idle = timedelta(minutes=self.config.session_idle_minutes)
            if (expires and expires <= now) or seen + idle <= now:
                s.delete(row)
                return None
            user = s.get(orm.UserRow, row.user_id)
            if user is None or user.status != "active":
                s.delete(row)
                return None
            if now - seen >= TOUCH_EVERY:
                row.last_seen_at = now
            return Principal(
                kind="user",
                role=user.role,  # type: ignore[arg-type]
                user_id=user.id,
                username=user.username,
                display_name=user.display_name or user.username,
                must_change_password=bool(user.must_change_password),
                csrf_token=row.csrf_token,
                session_id=row.id,
            )

    def revoke_session(self, token: str, *, client: str = "") -> None:
        with self.db.session() as s:
            row = s.scalar(select(orm.UserSessionRow).where(orm.UserSessionRow.token_hash == _hash_token(token)))
            if row is not None:
                user = s.get(orm.UserRow, row.user_id)
                self._audit(
                    s,
                    "signed_out",
                    actor=user.username if user else "",
                    subject=user.username if user else "",
                    client=client,
                )
                s.delete(row)

    def revoke_user_sessions(self, user_id: str, *, keep: str | None = None) -> int:
        with self.db.session() as s:
            q = delete(orm.UserSessionRow).where(orm.UserSessionRow.user_id == user_id)
            if keep:
                q = q.where(orm.UserSessionRow.id != keep)
            result: CursorResult[Any] = s.execute(q)  # type: ignore[assignment]
            return int(result.rowcount or 0)

    # --------------------------------------------------------------------------------------- password change
    def change_password(
        self, user_id: str, current: str, new: str, *, keep_session: str | None = None, client: str = ""
    ) -> None:
        with self.db.session() as s:
            row = s.get(orm.UserRow, user_id)
            if row is None:
                raise NotFoundError("no such account")
            if not passwords.verify_password(current, row.password_hash):
                self._audit(s, "password_change_failed", actor=row.username, subject=row.username, client=client)
                s.commit()
                raise SignInFailed("the current password is wrong")
            self._check_password(new, row.username)
            if passwords.verify_password(new, row.password_hash):
                raise UserError("the new password must differ from the current one")
            row.password_hash = passwords.hash_password(new)
            row.must_change_password = False
            self._audit(s, "password_changed", actor=row.username, subject=row.username, client=client)
        self.revoke_user_sessions(user_id, keep=keep_session)

    def reset_password(self, actor: str, user_id: str, *, client: str = "") -> str:
        """An administrator sets a temporary password; the person must change it at the next sign-in."""
        temp = temporary_password()
        with self.db.session() as s:
            row = s.get(orm.UserRow, user_id)
            if row is None:
                raise NotFoundError("no such account")
            row.password_hash = passwords.hash_password(temp)
            row.must_change_password = True
            row.failed_attempts = 0
            row.locked_until = None
            self._audit(s, "password_reset", actor=actor, subject=row.username, client=client)
        self.revoke_user_sessions(user_id)
        return temp

    # ------------------------------------------------------------------------------------ administration
    def update(
        self,
        actor: str,
        user_id: str,
        *,
        role: Role | None = None,
        status: str | None = None,
        display_name: str | None = None,
        client: str = "",
    ) -> UserView:
        if role is not None and role not in ROLES:
            raise UserError(f"the role is one of {', '.join(ROLES)}")
        if status is not None and status not in STATUSES:
            raise UserError(f"the status is one of {', '.join(STATUSES)}")
        revoke = False
        with self.db.session() as s:
            row = s.get(orm.UserRow, user_id)
            if row is None:
                raise NotFoundError("no such account")
            leaving_admin = (
                row.role == "admin"
                and row.status == "active"
                and ((role is not None and role != "admin") or (status is not None and status != "active"))
            )
            if leaving_admin and self._active_admins(s) <= 1:
                raise AccountConflict(
                    "this is the last active administrator: make another account an administrator first"
                )
            if role is not None and role != row.role:
                self._audit(s, "role_changed", actor=actor, subject=row.username, client=client, old=row.role, new=role)
                row.role = role
                revoke = True  # the person signs in again with what the new role allows
            if status is not None and status != row.status:
                self._audit(
                    s, "status_changed", actor=actor, subject=row.username, client=client, old=row.status, new=status
                )
                row.status = status
                revoke = revoke or status != "active"
            if display_name is not None:
                row.display_name = _one_line(display_name)[:120]
            result = self._view(row)
        if revoke:
            self.revoke_user_sessions(user_id)
        return result

    def unlock(self, actor: str, user_id: str, *, client: str = "") -> UserView:
        with self.db.session() as s:
            row = s.get(orm.UserRow, user_id)
            if row is None:
                raise NotFoundError("no such account")
            row.failed_attempts = 0
            row.locked_until = None
            self._audit(s, "unlocked", actor=actor, subject=row.username, client=client)
            return self._view(row)
