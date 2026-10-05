"""LlmAgentAdapter: a bare model (plus system prompt, tools and knowledge) as the target.

This lets AgentLab evaluate e.g. a Gemini, OpenRouter, OpenAI, Anthropic or local Ollama model
directly. Tool calls requested by the model are answered with the deterministic ``mock_result`` of
the declared tool, so tool *selection* and *arguments* can be evaluated without side effects.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from agentlab.adapters.base import ADAPTERS, AdapterCapabilities, AdapterContext, AgentAdapter
from agentlab.core.errors import AgentLabError, TargetError, UnsupportedCapability
from agentlab.core.models import AgentRequest, AgentResponse, RetrievedContext, TargetSpec, ToolCall, Usage
from agentlab.providers import Capability, CompletionRequest, LLMToolCall, Message, ToolSpec


class _Default(defaultdict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class LlmAgentAdapter(AgentAdapter):
    kind = "llm"

    def __init__(self, spec: TargetSpec, ctx: AdapterContext) -> None:
        super().__init__(spec, ctx)
        if spec.llm is None:
            raise TargetError("llm adapter requires target.llm")
        if ctx.providers is None:
            raise TargetError("llm targets need a ProviderManager")
        self.cfg = spec.llm
        self.provider = ctx.providers.get(self.cfg.provider)
        self.model = self.cfg.model or self.provider.config.model
        self._sessions: dict[str, list[Message]] = {}
        self.capabilities = AdapterCapabilities(
            reports_tool_calls=bool(self.cfg.tools), reports_contexts=bool(self.cfg.knowledge), reports_usage=True,
            notes=[f"model {self.provider.name}/{self.model}"])

    async def open(self) -> None:
        if self.cfg.tools and not self.provider.supports(Capability.TOOL_CALLING, self.model):
            # Model discovery may refine capabilities (e.g. Ollama /api/show). Try it once.
            try:
                await self.provider.discover()
            except AgentLabError:
                pass
            if not self.provider.supports(Capability.TOOL_CALLING, self.model):
                raise UnsupportedCapability(
                    f"model {self.provider.name}/{self.model} does not support tool calling; tool tests are blocked")

    def _system(self) -> Message:
        text = self.cfg.system_prompt
        if self.cfg.knowledge:
            docs = "\n\n".join(f"[document: {n}]\n{t}" for n, t in self.cfg.knowledge.items())
            text += ("\n\nUse ONLY the following knowledge base documents to answer factual questions. If the answer is "
                     f"not in them, say you do not know.\n\n{docs}")
        return Message(role="system", content=text)

    async def end_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    @staticmethod
    def _tool_result(defn: Any, args: dict[str, Any]) -> Any:
        res = defn.mock_result
        if isinstance(res, str) and "{" in res:
            try:
                return res.format_map(_Default(str, args))
            except (ValueError, IndexError, KeyError):
                return res
        return res

    async def send(self, request: AgentRequest) -> AgentResponse:
        history = self._sessions.setdefault(request.session_id, [self._system()])
        history.append(Message(role="user", content=request.input))
        tools = [ToolSpec(name=t.name, description=t.description, parameters=t.parameters) for t in self.cfg.tools]
        defs = {t.name: t for t in self.cfg.tools}
        out = AgentResponse()
        usage = Usage()
        t0 = time.perf_counter()
        try:
            for _round in range(self.cfg.max_tool_rounds + 1):
                resp = await self.provider.complete(CompletionRequest(
                    messages=history, model=self.model, tools=tools if _round < self.cfg.max_tool_rounds else [],
                    max_tokens=self.cfg.max_tokens, temperature=self.cfg.temperature))
                usage.input_tokens += resp.usage.input_tokens
                usage.output_tokens += resp.usage.output_tokens
                usage.llm_calls += 1
                usage.cost_usd += resp.cost_usd or 0.0
                if not resp.tool_calls:
                    out.output = resp.text
                    history.append(Message(role="assistant", content=resp.text))
                    break
                history.append(Message(role="assistant", content=resp.text,
                                       tool_calls=[LLMToolCall(id=c.id or f"call_{i}", name=c.name, arguments=c.arguments)
                                                   for i, c in enumerate(resp.tool_calls)]))
                for i, c in enumerate(resp.tool_calls):
                    defn = defs.get(c.name)
                    if defn is None:
                        result: Any = f"error: unknown tool {c.name}"
                        status = "error"
                    else:
                        result, status = self._tool_result(defn, c.arguments), "success"
                    out.tool_calls.append(ToolCall(name=c.name, arguments=c.arguments, result=result, status=status))
                    history.append(Message(role="tool", content=str(result), tool_call_id=c.id or f"call_{i}", name=c.name))
            else:
                out.output = out.output or ""
        except AgentLabError as exc:
            out.error = f"{exc.kind.value}: {exc}"
        out.usage = usage
        out.latency_ms = (time.perf_counter() - t0) * 1000
        if self.cfg.knowledge:
            out.contexts = [RetrievedContext(source=n, content=t) for n, t in self.cfg.knowledge.items()]
        return out


ADAPTERS.register("llm", LlmAgentAdapter, replace=True)
