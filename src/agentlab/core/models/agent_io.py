"""Provider-neutral representation of one interaction with a target agent."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from agentlab.core.models.base import Model


class ToolCall(Model):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    status: str = "success"
    latency_ms: float | None = None
    id: str | None = None


class RetrievedContext(Model):
    source: str
    content: str
    page: int | None = None
    section: str | None = None
    score: float | None = None


class AgentEvent(Model):
    """An observable event reported by the target (handoff, plan step, browser action, ...)."""

    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class Usage(Model):
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class Attachment(Model):
    name: str
    media_type: str
    path: str | None = None
    content_b64: str | None = None


class AgentRequest(Model):
    input: str
    session_id: str
    attachments: list[Attachment] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    credential: str | None = None


class AgentResponse(Model):
    output: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    contexts: list[RetrievedContext] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    events: list[AgentEvent] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    latency_ms: float = 0.0
    first_token_ms: float | None = None
    status_code: int | None = None
    error: str | None = None
    retries: int = Field(
        default=0,
        description="Times the call was repeated because the connection could not be made, so it never reached the agent",
    )
    raw: Any = None
    artifacts: list[str] = Field(default_factory=list)
    observed: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra observations collected by the adapter (browser URL, DOM, git diff, ...)",
    )

    @property
    def tool_names(self) -> list[str]:
        return [c.name for c in self.tool_calls]
