"""Every request gets an id that is returned in a header, and a request that failed or was slow is logged with it.

The id lets a person who reports "it said 500" name the request, and lets the log that is kept for issue reports be searched
for what happened during it. It is made here: an id sent by a client is ignored, because a client could name a request that
is not its own and so ask for another person's log lines.
"""

from __future__ import annotations

import logging
import re
import secrets
import time
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from agentlab.core import context

log = logging.getLogger("agentlab.api.request")
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
SLOW_MS = 2_000.0


#: The key under which this middleware leaves a mutable record in the scope. Routing hands the application a *copy* of the
#: scope, so what it sets there (the route, the state) is not seen from out here; a dict put in before the call is shared by
#: reference with every copy, so code further in can fill it, and ``record`` below reads it afterwards.
SCOPE_KEY = "agentlab.request"


def note(scope: Scope, **facts: str) -> None:
    """Say something about the request being served, for its log line. Does nothing outside a served request."""
    record = scope.get(SCOPE_KEY)
    if isinstance(record, dict):
        record.update({k: v for k, v in facts.items() if v})


def route_of(scope: Scope, record: dict[str, Any]) -> str:
    """The route as it is declared (``/submissions/{submission_id}``), so a line says what was asked and not for whom."""
    declared = record.get("route")
    if isinstance(declared, str):
        return declared
    return UUID.sub("{id}", str(scope.get("path", "")))[:200]


class RequestLog:
    """Pure ASGI middleware (it must not buffer the event stream)."""

    def __init__(self, app: ASGIApp, *, slow_ms: float = SLOW_MS) -> None:
        self.app = app
        self.slow_ms = slow_ms

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        identifier = secrets.token_hex(8)
        record: dict[str, Any] = {"request_id": identifier}
        scope[SCOPE_KEY] = record
        token = context.request_id.set(identifier)
        started = time.perf_counter()
        status = 0

        async def with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"]
                headers.append((b"x-request-id", identifier.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, with_id)
        except Exception:
            status = status or 500
            raise
        finally:
            took = (time.perf_counter() - started) * 1000
            self._record(scope, record, status, took)
            context.request_id.reset(token)

    def _record(self, scope: Scope, record: dict[str, Any], status: int, took_ms: float) -> None:
        if status == 401:  # an unauthenticated request says nothing about AgentLab, and anyone can send a thousand
            return
        if status < 400 and took_ms < self.slow_ms:
            return
        level = logging.ERROR if status >= 500 else logging.WARNING
        method = str(scope.get("method", ""))
        log.log(
            level,
            "%s %s answered %s in %d ms%s",
            method,
            route_of(scope, record),
            status or "no response",
            int(took_ms),
            " (slow)" if took_ms >= self.slow_ms else "",
            extra={"request_id": record["request_id"], "principal": str(record.get("principal", ""))},
        )
