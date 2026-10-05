"""Protocol-faithful fake LLM vendor servers used to contract-test the provider adapters.

They validate the request shape each real API documents (auth header names, body field
names, structured-output parameters) and record requests so tests can assert on them.
They are NOT the vendors: tests built on them prove adapter/contract behaviour, not that a
live key works.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse


class Recorder:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.fail_with: list[int] = []

    def take_failure(self) -> int | None:
        return self.fail_with.pop(0) if self.fail_with else None


def _text_for(messages: list[dict[str, Any]]) -> str:
    last = [m for m in messages if m.get("role") == "user"][-1]
    c = last.get("content")
    if isinstance(c, list):
        c = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    if "JSON Schema" in str(c):  # prompt-level JSON request (degraded structured output)
        return '```json\n{"verdict": "pass", "score": 0.9}\n```'
    return f"echo: {c}"


def openai_app(rec: Recorder, *, api_key: str | None = "sk-test-openai-key-123456", openrouter: bool = False) -> FastAPI:
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def chat(request: Request, authorization: str | None = Header(default=None)):  # type: ignore[no-untyped-def]
        body = await request.json()
        rec.requests.append({"path": "/v1/chat/completions", "headers": dict(request.headers), "body": body})
        if api_key and authorization != f"Bearer {api_key}":
            return JSONResponse({"error": {"message": "bad key"}}, status_code=401)
        code = rec.take_failure()
        if code:
            return JSONResponse({"error": {"message": "boom"}}, status_code=code, headers={"retry-after": "0"})
        if body.get("stream"):
            def gen():  # type: ignore[no-untyped-def]
                for piece in ["he", "llo"]:
                    yield "data: " + json.dumps({"choices": [{"delta": {"content": piece}}]}) + "\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(gen(), media_type="text/event-stream")
        msg: dict[str, Any] = {"role": "assistant", "content": _text_for(body["messages"])}
        if body.get("tools"):
            fn = body["tools"][0]["function"]["name"]
            msg = {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": fn, "arguments": json.dumps({"q": "x"})}}]}
        rf = body.get("response_format")
        if rf and rf.get("type") in ("json_schema", "json_object"):
            msg["content"] = json.dumps({"verdict": "pass", "score": 0.9})
        usage: dict[str, Any] = {"prompt_tokens": 11, "completion_tokens": 7}
        if openrouter:
            usage["cost"] = 0.00042
        return {"id": "chatcmpl-1", "model": body["model"], "choices": [{"message": msg, "finish_reason": "stop"}],
                "usage": usage}

    @app.get("/v1/models")
    async def models(request: Request):  # type: ignore[no-untyped-def]
        rec.requests.append({"path": "/v1/models", "headers": dict(request.headers)})
        if openrouter:
            return {"data": [
                {"id": "vendor/tool-model", "name": "Tool Model", "context_length": 8000,
                 "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                 "supported_parameters": ["tools", "structured_outputs", "response_format"],
                 "architecture": {"input_modalities": ["text", "image"]}},
                {"id": "vendor/plain-model", "name": "Plain", "context_length": 4000,
                 "pricing": {"prompt": "0", "completion": "0"}, "supported_parameters": ["temperature"],
                 "architecture": {"input_modalities": ["text"]}},
            ]}
        return {"data": [{"id": "gpt-test"}]}

    @app.post("/v1/embeddings")
    async def emb(request: Request):  # type: ignore[no-untyped-def]
        body = await request.json()
        return {"data": [{"embedding": [0.1, 0.2, 0.3]} for _ in body["input"]]}

    return app


def gemini_app(rec: Recorder, *, api_key: str = "AIza-test-gemini-key") -> FastAPI:
    app = FastAPI()

    def _auth(key: str | None) -> None:
        if key != api_key:
            raise HTTPException(401, "bad key")

    @app.post("/v1beta/models/{model}:generateContent")
    async def gen(model: str, request: Request, x_goog_api_key: str | None = Header(default=None)):  # type: ignore[no-untyped-def]
        body = await request.json()
        rec.requests.append({"path": f"/v1beta/models/{model}:generateContent", "headers": dict(request.headers),
                             "body": body})
        _auth(x_goog_api_key)
        code = rec.take_failure()
        if code:
            raise HTTPException(code, "boom")
        parts: list[dict[str, Any]] = [{"text": "echo: " + body["contents"][-1]["parts"][0].get("text", "")}]
        gc = body.get("generationConfig", {})
        if gc.get("responseMimeType") == "application/json":
            parts = [{"text": json.dumps({"verdict": "pass", "score": 0.8})}]
        if body.get("tools"):
            name = body["tools"][0]["functionDeclarations"][0]["name"]
            parts = [{"functionCall": {"name": name, "args": {"q": "x"}}}]
        return {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 5, "thoughtsTokenCount": 2},
                "modelVersion": model, "responseId": "r1"}

    @app.post("/v1beta/models/{model}:streamGenerateContent")
    async def stream(model: str, request: Request, x_goog_api_key: str | None = Header(default=None)):  # type: ignore[no-untyped-def]
        _auth(x_goog_api_key)

        def gen():  # type: ignore[no-untyped-def]
            for piece in ["ab", "cd"]:
                yield "data: " + json.dumps({"candidates": [{"content": {"parts": [{"text": piece}]}}]}) + "\r\n\r\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/v1beta/models")
    async def models(x_goog_api_key: str | None = Header(default=None)):  # type: ignore[no-untyped-def]
        _auth(x_goog_api_key)
        return {"models": [
            {"name": "models/gem-chat", "displayName": "Gem Chat", "inputTokenLimit": 1000,
             "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gem-embed", "supportedGenerationMethods": ["embedContent"]},
        ]}

    @app.post("/v1beta/models/{model}:batchEmbedContents")
    async def emb(model: str, request: Request, x_goog_api_key: str | None = Header(default=None)):  # type: ignore[no-untyped-def]
        _auth(x_goog_api_key)
        body = await request.json()
        return {"embeddings": [{"values": [0.5, 0.5]} for _ in body["requests"]]}

    return app


def ollama_app(rec: Recorder) -> FastAPI:
    app = FastAPI()

    @app.post("/api/chat")
    async def chat(request: Request):  # type: ignore[no-untyped-def]
        body = await request.json()
        rec.requests.append({"path": "/api/chat", "body": body})
        if body.get("stream"):
            def gen():  # type: ignore[no-untyped-def]
                yield json.dumps({"message": {"content": "he"}, "done": False}) + "\n"
                yield json.dumps({"message": {"content": "llo"}, "done": True}) + "\n"
            return StreamingResponse(gen(), media_type="application/x-ndjson")
        msg: dict[str, Any] = {"role": "assistant", "content": "echo: " + body["messages"][-1]["content"]}
        if body.get("format"):
            msg["content"] = json.dumps({"verdict": "pass", "score": 0.7})
        if body.get("tools"):
            msg = {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": body["tools"][0]["function"]["name"], "arguments": {"q": "x"}}}]}
        return {"model": body["model"], "message": msg, "done": True, "done_reason": "stop",
                "prompt_eval_count": 4, "eval_count": 3}

    @app.get("/api/tags")
    async def tags():  # type: ignore[no-untyped-def]
        return {"models": [{"name": "llama-tools:latest", "model": "llama-tools:latest"},
                           {"name": "tiny:latest", "model": "tiny:latest"}]}

    @app.post("/api/show")
    async def show(request: Request):  # type: ignore[no-untyped-def]
        body = await request.json()
        caps = ["completion", "tools"] if body["model"].startswith("llama-tools") else ["completion"]
        return {"capabilities": caps}

    @app.post("/api/embed")
    async def embed(request: Request):  # type: ignore[no-untyped-def]
        body = await request.json()
        return {"embeddings": [[1.0, 0.0] for _ in body["input"]]}

    return app


def anthropic_app(rec: Recorder, *, api_key: str = "sk-ant-test-key-1234567890") -> FastAPI:
    app = FastAPI()

    @app.post("/v1/messages")
    async def messages(request: Request, x_api_key: str | None = Header(default=None),
                       anthropic_version: str | None = Header(default=None)):  # type: ignore[no-untyped-def]
        body = await request.json()
        rec.requests.append({"path": "/v1/messages", "headers": dict(request.headers), "body": body})
        if x_api_key != api_key:
            return JSONResponse({"type": "error", "error": {"type": "authentication_error", "message": "bad"}},
                                status_code=401)
        code = rec.take_failure()
        if code:
            return JSONResponse({"type": "error", "error": {"type": "api_error", "message": "boom"}},
                                status_code=code, headers={"retry-after": "0"})
        content: list[dict[str, Any]] = [{"type": "text", "text": "echo"}]
        if "output_config" in body:
            content = [{"type": "text", "text": json.dumps({"verdict": "pass", "score": 0.95})}]
        if body.get("tools"):
            content = [{"type": "tool_use", "id": "toolu_1", "name": body["tools"][0]["name"], "input": {"q": "x"}}]
        return {"id": "msg_1", "type": "message", "role": "assistant", "model": body["model"], "content": content,
                "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 12, "output_tokens": 6, "cache_read_input_tokens": 3,
                          "cache_creation_input_tokens": 0}}

    @app.get("/v1/models")
    async def models(x_api_key: str | None = Header(default=None)):  # type: ignore[no-untyped-def]
        if x_api_key != api_key:
            return JSONResponse({"type": "error", "error": {"type": "authentication_error", "message": "bad"}},
                                status_code=401)
        return {"data": [{"id": "claude-opus-5-5", "type": "model", "display_name": "Opus 5.5",
                          "created_at": "2026-01-01T00:00:00Z", "max_input_tokens": 1000000}],
                "has_more": False, "first_id": "claude-opus-5-5", "last_id": "claude-opus-5-5"}

    return app
