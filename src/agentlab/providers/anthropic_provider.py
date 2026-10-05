"""Anthropic adapter built on the official ``anthropic`` SDK (optional dependency).

* Structured output uses ``output_config.format`` (JSON schema).
* Sampling parameters are omitted by default: recent Claude models reject non-default
  values. Set ``options.send_temperature: true`` for models that still accept them.
* Forced tool use (``tool_choice`` ``any``/``tool``) is rejected by recent models, so
  ``required`` degrades to ``auto`` unless ``options.allow_forced_tool_choice`` is set.
* SDK retries are disabled; AgentLab's own retry loop reports retry counts.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from agentlab.core.config import Pricing
from agentlab.core.errors import CredentialError, ProviderError, RateLimitError
from agentlab.providers.base import (
    Capability,
    CompletionRequest,
    CompletionResponse,
    LLMProvider,
    LLMToolCall,
    Message,
    ModelInfo,
    TokenUsage,
)

# USD per million tokens, cached from Anthropic's published pricing on 2026-09-25.
# Override per model with ``pricing:`` in the provider configuration.
DEFAULT_PRICING: dict[str, Pricing] = {
    "claude-opus-5-5": Pricing(input_per_mtok=4.0, output_per_mtok=20.0),
    "claude-opus-5": Pricing(input_per_mtok=5.0, output_per_mtok=25.0),
    "claude-sonnet-5-5": Pricing(input_per_mtok=2.0, output_per_mtok=10.0),
    "claude-sonnet-5": Pricing(input_per_mtok=2.0, output_per_mtok=10.0),
    "claude-haiku-4-5": Pricing(input_per_mtok=1.0, output_per_mtok=5.0),
    "claude-fable-5-1": Pricing(input_per_mtok=10.0, output_per_mtok=50.0),
}


class AnthropicProvider(LLMProvider):
    type_name = "anthropic"
    default_capabilities = frozenset({
        Capability.CHAT, Capability.STREAMING, Capability.STRUCTURED_OUTPUT, Capability.JSON_SCHEMA,
        Capability.TOOL_CALLING, Capability.MULTIMODAL, Capability.MODEL_DISCOVERY,
    })

    def __init__(self, config, credentials=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(config, credentials)
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("the 'anthropic' package is required for the anthropic provider "
                                "(pip install anthropic)") from exc
        self._anthropic = anthropic
        kwargs: dict[str, Any] = {"api_key": self.api_key(), "timeout": config.timeout, "max_retries": 0}
        if config.base_url:
            kwargs["base_url"] = config.base_url
        if config.headers:
            kwargs["default_headers"] = config.headers
        self._client = anthropic.AsyncAnthropic(**kwargs)

    def estimate_cost(self, model: str, usage: TokenUsage) -> float | None:
        cost = super().estimate_cost(model, usage)
        if cost is not None:
            return cost
        price = DEFAULT_PRICING.get(model)
        if price is None:
            return None
        return (usage.input_tokens * price.input_per_mtok + usage.output_tokens * price.output_per_mtok) / 1e6

    # ---------------------------------------------------------------- mapping
    @staticmethod
    def _blocks(m: Message) -> list[dict[str, Any]] | str:
        if isinstance(m.content, str):
            return m.content
        out: list[dict[str, Any]] = []
        for p in m.content:
            if p.type == "text":
                out.append({"type": "text", "text": p.text or ""})
            elif p.type == "image":
                if p.data_b64:
                    out.append({"type": "image", "source": {"type": "base64", "media_type": p.media_type,
                                                            "data": p.data_b64}})
                elif p.url:
                    out.append({"type": "image", "source": {"type": "url", "url": p.url}})
        return out

    def _payload(self, request: CompletionRequest, model: str) -> tuple[dict[str, Any], list[str]]:
        degraded: list[str] = []
        system = "\n\n".join(m.text() for m in request.messages if m.role == "system")
        messages: list[dict[str, Any]] = []
        for m in request.messages:
            if m.role == "system":
                continue
            if m.role == "tool":
                block = {"type": "tool_result", "tool_use_id": m.tool_call_id or "", "content": m.text()}
                if messages and messages[-1]["role"] == "user" and isinstance(messages[-1]["content"], list) \
                        and messages[-1]["content"] and messages[-1]["content"][-1].get("type") == "tool_result":
                    messages[-1]["content"].append(block)
                else:
                    messages.append({"role": "user", "content": [block]})
                continue
            content = self._blocks(m)
            if m.tool_calls:
                blocks = content if isinstance(content, list) else ([{"type": "text", "text": content}] if content else [])
                blocks += [{"type": "tool_use", "id": c.id or f"toolu_{i}", "name": c.name, "input": c.arguments}
                           for i, c in enumerate(m.tool_calls)]
                content = blocks
            messages.append({"role": m.role, "content": content})
        payload: dict[str, Any] = {"model": model, "max_tokens": request.max_tokens, "messages": messages}
        if system:
            payload["system"] = system
        if request.stop:
            payload["stop_sequences"] = request.stop
        if request.temperature is not None and self.config.options.get("send_temperature"):
            payload["temperature"] = request.temperature
        if request.json_schema is not None:
            payload["output_config"] = {"format": {"type": "json_schema", "schema": request.json_schema}}
        if request.tools:
            payload["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters}
                                for t in request.tools]
            if request.tool_choice == "required":
                if self.config.options.get("allow_forced_tool_choice"):
                    payload["tool_choice"] = {"type": "any"}
                else:
                    payload["tool_choice"] = {"type": "auto"}
                    degraded.append("forced_tool_choice")
            elif request.tool_choice == "none":
                payload["tool_choice"] = {"type": "none"}
        extra = self.config.options.get("extra_body")
        if extra:
            payload["extra_body"] = extra
        return payload, degraded

    def _map_error(self, exc: Exception) -> Exception:
        a = self._anthropic
        if isinstance(exc, a.RateLimitError):
            ra = exc.response.headers.get("retry-after") if getattr(exc, "response", None) else None
            return RateLimitError(f"{self.name}: rate limited", retry_after=float(ra) if ra else None)
        if isinstance(exc, a.AuthenticationError | a.PermissionDeniedError):
            return CredentialError(f"{self.name}: authentication/authorisation failed")
        if isinstance(exc, a.APIConnectionError | a.APITimeoutError):
            return ProviderError(f"{self.name}: connection problem: {exc}", retryable=True)
        if isinstance(exc, a.APIStatusError):
            return ProviderError(f"{self.name}: HTTP {exc.status_code}: {str(exc)[:300]}",
                                 retryable=exc.status_code >= 500)
        return ProviderError(f"{self.name}: {exc}")

    async def _complete(self, request: CompletionRequest, model: str) -> CompletionResponse:
        payload, degraded = self._payload(request, model)
        try:
            msg = await self._client.messages.create(**payload)
        except self._anthropic.BadRequestError as exc:
            if "output_config" in payload and "schema" in str(exc).lower():
                payload.pop("output_config")
                payload["messages"] = [*payload["messages"], {
                    "role": "user", "content": "Respond with a single JSON object only that validates against "
                    f"this JSON Schema: {request.json_schema}"}]
                degraded.append("json_schema")
                try:
                    msg = await self._client.messages.create(**payload)
                except Exception as exc2:
                    raise self._map_error(exc2) from exc2
            else:
                raise self._map_error(exc) from exc
        except Exception as exc:
            raise self._map_error(exc) from exc
        text = "".join(b.text for b in msg.content if b.type == "text")
        calls = [LLMToolCall(id=b.id, name=b.name, arguments=dict(b.input or {}))
                 for b in msg.content if b.type == "tool_use"]
        u = msg.usage
        input_tokens = (u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0) \
            + (getattr(u, "cache_creation_input_tokens", 0) or 0)
        return CompletionResponse(
            text=text, tool_calls=calls, provider=self.name, model=msg.model,
            usage=TokenUsage(input_tokens=input_tokens, output_tokens=u.output_tokens or 0),
            finish_reason=msg.stop_reason, request_id=getattr(msg, "_request_id", None), degraded=degraded,
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[str]:
        model = self.model_name(request)
        payload, _ = self._payload(request, model)
        try:
            async with self._client.messages.stream(**payload) as s:
                async for piece in s.text_stream:
                    yield piece
        except Exception as exc:
            raise self._map_error(exc) from exc

    async def list_models(self) -> list[ModelInfo]:
        out: list[ModelInfo] = []
        try:
            async for m in self._client.models.list(limit=1000):
                caps = set(self.default_capabilities)
                out.append(ModelInfo(
                    id=m.id, name=getattr(m, "display_name", None), provider=self.name,
                    context_length=getattr(m, "max_input_tokens", None), capabilities=sorted(caps),
                    pricing=DEFAULT_PRICING.get(m.id),
                ))
        except Exception as exc:
            raise self._map_error(exc) from exc
        return out

    async def aclose(self) -> None:
        await self._client.close()
