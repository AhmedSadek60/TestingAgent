"""The browser engine's shared resources: one Chromium per run and one isolated context per test attempt (spec section 18).

A context is a clean profile: no cookies, no storage, nothing shared with another test. Contexts are created for one
attempt and destroyed afterwards. Every request a page makes passes the evaluator's egress policy, so a hostile page
(or a hostile redirect) cannot steer the browser to a cloud metadata service.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from agentlab.browser.environment import browser_status, find_chromium
from agentlab.core.config import AgentLabConfig
from agentlab.core.errors import BrowserError, PolicyBlocked
from agentlab.security.egress import EgressPolicy

log = logging.getLogger(__name__)

INSTALL_HINT = "install the browser extra and run `playwright install chromium` (or set browser.executable_path)"
LOCAL_SCHEMES = ("data", "blob", "about", "chrome-error", "devtools")


def launch_options(config: AgentLabConfig) -> dict[str, Any]:
    """How Chromium is started (shared with the fixture agents that drive a browser of their own)."""
    args = ["--disable-dev-shm-usage"]
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        # Chromium refuses to start as root with its own sandbox. AgentLab only opens the target under test and its
        # own local test site, but run AgentLab as an ordinary user (or in the provided container) to keep it.
        args.append("--no-sandbox")
    return {"headless": config.browser.headless, "executable_path": find_chromium(config), "args": args}


@dataclass
class GuardedContext:
    """A Playwright context plus the list of requests the egress policy refused in it."""

    context: Any
    blocked: list[str] = field(default_factory=list)


class BrowserPool:
    """Lazily starts Playwright and Chromium on first use; ``aclose`` stops both. Bound to the event loop that first used it,
    so a new run (a new loop) starts a new one."""

    def __init__(self, config: AgentLabConfig) -> None:
        self.config = config
        self._pw: Any = None
        self._browser: Any = None
        self._lock = asyncio.Lock()

    @property
    def egress(self) -> EgressPolicy:
        sec = self.config.security
        return EgressPolicy(block_metadata=sec.block_metadata_endpoints, allow_private=sec.allow_private_networks)

    async def browser(self) -> Any:
        async with self._lock:
            if self._browser is not None and self._browser.is_connected():
                return self._browser
            ok, why = browser_status(self.config)
            if not ok:
                raise BrowserError(f"{why}; {INSTALL_HINT}")
            from playwright.async_api import async_playwright

            try:
                self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(**launch_options(self.config))
            except Exception as exc:
                await self._stop()
                raise BrowserError(f"could not start Chromium: {type(exc).__name__}: {str(exc)[:200]}") from exc
            return self._browser

    async def context(
        self,
        *,
        record_video_dir: str | None = None,
        inject_headers: dict[str, dict[str, str]] | None = None,
        **options: Any,
    ) -> GuardedContext:
        """A new isolated context whose requests are all checked against the egress policy.

        ``inject_headers`` maps an origin to headers added to the requests *to that origin only* (a bearer token for the
        target's own site), so a page that embeds a third-party resource never sends the credential there."""
        browser = await self.browser()
        opts: dict[str, Any] = {"viewport": {"width": 1280, "height": 800}, "accept_downloads": True, **options}
        if record_video_dir:
            opts["record_video_dir"] = record_video_dir
        ctx = await browser.new_context(**opts)
        ctx.set_default_timeout(self.config.browser.default_timeout_ms)
        return GuardedContext(ctx, await guard_requests(ctx, self.egress, inject_headers))

    async def aclose(self) -> None:
        async with self._lock:
            await self._stop()
        self._lock = asyncio.Lock()  # the next run may use a new event loop

    async def _stop(self) -> None:
        browser, pw, self._browser, self._pw = self._browser, self._pw, None, None
        try:
            if browser is not None:
                await browser.close()
        except Exception:  # noqa: S110 - best-effort cleanup
            pass
        try:
            if pw is not None:
                await pw.stop()
        except Exception:  # noqa: S110
            pass


def origin_of(url: str) -> str:
    parts = urlparse(url)
    return f"{parts.scheme}://{parts.netloc}"


async def guard_requests(
    context: Any, egress: EgressPolicy, inject_headers: dict[str, dict[str, str]] | None = None
) -> list[str]:
    """Abort every request the egress policy refuses and remember what was refused (the list is returned live).

    A WebSocket a page opens is a request too: it is closed when the policy refuses its address and passed through when it
    does not."""
    blocked: list[str] = []
    verdicts: dict[str, str | None] = {}

    async def verdict(url: str) -> str | None:
        host = urlparse(url).netloc
        if host not in verdicts:
            try:
                await asyncio.get_running_loop().run_in_executor(None, egress.check, url)
                verdicts[host] = None
            except PolicyBlocked as exc:
                verdicts[host] = str(exc)
        return verdicts[host]

    async def handler(route: Any) -> None:
        url = route.request.url
        if urlparse(url).scheme in LOCAL_SCHEMES:
            await route.continue_()
            return
        refused = await verdict(url)
        if refused is not None:
            blocked.append(f"{url}: {refused}")
            await route.abort("blockedbyclient")
            return
        extra = (inject_headers or {}).get(origin_of(url))
        if extra:
            await route.continue_(headers={**route.request.headers, **extra})
        else:
            await route.continue_()

    async def web_socket(ws: Any) -> None:
        refused = await verdict(ws.url)
        if refused is not None:
            blocked.append(f"{ws.url}: {refused}")
            await ws.close(code=1008, reason="blocked by the egress policy")
            return
        ws.connect_to_server()

    await context.route("**/*", handler)
    await context.route_web_socket("**/*", web_socket)
    return blocked
