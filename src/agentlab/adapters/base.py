"""Target agent adapters (spec sections 19 and 20).

An adapter turns one :class:`AgentRequest` into one :class:`AgentResponse`, hiding whether the
target is an in-process object, an HTTP API, a web UI driven by Playwright, a sandboxed
command, an MCP server or a bare LLM. Adapters declare *capabilities* so the planner and
executor know what can be observed (tool calls, retrieved contexts, ...).
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import Field

from agentlab.core.config import AgentLabConfig
from agentlab.core.models import AgentRequest, AgentResponse, TargetSpec
from agentlab.core.models.base import Model
from agentlab.core.plugins import Registry
from agentlab.security.credentials import CredentialManager
from agentlab.security.egress import EgressPolicy
from agentlab.storage.artifacts import ArtifactStore


class AdapterCapabilities(Model):
    sessions: bool = True  # server-side conversation state addressed by session id
    parallel_sessions: bool = True  # safe to run independent sessions concurrently
    streaming: bool = False
    attachments: bool = False
    reports_tool_calls: bool = False
    reports_contexts: bool = False
    reports_events: bool = False
    reports_usage: bool = False
    notes: list[str] = Field(default_factory=list)


@dataclass
class AdapterContext:
    """Dependencies handed to adapters (dependency injection; no globals)."""

    config: AgentLabConfig
    credentials: CredentialManager | None = None
    egress: EgressPolicy = field(default_factory=EgressPolicy)
    artifacts: ArtifactStore | None = None
    providers: Any = None  # ProviderManager (LLM-backed targets)
    sandbox: Any = None  # SandboxProvider
    workdir: Path | None = None
    run_id: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


class AgentAdapter(ABC):
    kind: str = "abstract"

    def __init__(self, spec: TargetSpec, ctx: AdapterContext) -> None:
        self.spec = spec
        self.ctx = ctx
        self.capabilities = AdapterCapabilities()

    async def open(self) -> None:  # noqa: B027 - optional hook
        return None

    async def close(self) -> None:  # noqa: B027
        return None

    async def new_session(self) -> str:
        return uuid.uuid4().hex

    async def end_session(self, session_id: str) -> None:  # noqa: B027
        return None

    @abstractmethod
    async def send(self, request: AgentRequest) -> AgentResponse: ...

    async def probe(self) -> dict[str, Any]:
        """Cheap, safe reachability check used during environment preparation."""
        return {"reachable": True}

    async def cancel(self) -> None:  # noqa: B027
        return None

    def describe(self) -> dict[str, Any]:
        return {"kind": self.kind, "capabilities": self.capabilities.model_dump()}

    async def __aenter__(self) -> AgentAdapter:
        await self.open()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()


ADAPTERS: Registry[type[AgentAdapter]] = Registry("adapters")


def create_adapter(kind: str, spec: TargetSpec, ctx: AdapterContext) -> AgentAdapter:
    return ADAPTERS.get(kind)(spec, ctx)


class TargetRuntime:
    """All interfaces available for one target; tests pick one by name."""

    def __init__(self, spec: TargetSpec, ctx: AdapterContext) -> None:
        self.spec = spec
        self.ctx = ctx
        self.adapters: dict[str, AgentAdapter] = {}
        self.errors: dict[str, str] = {}

    PRIORITY = ("mock", "llm", "api", "command", "mcp", "web")

    async def open(self) -> TargetRuntime:
        for kind in self.spec.interfaces():
            try:
                adapter = create_adapter(kind, self.spec, self.ctx)
                await adapter.open()
                self.adapters[kind] = adapter
            except Exception as exc:  # a broken interface blocks only the tests that need it
                self.errors[kind] = f"{type(exc).__name__}: {exc}"
        return self

    async def close(self) -> None:
        for a in self.adapters.values():
            try:
                await a.close()
            except Exception:  # noqa: S110 - best-effort cleanup
                pass

    def adapter(self, kind: str | None = None) -> AgentAdapter | None:
        if kind:
            return self.adapters.get(kind)
        for k in self.PRIORITY:
            if k in self.adapters:
                return self.adapters[k]
        return None

    def available(self) -> list[str]:
        return sorted(self.adapters)

    async def __aenter__(self) -> TargetRuntime:
        return await self.open()

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
