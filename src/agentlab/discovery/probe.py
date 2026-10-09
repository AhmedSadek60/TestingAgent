"""Safe behavioural probing used by the TargetDiscoveryAgent (before any test runs).

Probes are SAFE-class only: greetings, a capability question, a two-turn memory check, a
retrieval-style question when documents are known, and read-only tool-eliciting questions. They
never ask the target to perform side effects. What the target *says* about itself is recorded as
weak, self-reported evidence; what AgentLab *observes* (tool calls, retrieved contexts, handoff
events, session recall) is strong evidence.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from pydantic import Field

from agentlab.adapters.base import AgentAdapter
from agentlab.core.errors import AgentLabError
from agentlab.core.models import AgentRequest
from agentlab.core.models.base import Model


class ProbeObservation(Model):
    name: str
    input: str
    output: str = ""
    tool_calls: list[str] = Field(default_factory=list)
    tool_args: list[dict[str, Any]] = Field(default_factory=list)
    contexts: list[str] = Field(default_factory=list)
    event_types: list[str] = Field(default_factory=list)
    handoffs: list[tuple[str, str]] = Field(default_factory=list)  # (from, to) of each delegation the target reported
    citations: list[str] = Field(default_factory=list)
    latency_ms: float = 0.0
    tokens: int = 0  # what the target reported using to answer (0 when it reports nothing)
    cost_usd: float = 0.0
    error: str | None = None
    status_code: int | None = None


class ProbeResult(Model):
    observations: list[ProbeObservation] = Field(default_factory=list)
    tools_seen: list[str] = Field(default_factory=list)
    contexts_seen: int = 0
    event_types: list[str] = Field(default_factory=list)
    handoffs: list[tuple[str, str]] = Field(default_factory=list)  # delegations seen while probing, first seen first
    session_memory: bool | None = None
    reachable: bool = False
    errors: list[str] = Field(default_factory=list)
    self_reported_tools: list[str] = Field(default_factory=list)
    self_reported_topics: list[str] = Field(default_factory=list)
    streaming: bool = False
    attachments: bool = False
    latency_ms_p50: float = 0.0
    refusal_on_unknown: bool | None = None
    tokens: int = 0  # usage of all probe questions together: probing a hosted model is not free
    cost_usd: float = 0.0

    def obs(self, name: str) -> ProbeObservation | None:
        return next((o for o in self.observations if o.name == name), None)


_TOPICS = {
    "documents": r"\b(document|pdf|knowledge base|policy|policies|handbook|faq)\b",
    "email": r"\b(e-?mail|inbox)\b",
    "calendar": r"\b(calendar|meeting|schedule)\b",
    "search": r"\b(search|look ?up|browse|web)\b",
    "calculation": r"\b(calculat|math|arithmetic)\b",
    "code": r"\b(code|programming|repository|repo|git)\b",
    "weather": r"\bweather\b",
    "orders": r"\b(order|shipping|refund)\b",
    "files": r"\b(file|folder|upload)\b",
}


def _percentile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round((len(xs) - 1) * q)))]


PROBE_MIN_TIMEOUT_S = 45.0


def probe_timeout(spec: Any) -> float:
    """How long one probe may wait: at least 45 seconds, and longer than the target's own wait for an answer, so a slow
    assistant that the tests are allowed two minutes is not given up on after 45 seconds while probing."""
    waits = [PROBE_MIN_TIMEOUT_S]
    if getattr(spec, "web", None) is not None:
        waits.append(spec.web.reply_timeout_seconds + 15.0)
    if getattr(spec, "api", None) is not None:
        waits.append(spec.api.timeout_seconds + 15.0)
    return max(waits)


class Prober:
    def __init__(
        self, adapter: AgentAdapter, *, timeout: float = 45.0, rag_question: str | None = None, max_probes: int = 7
    ) -> None:
        self.adapter = adapter
        self.timeout = timeout
        self.rag_question = rag_question
        self.max_probes = max_probes

    async def _ask(self, name: str, text: str, session: str) -> ProbeObservation:
        t0 = time.perf_counter()
        try:
            resp = await asyncio.wait_for(
                self.adapter.send(AgentRequest(input=text, session_id=session, metadata={"probe": name})), self.timeout
            )
        except TimeoutError:
            return ProbeObservation(name=name, input=text, error=f"no response within {self.timeout:g}s")
        except AgentLabError as exc:
            return ProbeObservation(name=name, input=text, error=f"{exc.kind.value}: {str(exc)[:200]}")
        return ProbeObservation(
            name=name,
            input=text,
            output=resp.output[:1500],
            tool_calls=[c.name for c in resp.tool_calls],
            tool_args=[c.arguments for c in resp.tool_calls][:6],
            contexts=[c.source for c in resp.contexts],
            event_types=sorted({e.type for e in resp.events}),
            handoffs=[
                (str(e.data.get("from") or "?"), str(e.data["to"]))
                for e in resp.events
                if e.type == "handoff" and e.data.get("to")
            ],
            citations=resp.citations[:6],
            error=resp.error,
            latency_ms=resp.latency_ms or round((time.perf_counter() - t0) * 1000, 2),
            tokens=resp.usage.total_tokens,
            cost_usd=resp.usage.cost_usd,
            status_code=resp.status_code,
        )

    async def run(self) -> ProbeResult:
        res = ProbeResult()
        sess1 = await self.adapter.new_session()
        sess2 = await self.adapter.new_session()
        plan: list[tuple[str, str, str]] = [
            ("greeting", "Hello!", sess1),
            (
                "capabilities",
                "What can you help me with? Briefly list your capabilities and any tools you can use.",
                sess1,
            ),
            ("memory_set", "My favorite number is 7421. Please remember it.", sess2),
            ("memory_get", "What is my favorite number?", sess2),
            ("tool_math", "What is 17 * 23?", sess1),
            ("tool_weather", "What is the weather in Paris?", sess1),
        ]
        if self.rag_question:
            plan.append(("retrieval", self.rag_question, sess1))
        try:
            for name, text, sess in plan[: self.max_probes]:
                res.observations.append(await self._ask(name, text, sess))
        finally:
            for s in (sess1, sess2):
                try:
                    await self.adapter.end_session(s)
                except Exception:  # noqa: S110 - best-effort cleanup
                    pass
        obs = res.observations
        res.reachable = any(o.error is None and (o.output or o.tool_calls) for o in obs)
        res.errors = [f"{o.name}: {o.error}" for o in obs if o.error]
        res.tools_seen = sorted({t for o in obs for t in o.tool_calls})
        res.contexts_seen = sum(len(o.contexts) for o in obs)
        res.event_types = sorted({t for o in obs for t in o.event_types})
        res.handoffs = list(dict.fromkeys(h for o in obs for h in o.handoffs))
        mg = res.obs("memory_get")
        if mg and not mg.error:
            res.session_memory = "7421" in mg.output
        caps = res.obs("capabilities")
        if caps and caps.output:
            res.self_reported_tools = sorted(set(re.findall(r"\b[a-z]+(?:_[a-z0-9]+)+\b", caps.output)))[:15]
            res.self_reported_topics = sorted(k for k, rx in _TOPICS.items() if re.search(rx, caps.output, re.I))
        res.latency_ms_p50 = round(_percentile([o.latency_ms for o in obs if o.latency_ms], 0.5), 2)
        res.tokens = sum(o.tokens for o in obs)
        res.cost_usd = round(sum(o.cost_usd for o in obs), 8)
        caps_obj = self.adapter.capabilities
        res.streaming, res.attachments = caps_obj.streaming, caps_obj.attachments
        return res
