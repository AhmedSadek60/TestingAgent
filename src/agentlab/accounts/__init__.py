"""People who sign in to AgentLab: accounts, passwords, sessions and roles.

AgentLab started with one shared API token and no accounts. Accounts are added beside it: the token keeps working for the
command line and automation, and a person can also register, sign in with a password and be a *reporter* (Feedback & Support
only), a *maintainer* (everything the token reaches, plus triage) or an *admin* (a maintainer who also manages accounts).
"""

from agentlab.accounts.errors import AccountConflict, AccountLocked, SignInFailed
from agentlab.accounts.models import ROLES, Principal, Role, UserView
from agentlab.accounts.service import AccountService

__all__ = [
    "ROLES",
    "AccountConflict",
    "AccountLocked",
    "AccountService",
    "Principal",
    "Role",
    "SignInFailed",
    "UserView",
]
