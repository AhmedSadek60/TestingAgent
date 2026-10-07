"""A target given only as an OpenAPI document is tested at the endpoint the document describes, with the request shape the
document gives, and at no other host.

Before this, ``agentlab test --openapi URL`` made the *document's own address* the agent's address and POSTed every test
message to ``/openapi.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import BaseModel
from typer.testing import CliRunner

from agentlab.cli.main import app as cli
from agentlab.core.enums import TestStatus
from agentlab.core.models import ApiConfig, TargetSpec
from agentlab.orchestrator import RunOptions
from tests.integration.test_cli import CONFIG
from tests.support.lab import Lab
from tests.support.servers import serve

ONE_TEST = RunOptions(
    intensity="quick", suite="functional", second_wave=False, probe=False, only_tests=["CONV-GREETING-001"]
)


class AskRequest(BaseModel):
    question: str
    conversation_id: str | None = None


class AskResponse(BaseModel):
    answer: str


def asker(seen: list[dict[str, Any]], *, servers: list[dict[str, str]] | None = None) -> FastAPI:
    """An agent whose chat endpoint is neither /chat nor takes ``input``: only its OpenAPI document says how to talk to it."""
    app = FastAPI(title="Asker", servers=servers)

    @app.post("/v1/ask", response_model=AskResponse)
    async def ask(body: AskRequest) -> AskResponse:
        seen.append(body.model_dump())
        return AskResponse(answer="Hello! I can help with orders and returns. What do you need?")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def recorder() -> tuple[FastAPI, list[str]]:
    """A server that only records what reaches it."""
    hits: list[str] = []
    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST", "OPTIONS", "HEAD"])
    async def any_request(path: str) -> dict[str, str]:
        hits.append(path)
        return {"answer": "hello"}

    return app, hits


async def test_the_tests_talk_to_the_endpoint_the_document_describes_in_the_shape_it_describes(tmp_path: Path) -> None:
    seen: list[dict[str, Any]] = []
    with serve(asker(seen)) as server:
        async with Lab(tmp_path) as lab:
            outcome = await lab.run(
                TargetSpec(name="asker", api=ApiConfig(openapi_url=server.url + "/openapi.json")), ONE_TEST
            )
    assert [r.status for r in outcome.results] == [TestStatus.PASSED], outcome.warnings
    assert seen, "the question reached POST /v1/ask"
    assert all(set(body) == {"question", "conversation_id"} for body in seen), seen
    assert any(f"tests go to POST {server.url}/v1/ask" in w for w in outcome.warnings), outcome.warnings
    assert any("the request carries" in w and "question" in w for w in outcome.warnings)


async def test_a_server_the_document_names_on_another_host_is_never_sent_a_test(tmp_path: Path) -> None:
    other_app, other_hits = recorder()
    seen: list[dict[str, Any]] = []
    # the document points at the other server, as a hostile or merely misconfigured document could
    with serve(other_app) as other, serve(asker(seen, servers=[{"url": other.url}])) as server:
        async with Lab(tmp_path) as lab:
            outcome = await lab.run(
                TargetSpec(name="asker", api=ApiConfig(openapi_url=server.url + "/openapi.json")), ONE_TEST
            )
    assert other_hits == [], f"nothing may reach a host the target did not name: {other_hits}"
    assert not seen
    assert any(
        "tests are not sent to a host the target does not name" in w and "--api-url" in w for w in outcome.warnings
    )
    assert not outcome.tested, "no address, so nothing was tested and no verdict is given"
    assert all(r.status == TestStatus.BLOCKED for r in outcome.results)


async def test_confirming_the_address_with_api_url_tests_it_and_still_uses_the_documents_request_shape(
    tmp_path: Path,
) -> None:
    seen: list[dict[str, Any]] = []
    with serve(asker(seen)) as server:
        async with Lab(tmp_path) as lab:
            outcome = await lab.run(
                TargetSpec(
                    name="asker",
                    api=ApiConfig(url=server.url + "/v1/ask", openapi_url=server.url + "/openapi.json"),
                ),
                ONE_TEST,
            )
    assert [r.status for r in outcome.results] == [TestStatus.PASSED], outcome.warnings
    assert all(set(body) == {"question", "conversation_id"} for body in seen)


# ----------------------------------------------------------------------------------------------------- the CLI
@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "agentlab.yaml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "skills").mkdir()
    monkeypatch.chdir(tmp_path)
    for var in ("AGENTLAB_CONFIG", "AGENTLAB_MASTER_KEY"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def test_agentlab_test_with_only_openapi_plans_and_runs_against_the_documented_endpoint(project: Path) -> None:
    seen: list[dict[str, Any]] = []
    runner = CliRunner()
    with serve(asker(seen)) as server:
        doc = server.url + "/openapi.json"
        planned = runner.invoke(cli, ["test", "--openapi", doc, "--plan-only", "--json", "--intensity", "quick"])
        assert planned.exit_code == 0, planned.output
        said = json.loads(planned.stdout)["warnings"]
        assert any(f"tests go to POST {server.url}/v1/ask" in w for w in said), said
        ran = runner.invoke(
            cli,
            ["test", "--openapi", doc, "--intensity", "quick", "--suite", "functional", "--no-second-wave",
             "--only", "CONV-GREETING-001", "--no-probe", "--json", "--fail-on", "none"],
        )  # fmt: skip
        assert ran.exit_code == 0, ran.output
    out = json.loads(ran.stdout)
    assert out["summary"]["status"] == "completed" and seen
    assert [r["status"] for r in out["results"]] == ["passed"]
    assert all("conversation_id" in body for body in seen)
