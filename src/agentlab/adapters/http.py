"""AgentApiAdapter: black-box REST / SSE / GraphQL agent endpoints (spec section 19).

The request body is built from a template; the response is mapped to AgentResponse via
JSONPath expressions (with sensible auto-detection for OpenAI-style and common shapes).
All outbound requests pass the evaluator egress policy and redirects are re-checked.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
from jsonpath_ng.ext import parse as jp_parse

from agentlab.adapters.base import ADAPTERS, AdapterCapabilities, AdapterContext, AgentAdapter
from agentlab.core.errors import PolicyBlocked, TargetError, UnsupportedCapability
from agentlab.core.models import (
    AgentEvent,
    AgentRequest,
    AgentResponse,
    ApiConfig,
    RetrievedContext,
    TargetSpec,
    ToolCall,
    Usage,
)
from agentlab.security.redactor import get_redactor

MAX_BODY = 5_000_000
OUTPUT_KEYS = ("output", "response", "answer", "reply", "text", "message", "content", "result")


def render_template(node: Any, values: dict[str, Any]) -> Any:
    if isinstance(node, str):
        for k, v in values.items():
            token = "{{" + k + "}}"
            if node == token:
                return v
            node = node.replace(token, str(v) if v is not None else "")
        return node
    if isinstance(node, dict):
        return {k: render_template(v, values) for k, v in node.items()}
    if isinstance(node, list):
        return [render_template(v, values) for v in node]
    return node


def jp_first(expr: str | None, data: Any) -> Any:
    if not expr:
        return None
    try:
        found = jp_parse(expr).find(data)
    except Exception as exc:
        raise TargetError(f"invalid JSONPath '{expr}': {exc}") from exc
    return found[0].value if found else None


def jp_all(expr: str | None, data: Any) -> list[Any]:
    if not expr:
        return []
    return [m.value for m in jp_parse(expr).find(data)]


def _norm_tool_call(raw: Any) -> ToolCall | None:
    if not isinstance(raw, dict):
        return None
    fn = raw.get("function") if isinstance(raw.get("function"), dict) else raw
    name = fn.get("name") or fn.get("tool") or fn.get("tool_name")
    if not name:
        return None
    args = fn.get("arguments", fn.get("args", fn.get("input", {})))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"_raw": args}
    return ToolCall(name=str(name), arguments=args if isinstance(args, dict) else {"value": args},
                    result=raw.get("result", raw.get("output")), status=str(raw.get("status", "success")),
                    id=raw.get("id"))


def _norm_context(raw: Any) -> RetrievedContext | None:
    if isinstance(raw, str):
        return RetrievedContext(source="unknown", content=raw)
    if isinstance(raw, dict):
        content = raw.get("content") or raw.get("text") or raw.get("chunk") or ""
        source = raw.get("source") or raw.get("document") or raw.get("doc") or raw.get("title") or "unknown"
        return RetrievedContext(source=str(source), content=str(content), page=raw.get("page"),
                                section=raw.get("section"), score=raw.get("score"))
    return None


class AgentApiAdapter(AgentAdapter):
    kind = "api"

    def __init__(self, spec: TargetSpec, ctx: AdapterContext) -> None:
        super().__init__(spec, ctx)
        if spec.api is None:
            raise TargetError("api adapter requires target.api")
        self.cfg: ApiConfig = spec.api
        if self.cfg.protocol == "websocket":
            raise UnsupportedCapability("WebSocket agent endpoints are not supported in this build; "
                                        "use REST, SSE or GraphQL")
        self.capabilities = AdapterCapabilities(streaming=self.cfg.protocol == "sse", reports_usage=True,
                                                reports_tool_calls=bool(self.cfg.response.tool_calls),
                                                reports_contexts=bool(self.cfg.response.contexts),
                                                reports_events=bool(self.cfg.response.events))
        self._client: httpx.AsyncClient | None = None

    async def open(self) -> None:
        self.ctx.egress.check(self.cfg.url)
        self._client = httpx.AsyncClient(timeout=self.cfg.timeout_seconds, follow_redirects=False)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()

    def _headers(self, session_id: str) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json", **self.cfg.headers}
        if self.cfg.protocol == "sse":
            headers["Accept"] = "text/event-stream"
        if self.cfg.auth_credential:
            if self.ctx.credentials is None:
                raise TargetError("credential requested but no CredentialManager is configured")
            headers.update(self.ctx.credentials.auth_headers(self.cfg.auth_credential, self.cfg.url))
        if self.cfg.session_header:
            headers[self.cfg.session_header] = session_id
        return headers

    def _body(self, request: AgentRequest) -> Any:
        values = {"input": request.input, "session_id": request.session_id,
                  "attachments": [a.model_dump() for a in request.attachments]}
        if self.cfg.protocol == "graphql":
            return {"query": self.cfg.graphql_query or "", "variables": render_template(self.cfg.request_template, values)}
        return render_template(self.cfg.request_template, values)

    async def probe(self) -> dict[str, Any]:
        assert self._client is not None
        try:
            r = await self._client.request("OPTIONS" if self.cfg.method == "POST" else "HEAD", self.cfg.url,
                                           headers={k: v for k, v in self.cfg.headers.items()})
            return {"reachable": True, "status": r.status_code, "requires_auth": r.status_code in (401, 403)}
        except httpx.HTTPError as exc:
            return {"reachable": False, "error": str(exc)}

    async def send(self, request: AgentRequest) -> AgentResponse:
        if self._client is None:
            await self.open()
        assert self._client is not None
        headers = self._headers(request.session_id)
        body = self._body(request)
        url = self.cfg.url
        t0 = time.perf_counter()
        try:
            if self.cfg.protocol == "sse":
                return await self._send_sse(url, headers, body, t0)
            r = await self._request(url, headers, body)
        except httpx.TimeoutException as exc:
            return AgentResponse(error=f"timeout after {self.cfg.timeout_seconds}s", status_code=None,
                                 latency_ms=(time.perf_counter() - t0) * 1000, raw=str(exc)[:200])
        except httpx.HTTPError as exc:
            return AgentResponse(error=f"connection error: {type(exc).__name__}",
                                 latency_ms=(time.perf_counter() - t0) * 1000)
        latency = (time.perf_counter() - t0) * 1000
        return self._to_response(r, latency)

    async def _request(self, url: str, headers: dict[str, str], body: Any) -> httpx.Response:
        assert self._client is not None
        for _ in range(4):
            self.ctx.egress.check(url)
            if self.cfg.method == "GET":
                r = await self._client.get(url, headers=headers, params=body if isinstance(body, dict) else None)
            else:
                r = await self._client.request(self.cfg.method, url, headers=headers, json=body)
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                url = str(httpx.URL(url).join(r.headers["location"]))  # re-validated by egress at loop top
                continue
            return r
        raise PolicyBlocked("too many redirects")

    def _to_response(self, r: httpx.Response, latency: float) -> AgentResponse:
        if len(r.content) > MAX_BODY:
            return AgentResponse(error="response exceeded size limit", status_code=r.status_code, latency_ms=latency)
        text = r.text
        resp = AgentResponse(status_code=r.status_code, latency_ms=latency)
        if r.status_code >= 400:
            resp.error = f"HTTP {r.status_code}"
            resp.raw = get_redactor().redact_text(text[:500])[0]
            return resp
        try:
            data = r.json()
        except ValueError:
            resp.output = text
            return resp
        self._map(data, resp)
        resp.raw = data if len(text) < 20_000 else None
        return resp

    def _map(self, data: Any, resp: AgentResponse) -> None:
        m = self.cfg.response
        out = jp_first(m.output, data) if m.output else self._auto_output(data)
        resp.output = out if isinstance(out, str) else ("" if out is None else json.dumps(out))
        calls = jp_all(m.tool_calls, data) if m.tool_calls else self._auto_tool_calls(data)
        flat: list[Any] = []
        for c in calls:
            flat.extend(c if isinstance(c, list) else [c])
        resp.tool_calls = [tc for tc in (_norm_tool_call(c) for c in flat) if tc]
        if m.contexts:
            ctxs: list[Any] = []
            for c in jp_all(m.contexts, data):
                ctxs.extend(c if isinstance(c, list) else [c])
            resp.contexts = [x for x in (_norm_context(c) for c in ctxs) if x]
        if m.citations:
            cites: list[Any] = []
            for c in jp_all(m.citations, data):
                cites.extend(c if isinstance(c, list) else [c])
            resp.citations = [str(c.get("source", c)) if isinstance(c, dict) else str(c) for c in cites]
        if m.events:
            evs: list[Any] = []
            for e in jp_all(m.events, data):
                evs.extend(e if isinstance(e, list) else [e])
            resp.events = [AgentEvent(type=str(e.get("type", "event")), data={k: v for k, v in e.items() if k != "type"})
                           for e in evs if isinstance(e, dict)]
        usage = jp_first(m.usage, data) if m.usage else (data.get("usage") if isinstance(data, dict) else None)
        if isinstance(usage, dict):
            resp.usage = Usage(
                input_tokens=int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0),
                output_tokens=int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0),
                llm_calls=int(usage.get("llm_calls", 1) or 1), cost_usd=float(usage.get("cost_usd", usage.get("cost", 0)) or 0))

    @staticmethod
    def _auto_output(data: Any) -> Any:
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            ch = data.get("choices")
            if isinstance(ch, list) and ch and isinstance(ch[0], dict):
                msg = ch[0].get("message") or {}
                return msg.get("content") or ""
            for k in OUTPUT_KEYS:
                if k in data:
                    v = data[k]
                    return v if isinstance(v, str) else (v.get("content") if isinstance(v, dict) else v)
        return None

    @staticmethod
    def _auto_tool_calls(data: Any) -> list[Any]:
        if isinstance(data, dict):
            ch = data.get("choices")
            if isinstance(ch, list) and ch and isinstance(ch[0], dict):
                return (ch[0].get("message") or {}).get("tool_calls") or []
            v = data.get("tool_calls")
            if isinstance(v, list):
                return v
        return []

    async def _send_sse(self, url: str, headers: dict[str, str], body: Any, t0: float) -> AgentResponse:
        assert self._client is not None
        self.ctx.egress.check(url)
        resp = AgentResponse()
        pieces: list[str] = []
        first: float | None = None
        async with self._client.stream(self.cfg.method, url, headers=headers, json=body) as r:
            resp.status_code = r.status_code
            if r.status_code >= 400:
                resp.error = f"HTTP {r.status_code}"
                resp.latency_ms = (time.perf_counter() - t0) * 1000
                return resp
            total = 0
            event_name = ""
            async for line in r.aiter_lines():
                total += len(line)
                if total > MAX_BODY:
                    resp.error = "stream exceeded size limit"
                    break
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                if first is None:
                    first = (time.perf_counter() - t0) * 1000
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    pieces.append(payload)
                    continue
                self._absorb_sse(obj, event_name, pieces, resp)
        resp.output = "".join(pieces)
        resp.first_token_ms = first
        resp.latency_ms = (time.perf_counter() - t0) * 1000
        return resp

    def _absorb_sse(self, obj: Any, event_name: str, pieces: list[str], resp: AgentResponse) -> None:
        if not isinstance(obj, dict):
            return
        kind = obj.get("type") or event_name
        if kind in ("tool_call", "tool_use"):
            tc = _norm_tool_call(obj)
            if tc:
                resp.tool_calls.append(tc)
        elif kind in ("context", "retrieval"):
            c = _norm_context(obj)
            if c:
                resp.contexts.append(c)
        elif kind in ("handoff", "plan_step", "browser_action", "event"):
            resp.events.append(AgentEvent(type=kind, data={k: v for k, v in obj.items() if k != "type"}))
        elif kind == "usage":
            resp.usage = Usage(input_tokens=int(obj.get("input_tokens", 0)), output_tokens=int(obj.get("output_tokens", 0)),
                               llm_calls=int(obj.get("llm_calls", 1)), cost_usd=float(obj.get("cost_usd", 0)))
        else:
            ch = obj.get("choices")
            if isinstance(ch, list) and ch:
                delta = (ch[0].get("delta") or {}).get("content")
                if delta:
                    pieces.append(delta)
            elif isinstance(obj.get("delta"), str):
                pieces.append(obj["delta"])
            elif isinstance(obj.get("text"), str):
                pieces.append(obj["text"])


ADAPTERS.register("api", AgentApiAdapter, replace=True)
