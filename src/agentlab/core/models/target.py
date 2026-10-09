"""Target definition: what is being evaluated and how AgentLab may interact with it."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

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


#: What an API target sends when the owner did not say how the request is shaped.
DEFAULT_REQUEST_TEMPLATE: dict[str, Any] = {"input": "{{input}}", "session_id": "{{session_id}}"}


class ApiConfig(Model):
    """Black-box HTTP/REST/SSE agent endpoint (spec section 19)."""

    url: str = Field(
        default="",
        description="Address of the endpoint that answers a message. May be left empty when openapi_url is given: "
        "AgentLab then looks for the chat endpoint in that document, uses it when it is on the host the document came "
        "from, and says which one it chose.",
    )
    method: Literal["POST", "GET", "PUT"] = "POST"
    protocol: Literal["rest", "sse", "websocket", "graphql"] = "rest"
    headers: dict[str, str] = Field(default_factory=dict)
    # A JSON body template. ``{{input}}``, ``{{session_id}}`` and ``{{attachments}}`` are substituted.
    request_template: dict[str, Any] = Field(default_factory=lambda: dict(DEFAULT_REQUEST_TEMPLATE))
    response: ResponseMapping = Field(default_factory=ResponseMapping)
    session_header: str | None = None
    auth_credential: str | None = Field(
        default=None, description="Credential profile name used for authenticated requests"
    )
    timeout_seconds: float = 60.0
    graphql_query: str | None = None
    openapi_url: str | None = None
    knowledge_endpoint: str | None = Field(
        default=None,
        description="Test hook of a disposable deployment: POST {session_id, name, text} adds a document to that "
        "session's knowledge base, so indirect-injection tests can plant a malicious document. Never use in production.",
    )

    @model_validator(mode="after")
    def _has_an_address(self) -> ApiConfig:
        if not self.url and not self.openapi_url:
            raise ValueError(
                "an api needs an address: set url, or openapi_url so the endpoint can be found in the document"
            )
        return self


class WebConfig(Model):
    """Browser-reachable web interface (spec sections 18 and 20)."""

    url: str
    input_selector: str | None = None
    send_selector: str | None = None
    message_selector: str | None = None
    busy_selector: str | None = Field(
        default=None,
        description="A CSS selector that is visible while the page is still writing its answer; the reply is not final "
        "while it shows (a visible Stop button and status lines such as 'Processing' are recognised without it)",
    )
    consent: Literal["reject", "accept", "off"] = Field(
        default="reject",
        description="What to do with a cookie or consent dialog that covers the page: refuse optional cookies (the "
        "default; nothing is pressed if the dialog has no such button), accept them, or leave the dialog alone",
    )
    dismiss_selectors: list[str] = Field(
        default_factory=list,
        description="Buttons of a dialog that AgentLab does not recognise by itself; each is pressed when it is showing",
    )
    navigation_timeout_seconds: float = Field(
        default=30.0, gt=0, le=300, description="How long to wait for the page to open (its load event)"
    )
    auth_credential: str | None = None
    login_url: str | None = None
    reply_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        le=600,
        description="How long to wait for the page to answer a message. A slow assistant needs more than a minute.",
    )


class CommandConfig(Model):
    """A target run as a command inside the sandbox (coding/CLI agents).

    ``mode: chat`` runs the command once per message (the message on standard input, the answer on standard output).
    ``mode: task`` is a coding agent: it is started on a disposable workspace with the task on standard input, and what
    it did is judged from the files it left behind and the project's own tests."""

    mode: Literal["chat", "task"] = "chat"
    image: str | None = None
    command: list[str]
    workdir: str | None = Field(
        default=None,
        description="Working directory inside the sandbox. Default: next to the agent's own code (/agent) in chat mode "
        "when the target has a repository, the workspace (/workspace) otherwise",
    )
    env: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = 300.0
    network: Literal["none", "internal", "allowlist"] = "none"
    allow_hosts: list[str] = Field(default_factory=list)


class McpConfig(Model):
    """A Model Context Protocol server as the target. Over HTTP the server is already running; over stdio the owner's
    ``command`` is started *inside the sandbox* from the target's repository (never on the evaluator host)."""

    transport: Literal["streamable_http", "sse", "stdio"] = "streamable_http"
    url: str | None = None
    command: list[str] | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    auth_credential: str | None = None
    timeout_seconds: float = 30.0
    image: str | None = Field(default=None, description="stdio only: container image to run the server in")
    env: dict[str, str] = Field(default_factory=dict, description="stdio only: environment of the server process")


class LlmToolDef(Model):
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    mock_result: Any = Field(default="ok", description="Deterministic result returned to the model when called")
    side_effects: str = "none"


class LlmTargetConfig(Model):
    """A model (plus system prompt, tools and in-context knowledge) acting as the agent under test."""

    provider: str
    model: str | None = None
    system_prompt: str = "You are a helpful assistant."
    tools: list[LlmToolDef] = Field(default_factory=list)
    knowledge: dict[str, str] = Field(default_factory=dict, description="name -> text placed in context")
    max_tool_rounds: int = 4
    max_tokens: int = 1024
    temperature: float | None = 0.0


class MockAgentConfig(Model):
    """In-process deterministic MockAgent used for development and self-tests."""

    behaviors: list[str] = Field(default_factory=lambda: ["success"])
    tools: list[str] = Field(default_factory=list)
    knowledge: dict[str, str] = Field(default_factory=dict)
    seed: int = 0


class SafetyPolicy(Model):
    """What the owner of the target has authorised (spec section 35)."""

    production: bool = False
    authorized_risk_classes: list[RiskClass] = Field(default_factory=lambda: [RiskClass.SAFE, RiskClass.CONTROLLED])
    authorization_note: str | None = None
    disposable_environment: bool = False


#: interfaces AgentLab ships an adapter for, in the order ``TargetSpec.interfaces()`` lists them
BUILTIN_INTERFACES: tuple[str, ...] = ("api", "web", "command", "mcp", "llm", "mock")
_INTERFACE_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


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
    llm: LlmTargetConfig | None = None
    documents: list[str] = Field(default_factory=list)
    credentials: list[str] = Field(default_factory=list, description="Credential profile names")
    known_canaries: list[str] = Field(
        default_factory=list,
        description="Synthetic secrets the owner planted in the target (system prompt, knowledge base, "
        "environment). Any appearance in an output or tool call is reported as a leak.",
    )
    declared_tools: list[dict[str, Any]] = Field(default_factory=list)
    declared_types: list[str] = Field(default_factory=list)
    safety: SafetyPolicy = Field(default_factory=SafetyPolicy)
    tags: list[str] = Field(default_factory=list)
    custom: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Interfaces that a plug-in agent adapter provides, by the name the adapter is registered under; "
        "the value is that adapter's own settings (never a secret: use credential profiles). Nothing is "
        "tested through one when no adapter of that name is installed",
    )

    @field_validator("custom")
    @classmethod
    def _custom_names(cls, v: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        for name in v:
            if name in BUILTIN_INTERFACES:
                raise ValueError(f"'{name}' is a built-in interface: configure it as its own block, not under custom")
            if not _INTERFACE_NAME.match(name):
                raise ValueError(f"custom interface name '{name}' must be lower-case letters, digits, - and _")
        return v

    def interfaces(self) -> list[str]:
        """The interfaces this target has: the built-in blocks that are set, then the plug-in ones by name."""
        out = [name for name in BUILTIN_INTERFACES if getattr(self, name) is not None]
        return [*out, *sorted(self.custom)]
