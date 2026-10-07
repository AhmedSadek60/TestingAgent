"""Per-test execution traces.

A :class:`Trace` is the ordered, redacted record of everything observable during one
attempt of one test: requests, responses, LLM calls, tool calls, retrievals, handoffs,
browser actions, sandbox commands, assertions and judge decisions.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from pydantic import Field

from agentlab.core.enums import EventType
from agentlab.core.ids import new_id, utcnow
from agentlab.core.models.agent_io import AgentResponse
from agentlab.core.models.base import Model
from agentlab.tracing.events import Event, EventBus


class Trace(Model):
    id: str = Field(default_factory=new_id)
    run_id: str
    test_id: str
    attempt: int = 1
    timestamp: datetime = Field(default_factory=utcnow)
    events: list[Event] = Field(default_factory=list)

    def of_type(self, *types: EventType) -> list[Event]:
        return [e for e in self.events if e.type in types]


class TraceRecorder:
    """Records events into a trace and forwards them to the run's event bus."""

    def __init__(self, run_id: str, test_id: str, attempt: int = 1, bus: EventBus | None = None) -> None:
        self.trace = Trace(run_id=run_id, test_id=test_id, attempt=attempt)
        self.bus = bus
        self._t0 = time.perf_counter()

    def record(self, type: EventType, payload: dict[str, Any] | None = None) -> Event:
        payload = dict(payload or {})
        payload.setdefault("t_ms", round((time.perf_counter() - self._t0) * 1000, 2))
        payload.setdefault("attempt", self.trace.attempt)
        event = Event.create(self.trace.run_id, type, payload, self.trace.test_id)
        self.trace.events.append(event)
        if self.bus is not None:
            self.bus.publish(event)
        return event

    def record_response(self, turn: int, session: str, response: AgentResponse) -> None:
        """Expand an agent response into fine-grained trace events."""
        for call in response.tool_calls:
            self.record(EventType.TOOL_CALLED, {"turn": turn, "tool": call.name, "arguments_redacted": call.arguments})
            self.record(
                EventType.TOOL_RETURNED,
                {"turn": turn, "tool": call.name, "status": call.status, "result": _clip(call.result)},
            )
        for ctx in response.contexts:
            self.record(
                EventType.RETRIEVAL,
                {"turn": turn, "source": ctx.source, "page": ctx.page, "content": _clip(ctx.content)},
            )
        for ev in response.events:
            et = {
                "handoff": EventType.HANDOFF,
                "plan_step": EventType.PLAN_STEP,
                "browser_action": EventType.BROWSER_ACTION,
                "llm_call": EventType.LLM_CALLED,
            }.get(ev.type, EventType.AGENT_RESPONSE)
            self.record(et, {"turn": turn, "agent_event": ev.type, **ev.data})
        self.record(
            EventType.AGENT_RESPONSE,
            {
                "turn": turn,
                "session": session,
                "output": _clip(response.output),
                "latency_ms": response.latency_ms,
                "status_code": response.status_code,
                "tokens": {"input": response.usage.input_tokens, "output": response.usage.output_tokens},
                "cost_usd": response.usage.cost_usd,
                "error": response.error,
                **({"retries": response.retries} if response.retries else {}),
            },
        )


def _clip(value: Any, limit: int = 4000) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"...[{len(value) - limit} more chars]"
    return value
