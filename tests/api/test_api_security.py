"""Protecting the API itself. Whoever can call it can make AgentLab send requests, start containers and drive a browser, so
these tests treat every way in as hostile: no token, a stranger's web page, a rebinding DNS name, a path that climbs out of
the folders the operator allowed, a secret sent where it would be stored in the clear, evidence that should not be served.

Every secret below is invented and assembled from fragments so no scanner mistakes this file for a leak."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from agentlab.api.app import create_app
from agentlab.api.security import MAX_FAILURES, TokenGuard, confine, is_loopback, safe_filename
from agentlab.core.config import ServerConfig
from agentlab.core.errors import CredentialError, PolicyBlocked
from agentlab.services import Services
from tests.support.api import api_config, mock_target, running_api

TOKEN = "api-" + "Qw7Er5Ty3Ui9Op1As4Df"  # a made-up API token
SECRET = "sk-" + "proj" + "-" + "Zq81LmN4xW7sR2pK9vB3cD6e"  # looks like a provider key, is not one

PUBLIC = {("GET", "/health")}


def concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


# ============================================================================================ the token
async def test_every_endpoint_but_health_refuses_a_request_without_the_token(tmp_path: Path) -> None:
    async with running_api(tmp_path, token=TOKEN, headers={"Authorization": ""}) as api:
        schema = api.app.openapi()
        checked = 0
        for path, methods in schema["paths"].items():
            for method in methods:
                if (method.upper(), path) in PUBLIC:
                    continue
                r = await api.client.request(method.upper(), concrete(path), headers={"Authorization": ""})
                assert r.status_code == 401, f"{method.upper()} {path} answered {r.status_code} without a token"
                assert r.headers["www-authenticate"] == "Bearer"
                assert r.json()["error"]["kind"] == "unauthorized"
                checked += 1
        assert checked >= 40, "the check must cover the whole API"
        assert (await api.client.get("/health", headers={"Authorization": ""})).status_code == 200


async def test_the_token_is_accepted_as_a_bearer_or_as_an_api_key_and_nothing_else_is(tmp_path: Path) -> None:
    async with running_api(tmp_path, token=TOKEN, headers={"Authorization": ""}) as api:
        c = api.client
        assert (await c.get("/projects", headers={"Authorization": f"Bearer {TOKEN}"})).status_code == 200
        assert (await c.get("/projects", headers={"Authorization": f"bearer {TOKEN}"})).status_code == 200
        assert (await c.get("/projects", headers={"X-API-Key": TOKEN})).status_code == 200
        for headers in (
            {"Authorization": f"Bearer {TOKEN[:-1]}"},  # one character short
            {"Authorization": f"Bearer {TOKEN}x"},  # one too many
            {"Authorization": f"Bearer {TOKEN.upper()}"},
            {"Authorization": f"Basic {TOKEN}"},  # the wrong scheme
            {"Authorization": TOKEN},  # no scheme
            {"Authorization": "Bearer "},
            {"X-API-Key": ""},
            {"X-API-Key": TOKEN[::-1]},
        ):
            r = await c.get("/projects", headers=headers)
            assert r.status_code == 401, headers
            assert TOKEN not in r.text


async def test_a_client_that_keeps_guessing_is_slowed_down_even_when_it_then_gets_it_right(tmp_path: Path) -> None:
    async with running_api(tmp_path, token=TOKEN, headers={"Authorization": ""}) as api:
        c = api.client
        for _ in range(MAX_FAILURES):
            assert (await c.get("/projects", headers={"Authorization": "Bearer wrong"})).status_code == 401
        blocked = await c.get("/projects", headers={"Authorization": f"Bearer {TOKEN}"})
        assert blocked.status_code == 429 and blocked.json()["error"]["kind"] == "rate_limited"
        assert (await c.get("/health")).status_code == 200, "the health check stays available"


def test_the_failure_counter_forgets_old_attempts_and_cannot_be_grown_without_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agentlab.api.security as sec

    guard = TokenGuard(TOKEN)
    now = [1000.0]
    monkeypatch.setattr(sec.time, "monotonic", lambda: now[0])
    for _ in range(MAX_FAILURES):
        assert guard.check("1.2.3.4", "nope") is False
    assert guard.throttled("1.2.3.4") is True
    now[0] += sec.FAILURE_WINDOW_SECONDS + 1
    assert guard.throttled("1.2.3.4") is False, "a minute later it may try again"
    for i in range(5000):  # many addresses
        guard.check(f"10.0.{i // 250}.{i % 250}", "nope")
    assert len(guard._failures) <= 4097
    assert TokenGuard(None).check("anyone", None) is True and TokenGuard(None).required is False


# ======================================================================================== who may listen
async def test_the_server_refuses_to_listen_beyond_this_machine_without_a_token(tmp_path: Path) -> None:
    config = api_config(tmp_path, server=ServerConfig(host="0.0.0.0"))  # noqa: S104 - a configuration under test
    with pytest.raises(PolicyBlocked, match="no API token"):
        async with running_api(tmp_path, config=config):
            pass
    async with running_api(
        tmp_path / "ok", token=TOKEN, config=api_config(tmp_path / "ok", server=ServerConfig(host="0.0.0.0"))
    ) as api:  # noqa: S104
        assert (await api.client.get("/health")).json()["auth_required"] is True


@pytest.mark.parametrize(
    ("value", "error", "message"),
    [
        ("short", PolicyBlocked, "16 characters"),
        ("x" * 15, PolicyBlocked, "16 characters"),
        ("", CredentialError, "not set"),  # an empty variable is as good as none: the server must not start open
        (None, CredentialError, "not set"),
    ],
)
async def test_a_weak_or_missing_token_in_the_configuration_stops_the_server_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str | None, error: type[Exception], message: str
) -> None:
    if value is None:
        monkeypatch.delenv("AGENTLAB_TEST_TOKEN", raising=False)
    else:
        monkeypatch.setenv("AGENTLAB_TEST_TOKEN", value)
    services = Services.create(
        api_config(tmp_path, server=ServerConfig(token_ref="env:AGENTLAB_TEST_TOKEN")), base_dir=tmp_path
    )
    app = create_app(services, serve_ui=False)
    with pytest.raises(error, match=message):
        async with app.router.lifespan_context(app):
            pass
    services.store.db.dispose()


async def test_a_token_from_the_environment_or_the_credential_store_protects_the_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLAB_TEST_TOKEN", TOKEN)
    config = api_config(tmp_path, server=ServerConfig(token_ref="env:AGENTLAB_TEST_TOKEN"))
    services = Services.create(config, base_dir=tmp_path)
    app = create_app(services, serve_ui=False)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://anything.example") as c,
    ):
        assert (await c.get("/projects")).status_code == 401
        assert (await c.get("/projects", headers={"Authorization": f"Bearer {TOKEN}"})).status_code == 200
    services.store.db.dispose()


def test_which_addresses_are_this_machine() -> None:
    for host in ("127.0.0.1", "localhost", "::1", "[::1]", "127.1.2.3", "LOCALHOST"):
        assert is_loopback(host), host
    for host in ("0.0.0.0", "::", "192.168.1.5", "10.0.0.1", "example.com", "127.0.0.1.evil.net", ""):  # noqa: S104
        assert not is_loopback(host), host


# ============================================================================== web pages of other sites
async def test_without_a_token_only_this_machines_names_reach_the_api_so_dns_rebinding_gets_nowhere(
    tmp_path: Path,
) -> None:
    async with running_api(tmp_path, allowed_hosts=None) as api:  # the default: no token, so loopback names only
        c = api.client
        for host in ("localhost", "localhost:8080", "127.0.0.1:8765", "[::1]:8080"):
            r = await c.get("/projects", headers={"Host": host})
            assert r.status_code == 200, host
        for host in (
            "evil.example",
            "evil.example:8080",
            "127.0.0.1.evil.example",
            "localhost.evil.example",
            "10.0.0.5",
            "",
        ):
            r = await c.get("/projects", headers={"Host": host})
            assert r.status_code == 400 and r.json()["error"]["kind"] == "host_not_allowed", host


async def test_with_a_token_the_host_name_may_be_anything_a_proxy_uses(tmp_path: Path) -> None:
    async with running_api(tmp_path, token=TOKEN, allowed_hosts=None) as api:
        r = await api.client.get("/projects", headers={"Host": "agentlab.internal.example"})
        assert r.status_code == 200


async def test_a_page_of_another_site_cannot_make_the_server_do_anything(tmp_path: Path) -> None:
    async with running_api(tmp_path) as api:
        c = api.client
        evil = {"Origin": "https://evil.example"}
        for method, url, body in [
            ("POST", "/projects", {"name": "planted"}),
            ("POST", "/test-runs", {"target": mock_target()}),
            ("POST", "/credentials", {"name": "x", "scopes": ["a"], "secrets": {"token": TOKEN}}),
            ("DELETE", "/credentials/x", None),
        ]:
            r = await c.request(method, url, json=body, headers=evil)
            assert r.status_code == 403 and r.json()["error"]["kind"] == "origin_not_allowed", (method, url)
        assert (await c.get("/projects")).json() == [], "nothing was created"
        assert (await c.get("/credentials")).json() == []
        # the interface's own origin (same host) and tools that send no Origin at all are fine
        own = await c.post("/projects", json={"name": "mine"}, headers={"Origin": "http://testserver"})
        assert own.status_code == 201
        assert (await c.post("/projects", json={"name": "cli"})).status_code == 201
        for lookalike in ("http://testserver.evil.example", "http://evil.example/testserver", "null"):
            r = await c.post("/projects", json={"name": "no"}, headers={"Origin": lookalike})
            assert r.status_code == 403, lookalike


async def test_an_origin_the_operator_lists_may_call_the_api_from_a_browser(tmp_path: Path) -> None:
    config = api_config(tmp_path, server=ServerConfig(cors_origins=["https://console.example"]))
    async with running_api(tmp_path, config=config) as api:
        c = api.client
        ok = await c.post("/projects", json={"name": "from-console"}, headers={"Origin": "https://console.example"})
        assert ok.status_code == 201
        assert ok.headers["access-control-allow-origin"] == "https://console.example"
        assert "access-control-allow-credentials" not in ok.headers, "no cookies are ever involved"
        pre = await c.options(
            "/projects",
            headers={
                "Origin": "https://console.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert pre.status_code == 200 and "POST" in pre.headers["access-control-allow-methods"]
        other = await c.post("/projects", json={"name": "no"}, headers={"Origin": "https://evil.example"})
        assert other.status_code == 403
        assert "access-control-allow-origin" not in other.headers


async def test_every_response_says_it_must_not_be_sniffed_framed_or_shared(tmp_path: Path) -> None:
    async with running_api(tmp_path) as api:
        for url in ("/health", "/projects", "/no/such/route", "/openapi.json"):
            r = await api.client.get(url)
            assert r.headers["x-content-type-options"] == "nosniff", url
            assert r.headers["x-frame-options"] == "DENY" and r.headers["referrer-policy"] == "no-referrer", url
            assert r.headers["cross-origin-resource-policy"] == "same-origin", url


# ======================================================================================= paths on the server
def climb_attempts(allowed: Path, outside: Path) -> list[str]:
    link = allowed / "link-out"
    link.symlink_to(outside, target_is_directory=True)
    return [
        "/etc",
        "/",
        str(outside),
        str(allowed / ".." / outside.name),
        str(allowed) + "/../../../etc",
        str(link),  # a symlink inside the allowed folder that leads out of it
        str(link / "secret.txt"),
        "~",
        "~/.ssh",
        "../../etc",
        "relative/../../..",
        "/proc/self/environ",
    ]


async def test_a_request_can_name_server_paths_only_inside_the_folders_the_operator_allowed(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    (allowed / "repo").mkdir(parents=True)
    outside.mkdir()
    (outside / "secret.txt").write_text("not for the API caller")
    config = api_config(tmp_path, server=ServerConfig(allowed_paths=[str(allowed)]))
    async with running_api(tmp_path, config=config) as api:
        c = api.client
        for attempt in climb_attempts(allowed, outside):
            for where, spec in {
                "repository.path": {"name": "t", "repository": {"path": attempt}},
                "repository.archive": {"name": "t", "repository": {"archive": attempt}},
                "documents": {"name": "t", "mock": {}, "documents": [attempt]},
            }.items():
                for endpoint, body in (
                    ("/targets", {"spec": spec}),
                    ("/discover", {"target": spec}),
                    ("/test-plans", {"target": spec}),
                    ("/test-runs", {"target": spec}),
                ):
                    r = await c.post(endpoint, json=body)
                    assert r.status_code == 403, (endpoint, where, attempt, r.status_code, r.text[:200])
                    assert r.json()["error"]["kind"] == "policy_blocked"
                    assert "not for the API caller" not in r.text
        r = await c.post("/test-runs", json={"target": mock_target(), "options": {"user_test_files": ["/etc/passwd"]}})
        assert r.status_code == 403, "files of test cases follow the same rule"

        ok = await c.post("/targets", json={"spec": {"name": "inside", "repository": {"path": str(allowed / "repo")}}})
        assert ok.status_code == 201, ok.text
        assert ok.json()["spec"]["repository"]["path"] == str((allowed / "repo").resolve())


async def test_with_no_allowed_folders_a_request_cannot_name_a_server_path_at_all(tmp_path: Path) -> None:
    async with running_api(tmp_path) as api:
        r = await api.client.post("/targets", json={"spec": {"name": "t", "repository": {"path": str(tmp_path)}}})
        assert r.status_code == 403 and "allows none" in r.json()["error"]["message"]
        assert "upload" in r.json()["error"]["message"], "the message says what to do instead"


def test_confine_and_safe_filename_do_what_they_say(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    assert confine(str(root / "sub"), [root], what="x") == (root / "sub").resolve()
    for bad in (str(tmp_path), str(root) + "-sibling", str(root / ".." / "x"), "\x00", "/etc/passwd"):
        with pytest.raises(PolicyBlocked):
            confine(bad, [root], what="x")
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("..\\..\\windows\\system32\\cmd.exe") == "cmd.exe"
    assert safe_filename("a b/c d.md") == "c d.md"
    assert safe_filename("name\x00.md") == "name_.md"
    assert safe_filename("...") == "file" and safe_filename("") == "file" and safe_filename("////") == "file"
    assert safe_filename("x" * 500) == "x" * 120
    assert "/" not in safe_filename("<script>alert(1)</script>.md")


async def test_an_artifact_or_report_cannot_be_asked_for_by_a_path(tmp_path: Path) -> None:
    async with running_api(tmp_path) as api:
        c = api.client
        for url in (
            "/artifacts/../../etc/passwd",
            "/artifacts/%2e%2e%2f%2e%2e%2fetc%2fpasswd",
            "/artifacts/sha256-../../../etc/passwd",
            "/artifacts/sha256-" + "0" * 64,
            "/artifacts/sha256-" + "0" * 63,
            "/artifacts/%00",
            "/reports/%2e%2e%2fsecrets/files/json",
            "/reports/x/files/..%2f..%2fetc%2fpasswd",
            "/reports/x/files/json%00.html",
        ):
            r = await c.get(url)
            assert r.status_code in {400, 404, 422}, (url, r.status_code)
            assert "root:" not in r.text


# =============================================================================== secrets sent inline
INLINE_SECRETS = [
    ("api.headers", {"api": {"url": "http://x.example/chat", "headers": {"Authorization": "Bearer abc"}}}),
    ("api.headers", {"api": {"url": "http://x.example/chat", "headers": {"X-API-Key": "abc"}}}),
    ("api.headers", {"api": {"url": "http://x.example/chat", "headers": {"Cookie": "session=abc"}}}),
    (
        "api.headers",
        {"api": {"url": "http://x.example/chat", "headers": {"X-Session": SECRET}}},
    ),  # innocent name, secret value
    ("api.headers", {"api": {"url": "http://x.example/chat", "headers": {"X-Client-Secret": "abc"}}}),
    (
        "mcp.headers",
        {
            "mcp": {
                "transport": "streamable_http",
                "url": "http://x.example/mcp",
                "headers": {"Authorization": "Bearer abc"},
            }
        },
    ),
    ("mcp.env", {"mcp": {"transport": "stdio", "command": ["server"], "env": {"API_KEY": "abc"}}}),
    ("command.env", {"command": {"command": ["agent"], "env": {"DB_PASSWORD": "abc"}}}),
    ("command.env", {"command": {"command": ["agent"], "env": {"PLAIN": SECRET}}}),
]


@pytest.mark.parametrize(("where", "spec"), INLINE_SECRETS, ids=[f"{w}-{i}" for i, (w, _) in enumerate(INLINE_SECRETS)])
async def test_a_secret_sent_inside_a_target_definition_is_refused_not_stored(
    tmp_path: Path, where: str, spec: dict[str, Any]
) -> None:
    async with running_api(tmp_path) as api:
        c = api.client
        for endpoint, body in (
            ("/targets", {"spec": {"name": "t", **spec}}),
            ("/discover", {"target": {"name": "t", **spec}}),
            ("/test-plans", {"target": {"name": "t", **spec}}),
            ("/test-runs", {"target": {"name": "t", **spec}}),
        ):
            r = await c.post(endpoint, json=body)
            assert r.status_code == 403, (endpoint, r.status_code, r.text[:300])
            err = r.json()["error"]
            assert err["kind"] == "policy_blocked" and where in err["message"], err
            assert "POST /credentials" in err["message"], "it says where a secret belongs"
            assert SECRET not in r.text and "Bearer abc" not in r.text
        assert (await c.get("/targets")).json() == []
        assert SECRET.encode() not in b"\n".join(p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())


async def test_a_header_value_that_could_split_the_request_is_refused_and_ordinary_headers_are_kept(
    tmp_path: Path,
) -> None:
    async with running_api(tmp_path) as api:
        c = api.client
        for value in ("a\r\nX-Injected: 1", "a\nb", "a\x00b"):
            r = await c.post(
                "/targets",
                json={"spec": {"name": "t", "api": {"url": "http://x.example/c", "headers": {"X-Trace": value}}}},
            )
            assert r.status_code == 422 and "line break" in r.json()["error"]["message"], repr(value)
        bad_name = await c.post(
            "/targets", json={"spec": {"name": "t", "api": {"url": "http://x.example/c", "headers": {"X Bad:": "v"}}}}
        )
        assert bad_name.status_code == 422
        fine = await c.post(
            "/targets",
            json={
                "spec": {
                    "name": "t",
                    "api": {"url": "http://x.example/c", "headers": {"X-Trace": "abc", "Accept": "application/json"}},
                }
            },
        )
        assert fine.status_code == 201, fine.text
        assert fine.json()["spec"]["api"]["headers"]["X-Trace"] == "abc"


# =========================================================================================== evidence
async def test_restricted_evidence_needs_a_deliberate_request_and_a_document_is_never_a_page_of_the_server(
    tmp_path: Path,
) -> None:
    async with running_api(tmp_path) as api:
        c = api.client
        store = api.services.artifacts
        normal = store.put(b"hello", kind="note", media_type="text/plain", name="note.txt")
        restricted = store.put(
            b"SCREENSHOT-BYTES-OF-A-SIGNED-IN-PAGE",
            kind="screenshot",
            media_type="image/png",
            name="shot.png",
            sensitivity="restricted",
        )
        page = store.put(
            "<script>alert(1)</script><h1>from a target</h1>", kind="snapshot", media_type="text/html", name="page.html"
        )
        svg = store.put(
            '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>',
            kind="snapshot",
            media_type="image/svg+xml",
            name="x.svg",
        )
        blob = store.put(b"\x00\x01binary", kind="blob", media_type="application/octet-stream", name="b.bin")

        assert (await c.get(f"/artifacts/{normal.id}")).content == b"hello"
        refused = await c.get(f"/artifacts/{restricted.id}")
        assert refused.status_code == 403 and "restricted" in refused.json()["error"]["message"]
        assert b"SCREENSHOT-BYTES" not in refused.content
        allowed = await c.get(f"/artifacts/{restricted.id}", params={"include_restricted": "true"})
        assert allowed.status_code == 200 and allowed.content == b"SCREENSHOT-BYTES-OF-A-SIGNED-IN-PAGE"

        for ref in (page, svg):
            shown = await c.get(f"/artifacts/{ref.id}")
            assert shown.headers["content-disposition"].startswith("attachment"), "downloaded, not shown, unless asked"
            assert (
                "sandbox" in shown.headers["content-security-policy"]
                and "default-src 'none'" in shown.headers["content-security-policy"]
            )
            inline = await c.get(f"/artifacts/{ref.id}", params={"inline": "true"})
            assert inline.headers["content-disposition"].startswith("inline")
            assert "sandbox" in inline.headers["content-security-policy"], "even when shown, it runs with no rights"
            assert inline.headers["x-content-type-options"] == "nosniff"
        assert (
            (await c.get(f"/artifacts/{blob.id}", params={"inline": "true"}))
            .headers["content-disposition"]
            .startswith("attachment")
        )
        assert 'filename="note.txt"' in (await c.get(f"/artifacts/{normal.id}")).headers["content-disposition"]


async def test_the_name_of_a_downloaded_file_cannot_break_out_of_the_header(tmp_path: Path) -> None:
    async with running_api(tmp_path) as api:
        ref = api.services.artifacts.put(
            b"x", kind="note", media_type="text/plain", name='a"; filename="../../evil.sh\r\nX-Injected: 1'
        )
        r = await api.client.get(f"/artifacts/{ref.id}")
        assert r.status_code == 200
        disposition = r.headers["content-disposition"]
        assert "\r" not in disposition and "\n" not in disposition and "../" not in disposition
        assert "x-injected" not in r.headers


# ======================================================================================== what leaks
async def test_error_messages_and_the_log_never_contain_a_secret_that_was_sent(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async with running_api(tmp_path) as api:
        c = api.client
        for r in [
            await c.post(
                "/test-runs",
                json={"target": {"name": "t", "api": {"url": "http://x.example/c", "headers": {"X-Session": SECRET}}}},
            ),
            await c.post("/credentials", json={"name": "bad name", "scopes": ["a"], "secrets": {"token": SECRET}}),
            await c.post(
                "/credentials", json={"name": "ok", "kind": "basic", "scopes": ["a"], "secrets": {"password": SECRET}}
            ),
            await c.get(f"/test-runs/{SECRET}"),
            await c.get(f"/skills/{SECRET}"),
            await c.get("/models", params={"provider": SECRET}),
        ]:
            assert r.status_code >= 400
            assert SECRET not in r.text, r.text
        assert SECRET not in caplog.text
    assert os.environ.get("AGENTLAB_TEST_NEVER_SET") is None
