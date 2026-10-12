"""Who may see and change what in Feedback & Support.

The rules are few, and they are applied where the data is read (as a clause of the query), so a route cannot forget them:

* A **reporter** (an account with the role ``reporter``) sees only the submissions they made, only the public part of the
  discussion, and nothing that says who the maintainers are or how they have rated the submission.
* A **maintainer** or **admin** (and the local machine) sees every submission, the internal notes and the audit trail, and
  triages them. The shared API token acts as a maintainer here too.
* A submission whose visibility is ``security`` is seen only by its reporter and by maintainers, admins and the local
  machine. The shared API token is a credential many people may hold, so it sees other people's security submissions only
  if the owner set ``feedback.service_token_sees_security``.
* A submission a caller may not see is answered exactly as one that does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import ColumnElement, and_, false, or_, true

from agentlab.accounts.models import Principal, Role
from agentlab.core.config import FeedbackConfig
from agentlab.storage import orm


@dataclass(frozen=True)
class Actor:
    """Who is acting, reduced to what Feedback & Support needs to decide."""

    kind: Literal["service", "local", "user"]
    role: Role
    user_id: str | None
    label: str  # what is stored with what they did: a user name, "shared API token" or "local"
    display_name: str
    is_staff: bool  # a maintainer, an admin, the shared token or the local machine
    sees_security: bool  # may see security-sensitive submissions made by other people

    @property
    def key(self) -> str:
        """A stable identity for rate limits and audit."""
        return self.user_id or f"{self.kind}:{self.label}"

    @property
    def stored_label(self) -> str:
        """The name kept on a submission: a person's display name, otherwise what the credential is called."""
        return self.display_name if self.kind == "user" and self.display_name else self.label


def actor_for(principal: Principal, config: FeedbackConfig) -> Actor:
    staff = principal.is_operator and not principal.must_change_password
    if principal.kind == "service":
        sees = config.service_token_sees_security
    else:
        sees = staff  # a maintainer, an admin, or the local machine
    return Actor(
        kind=principal.kind,
        role=principal.role,
        user_id=principal.user_id,
        label=principal.label,
        display_name=principal.display_name or principal.username or principal.label,
        is_staff=staff,
        sees_security=sees,
    )


def owns(actor: Actor) -> ColumnElement[bool]:
    """The submissions this actor made. The shared token and the local machine have no account, so theirs are the ones
    stored with no account and with their label."""
    S = orm.SubmissionRow
    if actor.user_id is not None:
        return S.reporter_user_id == actor.user_id
    return and_(S.reporter_user_id.is_(None), S.reporter_label == actor.label)


def visible(actor: Actor) -> ColumnElement[bool]:
    """The submissions this actor may see. The only definition: lists, details, counts, suggestions and relations all use it."""
    S = orm.SubmissionRow
    if not actor.is_staff:
        return owns(actor) if actor.user_id is not None else false()
    if actor.sees_security:
        return true()
    return or_(S.visibility != "security", owns(actor))
