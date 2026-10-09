"""The browser engine against real pages: isolation, egress control, credentials, the step vocabulary, the web adapter and
the evidence a UI test leaves behind. Everything runs on loopback pages AgentLab serves itself; nothing leaves the machine.
Needs Playwright and a Chromium build (marker ``browser``)."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import re
import socketserver
import threading
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import ValidationError

from agentlab.adapters.base import AdapterContext
from agentlab.adapters.web import WebAdapter
from agentlab.browser.auth import open_context
from agentlab.browser.discover import discover_web
from agentlab.browser.pool import BrowserPool
from agentlab.browser.session import BrowserSession
from agentlab.browser.site import LocalSite
from agentlab.core.config import AgentLabConfig
from agentlab.core.errors import BrowserError, CredentialError, TimeoutExceeded
from agentlab.core.models import AgentRequest, BrowserStep, TargetSpec, WebConfig
from agentlab.fixtures import BrowserAgentFixture
from agentlab.security.credentials import CredentialManager, CredentialProfile, EncryptedSecretStore
from agentlab.security.egress import EgressPolicy
from agentlab.storage.artifacts import MemoryArtifactStore
from tests.support.browser import browser_ok
from tests.support.servers import serve

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(not browser_ok(), reason="Playwright with Chromium is not available"),
]


@pytest.fixture
async def pool() -> AsyncIterator[BrowserPool]:
    p = BrowserPool(AgentLabConfig())
    yield p
    await p.aclose()


@pytest.fixture
def site() -> Any:
    s = LocalSite()
    with s.serve() as url:
        yield s, url


async def new_session(pool: BrowserPool, **kw: Any) -> BrowserSession:
    guarded = await pool.context()
    return BrowserSession(guarded, await guarded.context.new_page(), default_timeout_ms=4000, **kw)


def step(action: str, **kw: Any) -> BrowserStep:
    return BrowserStep(action=action, **kw)  # type: ignore[arg-type]


# ----------------------------------------------------------------------------------------------------------- isolation
async def test_two_contexts_share_no_storage(pool: BrowserPool, site: Any) -> None:
    _, url = site
    a, b = await new_session(pool), await new_session(pool)
    await a.run_step(step("goto", value=url + "/shop"))
    await b.run_step(step("goto", value=url + "/shop"))
    await a.page.evaluate("localStorage.setItem('k', 'from-a')")
    assert await b.page.evaluate("localStorage.getItem('k')") is None
    assert await a.page.evaluate("localStorage.getItem('k')") == "from-a"
    await a.close()
    await b.close()


async def test_the_pool_starts_one_browser_and_starts_again_after_it_is_closed(pool: BrowserPool) -> None:
    first = await pool.browser()
    assert await pool.browser() is first
    await pool.aclose()
    second = await pool.browser()
    assert second is not first and second.is_connected()


# --------------------------------------------------------------------------------------------------------- egress policy
async def test_a_page_cannot_reach_a_cloud_metadata_service(pool: BrowserPool) -> None:
    s = await new_session(pool)
    out = await s.run_step(step("goto", value="http://169.254.169.254/latest/meta-data/"))
    assert not out.ok and s.rec.load_failed
    assert len(s.blocked_requests) == 1 and "169.254.169.254" in s.blocked_requests[0]
    assert s.state()["blocked_requests"] == 1
    await s.close()


async def test_private_networks_can_be_forbidden_for_the_browser_as_for_everything_else(site: Any) -> None:
    _, url = site
    cfg = AgentLabConfig.model_validate({"security": {"allow_private_networks": False}})
    strict = BrowserPool(cfg)
    try:
        s = await new_session(strict)
        out = await s.run_step(step("goto", value=url + "/shop"))
        assert not out.ok and s.blocked_requests and "private" in s.blocked_requests[0]
        await s.close()
    finally:
        await strict.aclose()


class EchoSocket(socketserver.BaseRequestHandler):
    """Just enough of a WebSocket server (RFC 6455) to accept one connection, answer one text message and close. The
    server of the API cannot serve WebSockets without an optional library, and a test of the browser should not need one."""

    def handle(self) -> None:
        request = b""
        while b"\r\n\r\n" not in request:
            chunk = self.request.recv(4096)
            if not chunk:
                return
            request += chunk
        key = re.search(rb"Sec-WebSocket-Key: (\S+)", request, re.I)
        assert key is not None
        accept = base64.b64encode(hashlib.sha1(key.group(1) + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
        self.request.sendall(
            b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n"
        )
        head = self._read(2)
        length, mask = head[1] & 0x7F, self._read(4)
        text = bytes(b ^ mask[i % 4] for i, b in enumerate(self._read(length)))
        reply = b"echo:" + text
        self.request.sendall(bytes([0x81, len(reply)]) + reply + bytes([0x88, 0]))

    def _read(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = self.request.recv(n - len(data))
            if not chunk:
                raise ConnectionError("closed")
            data += chunk
        return data


@contextlib.contextmanager
def echo_socket() -> Iterator[int]:
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), EchoSocket)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def socket_page(url: str) -> FastAPI:
    """A page that opens a WebSocket to ``url`` and shows in the page what became of it."""
    app = FastAPI()

    @app.get("/page", response_class=HTMLResponse)
    async def page() -> str:
        return (
            "<p id=out>waiting</p><script>const out = document.getElementById('out');"
            f"const ws = new WebSocket('{url}');"
            "ws.onopen = () => ws.send('hello'); ws.onmessage = (e) => { out.textContent = e.data; };"
            "ws.onerror = () => { out.textContent = 'error'; };"
            "ws.onclose = () => { if (out.textContent === 'waiting') out.textContent = 'closed'; };</script>"
        )

    return app


async def test_a_websocket_the_page_opens_works_when_the_policy_allows_its_address(pool: BrowserPool) -> None:
    """A chat page that streams over a WebSocket must keep working: the guard passes an allowed connection through."""
    with echo_socket() as port, serve(socket_page(f"ws://127.0.0.1:{port}/")) as target:
        s = await new_session(pool)
        out = await s.run_step(step("goto", value=target.url + "/page"))
        assert out.ok
        await s.page.wait_for_function("document.getElementById('out').textContent !== 'waiting'", timeout=4000)
        assert await s.page.text_content("#out") == "echo:hello"
        assert s.blocked_requests == []
        await s.close()


async def test_a_websocket_to_a_forbidden_address_is_refused_and_recorded_like_any_request(pool: BrowserPool) -> None:
    """Playwright's ``route`` does not see WebSockets, so without their own guard a page could open one to an address the
    policy forbids, and nothing would say so."""
    with serve(socket_page("ws://169.254.169.254/ws")) as target:
        s = await new_session(pool)
        await s.run_step(step("goto", value=target.url + "/page"))
        await s.page.wait_for_function("document.getElementById('out').textContent !== 'waiting'", timeout=4000)
        assert any("169.254.169.254" in b for b in s.blocked_requests), s.blocked_requests
        assert s.state()["blocked_requests"] >= 1
        await s.close()


# --------------------------------------------------------------------------------------------------------- credentials
def echo_app(seen: list[dict[str, str]], other_port: int | None = None) -> FastAPI:
    app = FastAPI()

    @app.get("/echo")
    async def echo(request: Request) -> PlainTextResponse:
        seen.append(dict(request.headers))
        return PlainTextResponse("ok", headers={"Access-Control-Allow-Origin": "*"})

    @app.get("/page", response_class=HTMLResponse)
    async def page() -> str:
        third = f"fetch('http://127.0.0.1:{other_port}/echo', {{mode: 'no-cors'}});" if other_port else ""
        return f"<script>fetch('/echo'); {third}</script><p>loaded</p>"

    return app


async def test_a_header_credential_is_sent_to_the_targets_origin_and_nowhere_else(
    pool: BrowserPool, tmp_path: Path
) -> None:
    mine: list[dict[str, str]] = []
    third_party: list[dict[str, str]] = []
    creds = CredentialManager(EncryptedSecretStore(tmp_path / "s.enc"))
    creds.add(CredentialProfile(name="tok", kind="bearer"), {"token": "secret-token"})
    with serve(echo_app(third_party)) as other, serve(echo_app(mine, other.port)) as target:
        web = WebConfig(url=target.url + "/page", auth_credential="tok")
        opened = await open_context(pool, web, creds)
        page = await opened.guarded.context.new_page()
        await page.goto(web.url, wait_until="networkidle")
        await opened.guarded.context.close()
    assert mine and mine[-1].get("authorization") == "Bearer secret-token"
    assert third_party, "the page did call the other origin"
    assert all("authorization" not in h for h in third_party), "the credential never leaves the target's own origin"


async def test_a_credential_profile_that_does_not_exist_is_a_credential_error(
    pool: BrowserPool, tmp_path: Path
) -> None:
    creds = CredentialManager(EncryptedSecretStore(tmp_path / "s.enc"))
    with pytest.raises(CredentialError, match="not configured"):
        await open_context(pool, WebConfig(url="http://127.0.0.1:9/", auth_credential="nobody"), creds)


async def test_the_login_form_is_filled_in_and_the_password_is_not_recorded(pool: BrowserPool, site: Any) -> None:
    state, url = site
    s = await new_session(pool)
    await s.form_login(url + "/login", "demo", "hunter2-not-a-secret")
    assert state.snapshot()["logins"] == 1
    login = next(a for a in s.rec.actions if a["action"] == "login")
    assert login["ok"] and "hunter2" not in repr(s.rec.actions)
    await s.close()


async def test_a_single_page_login_that_finishes_after_the_click_is_not_mistaken_for_a_failure(
    pool: BrowserPool, site: Any
) -> None:
    """A page that signs in without loading another page removes its form a moment after the click."""
    _state, url = site
    page_html = (
        "<form onsubmit=\"event.preventDefault(); setTimeout(() => document.querySelector('form').remove(), 1500)\">"
        "<input name=username type=text><input type=password><button type=submit>Sign in</button></form>"
    )
    s = await new_session(pool)
    await s.page.route(url + "/login", lambda route: route.fulfill(status=200, content_type="text/html", body=page_html))
    await s.form_login(url + "/login", "demo", "hunter2-not-a-secret")
    login = next(a for a in s.rec.actions if a["action"] == "login")
    assert login["ok"]
    await s.close()


CHAT_PAGE = """<button>NEW CHAT</button><div id=log></div><input id=m><button id=send onclick="go()">Send</button>
<script>
function go() {
  const log = document.getElementById('log'); const q = document.getElementById('m').value;
  log.innerHTML += '<div>' + q + '</div><div id=bot>%s</div>';
  %s
}
</script>"""
FINISHES = """const stop = document.createElement('button'); stop.textContent = 'STOP'; document.body.appendChild(stop);
  setTimeout(() => { document.getElementById('bot').textContent = 'Paris is the capital of France.'; stop.remove();
    const copy = document.createElement('button'); copy.textContent = 'COPY'; document.body.appendChild(copy); }, 2500);"""


async def test_a_status_line_and_a_stop_button_are_not_the_reply_and_page_buttons_are_not_part_of_it(
    pool: BrowserPool, site: Any
) -> None:
    """The page shows "Processing\u2026" and a STOP button for a while, then the answer and a COPY button."""
    _state, url = site
    s = await new_session(pool)
    html = CHAT_PAGE % ("Processing\u2026", FINISHES)
    await s.page.route(url + "/chat-test", lambda route: route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html))
    await s.page.goto(url + "/chat-test")
    from agentlab.browser.chat import send_chat

    reply, replied = await send_chat(s.page, "What is the capital of France?", wait_seconds=15)
    assert replied and reply == "Paris is the capital of France."
    await s.close()


async def test_a_page_that_only_ever_shows_a_status_line_did_not_answer(pool: BrowserPool, site: Any) -> None:
    _state, url = site
    s = await new_session(pool)
    html = CHAT_PAGE % ("Thinking\u2026", "")
    await s.page.route(url + "/chat-test", lambda route: route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html))
    await s.page.goto(url + "/chat-test")
    from agentlab.browser.chat import send_chat

    reply, replied = await send_chat(s.page, "What is the capital of France?", wait_seconds=3)
    assert (reply, replied) == ("", False)
    await s.close()


async def test_a_login_that_keeps_asking_is_blocked_not_failed(pool: BrowserPool) -> None:
    lookalike = LocalSite(lookalike=True)
    with lookalike.serve() as url:
        s = await new_session(pool)
        with pytest.raises(CredentialError, match="could not sign in"):
            await s.form_login(url + "/login", "demo", "x")
        await s.close()


# ---------------------------------------------------------------------------------------------------- the step vocabulary
async def test_a_task_of_steps_runs_in_order_and_is_recorded(pool: BrowserPool, site: Any) -> None:
    state, url = site
    saved: list[tuple[str, str]] = []

    def save(data: bytes, *, kind: str, name: str, media_type: str) -> str:
        saved.append((kind, media_type))
        assert data[:4] == b"\x89PNG"
        return f"art-{len(saved)}"

    guarded = await pool.context()
    s = BrowserSession(guarded, await guarded.context.new_page(), save=save, default_timeout_ms=4000)
    steps = [
        step("goto", value=url + "/shop"),
        step("click", role="button", name="Add to cart"),
        step("expect_url", value="/cart"),
        step("expect_text", value="blue mug"),
        step("screenshot"),
    ]
    results = [await s.run_step(x) for x in steps]
    assert all(r.ok for r in results), [r.detail for r in results]
    assert [a["action"] for a in s.rec.actions] == ["goto", "click", "expect_url", "expect_text", "screenshot"]
    assert state.snapshot()["cart"] == ["blue mug"] and saved == [("screenshot", "image/png")]
    assert s.rec.actions[-1]["screenshot"] == "art-1" and s.state()["step_failures"] == 0
    await s.close()


async def test_a_missing_element_is_an_observation_and_stops_the_task(pool: BrowserPool, site: Any) -> None:
    _, url = site
    s = await new_session(pool)
    await s.run_step(step("goto", value=url + "/shop"))
    out = await s.run_step(step("click", target="#no-such-button", timeout_ms=700))
    assert not out.ok and BrowserSession.stops(step("click"), out)
    assert s.state()["step_failures"] == 1
    check = await s.run_step(step("expect_visible", target="#no-such-button", timeout_ms=500))
    assert not check.ok and not BrowserSession.stops(step("expect_visible"), check), (
        "a failed expectation does not stop"
    )
    assert s.state()["expectation_failures"] == 1 and s.state()["step_failures"] == 1
    await s.close()


async def test_an_overlay_that_covers_a_button_makes_the_click_fail(pool: BrowserPool, site: Any) -> None:
    state, url = site
    s = await new_session(pool)
    await s.run_step(step("goto", value=url + "/newsletter"))
    await s.run_step(step("fill", target="input[name=email]", value="a@example.com"))
    blocked = await s.run_step(step("click", role="button", name="Subscribe", timeout_ms=800))
    assert not blocked.ok and state.snapshot()["newsletter"] == []
    await s.run_step(step("click", target="#accept-cookies"))
    ok = await s.run_step(step("click", role="button", name="Subscribe"))
    assert ok.ok and state.snapshot()["newsletter"] == ["a@example.com"]
    await s.close()


async def test_script_errors_dialogs_and_failed_requests_are_collected(pool: BrowserPool) -> None:
    app = FastAPI()

    @app.get("/page", response_class=HTMLResponse)
    async def page() -> str:
        return "<img src='/missing.png'><script>setTimeout(() => alert('hi'), 0); throw new Error('boom')</script>"

    with serve(app) as srv:
        s = await new_session(pool)
        await s.run_step(step("goto", value=srv.url + "/page"))
        await s.run_step(step("wait", value="500"))
        state = s.state()
        await s.close()
    assert state["page_errors"] == 1 and "boom" in state["page_error_messages"][0]
    assert state["dialog_count"] == 1 and state["dialogs"][0] == {"type": "alert", "message": "hi"}
    assert state["failed_requests"] == 1, "the missing image is a failed request, not a failed page"
    assert state["load_failed"] is False


async def test_a_page_that_answers_404_is_a_failed_load(pool: BrowserPool, site: Any) -> None:
    _, url = site
    s = await new_session(pool)
    out = await s.run_step(step("goto", value=url + "/shop/missing-item"))
    assert not out.ok and "404" in out.detail and s.state()["load_failed"] and s.state()["http_status"] == 404
    await s.close()


async def test_files_can_be_attached_and_downloaded(pool: BrowserPool) -> None:
    s = await new_session(pool)
    page = "<input type=file id=f><a id=dl download=hello.txt href='data:text/plain,hello'>get</a>"
    await s.run_step(step("goto", value="data:text/html," + page))
    up = await s.run_step(step("upload", target="#f", value="gen://txt/text?text=hello"))
    assert up.ok, up.detail
    assert (await s.page.evaluate("document.querySelector('#f').files[0].size")) > 0
    down = await s.run_step(step("download", target="#dl"))
    assert down.ok, down.detail
    assert s.rec.downloads[0]["name"] == "hello.txt" and s.rec.downloads[0]["size"] == 5
    await s.close()


# ---------------------------------------------------------------------------------------- the web adapter on a chat page
@pytest.fixture
def surf() -> Any:
    with BrowserAgentFixture.build("correct").deployed() as target:
        yield TargetSpec(**target)


def adapter_for(spec: TargetSpec, pool: BrowserPool, **web: Any) -> WebAdapter:
    if web:
        assert spec.web is not None
        spec = spec.model_copy(update={"web": spec.web.model_copy(update=web)})
    return WebAdapter(spec, AdapterContext(config=AgentLabConfig(), extras={"browser_pool": pool}))


async def test_a_message_typed_into_the_page_is_answered(pool: BrowserPool, surf: TargetSpec) -> None:
    adapter = adapter_for(surf, pool)
    await adapter.open()
    sid = await adapter.new_session()
    reply = await adapter.send(AgentRequest(input="What is the capital of France?", session_id=sid))
    assert reply.error is None and "Paris" in reply.output
    assert "What is the capital" not in reply.output, "the echo of the question is not part of the reply"
    await adapter.close()


async def test_a_page_that_never_answers_is_a_timeout_error_not_an_empty_answer(
    pool: BrowserPool, surf: TargetSpec
) -> None:
    """No reply says nothing about the answer: the test must end in ERROR, not fail its checks on an empty reply."""
    store = MemoryArtifactStore()
    adapter = adapter_for(surf, pool, message_selector=".nothing-matches-this", reply_timeout_seconds=2)
    adapter.ctx.artifacts = store
    await adapter.open()
    sid = await adapter.new_session()
    with pytest.raises(TimeoutExceeded, match=r"no reply appeared.*waited up to 2s\) \[screenshot sha256-") as caught:
        await adapter.send(AgentRequest(input="What is the capital of France?", session_id=sid))
    shot = str(caught.value).split("[screenshot ")[1].rstrip("]")
    assert store.get(shot).startswith(b"\x89PNG"), "the screenshot of the page is kept as evidence"
    await adapter.close()


async def test_a_page_that_cannot_be_opened_is_a_browser_error_not_an_empty_answer(
    pool: BrowserPool, surf: TargetSpec
) -> None:
    adapter = adapter_for(surf, pool, url="http://127.0.0.1:9/")
    await adapter.open()
    sid = await adapter.new_session()
    with pytest.raises(BrowserError, match="could not open"):
        await adapter.send(AgentRequest(input="hello", session_id=sid))
    await adapter.close()


def test_the_reply_wait_of_a_web_target_is_bounded() -> None:
    assert WebConfig(url="https://example.test/").reply_timeout_seconds == 60.0
    assert WebConfig(url="https://example.test/", reply_timeout_seconds=180).reply_timeout_seconds == 180
    for bad in (0, -1, 601):
        with pytest.raises(ValidationError):
            WebConfig(url="https://example.test/", reply_timeout_seconds=bad)


async def test_explicit_selectors_read_exactly_the_messages_of_the_agent(pool: BrowserPool, surf: TargetSpec) -> None:
    adapter = adapter_for(surf, pool, input_selector="#message", send_selector="#send", message_selector=".bot")
    await adapter.open()
    sid = await adapter.new_session()
    reply = await adapter.send(AgentRequest(input="What is the capital of France?", session_id=sid))
    assert reply.output == "Surf Bot: The capital of France is Paris."
    await adapter.close()


async def test_two_sessions_are_two_browser_contexts_and_do_not_share_a_conversation(
    pool: BrowserPool, surf: TargetSpec
) -> None:
    adapter = adapter_for(surf, pool)
    await adapter.open()
    a, b = await adapter.new_session(), await adapter.new_session()
    await adapter.send(AgentRequest(input="My project deadline is October 20.", session_id=a))
    mine = await adapter.send(AgentRequest(input="What date did I mention?", session_id=a))
    other = await adapter.send(AgentRequest(input="What date did I mention?", session_id=b))
    assert "October 20" in mine.output
    assert "October 20" not in other.output
    await adapter.end_session(a)
    await adapter.close()


async def test_the_adapter_reports_a_page_that_is_not_there_and_a_page_that_is(
    pool: BrowserPool, surf: TargetSpec
) -> None:
    assert (await adapter_for(surf, pool).probe())["reachable"] is True
    gone = adapter_for(surf, pool, url="http://127.0.0.1:9/")
    result = await gone.probe()
    assert result["reachable"] is False and result["error"]


async def test_the_page_is_described_without_typing_anything(pool: BrowserPool, surf: TargetSpec) -> None:
    info = await discover_web(pool, surf)
    assert info["reachable"] and info["status"] == 200 and info["title"] == "Surf Bot"
    assert info["chat_ui"] is True and info["forms"] == 1
    assert {"tag": "textarea", "type": "textarea", "name": "message"} in info["inputs"]
    assert info["buttons"] == ["Send"]


async def test_a_page_that_cannot_be_opened_is_described_as_unreachable(pool: BrowserPool) -> None:
    spec = TargetSpec(name="x", web=WebConfig(url="http://127.0.0.1:9/"))
    info = await discover_web(pool, spec)
    assert info["reachable"] is False and info["error"]


def test_the_web_adapter_declares_what_it_cannot_observe(pool: BrowserPool, surf: TargetSpec) -> None:
    caps = adapter_for(surf, pool).capabilities
    assert caps.conversational and caps.sessions and caps.parallel_sessions
    assert not (caps.reports_tool_calls or caps.reports_contexts or caps.reports_usage)
    assert not (caps.canary_seeding or caps.knowledge_injection or caps.tool_output_injection or caps.omit_auth)


async def test_the_egress_policy_is_checked_before_the_page_is_opened(pool: BrowserPool) -> None:
    from agentlab.core.errors import PolicyBlocked

    spec = TargetSpec(name="x", web=WebConfig(url="http://169.254.169.254/"))
    adapter = WebAdapter(
        spec, AdapterContext(config=AgentLabConfig(), egress=EgressPolicy(), extras={"browser_pool": pool})
    )
    with pytest.raises(PolicyBlocked):
        await adapter.open()


# ----------------------------------------------------------------------------- consent dialogs, slow pages, set-up errors
CHAT = """<div id=log></div><input id=m><button id=send onclick="go()">Send</button>
<script>function go() {{ document.getElementById('log').innerHTML +=
  '<div>' + document.getElementById('m').value + '</div><div class=bot>Paris is the capital of France.</div>'; }}
{dialog_script}</script>"""
DIALOG = """const d = document.createElement('div'); d.id = 'cmp'; d.setAttribute('role', 'dialog');
d.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:99';
d.innerHTML = '<div style="background:#fff;margin:40px;padding:20px"><h2>Consent to Cookies &amp; Data Processing</h2>' +
  '<p>We and our 315 partners use cookies to process your data.</p><button>Settings</button>{buttons}</div>';
d.querySelectorAll('button[data-closes]').forEach(b => b.onclick = () => d.remove());
setTimeout(() => document.body.appendChild(d), {delay});"""
BOTH = "<button data-closes=1>Reject all</button><button data-closes=1>Accept all</button>"
ACCEPT_ONLY = "<button data-closes=1>Accept all</button>"


async def _chat_page(pool: BrowserPool, site: Any, buttons: str, delay: int = 0) -> Any:
    _state, url = site
    s = await new_session(pool)
    s.page.set_default_timeout(1500)
    html = CHAT.format(dialog_script=DIALOG.format(buttons=buttons, delay=delay))
    await s.page.route(
        url + "/c", lambda route: route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
    )
    await s.page.goto(url + "/c")
    return s


async def test_a_consent_dialog_that_offers_to_reject_is_closed_before_the_message_is_sent(
    pool: BrowserPool, site: Any
) -> None:
    from agentlab.browser.chat import send_chat

    s = await _chat_page(pool, site, BOTH)
    await s.page.wait_for_selector("#cmp")
    reply, replied = await send_chat(s.page, "What is the capital of France?", wait_seconds=10)
    assert replied and reply == "Paris is the capital of France."
    assert await s.page.locator("#cmp").count() == 0
    await s.close()


async def test_a_dialog_that_appears_after_the_page_loaded_is_closed_when_the_click_is_blocked(
    pool: BrowserPool, site: Any
) -> None:
    from agentlab.browser.chat import send_chat

    s = await _chat_page(pool, site, BOTH, delay=400)
    reply, replied = await send_chat(s.page, "What is the capital of France?", wait_seconds=10)
    assert replied and reply == "Paris is the capital of France."
    await s.close()


async def test_a_dialog_without_a_reject_button_is_never_accepted_unless_asked_and_the_error_says_what_to_set(
    pool: BrowserPool, site: Any
) -> None:
    from agentlab.browser.chat import send_chat

    s = await _chat_page(pool, site, ACCEPT_ONLY)
    await s.page.wait_for_selector("#cmp")
    with pytest.raises(BrowserError, match=r"consent dialog covers the page.*set web\.consent: accept"):
        await send_chat(s.page, "What is the capital of France?", wait_seconds=5)
    assert await s.page.locator("#cmp").count() == 1, "nothing was accepted on the site's behalf"
    await s.close()

    s = await _chat_page(pool, site, ACCEPT_ONLY)
    await s.page.wait_for_selector("#cmp")
    reply, replied = await send_chat(s.page, "What is the capital of France?", consent="accept", wait_seconds=10)
    assert replied and reply == "Paris is the capital of France."
    await s.close()


async def test_a_button_named_by_the_owner_closes_a_dialog_no_pattern_knows(pool: BrowserPool, site: Any) -> None:
    from agentlab.browser.chat import send_chat

    s = await _chat_page(pool, site, "<button data-closes=1 id=zzz>Continuer</button>")
    await s.page.wait_for_selector("#cmp")
    reply, replied = await send_chat(
        s.page, "What is the capital of France?", dismiss_selectors=["#zzz"], wait_seconds=10
    )
    assert replied and reply == "Paris is the capital of France."
    await s.close()


async def test_a_page_whose_load_event_never_fires_is_used_once_the_document_is_ready(
    pool: BrowserPool, site: Any
) -> None:
    """An advertising image that never arrives holds the load event back for ever; the page itself is usable."""
    _state, url = site
    s = await new_session(pool)
    html = "<h1>Chat</h1><img src='/never.png'><input id=m>"
    await s.page.route(url + "/never.png", lambda route: None)  # the request is never answered
    await s.page.route(url + "/slow", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
    outcome = await s.run_step(BrowserStep(action="goto", value=url + "/slow", timeout_ms=1500))
    assert outcome.ok and "did not fire" in outcome.detail
    await s.close()


async def test_a_step_that_could_not_be_done_is_a_set_up_error_and_a_wrong_answer_is_not(
    pool: BrowserPool, site: Any
) -> None:
    _state, url = site
    s = await new_session(pool)
    missing = await s.run_step(BrowserStep(action="click", target="#does-not-exist", timeout_ms=800))
    assert not missing.ok and missing.environmental, "no such button: nothing was asked, nothing is known about the agent"
    await s.page.route(url + "/gone", lambda route: route.fulfill(status=404, content_type="text/html", body="no"))
    answered = await s.run_step(BrowserStep(action="goto", value=url + "/gone"))
    assert not answered.ok and not answered.environmental, "HTTP 404 is what the page answered"
    await s.close()


async def test_a_browser_test_whose_click_cannot_be_done_ends_in_error_not_in_a_failed_check(tmp_path: Any) -> None:
    """The Free Anon AI false failure: turn 1 was answered, turn 2's click timed out, and the test was reported as failed
    on the (empty) last answer. It must be ERROR, and the answered turn must stay in the evidence."""
    from agentlab.core.enums import ErrorKind, TestStatus
    from agentlab.core.models import TestCase
    from agentlab.orchestrator.options import RunOptions
    from tests.support.lab import Lab

    async with Lab(tmp_path) as lab:
        with BrowserAgentFixture.build("correct").deployed() as t:
            page = t["web"]["url"]
            test = TestCase(
                id="USER-TWO-TURNS-001",
                name="two turns, the second cannot be sent",
                category="browser",
                objective="o",
                browser_steps=[
                    BrowserStep(action="goto", value=page),
                    BrowserStep(action="chat", value="What is the capital of France?"),
                    BrowserStep(action="click", target="#no-such-send-button", timeout_ms=800),
                    BrowserStep(action="chat", value="What did I ask?"),
                ],
                assertions=[{"type": "contains", "params": {"values": ["France"]}}],
            )
            spec = TargetSpec(name="surf", web={"url": page}, safety=t["safety"])
            out = await lab.run(
                spec,
                RunOptions(
                    suite="functional",
                    include_skills=["agent-fingerprinting"],
                    second_wave=False,
                    user_tests=[test],
                    probe=False,
                ),
            )
        result = next(r for r in lab.store.list_results(out.run_id) if r.test_id == "USER-TWO-TURNS-001")
        assert result.status == TestStatus.ERROR and result.error_kind == ErrorKind.BROWSER_ERROR
        assert "no-such-send-button" in (result.attempts[0].error or "") or "Timeout" in (result.attempts[0].error or "")
