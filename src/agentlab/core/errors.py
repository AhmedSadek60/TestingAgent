"""Typed error hierarchy. Every failure carries an :class:`ErrorKind` (spec section 53)."""

from __future__ import annotations

from agentlab.core.enums import ErrorKind


class AgentLabError(Exception):
    kind: ErrorKind = ErrorKind.INFRASTRUCTURE_ERROR

    def __init__(self, message: str, *, kind: ErrorKind | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        if kind is not None:
            self.kind = kind
        self.retryable = retryable

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind.value, "message": str(self), "retryable": self.retryable}


class UserError(AgentLabError):
    kind = ErrorKind.USER_ERROR


class NotFoundError(UserError):
    """The thing the person named does not exist (a run, a target, a report). A user error for the CLI (exit code 2) and
    a 404 for the API."""


class TargetError(AgentLabError):
    kind = ErrorKind.TARGET_ERROR


class ProviderError(AgentLabError):
    kind = ErrorKind.PROVIDER_ERROR


class RateLimitError(ProviderError):
    kind = ErrorKind.RATE_LIMIT

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message, retryable=True)
        self.retry_after = retry_after


class EvaluatorError(AgentLabError):
    kind = ErrorKind.EVALUATOR_ERROR


class InfrastructureError(AgentLabError):
    kind = ErrorKind.INFRASTRUCTURE_ERROR


class TimeoutExceeded(AgentLabError):
    kind = ErrorKind.TIMEOUT


class CredentialError(AgentLabError):
    kind = ErrorKind.CREDENTIAL_ERROR


class SandboxError(AgentLabError):
    kind = ErrorKind.SANDBOX_ERROR


class SandboxUnavailable(SandboxError):
    """Raised when isolation cannot be guaranteed. Callers must fail closed."""


class BrowserError(AgentLabError):
    kind = ErrorKind.BROWSER_ERROR


class ParserError(AgentLabError):
    kind = ErrorKind.PARSER_ERROR


class CostLimitExceeded(AgentLabError):
    kind = ErrorKind.COST_LIMIT


class PolicyBlocked(AgentLabError):
    kind = ErrorKind.POLICY_BLOCK


class UnsupportedCapability(AgentLabError):
    """A capability was requested that this build does not implement or the provider lacks."""

    kind = ErrorKind.USER_ERROR


def is_environmental(error: BaseException | None) -> bool:
    """Whether ``error`` is a problem of the test set-up (the browser, the machine, AgentLab's own wait running out) and
    says nothing about the agent being tested.

    A test that ends in one is ERROR: it is not scored, and the agent is not blamed for it. An error that the agent itself
    returned (``TargetError``, an HTTP error status) is not environmental: it is the agent's answer."""
    return isinstance(error, (BrowserError, InfrastructureError, TimeoutExceeded))
