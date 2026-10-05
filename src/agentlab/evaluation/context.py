"""Evaluation context shared by assertion plug-ins."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agentlab.core.models import AgentProfile, AgentResponse, TestCase

_PLACEHOLDER = re.compile(r"\{\{\s*canary:([A-Za-z0-9_\-]+)\s*\}\}")


class PlaceholderResolver:
    """Resolves ``{{canary:name}}`` to unique per-run synthetic canaries (stable within a run)."""

    def __init__(self, prefix: str = "AGENTLAB_CANARY") -> None:
        from agentlab.security.canary import CanaryRegistry

        self.registry = CanaryRegistry(prefix=prefix)
        self._by_name: dict[str, str] = {}

    def canary(self, name: str) -> str:
        if name not in self._by_name:
            self._by_name[name] = self.registry.issue(name)
        return self._by_name[name]

    def resolve(self, text: str) -> str:
        return _PLACEHOLDER.sub(lambda m: self.canary(m.group(1)), text)

    def resolve_obj(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.resolve(obj)
        if isinstance(obj, dict):
            return {k: self.resolve_obj(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.resolve_obj(v) for v in obj]
        return obj

    @property
    def values(self) -> list[str]:
        return list(self._by_name.values())


@dataclass
class EvalContext:
    test: TestCase
    turn_index: int
    response: AgentResponse
    responses: list[AgentResponse]
    inputs: list[str]
    sessions: list[str]
    resolver: PlaceholderResolver
    profile: AgentProfile | None = None
    known_sources: set[str] = field(default_factory=set)
    state: dict[str, Any] = field(default_factory=dict)  # browser / workspace / mcp observations

    @property
    def resolve(self) -> Callable[[Any], Any]:
        return self.resolver.resolve_obj

    def surface_text(self, response: AgentResponse | None = None) -> str:
        """Every observable channel of a response flattened to text (for leak scanning)."""
        r = response or self.response
        parts = [r.output]
        for c in r.tool_calls:
            parts.append(str(c.arguments))
            parts.append(str(c.result))
        for e in r.events:
            parts.append(str(e.data))
        parts.extend(r.citations)
        return "\n".join(p for p in parts if p)
