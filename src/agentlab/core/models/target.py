"""Target definition: what is being evaluated and how AgentLab may interact with it."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from agentlab.core.enums import RiskClass
from agentlab.core.models.base import Model


class RepositorySource(Model):
    """A repository to analyse. Exactly one of url / path / archive should be set."""

    url: str | None = None
    ref: str | None = None
    path: str | None = None
    archive: str | None = None

    @field_validator("url")
    @classmethod
    def _https_only(cls, v: str | None) -> str | None:
        if v and not (v.startswith("https://") or v.startswith("git@")):
            raise ValueError("repository url must use https:// (or git@ for ssh)")
        return v


class ResponseMapping(Model):
    """JSONPath expressions telling the HTTP adapter where to find data in a response."""

    output: str | None = None
    tool_calls: str | None = None
    contexts: str | None = None
    citations: str | None = None
    events: str | None = None
    usage: str | None = None
    session_id: str | None = None


class ApiConfig(Model):
    """Black-box HTTP/REST/SSE agent endpoint (spec section 19)."""

    url: str
    method: Literal["POST", "GET", "PUT"] = "POST"
    protocol: Literal["rest", "sse", "websocket", "graphql"] = "rest"
    headers: dict[str, str] = Field(default_factory=dict)
    # A JSON body template. ``{{input}}``, ``{{session_id}}`` and ``{{attachments}}`` are substituted.
    request_template: dict[str, Any] = Field(
        default_factory=lambda: {"input": "{{input}}", "session_id": "{{session_id}}"}
    )
    response: ResponseMapping = Field(default_factory=ResponseMapping)
    session_header: str | None = None
    auth_credential: str | None = Field(
        default=None, description="Credential profile name used for authenticated requests"
    )
    timeout_seconds: float = 60.0
    graphql_query: str | None = None
    openapi_url: str | None = None


class WebConfig(Model):
    """Browser-reachable web interface (spec sections 18 and 20)."""

    url: str
    input_selector: str | None = None
    send_selector: str | None = None
    message_selector: str | None = None
    auth_credential: str | None = None
    login_url: str | None = None


class CommandConfig(Model):
    """A target run as a command inside the sandbox (coding/CLI agents)."""

    image: str | None = None
    command: list[str]
    workdir: str = "/workspace"
    env: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = 300.0
    network: Literal["none", "internal", "allowlist"] = "none"
    allow_hosts: list[str] = Field(default_factory=list)


class McpConfig(Model):
    transport: Literal["streamable_http", "sse", "stdio"] = "streamable_http"
    url: str | None = None
    command: list[str] | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    auth_credential: str | None = None


class MockAgentConfig(Model):
    """In-process deterministic MockAgent used for development and self-tests."""

    behaviors: list[str] = Field(default_factory=lambda: ["success"])
    tools: list[str] = Field(default_factory=list)
    knowledge: dict[str, str] = Field(default_factory=dict)
    seed: int = 0


class SafetyPolicy(Model):
    """What the owner of the target has authorised (spec section 35)."""

    production: bool = False
    authorized_risk_classes: list[RiskClass] = Field(
        default_factory=lambda: [RiskClass.SAFE, RiskClass.CONTROLLED]
    )
    authorization_note: str | None = None
    disposable_environment: bool = False


class TargetSpec(Model):
    """Everything known about a target before discovery. Loaded from ``target.yaml``."""

    name: str
    description: str | None = None
    objective: str | None = None
    version: str | None = None
    repository: RepositorySource | None = None
    api: ApiConfig | None = None
    web: WebConfig | None = None
    command: CommandConfig | None = None
    mcp: McpConfig | None = None
    mock: MockAgentConfig | None = None
    documents: list[str] = Field(default_factory=list)
    credentials: list[str] = Field(default_factory=list, description="Credential profile names")
    declared_tools: list[dict[str, Any]] = Field(default_factory=list)
    declared_types: list[str] = Field(default_factory=list)
    safety: SafetyPolicy = Field(default_factory=SafetyPolicy)
    tags: list[str] = Field(default_factory=list)

    def interfaces(self) -> list[str]:
        out = []
        for name in ("api", "web", "command", "mcp", "mock"):
            if getattr(self, name) is not None:
                out.append(name)
        return out
