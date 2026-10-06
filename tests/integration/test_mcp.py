"""MCP targets: the adapter against a real MCP server, the static engine and planning for an interface that cannot be
chatted with. The server is the ``mcp`` fixture, served on loopback over streamable HTTP."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from agentlab.adapters.base import AdapterContext, TargetRuntime
from agentlab.adapters.mcp import McpAdapter, flatten_content, parse_tool_call, side_effects_of
from agentlab.core.config import AgentLabConfig, ReportingConfig, SandboxConfig, SecurityConfig, StorageConfig
from agentlab.core.enums import TestStatus
from agentlab.core.errors import PolicyBlocked, TargetError, UnsupportedCapability, UserError
from agentlab.core.models import AgentRequest, TargetSpec
from agentlab.fixtures import McpToolServer
from agentlab.orchestrator import RunOptions, TestOrchestratorAgent
from agentlab.security.egress import EgressPolicy
from agentlab.services import Services


def call(tool: str, **arguments: Any) -> AgentRequest:
    return AgentRequest(input=json.dumps({"tool": tool, "arguments": arguments}), session_id="s")


def spec_for(target: dict[str, Any]) -> TargetSpec:
    return TargetSpec(**target)


@pytest.fixture(scope="module")
def correct() -> Iterator[dict[str, Any]]:
    with McpToolServer.build("correct").deployed() as target:
        yield target


@pytest.fixture(scope="module")
def flawed() -> Iterator[dict[str, Any]]:
    with McpToolServer.build("flawed").deployed() as target:
        yield target


async def opened(target: dict[str, Any], **ctx: Any) -> McpAdapter:
    cfg = AgentLabConfig()
    adapter = McpAdapter(spec_for(target), AdapterContext(config=cfg, **ctx))
    await adapter.open()
    return adapter


# ============================================================================================ what a test sends
def test_a_tool_call_is_json_with_a_tool_and_arguments() -> None:
    assert parse_tool_call('{"tool": "read_file", "arguments": {"path": "a.txt"}}') == ("read_file", {"path": "a.txt"})
    assert parse_tool_call('{"tool": "ping"}') == ("ping", {})
    for bad in ("What is the weather?", "[]", '{"arguments": {}}', '{"tool": ""}', '{"tool": "x", "arguments": [1]}'):
        with pytest.raises(UserError, match="tool call|JSON|arguments"):
            parse_tool_call(bad)


def test_what_a_server_says_about_side_effects_beats_a_guess_from_the_name() -> None:
    class Hints:
        read_only_hint = True
        destructive_hint = None

    class Danger:
        read_only_hint = None
        destructive_hint = True

    assert side_effects_of("run_command", "Run an allow-listed command", Hints()) == "read"
    assert side_effects_of("tidy", "Tidy up", Danger()) == "destructive"
    assert side_effects_of("send_email", "Send an email", None) in {"external", "write"}


def test_non_text_results_are_described_not_decoded() -> None:
    class Block:
        def __init__(self, type: str, **kw: Any) -> None:
            self.type = type
            self.__dict__.update(kw)

    text = flatten_content([Block("text", text="hello"), Block("image", mime_type="image/png", data="AAAA")])
    assert text == "hello\n[image content: image/png]"


# ============================================================================================ the adapter
async def test_the_adapter_lists_and_calls_the_servers_tools(correct: dict[str, Any]) -> None:
    adapter = await opened(correct)
    try:
        assert adapter.capabilities.conversational is False and adapter.capabilities.reports_tool_calls
        probe = await adapter.probe()
        assert probe["reachable"] is True and probe["tools"] == 8
        tools = {t.name: t for t in await adapter.discover_tools()}
        assert sorted(tools) == [
            "calculator", "fetch_url", "get_weather", "read_file", "run_command", "run_sql", "search_kb", "send_email",
        ]  # fmt: skip
        assert tools["read_file"].parameters["required"] == ["path"] and tools["read_file"].side_effects == "read"
        assert tools["send_email"].side_effects != "read" and tools["send_email"].source.startswith("mcp:")

        ok = await adapter.send(call("get_weather", city="Paris"))
        assert ok.error is None and "18" in ok.output
        assert ok.tool_calls[0].name == "get_weather" and ok.tool_calls[0].status == "success"
        assert ok.events[0].type == "mcp_result" and ok.events[0].data["is_error"] is False

        rejected = await adapter.send(call("get_weather"))
        assert rejected.error is None, "a tool that says no is an answer, not a transport failure"
        assert rejected.events[0].data["is_error"] is True and rejected.tool_calls[0].status == "error"
        assert "required" in rejected.output

        unknown = await adapter.send(call("no_such_tool"))
        assert unknown.events[0].data["is_error"] is True or unknown.error
    finally:
        await adapter.close()


async def test_a_second_connection_does_not_disturb_the_first(correct: dict[str, Any]) -> None:
    """The connection is owned by one task, so requests from any task, in parallel, are safe."""
    import asyncio

    adapter = await opened(correct)
    try:
        replies = await asyncio.gather(*[adapter.send(call("calculator", expression=f"{n} * 2")) for n in range(8)])
        assert [str(2 * n) in r.output for n, r in enumerate(replies)] == [True] * 8
    finally:
        await adapter.close()
    await adapter.close()  # closing twice is harmless


async def test_a_server_that_is_not_there_is_reported_not_raised_into_the_run(tmp_path: Path) -> None:
    target = McpToolServer.build("correct").target("http://127.0.0.1:9")
    runtime = TargetRuntime(spec_for(target), AdapterContext(config=AgentLabConfig()))
    await runtime.open()
    try:
        assert runtime.available() == [] and "mcp" in runtime.errors and "cannot connect" in runtime.errors["mcp"]
    finally:
        await runtime.close()


async def test_the_server_can_demand_a_credential() -> None:
    with McpToolServer.build("correct").deployed(token="s3cret") as target:
        with pytest.raises(TargetError, match="cannot connect"):
            await opened(target)
        target["mcp"]["headers"] = {"Authorization": "Bearer s3cret"}
        adapter = await opened(target)
        try:
            assert "84" in (await adapter.send(call("calculator", expression="12 * 7"))).output
        finally:
            await adapter.close()


async def test_the_evaluator_never_connects_to_a_metadata_address() -> None:
    target = McpToolServer.build("correct").target("http://169.254.169.254")
    adapter = McpAdapter(spec_for(target), AdapterContext(config=AgentLabConfig(), egress=EgressPolicy()))
    with pytest.raises(PolicyBlocked):
        await adapter.open()


async def test_stdio_needs_the_sandbox_and_never_starts_the_command_on_the_host() -> None:
    spec = TargetSpec(name="stdio", mcp={"transport": "stdio", "command": ["python", "server.py"]})  # type: ignore[arg-type]
    adapter = McpAdapter(spec, AdapterContext(config=AgentLabConfig(), sandbox=None))
    with pytest.raises(PolicyBlocked, match="sandbox"):
        await adapter.open()
    with pytest.raises(UserError, match="mcp.command"):
        McpAdapter(TargetSpec(name="x", mcp={"transport": "stdio"}), AdapterContext(config=AgentLabConfig()))  # type: ignore[arg-type]
    with pytest.raises(UserError, match="mcp.url"):
        McpAdapter(TargetSpec(name="x", mcp={"transport": "sse"}), AdapterContext(config=AgentLabConfig()))  # type: ignore[arg-type]


def test_the_install_hint_names_the_extra() -> None:
    assert "agentlab[mcp]" in __import__("agentlab.adapters.mcp", fromlist=["INSTALL_HINT"]).INSTALL_HINT
    assert issubclass(UnsupportedCapability, Exception)


# ============================================================================================ planning and runs
def services_for(tmp: Path) -> Services:
    cfg = AgentLabConfig(
        storage=StorageConfig(
            database_url=f"sqlite:///{tmp}/lab.db",
            artifacts_dir=str(tmp / "artifacts"),
            secrets_file=str(tmp / "s.enc"),
        ),
        security=SecurityConfig(sandbox=SandboxConfig(provider="disabled")),
        reporting=ReportingConfig(formats=[]),
    )
    return Services.create(cfg, base_dir=tmp)


async def test_an_mcp_target_gets_tool_call_tests_and_no_chat_tests(correct: dict[str, Any], tmp_path: Path) -> None:
    sv = services_for(tmp_path)
    try:
        out = await TestOrchestratorAgent(sv).run(spec_for(correct), RunOptions(intensity="quick", plan_only=True))
        plan = out.plans[0]
        ids = [t.id for t in plan.test_cases()]
        assert ids and all(i.startswith("MCP-") for i in ids), f"only tool-call tests belong here: {ids}"
        assert any(i.startswith("MCP-DESCRIPTION-") for i in ids) and any(i.startswith("MCP-CALL-") for i in ids)
        assert plan.counts()["blocked"] == 0
    finally:
        sv.store.db.dispose()


async def test_a_flawed_server_fails_where_the_correct_one_passes(
    correct: dict[str, Any], flawed: dict[str, Any], tmp_path: Path
) -> None:
    results: dict[str, dict[str, TestStatus]] = {}
    for label, target in (("correct", correct), ("flawed", flawed)):
        sv = services_for(tmp_path / label)
        try:
            out = await TestOrchestratorAgent(sv).run(
                spec_for(target), RunOptions(intensity="quick", second_wave=False)
            )
            results[label] = {r.test_id: r.status for r in out.results}
        finally:
            sv.store.db.dispose()
    assert set(results["correct"].values()) == {TestStatus.PASSED}
    failed = {t for t, s in results["flawed"].items() if s == TestStatus.FAILED}
    assert any("PATH-TRAVERSAL" in t for t in failed) and any("DESCRIPTION" in t for t in failed), failed


# ============================================================================================ stdio, in the sandbox
needs_docker = pytest.mark.docker


@needs_docker
async def test_an_mcp_server_over_stdio_runs_inside_the_sandbox_only() -> None:
    import shutil
    import subprocess

    from agentlab.sandbox import DockerSandboxProvider

    if not shutil.which("docker") or subprocess.run(["docker", "version"], capture_output=True).returncode != 0:  # noqa: S603,S607
        pytest.skip("Docker daemon not available")
    server_dir = Path(__file__).resolve().parents[1] / "support" / "stdio_mcp"
    spec = TargetSpec(
        name="stdio-server",
        repository={"path": str(server_dir)},  # type: ignore[arg-type]
        mcp={"transport": "stdio", "command": ["python", "/agent/server.py"], "timeout_seconds": 30},  # type: ignore[arg-type]
    )
    adapter = McpAdapter(spec, AdapterContext(config=AgentLabConfig(), sandbox=DockerSandboxProvider()))
    await adapter.open()
    try:
        assert [t.name for t in await adapter.discover_tools()] == ["echo", "where"]
        assert "echo: hi" in (await adapter.send(call("echo", text="hi"))).output
        where = json.loads((await adapter.send(call("where"))).output)
        assert where["in_container"] is True, "the untrusted server must run in the container"
        assert where["uid"] == 65534 and where["cwd"] == "/agent"
        assert where["network"] is False, "and without a network"
        assert (await adapter.send(call("echo"))).tool_calls[0].status == "error"
    finally:
        await adapter.close()
    assert adapter._sandbox is None, "the sandbox is removed when the adapter is closed"
