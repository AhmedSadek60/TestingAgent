"""Accounts, attacked: a reporter against every endpoint AgentLab had before, hostile input, and secrets in logs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from agentlab.core.config import AccountsConfig
from tests.api.test_accounts_api import OTHER, PASSWORD, TOKEN, accounts, browser, csrf, server
from tests.support.api import Api, product_logging

#: Answered without any credential on purpose: the health check, the sign-in and registration pages' own calls, and the
#: report viewer, which proves itself with a short-lived signed link.
PUBLIC = {
    "/health",
    "/auth/options",
    "/auth/register",
    "/auth/login",
    "/auth/logout",
}  # logout: already out is not an error
#: Open to every signed-in person, reporters included.
FOR_EVERYONE = {"/auth/me", "/auth/logout", "/auth/password"}
#: What a reporter may do in Feedback & Support, by method and path. Everything else AgentLab has is refused to them.
REPORTER_MAY = {
    ("GET", "/submissions/options"),
    ("GET", "/submissions/summary"),
    ("GET", "/submissions/similar"),
    ("POST", "/submissions"),
    ("GET", "/submissions"),
    ("GET", "/submissions/{submission_id}"),
    ("POST", "/submissions/{submission_id}/comments"),
    ("POST", "/submissions/{submission_id}/seen"),
    ("GET", "/submissions/{submission_id}/comments"),
    ("GET", "/submissions/{submission_id}/history"),
    ("POST", "/submissions/{submission_id}/attachments"),
    ("GET", "/submissions/{submission_id}/attachments"),
    ("GET", "/submissions/{submission_id}/attachments/{attachment_id}"),
    ("GET", "/submissions/{submission_id}/attachments/{attachment_id}/preview"),
    ("GET", "/submissions/{submission_id}/attachments/{attachment_id}/download"),
    ("DELETE", "/submissions/{submission_id}/attachments/{attachment_id}"),
}
ADMIN_ONLY_PREFIX = "/users"


def operations(api: Api) -> list[tuple[str, str]]:
    doc: dict[str, Any] = api.app.openapi()
    found = []
    for path, methods in doc["paths"].items():
        for method in methods:
            if method in {"get", "post", "put", "patch", "delete"}:
                found.append((method.upper(), path))
    return found


def concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x1", path)


async def reporter_session(api: Api, name: str = "walter") -> tuple[httpx.AsyncClient, dict[str, str]]:
    client = browser(api)
    r = await client.post("/auth/register", json={"username": name, "password": PASSWORD})
    assert r.status_code == 201, r.text
    return client, {"X-CSRF-Token": r.json()["me"]["csrf_token"]}


async def test_a_reporter_is_refused_by_every_endpoint_that_existed_before_accounts(tmp_path: Path) -> None:
    """Not a list of the routes someone remembered: every operation the API describes, from its own OpenAPI document.
    A reporter reaches the account calls and the Feedback & Support calls named above, and nothing else."""
    async with server(tmp_path) as api:
        client, headers = await reporter_session(api)
        tested, allowed = 0, []
        for method, path in operations(api):
            if path in PUBLIC or path in FOR_EVERYONE or (method, path) in REPORTER_MAY:
                continue
            r = await client.request(method, concrete(path), headers=headers, json={} if method != "GET" else None)
            tested += 1
            if r.status_code != 403:
                allowed.append((method, path, r.status_code))
        assert tested > 40, "the API's operations were not found"
        assert allowed == [], f"a reporter was not refused by: {allowed}"
        await client.aclose()


async def test_nobody_without_a_credential_reaches_any_endpoint_but_the_public_ones(tmp_path: Path) -> None:
    async with server(tmp_path) as api, browser(api) as anonymous:
        reached = []
        for method, path in operations(api):
            if path in PUBLIC:
                continue
            r = await anonymous.request(method, concrete(path), json={} if method != "GET" else None)
            if r.status_code != 401:
                reached.append((method, path, r.status_code))
        assert reached == [], f"answered without credentials: {reached}"


async def test_the_account_endpoints_are_refused_to_a_reporter_and_to_the_shared_token(tmp_path: Path) -> None:
    async with server(tmp_path) as api:
        client, headers = await reporter_session(api)
        for method, path in (
            ("GET", "/users"),
            ("POST", "/users"),
            ("PATCH", "/users/x1"),
            ("GET", "/users/events"),
            ("POST", "/users/x1/reset-password"),
            ("POST", "/users/x1/unlock"),
        ):
            body = (
                {"username": "mallory", "role": "admin"} if method == "POST" and path == "/users" else {"role": "admin"}
            )
            for who, request in (("reporter", client.request), ("token", api.client.request)):
                r = await request(method, path, headers=headers if who == "reporter" else None, json=body)
                assert r.status_code == 403, (who, method, path, r.status_code)
        mine = (await client.get("/auth/me")).json()
        assert mine["role"] == "reporter" and mine["can_manage_accounts"] is False
        await client.aclose()


async def test_a_reporter_cannot_make_themselves_anything_else(tmp_path: Path) -> None:
    async with server(tmp_path) as api:
        client, headers = await reporter_session(api)
        me = (await client.get("/auth/me")).json()
        account_id = accounts(api).by_username("walter").id  # type: ignore[union-attr]
        for body in ({"role": "admin"}, {"status": "active", "role": "maintainer"}):
            assert (await client.patch(f"/users/{account_id}", json=body, headers=headers)).status_code == 403
        registered = await client.post(
            "/auth/register", json={"username": "walter2", "password": PASSWORD, "role": "admin"}
        )
        assert registered.status_code == 422, "a role is not something a registration may ask for"
        assert accounts(api).by_username("walter").role == "reporter" and me["role"] == "reporter"  # type: ignore[union-attr]
        await client.aclose()


async def test_a_new_login_always_gets_a_new_session_whatever_cookie_was_offered(tmp_path: Path) -> None:
    async with server(tmp_path) as api, browser(api) as client:
        await client.post("/auth/register", json={"username": "xavier", "password": PASSWORD})
        first = client.cookies.get("agentlab_session")
        planted = "attacker-chosen-session-value-0123456789abcdef"
        client.cookies.clear()
        client.cookies.set("agentlab_session", planted)
        r = await client.post("/auth/login", json={"username": "xavier", "password": PASSWORD})
        assert r.status_code == 200
        issued = r.cookies.get("agentlab_session")
        assert issued and issued not in {planted, first}, "no session fixation"


@pytest.mark.parametrize(
    "name",
    [
        "' OR '1'='1",
        "admin'--",
        "x'; DROP TABLE users; --",
        "\u0000",
        "a" * 5000,
        "<script>alert(1)</script>",
        "../../etc/passwd",
    ],
)
async def test_hostile_user_names_are_a_clean_refusal_never_a_server_error(tmp_path: Path, name: str) -> None:
    async with server(tmp_path) as api, browser(api) as client:
        for path in ("/auth/login", "/auth/register"):
            r = await client.post(path, json={"username": name, "password": PASSWORD})
            assert r.status_code in (401, 422), (path, r.status_code, r.text[:200])
            assert r.headers["content-type"].startswith("application/json")
        assert accounts(api).count() == 0


async def test_a_display_name_with_markup_is_only_ever_data(tmp_path: Path) -> None:
    async with server(tmp_path) as api, browser(api) as client:
        evil = "<img src=x onerror=alert(1)>"
        r = await client.post(
            "/auth/register", json={"username": "yolanda", "password": PASSWORD, "display_name": evil}
        )
        assert r.status_code == 201 and r.json()["account"]["display_name"] == evil
        assert r.headers["content-type"].startswith("application/json")
        assert r.headers["x-content-type-options"] == "nosniff"


async def test_a_password_never_reaches_the_log_or_an_error_message(tmp_path: Path) -> None:
    with product_logging() as log:
        async with server(tmp_path, max_failed_attempts=3) as api, browser(api) as client:
            await client.post("/auth/register", json={"username": "zed-user", "password": PASSWORD})
            client.cookies.clear()
            answers = []
            for _ in range(5):
                r = await client.post("/auth/login", json={"username": "zed-user", "password": OTHER})
                answers.append(r.text)
            weak = await client.post("/auth/register", json={"username": "zed-two", "password": "password1234"})
            answers.append(weak.text)
            audit = str(accounts(api).events(200))
    everything = log.getvalue() + "".join(answers)
    for secret in (PASSWORD, OTHER, "password1234"):
        assert secret not in everything
    assert PASSWORD not in audit and OTHER not in audit


async def test_a_session_of_one_account_never_acts_as_another(tmp_path: Path) -> None:
    async with server(tmp_path) as api:
        one, one_headers = await reporter_session(api, "person-one")
        two, _ = await reporter_session(api, "person-two")
        assert (await one.get("/auth/me")).json()["username"] == "person-one"
        assert (await two.get("/auth/me")).json()["username"] == "person-two"
        # one's CSRF token is not valid on two's session
        r = await two.post(
            "/auth/password", json={"current_password": PASSWORD, "new_password": OTHER}, headers=one_headers
        )
        assert r.status_code == 403
        await one.aclose()
        await two.aclose()


async def test_the_shared_token_is_not_accepted_as_a_cookie_and_a_cookie_is_not_a_token(tmp_path: Path) -> None:
    async with server(tmp_path) as api, browser(api) as client:
        client.cookies.set("agentlab_session", TOKEN)
        assert (await client.get("/projects")).status_code == 401
        reg = await httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://testserver").post(
            "/auth/register", json={"username": "cookie-person", "password": PASSWORD}
        )
        cookie_value = reg.cookies.get("agentlab_session")
        assert cookie_value
        as_token = await api.client.get("/projects", headers={"Authorization": f"Bearer {cookie_value}"})
        assert as_token.status_code == 401


def test_accounts_defaults_are_the_safe_ones() -> None:
    cfg = AccountsConfig()
    assert cfg.default_role == "reporter", "a registration must not be a maintainer unless the owner said so"
    assert cfg.min_password_length >= 12 and cfg.max_failed_attempts <= 10 and cfg.cookie_secure == "auto"
    assert csrf is not None  # (imported for the helpers' sake)
