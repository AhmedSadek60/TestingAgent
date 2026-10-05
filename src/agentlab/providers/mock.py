"""Deterministic MockProvider (spec section 45).

Lets the whole platform run without API keys. Responses come from ordered *rules*
(regex on the last user message, or on the rubric metric for judge calls) or from a
FIFO *script*. When nothing matches, structured requests receive a schema-shaped
placeholder that is explicitly flagged ``uncertain`` so a mock judge can never
produce a fabricated passing verdict.
"""

from __future__ import annotations

import json
import re
from collections import deque
from collections.abc import Callable
from typing import Any

from agentlab.core.errors import ProviderError
from agentlab.providers.base import (
    Capability,
    CompletionRequest,
    CompletionResponse,
    LLMProvider,
    LLMToolCall,
    ModelInfo,
    TokenUsage,
)

Responder = Callable[[CompletionRequest], "str | dict[str, Any] | list[Any] | CompletionResponse"]


def default_instance(schema: dict[str, Any], overrides: dict[str, Any] | None = None) -> Any:
    """Smallest value satisfying ``schema`` (used for placeholder structured output)."""
    overrides = overrides or {}
    t = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    if t == "object" or "properties" in schema:
        out = {}
        for k, sub in (schema.get("properties") or {}).items():
            out[k] = overrides[k] if k in overrides else default_instance(sub, overrides)
        return out
    if t == "array":
        return []
    if t == "string":
        return "mock"
    if t == "integer":
        return 0
    if t == "number":
        return 0.0
    if t == "boolean":
        return False
    return None


class MockProvider(LLMProvider):
    type_name = "mock"
    default_capabilities = frozenset(
        {
            Capability.CHAT,
            Capability.STREAMING,
            Capability.STRUCTURED_OUTPUT,
            Capability.JSON_SCHEMA,
            Capability.TOOL_CALLING,
            Capability.EMBEDDINGS,
            Capability.MODEL_DISCOVERY,
            Capability.MULTIMODAL,
        }
    )

    def __init__(self, config, credentials=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(config, credentials)
        self.calls: list[CompletionRequest] = []
        self._script: deque[Any] = deque()
        self._rules: list[tuple[re.Pattern[str], Any]] = []
        self.fail_next: list[Exception] = []
        for rule in config.options.get("rules", []):
            self.when(rule["match"], rule["respond"])
        for item in config.options.get("script", []):
            self._script.append(item)

    # ---- scripting API ------------------------------------------------------
    def when(self, pattern: str, response: Any) -> MockProvider:
        self._rules.append((re.compile(pattern, re.S | re.I), response))
        return self

    def enqueue(self, *responses: Any) -> MockProvider:
        self._script.extend(responses)
        return self

    # ---- provider implementation ------------------------------------------------
    @staticmethod
    def _tokens(text: str) -> int:
        return max(1, len(text) // 4)

    async def _complete(self, request: CompletionRequest, model: str) -> CompletionResponse:
        self.calls.append(request)
        if self.fail_next:
            raise self.fail_next.pop(0)
        prompt = "\n".join(m.text() for m in request.messages)
        last_user = next((m.text() for m in reversed(request.messages) if m.role == "user"), "")
        chosen: Any = None
        if self._script:
            chosen = self._script.popleft()
        else:
            for pat, resp in self._rules:
                if pat.search(prompt) or pat.search(last_user):
                    chosen = resp
                    break
        if callable(chosen):
            chosen = chosen(request)
        if isinstance(chosen, CompletionResponse):
            return chosen
        calls: list[LLMToolCall] = []
        parsed: Any = None
        if chosen is None:
            if request.json_schema is not None:
                parsed = default_instance(request.json_schema, {"uncertain": True, "confidence": 0.0})
                text = json.dumps(parsed)
            elif request.tools and request.tool_choice == "required":
                t = request.tools[0]
                calls = [LLMToolCall(id="mock_1", name=t.name, arguments=default_instance(t.parameters) or {})]
                text = ""
            else:
                text = f"[mock] {last_user[:80]}"
        elif isinstance(chosen, str):
            text = chosen
        elif isinstance(chosen, dict) and "tool_calls" in chosen:
            calls = [
                LLMToolCall(id=f"mock_{i}", name=c["name"], arguments=c.get("arguments", {}))
                for i, c in enumerate(chosen["tool_calls"])
            ]
            text = chosen.get("text", "")
        elif isinstance(chosen, dict | list):
            parsed = chosen
            text = json.dumps(chosen)
        else:
            raise ProviderError(f"unsupported mock response type {type(chosen)}")
        return CompletionResponse(
            text=text,
            tool_calls=calls,
            parsed=parsed,
            provider=self.name,
            model=model,
            finish_reason="stop",
            usage=TokenUsage(input_tokens=self._tokens(prompt), output_tokens=self._tokens(text)),
            cost_usd=0.0,
        )

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        from agentlab.storage.vectors import HashingEmbedder

        return HashingEmbedder().embed(texts)

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                id=self.config.model or "mock-model", provider=self.name, capabilities=sorted(self.default_capabilities)
            )
        ]
