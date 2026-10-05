"""LLMProvider abstraction (spec section 3).

The evaluation engine depends only on this interface. Concrete adapters translate
:class:`CompletionRequest` into a vendor API call and back. Capabilities are
*negotiated*: an adapter reports what a model supports (from configuration, live
model discovery, or conservative per-adapter defaults) and callers degrade
gracefully when a feature is missing instead of assuming it exists.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Any

import jsonschema
from pydantic import Field

from agentlab.core.config import Pricing, ProviderConfig
from agentlab.core.errors import CredentialError, ProviderError, RateLimitError, UnsupportedCapability
from agentlab.core.models.base import Model
from agentlab.security.credentials import CredentialManager, resolve_reference

log = logging.getLogger(__name__)


class Capability(StrEnum):
    CHAT = "chat"
    STREAMING = "streaming"
    STRUCTURED_OUTPUT = "structured_output"  # JSON mode (valid JSON, no schema guarantee)
    JSON_SCHEMA = "json_schema"  # schema-constrained output
    TOOL_CALLING = "tool_calling"
    MULTIMODAL = "multimodal"
    EMBEDDINGS = "embeddings"
    MODEL_DISCOVERY = "model_discovery"


class ContentPart(Model):
    type: str  # text | image
    text: str | None = None
    media_type: str | None = None
    data_b64: str | None = None
    url: str | None = None


class LLMToolCall(Model):
    id: str | None = None
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(Model):
    role: str  # system | user | assistant | tool
    content: str | list[ContentPart] = ""
    tool_calls: list[LLMToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None

    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        return "\n".join(p.text or "" for p in self.content if p.type == "text")


class ToolSpec(Model):
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class CompletionRequest(Model):
    messages: list[Message]
    model: str | None = None
    temperature: float | None = 0.0
    max_tokens: int = 2048
    json_schema: dict[str, Any] | None = None
    schema_name: str = "result"
    tools: list[ToolSpec] = Field(default_factory=list)
    tool_choice: str | None = None  # auto | none | required
    stop: list[str] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TokenUsage(Model):
    input_tokens: int = 0
    output_tokens: int = 0


class CompletionResponse(Model):
    text: str = ""
    tool_calls: list[LLMToolCall] = Field(default_factory=list)
    parsed: Any = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    cost_usd: float | None = None
    latency_ms: float = 0.0
    first_token_ms: float | None = None
    provider: str
    model: str
    retries: int = 0
    finish_reason: str | None = None
    degraded: list[str] = Field(default_factory=list, description="Capabilities emulated instead of native")
    request_id: str | None = None


class ModelInfo(Model):
    id: str
    name: str | None = None
    context_length: int | None = None
    capabilities: list[Capability] = Field(default_factory=list)
    pricing: Pricing | None = None
    provider: str = ""


class LLMProvider(ABC):
    """Base class for provider adapters. Subclasses implement ``_complete`` and friends."""

    type_name: str = "abstract"
    default_capabilities: frozenset[Capability] = frozenset({Capability.CHAT})

    def __init__(self, config: ProviderConfig, credentials: CredentialManager | None = None) -> None:
        self.config = config
        self.credentials = credentials
        self.name = config.name
        self._model_cache: dict[str, ModelInfo] = {}

    # ------------------------------------------------------------------ helpers
    def api_key(self, required: bool = True) -> str | None:
        ref = self.config.api_key_ref
        if not ref:
            if required:
                raise ProviderError(f"provider '{self.name}' has no api_key_ref configured")
            return None
        if ref.startswith("secret:"):
            if self.credentials is None:
                raise ProviderError("secret: references need a CredentialManager")
            fields = self.credentials.fields(ref.split(":", 1)[1])
            return fields.get("key") or fields.get("token")
        return resolve_reference(ref)

    def model_name(self, request: CompletionRequest | None = None) -> str:
        model = (request.model if request else None) or self.config.model
        if not model:
            raise ProviderError(f"provider '{self.name}' has no model configured")
        return model

    def capabilities(self, model: str | None = None) -> set[Capability]:
        """Capability negotiation: explicit config > discovered model info > adapter defaults."""
        if self.config.capabilities is not None:
            return {Capability(c) for c in self.config.capabilities}
        info = self._model_cache.get(model or self.config.model or "")
        if info is not None and info.capabilities:
            return set(info.capabilities)
        return set(self.default_capabilities)

    def supports(self, cap: Capability, model: str | None = None) -> bool:
        return cap in self.capabilities(model)

    def estimate_cost(self, model: str, usage: TokenUsage) -> float | None:
        price = self.config.pricing.get(model)
        if price is None:
            info = self._model_cache.get(model)
            price = info.pricing if info else None
        if price is None:
            return None
        return (usage.input_tokens * price.input_per_mtok + usage.output_tokens * price.output_per_mtok) / 1e6

    # ------------------------------------------------------------------ public API
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        model = self.model_name(request)
        caps = self.capabilities(model)
        degraded: list[str] = []
        req = request
        if request.tools and Capability.TOOL_CALLING not in caps:
            raise UnsupportedCapability(f"{self.name}/{model} does not declare tool calling")
        if request.json_schema is not None and Capability.JSON_SCHEMA not in caps:
            # Graceful degradation: ask for JSON in the prompt and validate afterwards.
            degraded.append("json_schema")
            instruction = (
                "Respond with a single JSON object only (no prose, no code fences) that validates "
                f"against this JSON Schema:\n{json.dumps(request.json_schema)}"
            )
            req = request.model_copy(update={
                "messages": [*request.messages, Message(role="user", content=instruction)],
                "json_schema": None,
                "metadata": {**request.metadata, "_json_mode": Capability.STRUCTURED_OUTPUT in caps},
            })
        attempts = 0
        start = time.perf_counter()
        while True:
            try:
                resp = await self._complete(req, model)
                if request.json_schema is not None:
                    self._finalise_structured(resp, request.json_schema)
                break
            except RateLimitError as exc:
                attempts += 1
                if attempts > self.config.max_retries:
                    raise
                await asyncio.sleep(min(exc.retry_after if exc.retry_after is not None else self._backoff(attempts), 30))
            except ProviderError as exc:
                attempts += 1
                if not exc.retryable or attempts > self.config.max_retries:
                    raise
                await asyncio.sleep(min(self._backoff(attempts), 30))
        resp.latency_ms = round((time.perf_counter() - start) * 1000, 2)
        resp.retries = attempts
        resp.degraded = degraded + resp.degraded
        if resp.cost_usd is None:
            resp.cost_usd = self.estimate_cost(model, resp.usage)
        return resp

    def _backoff(self, attempt: int) -> float:
        base = float(self.config.options.get("backoff_base", 1.0))
        return base * (2 ** (attempt - 1)) + (random.random() * base * 0.25)

    def _finalise_structured(self, resp: CompletionResponse, schema: dict[str, Any]) -> None:
        """Parse and validate structured output. Invalid output is a retryable provider error."""
        if resp.parsed is None:
            resp.parsed = parse_json_loose(resp.text)
        if resp.parsed is None:
            raise ProviderError(f"{self.name}/{resp.model} did not return parseable JSON", retryable=True)
        try:
            jsonschema.validate(resp.parsed, schema)
        except jsonschema.ValidationError as exc:
            raise ProviderError(
                f"{self.name}/{resp.model} returned JSON that does not match the schema: {exc.message[:200]}",
                retryable=True) from exc

    @abstractmethod
    async def _complete(self, request: CompletionRequest, model: str) -> CompletionResponse: ...

    async def stream(self, request: CompletionRequest) -> AsyncIterator[str]:
        """Default streaming falls back to a single chunk; adapters override when native."""
        resp = await self.complete(request)
        yield resp.text

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        raise UnsupportedCapability(f"provider '{self.name}' does not implement embeddings")

    async def list_models(self) -> list[ModelInfo]:
        raise UnsupportedCapability(f"provider '{self.name}' does not implement model discovery")

    async def discover(self) -> list[ModelInfo]:
        models = await self.list_models()
        for m in models:
            self._model_cache[m.id] = m
        return models

    async def aclose(self) -> None:  # pragma: no cover - adapters with clients override
        return None


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_json_loose(text: str) -> Any:
    """Parse JSON from model text: raw, fenced, or the first balanced object."""
    if not text:
        return None
    for candidate in (text.strip(), *(m.strip() for m in _FENCE.findall(text))):
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            pass
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def raise_for_status(provider: str, status: int, body: str, headers: dict[str, str] | None = None) -> None:
    if status < 400:
        return
    snippet = body[:500]
    if status in (401, 403):
        raise CredentialError(f"{provider}: authentication/authorisation failed (HTTP {status})")
    if status == 429:
        ra = (headers or {}).get("retry-after")
        raise RateLimitError(f"{provider}: rate limited (429): {snippet}",
                             retry_after=float(ra) if ra and ra.replace(".", "").isdigit() else None)
    retryable = status >= 500 or status in (408, 409)
    raise ProviderError(f"{provider}: HTTP {status}: {snippet}", retryable=retryable)
