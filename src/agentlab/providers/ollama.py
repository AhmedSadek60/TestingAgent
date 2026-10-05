"""Ollama adapter using the native API (``/api/chat``, ``/api/tags``, ``/api/show``).

Capabilities come from ``/api/show`` (``capabilities``: completion, tools, vision,
embedding, ...) when available, so tool calling is only used on models that report it.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

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


class OllamaProvider(LLMProvider):
    type_name = "ollama"
    default_capabilities = frozenset({
        Capability.CHAT, Capability.STREAMING, Capability.STRUCTURED_OUTPUT, Capability.JSON_SCHEMA,
        Capability.MODEL_DISCOVERY,
    })

    def __init__(self, config, credentials=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(config, credentials)
        self.base_url = (config.base_url or "http://localhost:11434").rstrip("/")
        if self.base_url.endswith("/v1"):
            self.base_url = self.base_url[:-3]
        self._client = httpx.AsyncClient(timeout=config.timeout)

    def _headers(self) -> dict[str, str]:
        h = dict(self.config.headers)
        key = self.api_key(required=False)
        if key:
            h["Authorization"] = f"Bearer {key}"
        return h

    @staticmethod
    def _messages(messages: list[Message]) -> list[dict[str, Any]]:
        out = []
        for m in messages:
            entry: dict[str, Any] = {"role": m.role, "content": m.text()}
            if not isinstance(m.content, str):
                imgs = [p.data_b64 for p in m.content if p.type == "image" and p.data_b64]
                if imgs:
                    entry["images"] = imgs
            if m.tool_calls:
                entry["tool_calls"] = [{"function": {"name": c.name, "arguments": c.arguments}}
                                       for c in m.tool_calls]
            out.append(entry)
        return out

    def _body(self, request: CompletionRequest, model: str, stream: bool) -> dict[str, Any]:
        body: dict[str, Any] = {"model": model, "messages": self._messages(request.messages), "stream": stream,
                                "options": {"num_predict": request.max_tokens}}
        if request.temperature is not None:
            body["options"]["temperature"] = request.temperature
        if request.json_schema is not None:
            body["format"] = request.json_schema
        elif request.metadata.get("_json_mode"):
            body["format"] = "json"
        if request.tools:
            body["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters}} for t in request.tools]
        return body

    async def _complete(self, request: CompletionRequest, model: str) -> CompletionResponse:
        try:
            r = await self._client.post(f"{self.base_url}/api/chat", headers=self._headers(),
                                        json=self._body(request, model, stream=False))
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name}: cannot reach Ollama at {self.base_url}: {exc}",
                                retryable=True) from exc
        raise_for_status(self.name, r.status_code, r.text, dict(r.headers))
        data = r.json()
        msg = data.get("message") or {}
        calls = [LLMToolCall(name=c["function"]["name"], arguments=c["function"].get("arguments") or {})
                 for c in msg.get("tool_calls") or []]
        return CompletionResponse(
            text=msg.get("content", ""), tool_calls=calls, provider=self.name, model=data.get("model", model),
            usage=TokenUsage(input_tokens=data.get("prompt_eval_count", 0) or 0,
                             output_tokens=data.get("eval_count", 0) or 0),
            finish_reason=data.get("done_reason"), cost_usd=0.0,
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[str]:
        model = self.model_name(request)
        async with self._client.stream("POST", f"{self.base_url}/api/chat", headers=self._headers(),
                                       json=self._body(request, model, stream=True)) as r:
            if r.status_code >= 400:
                raise_for_status(self.name, r.status_code, (await r.aread()).decode(), dict(r.headers))
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                piece = (chunk.get("message") or {}).get("content")
                if piece:
                    yield piece
                if chunk.get("done"):
                    break

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        r = await self._client.post(f"{self.base_url}/api/embed", headers=self._headers(),
                                    json={"model": model or self.model_name(), "input": texts})
        raise_for_status(self.name, r.status_code, r.text, dict(r.headers))
        return r.json()["embeddings"]

    async def _show(self, model: str) -> set[Capability]:
        r = await self._client.post(f"{self.base_url}/api/show", headers=self._headers(), json={"model": model})
        if r.status_code >= 400:
            return set(self.default_capabilities)
        reported = set(r.json().get("capabilities") or [])
        if not reported:
            return set(self.default_capabilities)
        caps = {Capability.MODEL_DISCOVERY}
        if "completion" in reported:
            caps |= {Capability.CHAT, Capability.STREAMING, Capability.STRUCTURED_OUTPUT, Capability.JSON_SCHEMA}
        if "tools" in reported:
            caps.add(Capability.TOOL_CALLING)
        if "vision" in reported:
            caps.add(Capability.MULTIMODAL)
        if "embedding" in reported:
            caps.add(Capability.EMBEDDINGS)
        return caps

    async def list_models(self) -> list[ModelInfo]:
        try:
            r = await self._client.get(f"{self.base_url}/api/tags", headers=self._headers())
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name}: cannot reach Ollama at {self.base_url}: {exc}") from exc
        raise_for_status(self.name, r.status_code, r.text, dict(r.headers))
        out = []
        for m in r.json().get("models", []):
            caps = await self._show(m["name"])
            out.append(ModelInfo(id=m["name"], name=m.get("model", m["name"]), capabilities=sorted(caps),
                                 provider=self.name))
        return out

    async def aclose(self) -> None:
        await self._client.aclose()
