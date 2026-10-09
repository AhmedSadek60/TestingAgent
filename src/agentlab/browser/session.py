"""One browser session: a page in an isolated context, the steps a test runs on it, and everything the page did.

The recording is evidence, not interpretation. It lists what happened (script errors, dialogs, refused requests, failed
steps, downloads) and the engine hands it to the assertions as ``browser.*`` state; whether that is good or bad is decided
by the test's assertions, never here.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentlab.browser.chat import first_visible, send_chat, visible_text
from agentlab.browser.consent import dismiss_consent
from agentlab.browser.pool import GuardedContext
from agentlab.core.errors import AgentLabError, CredentialError, PolicyBlocked, UserError, is_environmental
from agentlab.core.models import BrowserStep, WebConfig
from agentlab.documents.attachments import load_attachment
from agentlab.evaluation.context import PlaceholderResolver

MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
MAX_PAGE_TEXT = 20_000
IGNORED_FAILED_RESOURCES = ("favicon.ico",)
LOGIN_SETTLE_MS = 10_000  # how long a login form may keep showing after the click before the sign-in counts as failed
USERNAME_SELECTORS = (
    "input[autocomplete=username]:visible",
    "input[type=email]:visible",
    "input[name*=user i]:visible",
    "input[name*=email i]:visible",
    "input[name*=login i]:visible",
    "input[type=text]:visible",
)
SUBMIT_SELECTORS = (
    "button[type=submit]:visible",
    "input[type=submit]:visible",
    "button:has-text('Log in'):visible",
    "button:has-text('Sign in'):visible",
)
#: after one of these fails the page is not in the state the next steps expect, so the rest are skipped
STOPS_ON_FAILURE = {"goto", "click", "fill", "press", "select", "check", "upload", "download", "chat"}

Saver = Callable[..., str | None]  # (data, kind=, name=, media_type=) -> artifact id


@dataclass
class Recording:
    actions: list[dict[str, Any]] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    failed_requests: list[str] = field(default_factory=list)
    dialogs: list[dict[str, str]] = field(default_factory=list)
    downloads: list[dict[str, Any]] = field(default_factory=list)
    expectation_failures: list[str] = field(default_factory=list)
    step_failures: list[str] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    load_failed: bool = False
    http_status: int | None = None


@dataclass
class StepOutcome:
    ok: bool = True
    detail: str = ""
    reply: str | None = None  # set by a chat step
    replied: bool = True
    screenshot: str | None = None  # artifact id of a screenshot step
    latency_ms: float = 0.0
    #: the step failed because of the set-up (a timeout, a missing element, a page that did not open or never answered),
    #: not because the page under test answered badly (an HTTP error status is an answer, not this)
    environmental: bool = False


class BrowserSession:
    def __init__(
        self,
        guarded: GuardedContext,
        page: Any,
        *,
        web: WebConfig | None = None,
        resolver: PlaceholderResolver | None = None,
        fixtures_dir: Path | None = None,
        save: Saver | None = None,
        default_timeout_ms: int = 15_000,
    ) -> None:
        self.guarded = guarded
        self.context = guarded.context
        self.page = page
        self.web = web
        self.resolver = resolver
        self.fixtures_dir = fixtures_dir
        self.save = save
        self.default_timeout_ms = default_timeout_ms
        self.rec = Recording()
        self.accept_dialogs = False
        self._t0 = time.perf_counter()
        page.on("pageerror", lambda exc: self.rec.page_errors.append(str(exc)[:300]))
        page.on("console", self._on_console)
        page.on("dialog", self._on_dialog)
        page.on("response", self._on_response)

    # ------------------------------------------------------------------------------------------------- listeners
    def _on_console(self, msg: Any) -> None:
        if msg.type == "error" and "Failed to load resource" not in msg.text:
            self.rec.console_errors.append(msg.text[:300])

    async def _on_dialog(self, dialog: Any) -> None:
        self.rec.dialogs.append({"type": dialog.type, "message": dialog.message[:200]})
        if self.accept_dialogs:
            await dialog.accept()
        else:
            await dialog.dismiss()

    def _on_response(self, response: Any) -> None:
        if response.status >= 400 and not any(response.url.endswith(i) for i in IGNORED_FAILED_RESOURCES):
            self.rec.failed_requests.append(f"{response.status} {response.url}"[:300])

    # ----------------------------------------------------------------------------------------------- evidence
    def _store(self, data: bytes, *, kind: str, name: str, media_type: str) -> str | None:
        return self.save(data, kind=kind, name=name, media_type=media_type) if self.save else None

    @property
    def blocked_requests(self) -> list[str]:
        return self.guarded.blocked

    async def page_text(self) -> str:
        return (await visible_text(self.page))[:MAX_PAGE_TEXT]

    async def screenshot(self, name: str = "page") -> str | None:
        """A PNG of the current viewport, stored as evidence. Returns the artifact id (None when nothing is stored)."""
        try:
            png = await self.page.screenshot(full_page=False)
        except Exception:
            return None
        art = self._store(png, kind="screenshot", name=f"{name}.png", media_type="image/png")
        if art:
            self.rec.screenshots.append(art)
        return art

    def state(self) -> dict[str, Any]:
        """The ``browser.*`` observations assertions read."""
        r = self.rec
        return {
            "page_errors": len(r.page_errors),
            "page_error_messages": r.page_errors[:5],
            "console_errors": len(r.console_errors),
            "failed_requests": len(r.failed_requests),
            "blocked_requests": len(self.blocked_requests),
            "dialog_count": len(r.dialogs),
            "dialogs": r.dialogs[:5],
            "downloads": r.downloads,
            "expectation_failures": len(r.expectation_failures),
            "step_failures": len(r.step_failures),
            "load_failed": r.load_failed,
            "http_status": r.http_status,
            "url": self.page.url,
            "actions": len(r.actions),
        }

    # ------------------------------------------------------------------------------------------------ steps
    def resolve(self, text: str | None) -> str | None:
        if text is None or self.resolver is None:
            return text
        return self.resolver.resolve(text)

    def _locator(self, step: BrowserStep) -> Any:
        if step.role:
            return self.page.get_by_role(step.role, name=self.resolve(step.name)).first
        target = self.resolve(step.target)
        if not target:
            raise UserError(f"the '{step.action}' step needs a target (a selector) or a role")
        return self.page.locator(target).first

    async def run_step(self, step: BrowserStep) -> StepOutcome:
        t0 = time.perf_counter()
        timeout = min(step.timeout_ms, self.default_timeout_ms * 4)
        if step.action == "chat":  # a slow assistant is allowed the time the target says it needs
            timeout = min(step.timeout_ms, max(timeout, self.reply_timeout_ms))
        elif step.action == "goto":  # and a heavy page the time it needs to open
            timeout = min(step.timeout_ms, max(timeout, self.navigation_timeout_ms))
        outcome = StepOutcome()
        try:
            outcome = await getattr(self, f"_do_{step.action}")(step, timeout)
        except PolicyBlocked as exc:
            outcome = StepOutcome(False, f"blocked by policy: {exc}")
        except AgentLabError as exc:
            outcome = StepOutcome(False, str(exc), environmental=is_environmental(exc))
        except Exception as exc:  # a missing element, a timeout or a navigation error is an observation, not a crash
            outcome = StepOutcome(
                False,
                f"{type(exc).__name__}: {str(exc).splitlines()[0][:200] if str(exc) else ''}",
                environmental=True,
            )
        outcome.latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        if not outcome.ok and outcome.screenshot is None and step.action in STOPS_ON_FAILURE:
            # What was on the screen when the step failed is the evidence for why (a notice in the way, a login wall).
            with contextlib.suppress(Exception):
                outcome.screenshot = await asyncio.wait_for(
                    self.screenshot(name=f"failed-{step.action}-{len(self.rec.actions) + 1}"), timeout=10
                )
        self.rec.actions.append(
            {
                "action": step.action,
                "target": self.resolve(step.target) or self.resolve(step.value) or step.role or "",
                "ok": outcome.ok,
                "detail": outcome.detail,
                "t_ms": round((time.perf_counter() - self._t0) * 1000, 1),
                "screenshot": outcome.screenshot,
            }
        )
        if not outcome.ok:
            if step.action.startswith("expect_"):
                self.rec.expectation_failures.append(f"{step.action}: {outcome.detail}"[:300])
            else:
                self.rec.step_failures.append(f"{step.action}: {outcome.detail}"[:300])
        return outcome

    @property
    def navigation_timeout_ms(self) -> int:
        return int(self.web.navigation_timeout_seconds * 1000) if self.web else 30_000

    @property
    def reply_timeout_ms(self) -> int:
        return int(self.web.reply_timeout_seconds * 1000) if self.web else 60_000

    @staticmethod
    def stops(step: BrowserStep, outcome: StepOutcome) -> bool:
        return not outcome.ok and step.action in STOPS_ON_FAILURE

    async def _do_goto(self, step: BrowserStep, timeout: int) -> StepOutcome:
        url = self.resolve(step.value or step.target)
        if not url:
            raise UserError("the 'goto' step needs a url in 'value'")
        response = None
        try:
            response = await self.page.goto(url, wait_until="load", timeout=timeout)
        except Exception as exc:
            # A page full of advertising and trackers can take a long time to fire its load event while it is already
            # perfectly usable. If the document itself has been parsed, carry on instead of failing the test.
            state = await self._ready_state()
            if state not in {"interactive", "complete"} or self.page.url in {"", "about:blank"}:
                self.rec.load_failed = True
                return StepOutcome(False, f"navigation failed: {str(exc).splitlines()[0][:200]}", environmental=True)
            return StepOutcome(True, f"loaded {self.page.url} (its load event did not fire within {timeout // 1000}s)")
        self.rec.http_status = response.status if response else None
        if response is not None and response.status >= 400:
            self.rec.load_failed = True
            return StepOutcome(False, f"the page answered HTTP {response.status}")
        if self.web is not None:
            await dismiss_consent(self.page, self.web.consent, self.web.dismiss_selectors)
        return StepOutcome(True, f"loaded {self.page.url}")

    async def _ready_state(self) -> str:
        try:
            return str(await self.page.evaluate("document.readyState"))
        except Exception:
            return ""

    async def _do_click(self, step: BrowserStep, timeout: int) -> StepOutcome:
        await self._locator(step).click(timeout=timeout)
        return StepOutcome(True, "clicked")

    async def _do_fill(self, step: BrowserStep, timeout: int) -> StepOutcome:
        await self._locator(step).fill(self.resolve(step.value) or "", timeout=timeout)
        return StepOutcome(True, "filled")

    async def _do_press(self, step: BrowserStep, timeout: int) -> StepOutcome:
        key = self.resolve(step.value) or "Enter"
        if step.target or step.role:
            await self._locator(step).press(key, timeout=timeout)
        else:
            await self.page.keyboard.press(key)
        return StepOutcome(True, f"pressed {key}")

    async def _do_select(self, step: BrowserStep, timeout: int) -> StepOutcome:
        await self._locator(step).select_option(self.resolve(step.value) or "", timeout=timeout)
        return StepOutcome(True, "selected")

    async def _do_check(self, step: BrowserStep, timeout: int) -> StepOutcome:
        await self._locator(step).check(timeout=timeout)
        return StepOutcome(True, "checked")

    async def _do_upload(self, step: BrowserStep, timeout: int) -> StepOutcome:
        ref = self.resolve(step.value)
        if not ref:
            raise UserError("the 'upload' step needs a file reference in 'value' (a gen:// recipe or a fixture file)")
        att = load_attachment(ref, self.fixtures_dir, self.resolver)
        payload = {"name": att.name, "mimeType": att.media_type, "buffer": base64.b64decode(att.content_b64 or "")}
        await self._locator(step).set_input_files(payload, timeout=timeout)
        return StepOutcome(True, f"attached {att.name}")

    async def _do_expect_text(self, step: BrowserStep, timeout: int) -> StepOutcome:
        text = self.resolve(step.value)
        if not text:
            raise UserError("the 'expect_text' step needs the text in 'value'")
        scope = self.page.locator(self.resolve(step.target)) if step.target else self.page
        try:
            await scope.get_by_text(text).first.wait_for(state="visible", timeout=timeout)
        except Exception:
            return StepOutcome(False, f"the text {text!r} is not visible on the page")
        return StepOutcome(True, f"found {text!r}")

    async def _do_expect_url(self, step: BrowserStep, timeout: int) -> StepOutcome:
        fragment = self.resolve(step.value) or ""
        deadline = time.monotonic() + timeout / 1000
        while time.monotonic() < deadline:
            if fragment in self.page.url:
                return StepOutcome(True, self.page.url)
            await asyncio.sleep(0.1)
        return StepOutcome(False, f"the url is {self.page.url}, expected it to contain {fragment!r}")

    async def _do_expect_visible(self, step: BrowserStep, timeout: int) -> StepOutcome:
        try:
            await self._locator(step).wait_for(state="visible", timeout=timeout)
        except Exception:
            return StepOutcome(False, f"{self.resolve(step.target) or step.role} is not visible")
        return StepOutcome(True, "visible")

    async def _do_wait(self, step: BrowserStep, timeout: int) -> StepOutcome:
        value = self.resolve(step.value) or self.resolve(step.target) or "500"
        if value.strip().isdigit():
            await asyncio.sleep(min(int(value), 30_000) / 1000)
            return StepOutcome(True, f"waited {value} ms")
        await self.page.locator(value).first.wait_for(state="visible", timeout=timeout)
        return StepOutcome(True, f"{value} appeared")

    async def _do_screenshot(self, step: BrowserStep, timeout: int) -> StepOutcome:
        art = await self.screenshot(name=f"step-{len(self.rec.actions) + 1}")
        return StepOutcome(
            True, "screenshot stored" if art else "screenshot not stored (no artifact store)", screenshot=art
        )

    async def _do_download(self, step: BrowserStep, timeout: int) -> StepOutcome:
        async with self.page.expect_download(timeout=timeout) as info:
            await self._locator(step).click(timeout=timeout)
        download = await info.value
        path = await download.path()
        data = Path(path).read_bytes()[: MAX_DOWNLOAD_BYTES + 1] if path else b""
        if len(data) > MAX_DOWNLOAD_BYTES:
            return StepOutcome(False, f"the download is larger than {MAX_DOWNLOAD_BYTES} bytes")
        art = self._store(
            data, kind="download", name=download.suggested_filename, media_type="application/octet-stream"
        )
        self.rec.downloads.append(
            {
                "name": download.suggested_filename,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "artifact": art,
            }
        )
        return StepOutcome(True, f"downloaded {download.suggested_filename} ({len(data)} bytes)")

    async def _do_dialog_accept(self, step: BrowserStep, timeout: int) -> StepOutcome:
        self.accept_dialogs = True
        return StepOutcome(True, "later dialogs are accepted")

    async def _do_dialog_dismiss(self, step: BrowserStep, timeout: int) -> StepOutcome:
        self.accept_dialogs = False
        return StepOutcome(True, "later dialogs are dismissed")

    async def _do_chat(self, step: BrowserStep, timeout: int) -> StepOutcome:
        text = self.resolve(step.value)
        if not text:
            raise UserError("the 'chat' step needs the message in 'value'")
        w = self.web
        reply, replied = await send_chat(
            self.page,
            text,
            input_selector=(w.input_selector if w else None) or self.resolve(step.target),
            send_selector=w.send_selector if w else None,
            message_selector=w.message_selector if w else None,
            busy_selector=w.busy_selector if w else None,
            consent=w.consent if w else "reject",
            dismiss_selectors=w.dismiss_selectors if w else (),
            wait_seconds=min(timeout / 1000, max(60.0, self.reply_timeout_ms / 1000)),
        )
        if not replied:
            return StepOutcome(
                False, "no reply appeared on the page in time", reply=reply, replied=False, environmental=True
            )
        return StepOutcome(True, f"reply of {len(reply)} characters", reply=reply)

    async def form_login(self, login_url: str, username: str, password: str) -> None:
        """Fill in and submit the login form of ``login_url``. The values never reach the action list or the trace.

        A form that cannot be found, or that is still showing afterwards, means the test user cannot sign in: that is a
        problem of the test set-up, not of the target, so it raises ``CredentialError`` and the tests are BLOCKED."""
        page = self.page
        try:
            await page.goto(login_url, wait_until="load", timeout=self.default_timeout_ms)
            user_box = await first_visible(page, USERNAME_SELECTORS)
            pass_box = await first_visible(page, ("input[type=password]:visible",))
            if user_box is None or pass_box is None:
                raise CredentialError(f"no login form found at {login_url}")
            await user_box.fill(username)
            await pass_box.fill(password)
            submit = await first_visible(page, SUBMIT_SELECTORS)
            if submit is not None:
                await submit.click()
            else:
                await pass_box.press("Enter")
            await page.wait_for_load_state("load", timeout=self.default_timeout_ms)
            # A single-page application signs in without loading another page, so the form can still be showing for a
            # moment after the click. Give it that moment: a form that is still there afterwards means the sign-in failed.
            with contextlib.suppress(Exception):
                await page.locator("input[type=password]:visible").first.wait_for(
                    state="hidden", timeout=min(self.default_timeout_ms, LOGIN_SETTLE_MS)
                )
            still_asking = await first_visible(page, ("input[type=password]:visible",)) is not None
        except CredentialError:
            self._record_login(login_url, False, "no login form found")
            raise
        except Exception as exc:
            self._record_login(login_url, False, f"{type(exc).__name__}")
            raise CredentialError(f"could not sign in at {login_url}: {type(exc).__name__}") from exc
        if still_asking:
            self._record_login(login_url, False, "the login form is still showing after submitting")
            raise CredentialError(f"the test user could not sign in at {login_url} (the login form is still showing)")
        self._record_login(login_url, True, "signed in as the configured test user")

    def _record_login(self, url: str, ok: bool, detail: str) -> None:
        self.rec.actions.append(
            {
                "action": "login",
                "target": url,
                "ok": ok,
                "detail": detail,
                "t_ms": round((time.perf_counter() - self._t0) * 1000, 1),
                "screenshot": None,
            }
        )

    async def close(self) -> None:
        with contextlib.suppress(Exception):  # a context that is already gone is not an error
            await self.context.close()
