"""WebAdapter: a target reached through its own web page, driven by Playwright (spec sections 18, 19 and 20).

The adapter does what a person does: it opens the page, types the message into the message box, presses send and reads
what appears. Because the page is the interface, nothing but the visible answer is observable: no tool calls, retrieved
contexts or token usage (the capabilities say so, and the tests that need them are BLOCKED rather than guessed).

A *session* is one browser context with its own cookies and storage, so two sessions of a test never share state. The
target's credential (``web.auth_credential``) signs each new context in. Every request the page makes passes the
evaluator's egress policy.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from agentlab.adapters.base import ADAPTERS, AdapterCapabilities, AdapterContext, AgentAdapter
from agentlab.browser.auth import open_context
from agentlab.browser.environment import browser_status
from agentlab.browser.pool import BrowserPool
from agentlab.browser.session import BrowserSession
from agentlab.core.errors import BrowserError, TargetError
from agentlab.core.models import AgentRequest, AgentResponse, BrowserStep, TargetSpec, WebConfig

CHAT_TIMEOUT_MS = 60_000
PROBE_TIMEOUT_S = 10.0


class WebAdapter(AgentAdapter):
    kind = "web"

    def __init__(self, spec: TargetSpec, ctx: AdapterContext) -> None:
        super().__init__(spec, ctx)
        if spec.web is None:
            raise TargetError("web adapter requires target.web")
        self.web: WebConfig = spec.web
        self.capabilities = AdapterCapabilities(
            conversational=True,
            sessions=True,
            parallel_sessions=True,  # every session is its own browser context
            notes=[
                "driven through the page like a person: only the visible reply is observable (no tool calls, "
                "retrieved contexts or usage), and nothing can be planted in the target"
            ],
        )
        pool = ctx.extras.get("browser_pool")
        self._pool: BrowserPool | None = pool if isinstance(pool, BrowserPool) else None
        self._owns_pool = False
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = asyncio.Lock()

    # ----------------------------------------------------------------------------------------------- lifecycle
    async def open(self) -> None:
        ok, why = browser_status(self.ctx.config)
        if not ok:
            raise BrowserError(why)
        await asyncio.to_thread(self.ctx.egress.check, self.web.url)
        if self._pool is None:
            self._pool = BrowserPool(self.ctx.config)
            self._owns_pool = True

    async def close(self) -> None:
        for sid in list(self._sessions):
            await self.end_session(sid)
        if self._owns_pool and self._pool is not None:
            await self._pool.aclose()
            self._pool = None

    async def probe(self) -> dict[str, Any]:
        """Is the page there? A plain request, so reachability is known before a browser is started."""
        try:
            async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S, follow_redirects=False) as client:
                r = await client.get(self.web.url)
        except Exception as exc:
            return {"reachable": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        ok = r.status_code < 500
        return {
            "reachable": ok,
            "status": r.status_code,
            "error": None if ok else f"the page answered HTTP {r.status_code}",
        }

    # ------------------------------------------------------------------------------------------------ sessions
    async def _session(self, session_id: str) -> BrowserSession:
        async with self._lock:
            if session_id in self._sessions:
                return self._sessions[session_id]
            if self._pool is None:
                raise BrowserError("the web adapter is not open")
            opened = await open_context(self._pool, self.web, self.ctx.credentials)
            page = await opened.guarded.context.new_page()
            session = BrowserSession(
                opened.guarded, page, web=self.web, default_timeout_ms=self.ctx.config.browser.default_timeout_ms
            )
            try:
                if opened.login is not None and self.web.login_url:
                    await session.form_login(self.web.login_url, *opened.login)
                loaded = await session.run_step(BrowserStep(action="goto", value=self.web.url))
            except BaseException:
                await session.close()
                raise
            if not loaded.ok:
                await session.close()
                raise TargetError(f"could not open {self.web.url}: {loaded.detail}")
            self._sessions[session_id] = session
            return session

    async def end_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is not None:
            await session.close()

    # ---------------------------------------------------------------------------------------------------- send
    async def send(self, request: AgentRequest) -> AgentResponse:
        session = await self._session(request.session_id)
        t0 = time.perf_counter()
        outcome = await session.run_step(BrowserStep(action="chat", value=request.input, timeout_ms=CHAT_TIMEOUT_MS))
        return AgentResponse(
            output=outcome.reply or "",
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            error=None if outcome.ok else outcome.detail,
            observed={"browser": True, "url": session.page.url},
        )


ADAPTERS.register("web", WebAdapter, replace=True)
