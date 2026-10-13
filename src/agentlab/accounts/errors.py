"""Failures of the accounts, each with the status the API gives it (see ``api/errors.py``)."""

from __future__ import annotations

from agentlab.core.errors import UserError


class SignInFailed(UserError):
    """The user name or the password is wrong. One message for both, so the answer does not say which accounts exist."""


class AccountLocked(UserError):
    """Too many wrong passwords in a row: the account is locked for a while."""

    def __init__(self, message: str, *, retry_after_seconds: int = 0) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class AccountConflict(UserError):
    """The request is valid but the account is in a state that does not allow it (a name taken, the last admin)."""
