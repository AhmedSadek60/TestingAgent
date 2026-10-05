"""Google Gemini adapter (Gemini API ``models.generateContent``).

* Auth: ``x-goog-api-key`` header.
* Structured output: ``generationConfig.responseMimeType = application/json`` plus
  ``responseJsonSchema``.
* Tools: ``tools[].functionDeclarations``; calls come back as ``functionCall`` parts.
* Usage: ``usageMetadata.promptTokenCount`` / ``candidatesTokenCount``.
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


class GeminiProvider(LLMProvider):
    type_name = "gemini"
    default_capabilities = frozenset(
        {
            Capability.CHAT,
            Capability.STREAMING,
            Capability.STRUCTURED_OUTPUT,
            Capability.JSON_SCHEMA,
            Capability.TOOL_CALLING,
            Capability.MULTIMODAL,
            Capability.EMBEDDINGS,
            Capability.MODEL_DISCOVERY,
        }
    )

    def __init__(self, config, credentials=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(config, credentials)
        self.base_url = (config.base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        self._client = httpx.AsyncClient(timeout=config.timeout)

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.api_key() or "", "Content-Type": "application/json", **self.config.headers}

    @staticmethod
    def _model_path(model: str) -> str:
        return model if model.startswith("models/") else f"models/{model}"

    @staticmethod
    def _contents(messages: list[Message]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        system_parts: list[dict[str, Any]] = []
        contents: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                system_parts.append({"text": m.text()})
                continue
            parts: list[dict[str, Any]] = []
            if isinstance(m.content, str):
                if m.content:
                    parts.append({"text": m.content})
            else:
                for p in m.content:
                    if p.type == "text":
                        parts.append({"text": p.text or ""})
                    elif p.type == "image" and p.data_b64:
                        parts.append({"inlineData": {"mimeType": p.media_type, "data": p.data_b64}})
            for c in m.tool_calls:
                parts.append({"functionCall": {"name": c.name, "args": c.arguments}})
            if m.role == "tool":
                contents.append(
                    {
                        "role": "user",
                        "parts": [{"functionResponse": {"name": m.name or "tool", "response": {"result": m.text()}}}],
                    }
                )
                continue
            contents.append({"role": "model" if m.role == "assistant" else "user", "parts": parts})
        system = {"parts": system_parts} if system_parts else None
        return system, contents

    def _body(self, request: CompletionRequest) -> dict[str, Any]:
        system, contents = self._contents(request.messages)
        gen: dict[str, Any] = {"maxOutputTokens": request.max_tokens}
        if request.temperature is not None:
            gen["temperature"] = request.temperature
        if request.stop:
            gen["stopSequences"] = request.stop
        if request.json_schema is not None:
            gen["responseMimeType"] = "application/json"
            gen["responseJsonSchema"] = request.json_schema
        elif request.metadata.get("_json_mode"):
            gen["responseMimeType"] = "application/json"
        body: dict[str, Any] = {"contents": contents, "generationConfig": gen}
        if system:
            body["systemInstruction"] = system
        if request.tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {"name": t.name, "description": t.description, "parameters": t.parameters}
                        for t in request.tools
                    ]
                }
            ]
        return body

    async def _complete(self, request: CompletionRequest, model: str) -> CompletionResponse:
        url = f"{self.base_url}/{self._model_path(model)}:generateContent"
        try:
            r = await self._client.post(url, headers=self._headers(), json=self._body(request))
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name}: connection error: {exc}", retryable=True) from exc
        raise_for_status(self.name, r.status_code, r.text, dict(r.headers))
        data = r.json()
        cands = data.get("candidates") or []
        if not cands:
            block = (data.get("promptFeedback") or {}).get("blockReason")
            raise ProviderError(f"{self.name}: no candidates returned (blockReason={block})")
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought"))
        calls = [
            LLMToolCall(name=p["functionCall"]["name"], arguments=p["functionCall"].get("args") or {})
            for p in parts
            if "functionCall" in p
        ]
        um = data.get("usageMetadata") or {}
        return CompletionResponse(
            text=text,
            tool_calls=calls,
            provider=self.name,
            model=data.get("modelVersion", model),
            usage=TokenUsage(
                input_tokens=um.get("promptTokenCount", 0) or 0,
                output_tokens=(um.get("candidatesTokenCount", 0) or 0) + (um.get("thoughtsTokenCount", 0) or 0),
            ),
            finish_reason=cands[0].get("finishReason"),
            request_id=data.get("responseId"),
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[str]:
        model = self.model_name(request)
        url = f"{self.base_url}/{self._model_path(model)}:streamGenerateContent?alt=sse"
        async with self._client.stream("POST", url, headers=self._headers(), json=self._body(request)) as r:
            if r.status_code >= 400:
                raise_for_status(self.name, r.status_code, (await r.aread()).decode(), dict(r.headers))
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                chunk = json.loads(line[5:])
                for c in chunk.get("candidates") or []:
                    for p in (c.get("content") or {}).get("parts") or []:
                        if p.get("text") and not p.get("thought"):
                            yield p["text"]

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        m = self._model_path(model or self.config.options.get("embedding_model", "gemini-embedding-001"))
        body = {"requests": [{"model": m, "content": {"parts": [{"text": t}]}} for t in texts]}
        r = await self._client.post(f"{self.base_url}/{m}:batchEmbedContents", headers=self._headers(), json=body)
        raise_for_status(self.name, r.status_code, r.text, dict(r.headers))
        return [e["values"] for e in r.json()["embeddings"]]

    async def list_models(self) -> list[ModelInfo]:
        out: list[ModelInfo] = []
        page: str | None = None
        while True:
            params: dict[str, str | int] = {"pageSize": 1000, **({"pageToken": page} if page else {})}
            r = await self._client.get(f"{self.base_url}/models", headers=self._headers(), params=params)
            raise_for_status(self.name, r.status_code, r.text, dict(r.headers))
            data = r.json()
            for m in data.get("models", []):
                methods = set(m.get("supportedGenerationMethods") or [])
                caps = {Capability.MODEL_DISCOVERY}
                if "generateContent" in methods:
                    caps |= set(self.default_capabilities) - {Capability.EMBEDDINGS}
                if "embedContent" in methods:
                    caps.add(Capability.EMBEDDINGS)
                out.append(
                    ModelInfo(
                        id=m["name"].removeprefix("models/"),
                        name=m.get("displayName"),
                        context_length=m.get("inputTokenLimit"),
                        capabilities=sorted(caps),
                        provider=self.name,
                    )
                )
            page = data.get("nextPageToken")
            if not page:
                return out

    async def aclose(self) -> None:
        await self._client.aclose()
