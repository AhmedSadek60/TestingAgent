"""An MCP server is one more address a credential is released for, and a server (or an open redirect on it) chooses where
its redirects lead. The MCP SDK decides which redirects its transports follow, so the guarantee AgentLab depends on is
pinned here and fails loudly if a newer SDK changes it (spec sections 2E and 11).

Every secret below is invented and assembled from fragments so no scanner mistakes this file for a leak."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from agentlab.adapters.base import AdapterContext
from agentlab.adapters.mcp import McpAdapter
from agentlab.core.config import AgentLabConfig
from agentlab.core.errors import CredentialError, PolicyBlocked, TargetError
from agentlab.core.models import TargetSpec
from agentlab.security.credentials import CredentialManager, CredentialProfile, EncryptedSecretStore
from agentlab.security.egress import EgressPolicy
from agentlab.security.redactor import SecretRedactor
from tests.support.servers import serve

TOKEN = "tok-" + "Mc91LpN4xW7sR2qZ"  # a made-up bearer token


def recorder(seen: list[dict[str, Any]]) -> FastAPI:
    """Notes every request it receives, with the headers it came with, and refuses it."""
    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST", "DELETE", "OPTIONS", "HEAD"], status_code=404)
    async def take(request: Request, path: str) -> dict[str, str]:
        seen.append(
            {"method": request.method, "path": path, "headers": {k.lower(): v for k, v in request.headers.items()}}
        )
        return {"error": "not an MCP server"}

    return app


def redirector(to: str, hits: list[str]) -> FastAPI:
    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST", "DELETE", "OPTIONS", "HEAD"])
    async def go(request: Request, path: str) -> RedirectResponse:  # noqa: ARG001
        hits.append(request.method)
        return RedirectResponse(to, status_code=307)  # keeps the method and the body

    return app


def credentials(tmp_path: Path) -> CredentialManager:
    manager = CredentialManager(EncryptedSecretStore(tmp_path / "secrets.enc"), redactor=SecretRedactor())
    manager.add(CredentialProfile(name="staff", kind="bearer", scopes=["127.0.0.1"]), {"token": TOKEN})
    return manager


@pytest.mark.parametrize("transport", ["streamable_http", "sse"])
async def test_an_mcp_endpoint_that_redirects_elsewhere_never_receives_the_credential(
    tmp_path: Path, transport: str
) -> None:
    """The credential was released for the configured address (the loopback host, here). Another origin is a third
    party, whatever the first server says, and must not be sent the bearer token in a header or anywhere else."""
    seen: list[dict[str, Any]] = []
    hits: list[str] = []
    with serve(recorder(seen)) as elsewhere, serve(redirector(elsewhere.url + "/collect", hits)) as origin:
        spec = TargetSpec(
            name="mcp-redirect",
            mcp={"transport": transport, "url": origin.url + "/mcp", "auth_credential": "staff", "timeout_seconds": 10},  # type: ignore[arg-type]
        )
        adapter = McpAdapter(spec, AdapterContext(config=AgentLabConfig(), credentials=credentials(tmp_path)))
        with pytest.raises(TargetError, match="cannot connect") as failure:
            await adapter.open()
        await adapter.close()
    assert hits, "the first server did redirect, so the check below looked at a real redirect"
    assert not [r for r in seen if "authorization" in r["headers"]], seen
    assert TOKEN not in str(failure.value), "an error message never repeats the credential"
    assert not seen, f"a redirect to another origin is not followed at all: {seen}"


@pytest.mark.parametrize("transport", ["streamable_http", "sse"])
async def test_an_mcp_credential_is_only_released_for_the_address_it_was_scoped_to(
    tmp_path: Path, transport: str
) -> None:
    manager = credentials(tmp_path)
    manager.add(
        CredentialProfile(name="elsewhere", kind="bearer", scopes=["only.example.net"]), {"token": TOKEN + "-other"}
    )
    spec = TargetSpec(
        name="mcp-scope",
        mcp={"transport": transport, "url": "http://127.0.0.1:9/mcp", "auth_credential": "elsewhere"},  # type: ignore[arg-type]
    )
    adapter = McpAdapter(spec, AdapterContext(config=AgentLabConfig(), credentials=manager))
    with pytest.raises(CredentialError, match="not scoped for host '127.0.0.1'") as refused:
        await adapter.open()
    assert TOKEN not in str(refused.value)


async def test_the_default_egress_policy_still_guards_an_mcp_server_that_names_a_metadata_address() -> None:
    spec = TargetSpec(name="mcp-metadata", mcp={"transport": "sse", "url": "http://169.254.169.254/latest/sse"})  # type: ignore[arg-type]
    adapter = McpAdapter(spec, AdapterContext(config=AgentLabConfig(), egress=EgressPolicy()))
    with pytest.raises(PolicyBlocked):
        await adapter.open()
