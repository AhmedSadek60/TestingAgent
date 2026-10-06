"""Evaluation context shared by assertion plug-ins."""

from __future__ import annotations

import base64
import copy
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agentlab.core.models import AgentProfile, AgentResponse, TestCase

_PLACEHOLDER = re.compile(r"\{\{\s*canary:([A-Za-z0-9_\-]+)\s*\}\}")
_B64_PLACEHOLDER = re.compile(r"\{\{\s*b64:canary:([A-Za-z0-9_\-]+)\s*\}\}")
_VARIABLE = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")


class PlaceholderResolver:
    """Resolves ``{{canary:name}}`` to unique per-run synthetic canaries (stable within a run).

    ``with_variables`` derives a resolver that also knows values that exist only for one attempt (``{{site_url}}``, the
    address of the instrumented test site served for that attempt). The derived resolver shares the canaries of its
    parent, so a marker issued while resolving an attempt is the one the run's assertions look for."""

    def __init__(self, prefix: str = "AGENTLAB_CANARY") -> None:
        from agentlab.security.canary import CanaryRegistry

        self.registry = CanaryRegistry(prefix=prefix)
        self._by_name: dict[str, str] = {}
        self._variables: dict[str, str] = {}

    def with_variables(self, **variables: str) -> PlaceholderResolver:
        child = copy.copy(self)  # shallow: the registry and the canary table are shared with the parent on purpose
        child._variables = {**self._variables, **variables}
        return child

    def canary(self, name: str) -> str:
        if name not in self._by_name:
            self._by_name[name] = self.registry.issue(name)
        return self._by_name[name]

    def register_known(self, values: list[str]) -> None:
        """Synthetic secrets the *user* planted in the target (declared in target.yaml); any sighting is a leak."""
        for i, v in enumerate(values):
            if v and f"declared_{i + 1}" not in self._by_name:
                self._by_name[f"declared_{i + 1}"] = v
                self.registry.register_value(f"declared_{i + 1}", v)

    def resolve(self, text: str) -> str:
        text = _B64_PLACEHOLDER.sub(lambda m: base64.b64encode(self.canary(m.group(1)).encode()).decode(), text)
        text = _PLACEHOLDER.sub(lambda m: self.canary(m.group(1)), text)
        if self._variables:  # names this attempt does not define (``{{input}}`` of a request template) stay as they are
            text = _VARIABLE.sub(lambda m: self._variables.get(m.group(1), m.group(0)), text)
        return text

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
