"""The browser engine: drive a web target's own interface the way a person does, with Playwright (spec section 18).

One attempt gets a **new, empty browser context** (no cookies, no storage, nothing from another test), signed in as the
test user when the target names a credential. The test's declarative steps run in order; every action is recorded with its
outcome and a screenshot can be stored at any point. What the page did on its own (script errors, ``alert()`` dialogs,
failed requests, requests AgentLab's egress policy refused) is collected as ``browser.*`` state for the assertions to read.
This engine records; it never decides whether anything was good or bad.

Playwright's trace (a zip with DOM snapshots and screenshots) and, when enabled, a video are stored as evidence.
"""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
from pathlib import Path
from typing import Any

from agentlab.adapters.web import WebAdapter
from agentlab.browser.auth import open_context
from agentlab.browser.pool import BrowserPool
from agentlab.browser.session import BrowserSession, StepOutcome
from agentlab.core.config import AgentLabConfig
from agentlab.core.enums import EventType
from agentlab.core.errors import BrowserError, UserError
from agentlab.core.models import AgentEvent, AgentResponse, TestCase, WebConfig
from agentlab.execution.engines import ENGINES, AttemptEnv, AttemptOutcome, ExecutionEngine, save_artifact
from agentlab.execution.limits import LimitReached, LimitTracker

BROWSER_NAME = "chromium"


def web_config(env: AttemptEnv) -> WebConfig | None:
    if isinstance(env.adapter, WebAdapter):
        return env.adapter.web
    runtime = env.extras.get("runtime")
    return runtime.spec.web if runtime is not None else None


class BrowserExecutionEngine(ExecutionEngine):
    name = "browser"

    def handles(self, test: TestCase) -> bool:
        return bool(test.browser_steps)

    async def run(self, test: TestCase, env: AttemptEnv) -> AttemptOutcome:
        pool: BrowserPool | None = env.extras.get("browser_pool")
        config: AgentLabConfig | None = env.extras.get("config")
        if pool is None or config is None:
            raise BrowserError("no browser is available to this run")
        web = web_config(env)
        out = AttemptOutcome(sessions=["browser"])
        fixtures_ref = test.context.get("fixtures_dir") or env.extras.get("fixtures_dir")
        with tempfile.TemporaryDirectory(prefix="agentlab-browser-") as raw:
            tmp = Path(raw)
            video_dir = str(tmp / "video") if config.browser.record_video else None
            opened = await open_context(
                pool,
                web,
                env.extras.get("credentials"),
                record_video_dir=video_dir,
                credential=test.required_credentials[0] if test.required_credentials else None,
            )
            context = opened.guarded.context
            tracing = config.browser.record_trace
            if tracing:
                await context.tracing.start(screenshots=True, snapshots=True)
            page = await context.new_page()
            session = BrowserSession(
                opened.guarded,
                page,
                web=web,
                resolver=env.resolver,
                fixtures_dir=Path(fixtures_ref) if fixtures_ref else None,
                save=self._saver(env, test, out),
                default_timeout_ms=config.browser.default_timeout_ms,
            )
            trace_artifact: str | None = None
            video_artifact: str | None = None
            try:
                if opened.login is not None:
                    if web is None or not web.login_url:  # open_context only returns a login for a web target with one
                        raise UserError("the credential asks for a form login but the target has no web.login_url")
                    await session.form_login(web.login_url, *opened.login)
                await self._steps(test, env, session, out)
                await self._finish(test, env, session, out)
            finally:
                if tracing:
                    trace_artifact = await self._stop_tracing(test, env, context, tmp)
                await session.close()
                if video_dir:
                    video_artifact = self._video(test, env, tmp / "video")
                # the Playwright trace and the video are evidence of the attempt, so a finding can point at them
                out.artifacts.extend(a for a in (trace_artifact, video_artifact) if a)
            self._remember(test, env, session, trace_artifact, video_artifact)
        out.state["browser"] = session.state()
        return out

    # ------------------------------------------------------------------------------------------------- the steps
    async def _steps(self, test: TestCase, env: AttemptEnv, session: BrowserSession, out: AttemptOutcome) -> None:
        for i, step in enumerate(test.browser_steps):
            env.cancel.raise_if_cancelled()
            env.limits.check_run()
            try:
                outcome = await asyncio.wait_for(session.run_step(step), timeout=max(0.05, env.budget.remaining_time()))
            except TimeoutError:
                out.timed_out = True
                env.trace.record(
                    EventType.ERROR,
                    {
                        "step": i,
                        "kind": "TIMEOUT",
                        "message": f"the test did not finish within {env.budget.timeout:g}s",
                    },
                )
                break
            action = session.rec.actions[-1]
            env.trace.record(
                EventType.BROWSER_ACTION,
                {
                    "step": i,
                    "action": step.action,
                    "target": action["target"],
                    "ok": outcome.ok,
                    "detail": outcome.detail,
                },
            )
            if step.action == "chat":
                self._add_reply(env, session, out, i, step.value, outcome)
            env.limits.record(
                env.budget, steps=1 if step.action == "chat" else 0, browser_actions=1, category=test.category
            )
            try:
                LimitTracker.check_test(env.budget)
                env.limits.check_run()
            except LimitReached as lr:
                out.stopped = lr
                env.trace.record(
                    EventType.LIMIT_REACHED, {"status": lr.status.value, "message": str(lr), "scope": lr.scope}
                )
                break
            if session.stops(step, outcome):
                if outcome.environmental:
                    # The step could not be done (a timeout, a missing element, no reply): the agent was never asked, or
                    # never answered, so the test is ERROR (not scored), not a failed check on whatever was read.
                    seen = f" [screenshot {action.get('screenshot')}]" if action.get("screenshot") else ""
                    out.error = BrowserError(f"{step.action}: {outcome.detail}{seen}")
                break

    @staticmethod
    def _add_reply(
        env: AttemptEnv,
        session: BrowserSession,
        out: AttemptOutcome,
        turn: int,
        typed: str | None,
        outcome: StepOutcome,
    ) -> None:
        text = session.resolve(typed) or ""
        resp = AgentResponse(
            output=outcome.reply or "",
            latency_ms=outcome.latency_ms,
            error=None if outcome.ok else outcome.detail,
            observed={"browser": True, "url": session.page.url},
        )
        out.inputs.append(text)
        out.responses.append(resp)
        env.trace.record(
            EventType.AGENT_REQUEST, {"turn": turn, "session": "browser", "input": text, "attachments": []}
        )
        env.trace.record_response(turn, "browser", resp)

    async def _finish(self, test: TestCase, env: AttemptEnv, session: BrowserSession, out: AttemptOutcome) -> None:
        """Evidence of where the attempt ended, and one observation to attach assertions to when no message was sent."""
        last = session.rec.actions[-1]["action"] if session.rec.actions else ""
        if last != "screenshot":
            await session.screenshot("final")
        if not out.responses:
            text = await session.page_text()
            steps = ", ".join(s.action for s in test.browser_steps)
            out.inputs.append(f"browser steps: {steps}")
            resp = AgentResponse(output=text, observed={"browser": True, "url": session.page.url})
            out.responses.append(resp)
            env.trace.record_response(0, "browser", resp)
        out.responses[-1].events.extend(
            AgentEvent(type="browser_action", data={k: a[k] for k in ("action", "target", "ok")})
            for a in session.rec.actions[:200]
        )

    # ------------------------------------------------------------------------------------------------ evidence
    @staticmethod
    def _saver(env: AttemptEnv, test: TestCase, out: AttemptOutcome) -> Any:
        def save(data: bytes, *, kind: str, name: str, media_type: str) -> str | None:
            art = save_artifact(
                env,
                data,
                kind=kind,
                name=f"{test.id}-a{env.attempt}-{name}",
                test_id=test.id,
                media_type=media_type,
            )
            if art:
                out.artifacts.append(art)
            return art

        return save

    async def _stop_tracing(self, test: TestCase, env: AttemptEnv, context: Any, tmp: Path) -> str | None:
        path = tmp / "trace.zip"
        try:
            await context.tracing.stop(path=str(path))
            data = path.read_bytes()
        except Exception:  # a trace that could not be written costs the evidence, not the test
            return None
        return save_artifact(
            env,
            data,
            kind="browser_trace",
            name=f"{test.id}-a{env.attempt}.trace.zip",
            test_id=test.id,
            media_type="application/zip",
        )

    @staticmethod
    def _video(test: TestCase, env: AttemptEnv, folder: Path) -> str | None:
        for path in sorted(folder.glob("*.webm"))[:1]:
            with contextlib.suppress(Exception):
                return save_artifact(
                    env,
                    path.read_bytes(),
                    kind="browser_video",
                    name=f"{test.id}-a{env.attempt}.webm",
                    test_id=test.id,
                    media_type="video/webm",
                )
        return None

    @staticmethod
    def _remember(
        test: TestCase, env: AttemptEnv, session: BrowserSession, trace: str | None, video: str | None
    ) -> None:
        """Keep the action list, screenshots and trace for the report's evidence section."""
        store = env.extras.get("store")
        if store is None:
            return
        with contextlib.suppress(Exception):  # evidence bookkeeping must not fail the attempt
            store.save_browser_session(
                env.run_id,
                test.id,
                BROWSER_NAME,
                trace,
                video,
                list(session.rec.screenshots),
                list(session.rec.actions),
                {
                    "attempt": env.attempt,
                    "final_url": session.page.url,
                    "blocked_requests": len(session.blocked_requests),
                },
            )


ENGINES.register("browser", BrowserExecutionEngine, replace=True)
