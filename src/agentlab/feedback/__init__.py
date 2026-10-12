"""Feedback & Support: feature requests and issue reports, with a discussion, a status history and an audit trail.

Everything that decides who may see or change a submission lives in ``policy.py`` and ``service.py`` and is applied to the
database query, so a route cannot forget it: what a caller may not see is not fetched, and is answered as "not found".
"""

from agentlab.feedback.errors import QuotaExceeded, VersionConflict
from agentlab.feedback.models import CommentIn, RelationIn, SubmissionIn, SubmissionPatch
from agentlab.feedback.policy import Actor, actor_for
from agentlab.feedback.service import SubmissionFilter, SubmissionService

__all__ = [
    "Actor",
    "CommentIn",
    "QuotaExceeded",
    "RelationIn",
    "SubmissionFilter",
    "SubmissionIn",
    "SubmissionPatch",
    "SubmissionService",
    "VersionConflict",
    "actor_for",
]
