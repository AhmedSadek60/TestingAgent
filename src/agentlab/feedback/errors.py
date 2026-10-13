"""Failures of the feedback features, each with the status the API gives it (see ``api/errors.py``)."""

from __future__ import annotations

from agentlab.core.errors import UserError


class QuotaExceeded(UserError):
    """A person has made as many submissions today as the server allows."""

    def __init__(self, message: str, *, retry_after_seconds: int = 0) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class VersionConflict(UserError):
    """The submission was changed by someone else since the caller read it."""
