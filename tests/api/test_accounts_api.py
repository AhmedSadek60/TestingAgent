"""Accounts over HTTP: registration, sign-in, the cookie, CSRF, and what each role may reach."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

import httpx

from agentlab.accounts import AccountService
from agentlab.core.config import AccountsConfig
from tests.support.api import Api, api_config, running_api

TOKEN = "svc-" + "Qm7Hs2Vx9Lp4Zt6Ey1Nc"  # a made-up shared API token
PASSWORD = "correct-horse-battery-7"  # made up
OTHER = "another-long-secret-phrase-9"  # made up


@contextlib.asynccontextmanager
async def server(tmp_path: Path, *, token: str | None = TOKEN, **accounts: object) -> AsyncIterator[Api]:
    config = api_config(tmp_path, accounts=AccountsConfig(**accounts))  # type: ignore[arg-type]
    async with running_api(tmp_path, token=token, config=config) as api:
        yield api


def browser(api: Api, **headers: str) -> httpx.AsyncClient:
    """A client the way a browser is one: no API token, a cookie jar, and cookies sent back by themselves."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app, raise_app_exceptions=False),
        base_url="http://testserver",
        headers=headers,
        timeout=60,
    )


def accounts(api: Api) -> AccountService:
    assert api.state.accounts is not None
    return api.state.accounts


async def sign_in(api: Api, name: str, password: str = PASSWORD, **headers: str) -> tuple[httpx.AsyncClient, dict]:
    client = browser(api, **headers)
    r = await client.post("/auth/login", json={"username": name, "password": password})
    assert r.status_code == 200, r.text
    return client, r.json()


def _own_password(svc: AccountService, user_id: str) -> None:
    """Accounts made with a chosen password are not asked to change it again (the command line does the same)."""
    from agentlab.storage import orm

    with svc.db.session() as s:
        row = s.get(orm.UserRow, user_id)
        assert row is not None
        row.must_change_password = False


def csrf(login: dict) -> dict[str, str]:
    return {"X-CSRF-Token": login["me"]["csrf_token"]}


# ============================================================================================ what is public
async def test_the_sign_in_page_can_ask_how_to_sign_in_before_anyone_is_signed_in(tmp_path: Path) -> None:
    async with server(tmp_path) as api, browser(api) as anonymous:
        r = await anonymous.get("/auth/options")
        assert r.status_code == 200
        assert r.json() == {
            "registration": "open",
            "min_password_length": 12,
            "api_token_sign_in": True,
            "password_sign_in": True,
        }
        assert (await anonymous.get("/auth/me")).status_code == 401, "who am I needs someone"
        assert (await anonymous.get("/projects")).status_code == 401


# ================================================================================================ registering
async def test_registering_makes_a_reporter_with_a_safe_session_cookie(tmp_path: Path) -> None:
    async with server(tmp_path) as api, browser(api) as client:
        r = await client.post(
            "/auth/register", json={"username": "Alice", "password": PASSWORD, "display_name": "Alice A."}
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["signed_in"] is True and body["account"]["username"] == "alice"
        assert body["account"]["role"] == "reporter" and body["me"]["operator"] is False
        assert PASSWORD not in r.text and "scrypt$" not in r.text and "password_hash" not in r.text
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
        assert "secure" not in cookie, "this request came over plain http"
        me = await client.get("/auth/me")
        assert me.status_code == 200 and me.json()["username"] == "alice" and me.json()["kind"] == "user"


async def test_the_cookie_is_secure_when_the_request_came_over_https_through_a_proxy(tmp_path: Path) -> None:
    async with server(tmp_path) as api, browser(api, **{"X-Forwarded-Proto": "https"}) as client:
        r = await client.post("/auth/register", json={"username": "bob-secure", "password": PASSWORD})
        assert "secure" in r.headers["set-cookie"].lower()


async def test_registration_can_be_closed_or_wait_for_an_administrator(tmp_path: Path) -> None:
    async with server(tmp_path / "a", registration="closed") as api, browser(api) as client:
        r = await client.post("/auth/register", json={"username": "carol", "password": PASSWORD})
        assert r.status_code == 403 and r.json()["error"]["kind"] == "policy_blocked"
    async with server(tmp_path / "b", registration="approval") as api, browser(api) as client:
        r = await client.post("/auth/register", json={"username": "dave", "password": PASSWORD})
        assert r.status_code == 201 and r.json()["signed_in"] is False and r.json()["me"] is None
        assert "set-cookie" not in r.headers
        denied = await client.post("/auth/login", json={"username": "dave", "password": PASSWORD})
        assert denied.status_code == 409 and "approve" in denied.json()["error"]["message"]


async def test_a_weak_password_or_a_taken_name_is_refused_with_the_reason(tmp_path: Path) -> None:
    async with server(tmp_path) as api, browser(api) as client:
        weak = await client.post("/auth/register", json={"username": "erin", "password": "short1"})
        assert weak.status_code == 422 and "at least 12" in weak.json()["error"]["message"]
        assert (await client.post("/auth/register", json={"username": "erin", "password": PASSWORD})).status_code == 201
        taken = await client.post("/auth/register", json={"username": "ERIN", "password": OTHER})
        assert taken.status_code == 409 and taken.json()["error"]["kind"] == "conflict"


# ===================================================================== what a reporter may and may not reach
async def test_a_reporter_is_kept_out_of_everything_agentlab_had_before_accounts(tmp_path: Path) -> None:
    async with server(tmp_path) as api:
        reporter = browser(api)
        assert (
            await reporter.post("/auth/register", json={"username": "frank", "password": PASSWORD})
        ).status_code == 201
        for path in ("/projects", "/targets", "/test-runs", "/credentials", "/providers", "/skills", "/settings"):
            r = await reporter.get(path)
            assert r.status_code == 403, (path, r.status_code)
            assert "Feedback & Support only" in r.json()["error"]["message"]
        assert (await reporter.get("/auth/me")).status_code == 200
        # the shared token keeps its power, exactly as before
        assert (await api.client.get("/projects")).status_code == 200
        assert (await api.client.get("/providers")).status_code == 200
        await reporter.aclose()


async def test_a_maintainer_has_what_the_shared_token_has_but_cannot_manage_accounts(tmp_path: Path) -> None:
    async with server(tmp_path) as api:
        svc = accounts(api)
        user, _ = svc.create_user("cli", "gina-maint", role="maintainer", password=PASSWORD)
        _own_password(svc, user.id)
        client, _ = await sign_in(api, "gina-maint")
        me = (await client.get("/auth/me")).json()
        assert me["kind"] == "user" and me["role"] == "maintainer" and me["operator"] is True
        assert me["can_manage_accounts"] is False
        assert (await client.get("/projects")).status_code == 200
        assert (await client.get("/users")).status_code == 403


async def test_account_administration_is_for_administrators_only(tmp_path: Path) -> None:
    async with server(tmp_path) as api:
        svc = accounts(api)
        for name, role in (("root-admin", "admin"), ("plain-maint", "maintainer")):
            user, _ = svc.create_user("cli", name, role=role, password=PASSWORD)  # type: ignore[arg-type]
            _own_password(svc, user.id)
        maintainer, _ = await sign_in(api, "plain-maint")
        assert (await maintainer.get("/users")).status_code == 403
        assert (await maintainer.get("/projects")).status_code == 200
        assert (await api.client.get("/users")).status_code == 403, "the shared token is a service, not a person"
        admin, login = await sign_in(api, "root-admin")
        listed = await admin.get("/users")
        assert listed.status_code == 200 and {u["username"] for u in listed.json()} >= {"root-admin", "plain-maint"}
        assert "scrypt$" not in listed.text and "password_hash" not in listed.text and PASSWORD not in listed.text


# =================================================================================================== sign-in
async def test_a_wrong_password_and_an_unknown_user_get_the_same_answer(tmp_path: Path) -> None:
    async with server(tmp_path) as api, browser(api) as client:
        await client.post("/auth/register", json={"username": "hank", "password": PASSWORD})
        answers = []
        for name, password in (("hank", OTHER), ("nobody-here", PASSWORD)):
            r = await client.post("/auth/login", json={"username": name, "password": password})
            assert r.status_code == 401
            answers.append((r.json()["error"]["kind"], r.json()["error"]["message"]))
        assert answers[0] == answers[1]
        assert "set-cookie" not in r.headers


async def test_repeated_wrong_passwords_lock_the_account_and_say_when_to_try_again(tmp_path: Path) -> None:
    async with server(tmp_path, max_failed_attempts=3, lock_minutes=10) as api, browser(api) as client:
        await client.post("/auth/register", json={"username": "iris", "password": PASSWORD})
        client.cookies.clear()
        for _ in range(3):
            assert (await client.post("/auth/login", json={"username": "iris", "password": OTHER})).status_code == 401
        locked = await client.post("/auth/login", json={"username": "iris", "password": PASSWORD})
        assert locked.status_code == 429 and locked.json()["error"]["kind"] == "rate_limited"
        assert int(locked.headers["retry-after"]) > 0
        user = accounts(api).by_username("iris")
        assert user is not None
        accounts(api).unlock("test", user.id)
        assert (await client.post("/auth/login", json={"username": "iris", "password": PASSWORD})).status_code == 200


async def test_signing_out_ends_the_session_on_the_server_not_only_in_the_browser(tmp_path: Path) -> None:
    async with server(tmp_path) as api, browser(api) as client:
        await client.post("/auth/register", json={"username": "jack", "password": PASSWORD})
        stolen = dict(client.cookies)
        me = await client.get("/auth/me")
        out = await client.post("/auth/logout", headers={"X-CSRF-Token": me.json()["csrf_token"]})
        assert out.status_code == 204
        replay = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app, raise_app_exceptions=False),
            base_url="http://testserver",
            cookies=stolen,
        )
        assert (await replay.get("/auth/me")).status_code == 401, "a copy of the cookie is worth nothing after sign-out"
        await replay.aclose()


# ========================================================================================================= CSRF
async def test_a_request_authenticated_by_cookie_that_changes_something_needs_the_csrf_token(tmp_path: Path) -> None:
    async with server(tmp_path) as api, browser(api) as client:
        reg = await client.post("/auth/register", json={"username": "kate", "password": PASSWORD})
        token = reg.json()["me"]["csrf_token"]
        body = {"current_password": PASSWORD, "new_password": OTHER}
        assert (await client.post("/auth/password", json=body)).status_code == 403
        assert (await client.post("/auth/password", json=body, headers={"X-CSRF-Token": "wrong"})).status_code == 403
        ok = await client.post("/auth/password", json=body, headers={"X-CSRF-Token": token})
        assert ok.status_code == 204
        assert (await client.get("/auth/me")).status_code == 200, "reading needs no token"


async def test_a_request_from_another_site_is_refused_before_it_reaches_the_account_code(tmp_path: Path) -> None:
    async with server(tmp_path) as api, browser(api, Origin="https://evil.example") as client:
        r = await client.post("/auth/login", json={"username": "x", "password": "y"})
        assert r.status_code == 403 and r.json()["error"]["kind"] == "origin_not_allowed"


# ====================================================================================== temporary passwords
async def test_an_account_with_a_temporary_password_can_only_change_it_until_it_has(tmp_path: Path) -> None:
    async with server(tmp_path) as api:
        svc = accounts(api)
        root, _ = svc.create_user("cli", "root-admin", role="admin", password=PASSWORD)
        _own_password(svc, root.id)
        admin, login = await sign_in(api, "root-admin")
        made = await admin.post("/users", json={"username": "lena", "role": "maintainer"}, headers=csrf(login))
        assert made.status_code == 201
        temporary = made.json()["temporary_password"]
        assert temporary and made.json()["account"]["must_change_password"] is True
        lena, session = await sign_in(api, "lena", temporary)
        assert session["me"]["must_change_password"] is True and session["me"]["operator"] is False
        blocked = await lena.get("/projects")
        assert blocked.status_code == 403 and "temporary password" in blocked.json()["error"]["message"]
        assert (await lena.get("/auth/me")).status_code == 200
        changed = await lena.post(
            "/auth/password", json={"current_password": temporary, "new_password": OTHER}, headers=csrf(session)
        )
        assert changed.status_code == 204
        assert (await lena.get("/projects")).status_code == 200, "a maintainer, once the password is her own"


async def test_a_disabled_account_loses_its_open_session_at_once(tmp_path: Path) -> None:
    async with server(tmp_path) as api:
        svc = accounts(api)
        client = browser(api)
        reg = await client.post("/auth/register", json={"username": "mike", "password": PASSWORD})
        assert (await client.get("/auth/me")).status_code == 200
        svc.update("cli", reg.json()["account"]["id"], status="disabled")
        assert (await client.get("/auth/me")).status_code == 401
        await client.aclose()


async def test_administering_a_missing_account_is_a_404_and_the_last_admin_is_protected(tmp_path: Path) -> None:
    async with server(tmp_path) as api:
        svc = accounts(api)
        only, _ = svc.create_user("cli", "only-admin", role="admin", password=PASSWORD)
        _own_password(svc, only.id)
        admin, login = await sign_in(api, "only-admin")
        missing = await admin.patch("/users/no-such-id", json={"role": "reporter"}, headers=csrf(login))
        assert missing.status_code == 404
        last = await admin.patch(f"/users/{only.id}", json={"role": "reporter"}, headers=csrf(login))
        assert last.status_code == 409 and "last active administrator" in last.json()["error"]["message"]


# ============================================================================= the shared token, as before
async def test_the_shared_token_header_wins_over_a_cookie_and_still_opens_everything(tmp_path: Path) -> None:
    async with server(tmp_path) as api:
        reporter = browser(api)
        await reporter.post("/auth/register", json={"username": "nina", "password": PASSWORD})
        assert (await reporter.get("/projects")).status_code == 403
        both = await reporter.get("/projects", headers={"Authorization": f"Bearer {TOKEN}"})
        assert both.status_code == 200
        wrong = await reporter.get("/projects", headers={"Authorization": "Bearer not-the-token-at-all-0000"})
        assert wrong.status_code == 401
        me = await api.client.get("/auth/me")
        assert (
            me.json()["kind"] == "service"
            and me.json()["operator"] is True
            and me.json()["can_manage_accounts"] is False
        )
        await reporter.aclose()


async def test_a_server_that_needs_no_token_is_local_and_nothing_changes_for_it(tmp_path: Path) -> None:
    async with server(tmp_path, token=None) as api, browser(api) as client:
        me = await client.get("/auth/me")
        assert me.status_code == 200 and me.json()["kind"] == "local" and me.json()["can_manage_accounts"] is True
        assert (await client.get("/projects")).status_code == 200
