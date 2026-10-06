"""The local-site engine: a browser agent is given a task on AgentLab's instrumented site and judged by what the site saw.

AgentLab cannot watch a browser agent's own browser, and it should not take the agent's word for what it did. So the
test serves a small web site on a loopback port for the duration of one attempt, hands the agent the task with the
site's address (``{{site_url}}``) and, when the agent has finished, reads the *site's* record of what happened: what is in
the cart, whether anything was bought, whether a tracking URL was fetched, whether the account was deleted, where the
credentials were sent.

The conversation with the agent is the ordinary one (this engine extends :class:`ConversationEngine`); only the
environment around it is new. The site is destroyed after each attempt, so no state leaks between tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
from typing import Any

from agentlab.browser.site import LocalSite
from agentlab.core.enums import EventType
from agentlab.core.errors import UserError
from agentlab.core.models import TestCase
from agentlab.execution.engines import ENGINES, AttemptEnv, AttemptOutcome, ConversationEngine, save_artifact

SITES = ("shop",)
AGENT_BROWSER = "agent's own browser"  # the browser the target used; AgentLab saw only the site


class LocalSiteEngine(ConversationEngine):
    name = "site"

    def handles(self, test: TestCase) -> bool:
        return bool(test.context.get("site"))

    async def run(self, test: TestCase, env: AttemptEnv) -> AttemptOutcome:
        cfg = env.resolver.resolve_obj(dict(test.context["site"]))
        fixture = str(cfg.get("fixture", "shop"))
        if fixture not in SITES:
            raise UserError(f"unknown test site '{fixture}' (available: {', '.join(SITES)})")
        site = LocalSite(marker=cfg.get("marker"), lookalike=bool(cfg.get("lookalike")))
        stack = contextlib.ExitStack()
        url = await asyncio.to_thread(lambda: stack.enter_context(site.serve()))
        try:
            attempt = dataclasses.replace(env, resolver=env.resolver.with_variables(site_url=url))
            out = await super().run(test, attempt)
            snapshot, log = site.snapshot(), site.request_log()
            screenshot = await self._screenshot(env, test, url)
        finally:
            await asyncio.to_thread(stack.close)
        out.state["site"] = snapshot
        env.trace.record(EventType.BROWSER_ACTION, {"observer": "local_site", "url": url, "observed": snapshot})
        artifact = save_artifact(
            env,
            {"test_id": test.id, "site": snapshot, "requests": log},
            kind="site_state",
            name=f"{test.id}-a{env.attempt}-site.json",
            test_id=test.id,
            sensitivity="normal",
        )
        if artifact:
            out.artifacts.append(artifact)
        if screenshot:
            out.artifacts.append(screenshot)
        self._remember(test, env, log, snapshot, screenshot)
        return out

    @staticmethod
    async def _screenshot(env: AttemptEnv, test: TestCase, url: str) -> str | None:
        """A picture of the cart as the site ended up (evidence for the report). Best effort: no browser, no picture."""
        pool = env.extras.get("browser_pool")
        if pool is None:
            return None
        try:
            guarded = await pool.context()
            try:
                page = await guarded.context.new_page()
                await page.goto(f"{url}/cart", wait_until="load", timeout=10_000)
                png = await page.screenshot(full_page=False)
            finally:
                await guarded.context.close()
        except Exception:
            return None
        return save_artifact(
            env,
            png,
            kind="screenshot",
            name=f"{test.id}-a{env.attempt}-site-cart.png",
            test_id=test.id,
            media_type="image/png",
            sensitivity="normal",
        )

    @staticmethod
    def _remember(
        test: TestCase, env: AttemptEnv, log: list[dict[str, Any]], snapshot: dict[str, Any], screenshot: str | None
    ) -> None:
        """The requests the site received, as the browsing history the report's evidence section shows."""
        store = env.extras.get("store")
        if store is None:
            return
        actions = [
            {
                "action": entry["method"],
                "target": entry["path"],
                "ok": int(entry["status"]) < 400,
                "detail": f"HTTP {entry['status']} (seen by the test site)",
                "t_ms": entry["t_ms"],
                "screenshot": None,
            }
            for entry in log
        ]
        with contextlib.suppress(Exception):  # evidence bookkeeping must not fail the attempt
            store.save_browser_session(
                env.run_id,
                test.id,
                AGENT_BROWSER,
                None,
                None,
                [screenshot] if screenshot else [],
                actions,
                {"observer": "local_site", "attempt": env.attempt, "site": snapshot},
            )


ENGINES.register("site", LocalSiteEngine, replace=True)
