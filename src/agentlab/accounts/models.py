"""What the rest of AgentLab sees of an account: never a hash, never a token."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Role = Literal["reporter", "maintainer", "admin"]
ROLES: tuple[Role, ...] = ("reporter", "maintainer", "admin")
#: What each role may do with the rest of AgentLab (runs, targets, credentials, reports): only these two classes are "operators".
OPERATOR_ROLES: frozenset[str] = frozenset({"maintainer", "admin"})


@dataclass(frozen=True)
class UserView:
    id: str
    username: str
    display_name: str
    email: str | None
    role: Role
    status: str  # active | pending | disabled
    must_change_password: bool
    created_at: datetime
    last_login_at: datetime | None
    locked_until: datetime | None


@dataclass(frozen=True)
class Principal:
    """Who is calling: the shared API token (``service``), a request on a machine that needs no token (``local``), or a
    signed-in person (``user``)."""

    kind: Literal["service", "local", "user"]
    role: Role
    user_id: str | None = None
    username: str | None = None
    display_name: str | None = None
    must_change_password: bool = False
    csrf_token: str | None = None
    session_id: str | None = None

    @property
    def is_operator(self) -> bool:
        """May use everything AgentLab offered before accounts existed (runs, targets, credentials, reports)."""
        return self.kind in {"service", "local"} or self.role in OPERATOR_ROLES

    @property
    def is_admin(self) -> bool:
        """May manage accounts. The shared token may not: it is a service credential, not a person."""
        return self.kind == "local" or (self.kind == "user" and self.role == "admin")

    @property
    def label(self) -> str:
        if self.kind == "user" and self.username:
            return self.username
        return "shared API token" if self.kind == "service" else "local"
