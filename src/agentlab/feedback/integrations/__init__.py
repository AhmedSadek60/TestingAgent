"""Issue trackers a submission can be published to: the interface, and the errors every tracker speaks.

AgentLab's own record stays the source of truth. A tracker gets a sanitized summary a maintainer has seen and confirmed, never
a file, a diagnostic or a package, and the core of Feedback & Support does not depend on any of them: with none switched on,
nothing here is ever called.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

ErrorKind = Literal["rate_limited", "unauthorized", "forbidden", "not_found", "disabled", "rejected", "unavailable"]


class TrackerError(Exception):
    """A tracker could not do what was asked, for a reason a person can act on."""

    def __init__(self, kind: ErrorKind, message: str, *, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.kind: ErrorKind = kind
        self.retry_after = retry_after


@dataclass(frozen=True)
class IssueDraft:
    """What would be sent. ``key`` names this publication, so that a retry finds an issue that was already made."""

    title: str
    body: str
    labels: tuple[str, ...]
    assignees: tuple[str, ...]
    key: str


@dataclass(frozen=True)
class ExternalIssue:
    provider: str
    external_id: str
    number: int | None
    url: str
    state: str  # open | closed


class IssueTracker(Protocol):
    """What AgentLab needs of an issue tracker. Another tracker is one more class that does these four things."""

    name: str

    def find(self, key: str) -> ExternalIssue | None:
        """The issue that was made for this key, when an earlier attempt made it and its answer was lost."""

    def create(self, draft: IssueDraft) -> ExternalIssue: ...

    def comment(self, issue: ExternalIssue, body: str) -> None: ...

    def close(self, issue: ExternalIssue) -> None: ...


MARKER_PREFIX = "agentlab-publication:"


def marker(key: str) -> str:
    """The line placed in an issue so that a retry can tell it is the one that was made for ``key``."""
    return f"<!-- {MARKER_PREFIX}{key} -->"
