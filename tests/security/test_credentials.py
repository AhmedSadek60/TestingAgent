"""Credentials are the most valuable thing AgentLab ever holds, so the properties that protect them are tested as
properties: where a secret may be sent, how it is stored, and what a failure says about it (spec sections 2E and 11).

Every secret below is invented and assembled from fragments so no scanner mistakes this file for a leak."""

from __future__ import annotations

import os
import stat
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from agentlab.core.errors import CredentialError
from agentlab.core.ids import utcnow
from agentlab.core.models import ApiConfig, TargetSpec
from agentlab.orchestrator import RunOptions
from agentlab.security.credentials import CredentialManager, CredentialProfile, EncryptedSecretStore
from agentlab.security.redactor import SecretRedactor
from tests.support.lab import Lab, everything_written
from tests.support.servers import serve

TOKEN = "tok-" + "Zq81LmN4xW7sR2pK"  # a made-up bearer token
OTHER = "pw-" + "Vb39TcHy5eU0dJ1a"


def manager(tmp_path: Path, **kw: Any) -> CredentialManager:
    store = EncryptedSecretStore(tmp_path / "secrets.enc", key=kw.pop("key", None))
    return CredentialManager(store, redactor=SecretRedactor())


# ================================================================================ where a credential may be sent
SCOPES = [
    # a host scope: that host and its subdomains, nothing that merely looks like it
    ("example.com", "https://example.com/chat", True),
    ("example.com", "https://api.example.com/chat", True),
    ("*.example.com", "https://api.example.com/x", True),
    ("EXAMPLE.com", "https://Example.COM/", True),
    ("example.com", "https://evilexample.com/", False),
    ("example.com", "https://example.com.evil.net/", False),
    ("example.com", "https://evil.net/?next=example.com", False),
    ("example.com", "https://evil.net/example.com", False),
    ("example.com", "https://example.com@evil.net/", False),
    ("example.com", "https://user:pass@evil.net/", False),
    ("10.0.0.5", "http://10.0.0.5:8080/", True),
    ("10.0.0.5", "http://10.0.0.50/", False),
    ("127.0.0.1", "http://127.0.0.1.evil.net/", False),
    # a URL scope: the scheme, the host (exactly), the port and the path (on a segment boundary) all have to match
    ("https://api.example.com", "https://api.example.com/v1/chat", True),
    ("https://api.example.com", "https://api.example.com", True),
    ("https://api.example.com", "https://api.example.com.evil.net/v1", False),
    ("https://api.example.com", "https://api.example.com@evil.net/v1", False),
    ("https://api.example.com", "https://api.example.com:8443/v1", False),
    ("https://api.example.com", "http://api.example.com/v1", False),
    ("http://localhost:8080", "http://localhost:8080/chat", True),
    ("http://localhost:8080", "http://localhost:80801/chat", False),
    ("http://localhost:8080", "http://localhost:8080.evil.net/chat", False),
    ("https://example.com/api", "https://example.com/api/chat", True),
    ("https://example.com/api", "https://example.com/api", True),
    ("https://example.com/api", "https://example.com/apix", False),
    ("https://example.com/api", "https://example.com/other", False),
    ("https://example.com/api/", "https://example.com/api/chat", True),
]


@pytest.mark.parametrize(("scope", "url", "allowed"), SCOPES)
def test_a_credential_is_released_only_to_the_places_it_is_scoped_to(
    tmp_path: Path, scope: str, url: str, allowed: bool
) -> None:
    creds = manager(tmp_path)
    creds.add(CredentialProfile(name="staff", kind="bearer", scopes=[scope]), {"token": TOKEN})
    if allowed:
        assert creds.auth_headers("staff", url) == {"Authorization": f"Bearer {TOKEN}"}
    else:
        with pytest.raises(CredentialError, match="not scoped") as caught:
            creds.auth_headers("staff", url)
        assert TOKEN not in str(caught.value), "the refusal does not repeat the secret"


def test_a_credential_with_several_scopes_is_released_when_any_one_matches(tmp_path: Path) -> None:
    creds = manager(tmp_path)
    creds.add(
        CredentialProfile(name="staff", kind="bearer", scopes=["a.example.com", "b.example.org"]), {"token": TOKEN}
    )
    assert creds.auth_headers("staff", "https://b.example.org/x")
    with pytest.raises(CredentialError):
        creds.auth_headers("staff", "https://c.example.net/x")


def test_every_credential_kind_is_scoped_the_same_way(tmp_path: Path) -> None:
    creds = manager(tmp_path)
    kinds = {
        "bearer": {"token": TOKEN},
        "oauth_token": {"token": TOKEN},
        "api_key": {"key": TOKEN},
        "basic": {"username": "tester", "password": OTHER},
        "cookies": {"session": TOKEN},
        "headers": {"X-Custom": TOKEN},
    }
    for kind, secrets in kinds.items():
        creds.add(CredentialProfile(name=kind, kind=kind, scopes=["good.example.com"]), secrets)  # type: ignore[arg-type]
        assert creds.auth_headers(kind, "https://good.example.com/")
        with pytest.raises(CredentialError, match="not scoped"):
            creds.auth_headers(kind, "https://bad.example.net/")
        with pytest.raises(CredentialError, match="not scoped"):
            creds.fields(kind, "https://bad.example.net/")


def test_a_browser_state_credential_is_scoped_private_and_deleted_after_use(tmp_path: Path) -> None:
    creds = manager(tmp_path)
    state = '{"cookies": [{"name": "sid", "value": "' + TOKEN + '"}]}'
    creds.add(CredentialProfile(name="session", kind="browser_state", scopes=["shop.example.com"]), {"state": state})
    with pytest.raises(CredentialError, match="not scoped"), creds.browser_state_file("session", "https://evil.net/"):
        pytest.fail("a state file must not be materialised for a host outside the scope")
    with creds.browser_state_file("session", "https://shop.example.com/") as path:
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600, "only the owner can read the materialised session"
        assert TOKEN in Path(path).read_text()
    assert not os.path.exists(path), "the file is gone as soon as the browser is done"
    with (
        pytest.raises(RuntimeError, match="boom"),
        creds.browser_state_file("session", "https://shop.example.com/") as path,
    ):
        raise RuntimeError("boom")
    assert not os.path.exists(path), "...even when the test blows up"


# ========================================================================================== how it is stored
def test_a_secret_is_stored_encrypted_and_the_files_are_private(tmp_path: Path) -> None:
    creds = manager(tmp_path)
    creds.add(CredentialProfile(name="staff", kind="bearer", scopes=["example.com"]), {"token": TOKEN})
    store_file, key_file = tmp_path / "secrets.enc", tmp_path / "secrets.key"
    assert TOKEN.encode() not in store_file.read_bytes(), "the secret is not in the store in the clear"
    assert "staff" not in store_file.read_text(errors="replace"), "not even the profile names are readable"
    for path in (store_file, key_file):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, f"{path.name} is readable by its owner only"
    assert CredentialManager(EncryptedSecretStore(store_file), redactor=SecretRedactor()).auth_headers("staff")


def test_a_key_file_other_users_can_read_is_refused(tmp_path: Path) -> None:
    manager(tmp_path).add(CredentialProfile(name="x", kind="bearer"), {"token": TOKEN})
    (tmp_path / "secrets.key").chmod(0o644)
    with pytest.raises(CredentialError, match="group/world accessible"):
        EncryptedSecretStore(tmp_path / "secrets.enc")


def test_the_wrong_key_opens_nothing_and_says_nothing_about_the_contents(tmp_path: Path) -> None:
    from cryptography.fernet import Fernet

    manager(tmp_path).add(CredentialProfile(name="staff", kind="bearer"), {"token": TOKEN})
    with pytest.raises(CredentialError, match="could not be decrypted") as caught:
        CredentialManager(EncryptedSecretStore(tmp_path / "secrets.enc", key=Fernet.generate_key()))
    assert TOKEN not in str(caught.value) and "staff" not in str(caught.value)


def test_a_tampered_store_is_rejected_rather_than_trusted(tmp_path: Path) -> None:
    manager(tmp_path).add(CredentialProfile(name="staff", kind="bearer"), {"token": TOKEN})
    path = tmp_path / "secrets.enc"
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0x01
    path.write_bytes(bytes(data))
    with pytest.raises(CredentialError, match="could not be decrypted"):
        CredentialManager(EncryptedSecretStore(path))


def test_the_master_key_can_come_from_the_environment_and_then_no_key_file_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AGENTLAB_MASTER_KEY", Fernet.generate_key().decode())
    creds = manager(tmp_path)
    creds.add(CredentialProfile(name="staff", kind="bearer"), {"token": TOKEN})
    assert not (tmp_path / "secrets.key").exists()
    assert CredentialManager(EncryptedSecretStore(tmp_path / "secrets.enc"), redactor=SecretRedactor()).has("staff")


def test_a_profile_never_shows_its_secret_when_it_is_listed_or_dumped(tmp_path: Path) -> None:
    creds = manager(tmp_path)
    creds.add(CredentialProfile(name="staff", kind="bearer", scopes=["example.com"]), {"token": TOKEN})
    creds.add(CredentialProfile(name="from-env", kind="bearer", references={"token": "env:AGENTLAB_SEC_TEST"}), None)
    listing = repr(creds.list_profiles()) + creds.get_profile("staff").model_dump_json()
    assert TOKEN not in listing and "token" not in repr(creds.get_profile("staff").public_view().get("fields", ""))
    assert creds.get_profile("from-env").public_view()["fields"] == ["token"], "field names only"


def test_a_reference_must_point_at_the_environment_and_a_missing_variable_is_a_setup_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    creds = manager(tmp_path)
    with pytest.raises(CredentialError, match="env:NAME"):
        creds.add(CredentialProfile(name="raw", kind="bearer", references={"token": TOKEN}))
    monkeypatch.delenv("AGENTLAB_SEC_TEST", raising=False)
    creds.add(CredentialProfile(name="ref", kind="bearer", references={"token": "env:AGENTLAB_SEC_TEST"}))
    with pytest.raises(CredentialError, match="AGENTLAB_SEC_TEST .* is not set"):
        creds.auth_headers("ref")
    monkeypatch.setenv("AGENTLAB_SEC_TEST", TOKEN)
    assert creds.auth_headers("ref") == {"Authorization": f"Bearer {TOKEN}"}
    assert TOKEN.encode() not in (tmp_path / "secrets.enc").read_bytes(), "a referenced value is never copied to disk"


def test_an_expired_credential_is_refused_and_a_missing_one_says_what_to_do(tmp_path: Path) -> None:
    creds = manager(tmp_path)
    creds.add(
        CredentialProfile(name="old", kind="bearer", expires_at=utcnow() - timedelta(minutes=1)), {"token": TOKEN}
    )
    with pytest.raises(CredentialError, match="expired"):
        creds.auth_headers("old")
    with pytest.raises(CredentialError, match=r"agentlab credentials add nobody"):
        creds.auth_headers("nobody")


def test_rotating_a_secret_replaces_it_and_both_values_stay_masked(tmp_path: Path) -> None:
    creds = manager(tmp_path)
    creds.add(CredentialProfile(name="staff", kind="bearer"), {"token": TOKEN})
    creds.rotate("staff", {"token": OTHER})
    assert creds.auth_headers("staff") == {"Authorization": f"Bearer {OTHER}"}
    assert creds.get_profile("staff").secret_version == 2 and len(creds.get_profile("staff").rotated_at) == 1
    assert OTHER.encode() not in (tmp_path / "secrets.enc").read_bytes()
    masked = creds.redactor.redact_text(f"old {TOKEN} new {OTHER}")[0]
    assert TOKEN not in masked and OTHER not in masked


def test_a_resolved_credential_is_masked_in_everything_that_is_written_afterwards(tmp_path: Path) -> None:
    creds = manager(tmp_path)
    creds.add(CredentialProfile(name="staff", kind="basic"), {"username": "tester", "password": OTHER})
    creds.auth_headers("staff")
    for shape in (OTHER, f"password={OTHER}", f"https://tester:{OTHER}@host/x"):
        assert OTHER not in creds.redactor.redact_text(shape)[0]
    import base64

    encoded = base64.b64encode(f"tester:{OTHER}".encode()).decode()
    assert encoded not in creds.redactor.redact_text(f"Authorization: Basic {encoded}")[0]


# ============================================================================== the credential in a live run
def _redirector(to: str) -> FastAPI:
    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST", "OPTIONS", "HEAD"])
    async def go(path: str) -> RedirectResponse:  # noqa: ARG001
        return RedirectResponse(to, status_code=307)  # keeps the method and the body

    return app


def _recorder(seen: list[dict[str, Any]], *, moved: str | None = None) -> FastAPI:
    """Answers every request and notes the headers it came with; ``moved="/old"`` first redirects ``/old/x`` to ``/new/x``."""
    app = FastAPI()
    if moved:

        @app.api_route(moved + "/{path:path}", methods=["GET", "POST", "OPTIONS", "HEAD"])
        async def go(path: str) -> RedirectResponse:
            return RedirectResponse(f"/new/{path}", status_code=307)

    @app.api_route("/{path:path}", methods=["GET", "POST", "OPTIONS", "HEAD"])
    async def take(request: Request, path: str) -> dict[str, str]:
        seen.append({"path": path, "headers": {k.lower(): v for k, v in request.headers.items()}})
        return {"output": "hello"}

    return app


async def test_a_redirect_to_another_origin_never_carries_the_credential(tmp_path: Path) -> None:
    """A target (or an open redirect on it) can answer 307 with a Location of its choosing. The credential was released
    for the target's own address; following that redirect with the same headers would hand it to a third party."""
    seen: list[dict[str, Any]] = []
    async with Lab(tmp_path) as lab:
        lab.services.credentials.add(
            CredentialProfile(name="staff", kind="bearer", scopes=["127.0.0.1"]), {"token": TOKEN}
        )
        lab.services.credentials.add(
            CredentialProfile(name="key", kind="api_key", scopes=["127.0.0.1"], header_name="X-Api-Key"),
            {"key": OTHER},
        )
        with serve(_recorder(seen)) as elsewhere, serve(_redirector(elsewhere.url + "/collect")) as origin:
            for credential in ("staff", "key"):
                spec = TargetSpec(
                    name=f"redirects-{credential}",
                    api=ApiConfig(url=origin.url + "/chat", auth_credential=credential),
                )
                await lab.run(
                    spec,
                    RunOptions(
                        intensity="quick", suite="functional", second_wave=False, only_tests=["CONV-GREETING-*"]
                    ),
                )
        assert seen, "the redirect was followed, so the check below looked at real requests"
        for request in seen:
            assert "authorization" not in request["headers"] and "x-api-key" not in request["headers"], request
        assert TOKEN.encode() not in everything_written(lab) and OTHER.encode() not in everything_written(lab)


async def test_a_redirect_that_stays_on_the_target_keeps_the_credential(tmp_path: Path) -> None:
    """Stripping is for other origins only: a target that moves its endpoint (``/chat`` -> ``/v2/chat``) still gets the
    credential it was released for, or every authenticated test would fail for a reason that is not the agent's."""
    seen: list[dict[str, Any]] = []
    app = _recorder(seen, moved="/old")

    async with Lab(tmp_path) as lab:
        lab.services.credentials.add(
            CredentialProfile(name="staff", kind="bearer", scopes=["127.0.0.1"]), {"token": TOKEN}
        )
        with serve(app) as srv:
            spec = TargetSpec(name="moved", api=ApiConfig(url=srv.url + "/old/chat", auth_credential="staff"))
            await lab.run(
                spec,
                RunOptions(intensity="quick", suite="functional", second_wave=False, only_tests=["CONV-GREETING-*"]),
            )
    landed = [r for r in seen if r["path"].startswith("new/")]
    assert landed and all(r["headers"].get("authorization") == f"Bearer {TOKEN}" for r in landed)
