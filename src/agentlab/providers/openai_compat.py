"""OpenAI Chat Completions compatible adapters.

Covers OpenAI itself, OpenRouter, and any OpenAI-compatible server (LM Studio, vLLM,
llama.cpp server, LiteLLM proxies, ...). Capabilities differ per server, so local
presets start conservative and only enable tool calling / JSON schema when the
configuration declares them.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from agentlab.core.config import Pricing
from agentlab.core.errors import ProviderError
from agentlab.providers.base import (
    Capability,
    CompletionRequest,
    CompletionResponse,
    LLMProvider,
    LLMToolCall,
    Message,
    ModelInfo,
    TokenUsage,
    raise_for_status,
)


def _content(m: Message) -> Any:
    if isinstance(m.content, str):
        return m.content
    parts: list[dict[str, Any]] = []
    for p in m.content:
        if p.type == "text":
            parts.append({"type": "text", "text": p.text or ""})
        elif p.type == "image":
            url = p.url or f"data:{p.media_type};base64,{p.data_b64}"
            parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


def to_openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        msg: dict[str, Any] = {"role": m.role, "content": _content(m)}
        if m.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": c.id or f"call_{i}",
                    "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                }
                for i, c in enumerate(m.tool_calls)
            ]
        if m.role == "tool":
            msg["tool_call_id"] = m.tool_call_id
        out.append(msg)
    return out


class OpenAICompatibleProvider(LLMProvider):
    type_name = "openai_compatible"
    default_base_url: str | None = None
    default_capabilities = frozenset({Capability.CHAT, Capability.STREAMING, Capability.MODEL_DISCOVERY})
    requires_key = False

    def __init__(self, config, credentials=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(config, credentials)
        base = config.base_url or self.default_base_url
        if not base:
            raise ProviderError(f"provider '{config.name}' needs base_url")
        self.base_url = base.rstrip("/")
        self._client = httpx.AsyncClient(timeout=config.timeout)

    def headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", **self.config.headers}
        key = self.api_key(required=self.requires_key)
        if key:
            h["Authorization"] = f"Bearer {key}"
        return h

    def build_body(self, request: CompletionRequest, model: str, stream: bool = False) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": to_openai_messages(request.messages),
            "max_tokens": request.max_tokens,
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.stop:
            body["stop"] = request.stop
        if request.json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": request.schema_name, "schema": request.json_schema, "strict": False},
            }
        elif request.metadata.get("_json_mode"):
            body["response_format"] = {"type": "json_object"}
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in request.tools
            ]
            if request.tool_choice:
                body["tool_choice"] = request.tool_choice
        if stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
        body.update(self.config.options.get("extra_body", {}))
        return body

    async def _complete(self, request: CompletionRequest, model: str) -> CompletionResponse:
        url = f"{self.base_url}/chat/completions"
        try:
            r = await self._client.post(url, headers=self.headers(), json=self.build_body(request, model))
        except httpx.TimeoutException as exc:
            raise ProviderError(f"{self.name}: timeout calling {url}", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name}: connection error: {exc}", retryable=True) from exc
        raise_for_status(self.name, r.status_code, r.text, dict(r.headers))
        data = r.json()
        if "error" in data and not data.get("choices"):
            raise ProviderError(f"{self.name}: {data['error']}")
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        calls = []
        for c in msg.get("tool_calls") or []:
            fn = c.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_raw": fn.get("arguments")}
            calls.append(LLMToolCall(id=c.get("id"), name=fn.get("name", ""), arguments=args))
        usage = data.get("usage") or {}
        content = msg.get("content")
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        cost = None
        if isinstance(usage.get("cost"), int | float):  # OpenRouter reports actual cost
            cost = float(usage["cost"])
        return CompletionResponse(
            text=content or "",
            tool_calls=calls,
            provider=self.name,
            model=data.get("model", model),
            usage=TokenUsage(
                input_tokens=usage.get("prompt_tokens", 0) or 0, output_tokens=usage.get("completion_tokens", 0) or 0
            ),
            finish_reason=choice.get("finish_reason"),
            request_id=data.get("id"),
            cost_usd=cost,
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[str]:
        model = self.model_name(request)
        url = f"{self.base_url}/chat/completions"
        async with self._client.stream(
            "POST", url, headers=self.headers(), json=self.build_body(request, model, stream=True)
        ) as r:
            if r.status_code >= 400:
                body = (await r.aread()).decode(errors="replace")
                raise_for_status(self.name, r.status_code, body, dict(r.headers))
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                for ch in chunk.get("choices") or []:
                    delta = (ch.get("delta") or {}).get("content")
                    if delta:
                        yield delta

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        r = await self._client.post(
            f"{self.base_url}/embeddings",
            headers=self.headers(),
            json={"model": model or self.config.options.get("embedding_model") or self.model_name(), "input": texts},
        )
        raise_for_status(self.name, r.status_code, r.text, dict(r.headers))
        return [d["embedding"] for d in r.json()["data"]]

    async def list_models(self) -> list[ModelInfo]:
        r = await self._client.get(f"{self.base_url}/models", headers=self.headers())
        raise_for_status(self.name, r.status_code, r.text, dict(r.headers))
        return [self._model_info(m) for m in r.json().get("data", [])]

    def _model_info(self, m: dict[str, Any]) -> ModelInfo:
        return ModelInfo(
            id=m["id"],
            name=m.get("name") or m["id"],
            provider=self.name,
            capabilities=sorted(self.default_capabilities),
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class OpenAIProvider(OpenAICompatibleProvider):
    type_name = "openai"
    default_base_url = "https://api.openai.com/v1"
    requires_key = True
    default_capabilities = frozenset(
        {
            Capability.CHAT,
            Capability.STREAMING,
            Capability.STRUCTURED_OUTPUT,
            Capability.JSON_SCHEMA,
            Capability.TOOL_CALLING,
            Capability.EMBEDDINGS,
            Capability.MODEL_DISCOVERY,
        }
    )


class OpenRouterProvider(OpenAICompatibleProvider):
    """OpenRouter. Model discovery returns live pricing and ``supported_parameters`` which
    drive capability negotiation per model."""

    type_name = "openrouter"
    default_base_url = "https://openrouter.ai/api/v1"
    requires_key = True
    default_capabilities = frozenset({Capability.CHAT, Capability.STREAMING, Capability.MODEL_DISCOVERY})

    def _model_info(self, m: dict[str, Any]) -> ModelInfo:
        caps = {Capability.CHAT, Capability.STREAMING, Capability.MODEL_DISCOVERY}
        params = set(m.get("supported_parameters") or [])
        if "tools" in params:
            caps.add(Capability.TOOL_CALLING)
        if "structured_outputs" in params:
            caps.add(Capability.JSON_SCHEMA)
        if "response_format" in params:
            caps.add(Capability.STRUCTURED_OUTPUT)
        modalities = (m.get("architecture") or {}).get("input_modalities") or []
        if "image" in modalities:
            caps.add(Capability.MULTIMODAL)
        pricing = None
        p = m.get("pricing") or {}
        try:
            pricing = Pricing(
                input_per_mtok=float(p.get("prompt", 0)) * 1e6, output_per_mtok=float(p.get("completion", 0)) * 1e6
            )
        except (TypeError, ValueError):
            pricing = None
        return ModelInfo(
            id=m["id"],
            name=m.get("name"),
            context_length=m.get("context_length"),
            capabilities=sorted(caps),
            pricing=pricing,
            provider=self.name,
        )

    def build_body(self, request: CompletionRequest, model: str, stream: bool = False) -> dict[str, Any]:
        body = super().build_body(request, model, stream)
        body.setdefault("usage", {"include": True})
        return body


class LMStudioProvider(OpenAICompatibleProvider):
    type_name = "lmstudio"
    default_base_url = "http://localhost:1234/v1"


class VLLMProvider(OpenAICompatibleProvider):
    type_name = "vllm"
    default_base_url = "http://localhost:8000/v1"


class LlamaCppProvider(OpenAICompatibleProvider):
    type_name = "llamacpp"
    default_base_url = "http://localhost:8080/v1"


def _elapsed_ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 2)
