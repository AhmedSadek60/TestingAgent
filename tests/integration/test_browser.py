"""The browser engine against real pages: isolation, egress control, credentials, the step vocabulary, the web adapter and
the evidence a UI test leaves behind. Everything runs on loopback pages AgentLab serves itself; nothing leaves the machine.
Needs Playwright and a Chromium build (marker ``browser``)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from agentlab.adapters.base import AdapterContext
from agentlab.adapters.web import WebAdapter
from agentlab.browser.auth import open_context
from agentlab.browser.discover import discover_web
from agentlab.browser.pool import BrowserPool
from agentlab.browser.session import BrowserSession
from agentlab.browser.site import LocalSite
from agentlab.core.config import AgentLabConfig
from agentlab.core.errors import CredentialError
from agentlab.core.models import AgentRequest, BrowserStep, TargetSpec, WebConfig
from agentlab.fixtures import BrowserAgentFixture
from agentlab.security.credentials import CredentialManager, CredentialProfile, EncryptedSecretStore
from agentlab.security.egress import EgressPolicy
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
