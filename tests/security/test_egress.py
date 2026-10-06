"""AgentLab makes requests on behalf of whatever it is pointed at: a target URL, an OpenAPI ``servers`` entry, a link in a
document, a redirect. None of those may steer the *evaluator host* to its cloud metadata service or, when the owner says
so, to a private network (spec section 10)."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from pydantic import ValidationError

from agentlab.core.config import SandboxConfig, SecurityConfig
from agentlab.core.errors import PolicyBlocked, UserError
from agentlab.core.models import ApiConfig, RepositorySource, TargetSpec
from agentlab.orchestrator import RunOptions
from agentlab.repository.ingest import RepositoryIngestor
from agentlab.security.egress import EgressPolicy
from tests.support.lab import Lab
from tests.support.servers import serve

METADATA_URLS = [
    "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
    "http://169.254.169.254:80/",
    "https://169.254.169.254/",
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://METADATA.GOOGLE.INTERNAL./computeMetadata/v1/".replace("INTERNAL.", "INTERNAL"),
    "http://metadata/computeMetadata/v1/",
    "http://[fd00:ec2::254]/latest/meta-data/",
    "http://100.100.100.200/latest/meta-data/",
    # the same address written the ways a URL parser and a resolver accept
    "http://2852039166/",  # decimal
    "http://0xa9fea9fe/",  # hexadecimal
    "http://0251.0376.0251.0376/",  # octal
    "http://169.254.43518/",  # mixed
    "http://[::ffff:169.254.169.254]/",  # an IPv4 address inside an IPv6 one
    "http://[::ffff:a9fe:a9fe]/",
    "http://[fe80::a00:27ff:fe4e:66a1]/",  # IPv6 link-local
]


@pytest.mark.parametrize("url", METADATA_URLS)
def test_cloud_metadata_endpoints_are_unreachable_however_the_address_is_written(url: str) -> None:
    with pytest.raises(PolicyBlocked):
        EgressPolicy().check(url)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://127.0.0.1:6379/_PING",
        "ftp://example.com/x",
        "javascript:alert(1)",
        "data:text/plain,hi",
    ],
)
def test_only_web_schemes_are_ever_requested(url: str) -> None:
    with pytest.raises(PolicyBlocked):
        EgressPolicy().check(url)


@pytest.mark.parametrize("url", ["http://", "http:///path", "https://:443/"])
def test_a_url_without_a_host_is_refused(url: str) -> None:
    with pytest.raises(PolicyBlocked, match="no host"):
        EgressPolicy().check(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/chat",
        "http://localhost:8000/chat",
        "http://[::1]:8000/chat",
        "http://10.1.2.3/chat",
        "http://192.168.0.10/chat",
        "http://172.16.5.4/chat",
        "http://0.0.0.0:8000/",
        "http://[::ffff:127.0.0.1]:8000/",
        "http://[::ffff:10.0.0.1]/",
    ],
)
def test_private_networks_can_be_forbidden_and_are_allowed_by_default(url: str) -> None:
    EgressPolicy().check(url)  # a developer's own machine and a company network are normal targets...
    with pytest.raises(PolicyBlocked, match="private"):
        EgressPolicy(allow_private=False).check(url)  # ...unless the owner of this installation says they are not


def test_a_hostname_that_resolves_to_the_metadata_service_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The name looks harmless; where it points is what matters (the classic DNS trick against an SSRF filter)."""

    def fake(host: str, *_a: Any, **_k: Any) -> list[tuple[Any, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    with pytest.raises(PolicyBlocked, match="link-local/metadata"):
        EgressPolicy().check("http://innocent.example.org/")


def test_one_bad_answer_among_several_is_enough_to_refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(host: str, *_a: Any, **_k: Any) -> list[tuple[Any, ...]]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    with pytest.raises(PolicyBlocked):
        EgressPolicy().check("http://two-faced.example.org/")


def test_the_metadata_block_is_on_by_default_and_only_an_explicit_opt_out_lifts_it() -> None:
    assert EgressPolicy().block_metadata is True and SecurityConfig().block_metadata_endpoints is True
    EgressPolicy(block_metadata=False).check("http://169.254.169.254/")


def test_a_name_that_does_not_resolve_is_left_to_fail_as_a_target_error() -> None:
    EgressPolicy().check("http://this-name-does-not-exist.invalid/")


# ======================================================================================== the places that use it
async def test_a_repository_url_is_checked_before_anything_is_fetched_and_ssh_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agentlab.repository.ingest as ingest

    async def never(*_a: Any, **_k: Any) -> None:
        pytest.fail("git must not be started for an address the policy refuses")

    monkeypatch.setattr(ingest, "_git_clone", never)
    for url in ("http://github.com/org/repo.git", "file:///srv/repo.git", "ext::sh -c 'touch /tmp/pwned'", "git://x/y"):
        with pytest.raises(ValidationError, match="must use https"):
            RepositorySource(url=url)  # the description of a repository already refuses every scheme but https
    ingestor = RepositoryIngestor(tmp_path, EgressPolicy())
    for url in ("https://169.254.169.254/latest/repo.git", "https://metadata.google.internal/repo.git"):
        with pytest.raises(PolicyBlocked, match="metadata"):
            await ingestor.ingest(RepositorySource(url=url))
    with pytest.raises(UserError, match="ssh repository URLs are not supported"):
        await ingestor.ingest(RepositorySource(url="git@github.com:org/repo.git"))
    assert not list(tmp_path.glob("agentlab-repo-*")), "nothing is left behind by a refused import"


async def test_a_target_in_a_forbidden_network_is_refused_before_a_single_request_is_made(tmp_path: Path) -> None:
    hits: list[str] = []
    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST", "OPTIONS", "HEAD"])
    async def take(path: str) -> dict[str, str]:
        hits.append(path)
        return {"output": "hi"}

    config = SecurityConfig(allow_private_networks=False, sandbox=SandboxConfig(provider="disabled"))
    async with Lab(tmp_path, security=config) as lab:
        with serve(app) as srv, pytest.raises(PolicyBlocked, match="private"):
            await lab.run(
                TargetSpec(name="inside", api=ApiConfig(url=srv.url + "/chat")), RunOptions(intensity="quick")
            )
    assert not hits, "the policy was applied before the target was contacted"


async def test_an_api_that_redirects_to_the_metadata_service_is_stopped_at_the_redirect(tmp_path: Path) -> None:
    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST", "OPTIONS", "HEAD"])
    async def bounce(path: str) -> RedirectResponse:
        return RedirectResponse("http://169.254.169.254/latest/meta-data/", status_code=307)

    async with Lab(tmp_path) as lab:
        with serve(app) as srv:
            out = await lab.run(
                TargetSpec(name="bounces", api=ApiConfig(url=srv.url + "/chat")),
                RunOptions(intensity="quick", suite="functional", second_wave=False, only_tests=["CONV-GREETING-*"]),
            )
    (result,) = out.results
    assert result.status.value in {"blocked", "error"}, "a refused address is not a verdict on the agent"
    assert "metadata" in (result.blocked_reason or result.attempts[0].error or ""), result
    assert not out.findings
