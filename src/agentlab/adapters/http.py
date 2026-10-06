"""AgentApiAdapter: black-box REST / SSE / GraphQL agent endpoints (spec section 19).

The request body is built from a template; the response is mapped to AgentResponse via
JSONPath expressions (with sensible auto-detection for OpenAI-style and common shapes).
All outbound requests pass the evaluator egress policy and redirects are re-checked.

A call is repeated (up to ``limits.max_retries``) only when the connection could not even be made, so nothing reached the
agent and repeating the call cannot repeat an action it took. A timeout while waiting for the answer is never retried.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import ssl
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

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
DEFAULT_PORTS = {"http": 80, "https": 443}
#: What a request may still carry once a redirect has taken it to an origin its credential was not released for.
HEADERS_SAFE_ELSEWHERE = {"content-type", "accept"}
#: Failures that happen before a request leaves this machine: the connection was never made, or no connection was free.
NEVER_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
T = TypeVar("T")


class Retries:
    """How many times one ``send`` repeated a call that never reached the agent (one per ``send``, so sessions that run in
    parallel never share a count)."""

    def __init__(self, limit: int) -> None:
        self.limit = max(0, limit)
        self.count = 0

    async def run(self, call: Callable[[], Awaitable[T]]) -> T:
        """Run ``call``; if the connection could not be made, wait a little and try again, as often as the limit allows."""
        while True:
            try:
                return await call()
            except NEVER_SENT as exc:
                if self.count >= self.limit or _is_certificate_problem(exc):
                    raise
                self.count += 1
                await asyncio.sleep(min(2.0, 0.25 * 2 ** (self.count - 1)))


def _is_certificate_problem(exc: BaseException | None) -> bool:
    """A bad certificate does not get better by asking again."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, ssl.SSLError):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


def origin_of(url: str | httpx.URL) -> tuple[str, str, int | None]:
    """(scheme, host, port): what "the same place" means for a credential, as for a browser's same-origin rule."""
    u = httpx.URL(str(url))
    return u.scheme, u.host.lower(), u.port or DEFAULT_PORTS.get(u.scheme)


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
    inner = raw.get("function")
    fn: dict[str, Any] = inner if isinstance(inner, dict) else raw
    name = fn.get("name") or fn.get("tool") or fn.get("tool_name")
    if not name:
        return None
    args = fn.get("arguments", fn.get("args", fn.get("input", {})))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"_raw": args}
    return ToolCall(
        name=str(name),
        arguments=args if isinstance(args, dict) else {"value": args},
        result=raw.get("result", raw.get("output")),
        status=str(raw.get("status", "success")),
        id=raw.get("id"),
    )


def _norm_context(raw: Any) -> RetrievedContext | None:
    if isinstance(raw, str):
        return RetrievedContext(source="unknown", content=raw)
    if isinstance(raw, dict):
        content = raw.get("content") or raw.get("text") or raw.get("chunk") or ""
        source = raw.get("source") or raw.get("document") or raw.get("doc") or raw.get("title") or "unknown"
        return RetrievedContext(
            source=str(source),
            content=str(content),
            page=raw.get("page"),
            section=raw.get("section"),
            score=raw.get("score"),
        )
    return None


class AgentApiAdapter(AgentAdapter):
    kind = "api"

    def __init__(self, spec: TargetSpec, ctx: AdapterContext) -> None:
        super().__init__(spec, ctx)
        if spec.api is None:
            raise TargetError("api adapter requires target.api")
        self.cfg: ApiConfig = spec.api
        if self.cfg.protocol == "websocket":
            raise UnsupportedCapability(
                "WebSocket agent endpoints are not supported in this build; use REST, SSE or GraphQL"
            )
        takes_attachments = "{{attachments}}" in json.dumps(self.cfg.request_template)
        self.capabilities = AdapterCapabilities(
            streaming=self.cfg.protocol == "sse",
            reports_usage=True,
            reports_tool_calls=bool(self.cfg.response.tool_calls),
            reports_contexts=bool(self.cfg.response.contexts),
            reports_citations=bool(self.cfg.response.citations),
            reports_events=bool(self.cfg.response.events),
            # attachments can be sent when the owner mapped them into the request body ...
            attachments=takes_attachments,
            # ... and images are understood when the owner also says the agent is multimodal
            multimodal=takes_attachments and "multimodal" in spec.declared_types,
            knowledge_injection=bool(self.cfg.knowledge_endpoint),
            # a request can be sent without credentials when the target is configured with a credential
            omit_auth=bool(self.cfg.auth_credential),
        )
        self._client: httpx.AsyncClient | None = None
        #: AgentLab's session id -> the conversation id the agent assigned (``response.session_id``)
        self._assigned: dict[str, str] = {}

    def _session_for(self, request: AgentRequest) -> str:
        """The id the agent knows this conversation by: the one it returned, or AgentLab's own until it returns one."""
        return self._assigned.get(request.session_id, request.session_id)

    async def open(self) -> None:
        if not self.cfg.url:
            raise TargetError(
                "the api has no address to send messages to (api.url is empty and none could be taken from the OpenAPI "
                "document); set api.url or pass --api-url"
            )
        self.ctx.egress.check(self.cfg.url)
        self._client = httpx.AsyncClient(timeout=self.cfg.timeout_seconds, follow_redirects=False)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()

    def _headers(
        self, session_id: str, *, omit_auth: bool = False, credential: str | None = None, url: str | None = None
    ) -> dict[str, str]:
        """Request headers for ``url`` (default: the agent's own address). ``credential`` is the profile the *test*
        asked for (``required_credentials``); it replaces the target's default one, so a test written for another
        identity is sent as that identity. The credential is released only for a URL inside its scope."""
        headers = {"Content-Type": "application/json", "Accept": "application/json", **self.cfg.headers}
        if self.cfg.protocol == "sse":
            headers["Accept"] = "text/event-stream"
        name = credential or self.cfg.auth_credential
        if name and not omit_auth:
            if self.ctx.credentials is None:
                raise TargetError("credential requested but no CredentialManager is configured")
            headers.update(self.ctx.credentials.auth_headers(name, url or self.cfg.url))
        if self.cfg.session_header:
            headers[self.cfg.session_header] = session_id
        return headers

    def _body(self, request: AgentRequest) -> Any:
        values = {
            "input": request.input,
            "session_id": self._session_for(request),
            "attachments": [a.model_dump() for a in request.attachments],
        }
        if self.cfg.protocol == "graphql":
            return {
                "query": self.cfg.graphql_query or "",
                "variables": render_template(self.cfg.request_template, values),
            }
        return render_template(self.cfg.request_template, values)

    async def probe(self) -> dict[str, Any]:
        assert self._client is not None
        try:
            r = await self._client.request(
                "OPTIONS" if self.cfg.method == "POST" else "HEAD",
                self.cfg.url,
                headers={k: v for k, v in self.cfg.headers.items()},
            )
            return {"reachable": True, "status": r.status_code, "requires_auth": r.status_code in (401, 403)}
        except httpx.HTTPError as exc:
            return {"reachable": False, "error": str(exc)}

    async def send(self, request: AgentRequest) -> AgentResponse:
        if self._client is None:
            await self.open()
        assert self._client is not None
        omit_auth = bool(request.metadata.get("omit_auth"))
        headers = self._headers(self._session_for(request), omit_auth=omit_auth, credential=request.credential)
        body = self._body(request)
        url = self.cfg.url
        t0 = time.perf_counter()
        retries = Retries(self.ctx.config.limits.max_retries)
        try:
            await self._plant_knowledge(request, headers)
            if self.cfg.protocol == "sse":
                streamed = await self._send_sse(url, headers, body, t0, retries)
                streamed.retries = retries.count
                return streamed
            r = await self._request(url, headers, body, retries)
        except httpx.TimeoutException as exc:
            return AgentResponse(
                error=f"timeout after {self.cfg.timeout_seconds}s",
                status_code=None,
                latency_ms=(time.perf_counter() - t0) * 1000,
                raw=str(exc)[:200],
                retries=retries.count,
            )
        except httpx.HTTPError as exc:
            return AgentResponse(
                error=f"connection error: {type(exc).__name__}",
                latency_ms=(time.perf_counter() - t0) * 1000,
                retries=retries.count,
            )
        latency = (time.perf_counter() - t0) * 1000
        response = self._to_response(r, latency, request.session_id)
        response.retries = retries.count
        return response

    async def _plant_knowledge(self, request: AgentRequest, headers: dict[str, str]) -> None:
        """Add the documents a test wants the agent to read, through the owner's test hook (``knowledge_endpoint``)."""
        docs = request.metadata.get("inject_knowledge") or {}
        if not docs:
            return
        if not self.cfg.knowledge_endpoint:
            raise UnsupportedCapability("this target has no knowledge_endpoint, so documents cannot be planted")
        assert self._client is not None
        if origin_of(self.cfg.knowledge_endpoint) != origin_of(self.cfg.url):
            # a hook on another origin gets headers built for *its* address, so a credential scoped to the agent is
            # refused there (a BLOCKED test) instead of being sent to a host it was never released for
            headers = self._headers(
                self._session_for(request),
                omit_auth=bool(request.metadata.get("omit_auth")),
                credential=request.credential,
                url=self.cfg.knowledge_endpoint,
            )
        for name, text in docs.items():
            self.ctx.egress.check(self.cfg.knowledge_endpoint)
            r = await self._client.post(
                self.cfg.knowledge_endpoint,
                headers=headers,
                json={"session_id": self._session_for(request), "name": name, "text": str(text)},
            )
            if r.status_code >= 400:
                raise TargetError(f"the knowledge endpoint refused document '{name}' (HTTP {r.status_code})")

    async def _hop(self, url: str, headers: dict[str, str], body: Any) -> httpx.Response:
        """One request to ``url``, without following a redirect."""
        assert self._client is not None
        if self.cfg.method == "GET":
            return await self._client.get(url, headers=headers, params=body if isinstance(body, dict) else None)
        return await self._client.request(self.cfg.method, url, headers=headers, json=body)

    async def _request(
        self, url: str, headers: dict[str, str], body: Any, retries: Retries | None = None
    ) -> httpx.Response:
        """Send the request and follow at most three redirects. Every hop passes the egress policy, and a hop to an
        origin other than the one the credential was released for is made without the credential (or any other header
        the owner configured): the target, or an open redirect on it, chooses where ``Location`` points."""
        assert self._client is not None
        for _ in range(4):
            self.ctx.egress.check(url)
            hop = functools.partial(self._hop, url, headers, body)
            r = await (retries.run(hop) if retries is not None else hop())
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                nxt = str(httpx.URL(url).join(r.headers["location"]))  # re-validated by egress at loop top
                if origin_of(nxt) != origin_of(url):
                    headers = {k: v for k, v in headers.items() if k.lower() in HEADERS_SAFE_ELSEWHERE}
                url = nxt
                continue
            return r
        raise PolicyBlocked("too many redirects")

    def _to_response(self, r: httpx.Response, latency: float, session_id: str | None = None) -> AgentResponse:
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
            if "json" in r.headers.get("content-type", "").lower():
                # A plain-text agent is fine; one that says "JSON" and sends a broken body is a target defect.
                resp.error = "the response is declared as JSON but is not valid JSON"
                resp.raw = get_redactor().redact_text(text[:500])[0]
                return resp
            resp.output = text
            return resp
        self._map(data, resp)
        if session_id is not None and self.cfg.response.session_id:
            assigned = jp_first(self.cfg.response.session_id, data)
            if assigned not in (None, ""):
                self._assigned[session_id] = str(assigned)
        resp.raw = data if len(text) < 20_000 else None
        if self.cfg.protocol == "graphql" and not resp.output:
            resp.error = self._graphql_error(data)
        return resp

    @staticmethod
    def _graphql_error(data: Any) -> str | None:
        """GraphQL reports a failed query with status 200 and an ``errors`` list; an answer that is missing because of one
        is the target's error, not an empty reply. (An answer that arrived alongside an error is kept as it is.)"""
        errors = data.get("errors") if isinstance(data, dict) else None
        if not isinstance(errors, list) or not errors:
            return None
        messages = [str(e.get("message", e) if isinstance(e, dict) else e)[:160] for e in errors[:3]]
        return "GraphQL error: " + get_redactor().redact_text("; ".join(messages))[0]

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
            resp.events = [
                AgentEvent(type=str(e.get("type", "event")), data={k: v for k, v in e.items() if k != "type"})
                for e in evs
                if isinstance(e, dict)
            ]
        usage = jp_first(m.usage, data) if m.usage else (data.get("usage") if isinstance(data, dict) else None)
        if isinstance(usage, dict):
            resp.usage = Usage(
                input_tokens=int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0),
                output_tokens=int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0),
                llm_calls=int(usage.get("llm_calls", 1) or 1),
                cost_usd=float(usage.get("cost_usd", usage.get("cost", 0)) or 0),
            )

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

    async def _send_sse(
        self, url: str, headers: dict[str, str], body: Any, t0: float, retries: Retries | None = None
    ) -> AgentResponse:
        assert self._client is not None
        self.ctx.egress.check(url)
        resp = AgentResponse()
        pieces: list[str] = []
        first: float | None = None
        client = self._client
        async with contextlib.AsyncExitStack() as stack:

            async def open_stream() -> httpx.Response:
                """Open the stream: the one step that can fail before the agent was reached."""
                return await stack.enter_async_context(client.stream(self.cfg.method, url, headers=headers, json=body))

            r = await (retries.run(open_stream) if retries is not None else open_stream())
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
            resp.usage = Usage(
                input_tokens=int(obj.get("input_tokens", 0)),
                output_tokens=int(obj.get("output_tokens", 0)),
                llm_calls=int(obj.get("llm_calls", 1)),
                cost_usd=float(obj.get("cost_usd", 0)),
            )
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
