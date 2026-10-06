"""The command adapter: a program that answers one message per run, started only inside a sandbox."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from agentlab.adapters.base import AdapterContext
from agentlab.adapters.command import EVENT_PREFIX, CommandAdapter, apply_events, split_events
from agentlab.core.config import AgentLabConfig
from agentlab.core.errors import SandboxUnavailable, UnsupportedCapability
from agentlab.core.models import AgentRequest, AgentResponse, CommandConfig, RepositorySource, TargetSpec
from agentlab.sandbox import DisabledSandboxProvider, DockerSandboxProvider
from agentlab.sandbox.base import ExecResult
from tests.support.docker import agentlab_containers, needs_docker

CHAT_AGENT = """
    import json, os, pathlib, sys
    message = sys.stdin.read().strip()
    session = os.environ["AGENTLAB_SESSION"]
    memory = pathlib.Path("/workspace") / ("memory-" + session + ".txt")
    low = message.lower()
    if low.startswith("my name is "):
        memory.write_text(message[11:].strip().rstrip("."))
        print("Nice to meet you.")
    elif "what is my name" in low:
        print("Your name is " + memory.read_text() + "." if memory.exists() else "I do not know your name.")
    elif low == "use a tool":
        print("AGENTLAB_EVENT " + json.dumps({"type": "tool_call", "name": "lookup", "arguments": {"q": "x"}, "result": "42"}))
        print("The answer is 42.")
    elif low == "crash":
        sys.exit(7)
    else:
        print("echo: " + message)
"""


def adapter_for(tmp_path: Path, *, sandbox: object, mode: str = "chat", **cfg: object) -> CommandAdapter:
    repo = tmp_path / "agent-repo"
    repo.mkdir(exist_ok=True)
    (repo / "agent.py").write_text(dedent(CHAT_AGENT), encoding="utf-8")
    spec = TargetSpec(
        name="cli-agent",
        repository=RepositorySource(path=str(repo)),
        command=CommandConfig(mode=mode, command=["python", "/agent/agent.py"], timeout_seconds=30, **cfg),  # type: ignore[arg-type]
    )
    return CommandAdapter(
        spec, AdapterContext(config=AgentLabConfig(), sandbox=sandbox, extras={"repo_path": str(repo)})
    )


def test_events_are_separated_from_the_answer() -> None:
    out = (
        "Hello there.\n"
        f'{EVENT_PREFIX}{{"type": "tool_call", "name": "search", "arguments": {{"q": "a"}}, "result": "r"}}\n'
        f'{EVENT_PREFIX}{{"type": "handoff", "to": "billing"}}\n'
        f"{EVENT_PREFIX}not json\n"
        f'{EVENT_PREFIX}{{"no_type": 1}}\n'
        "Second line.\n"
    )
    text, events = split_events(out)
    assert text.splitlines() == [
        "Hello there.",
        f"{EVENT_PREFIX}not json",
        f'{EVENT_PREFIX}{{"no_type": 1}}',
        "Second line.",
    ]
    assert [e["type"] for e in events] == ["tool_call", "handoff"]
    resp = AgentResponse(output=text)
    apply_events(resp, events)
    assert [(c.name, c.arguments, c.result, c.status) for c in resp.tool_calls] == [
        ("search", {"q": "a"}, "r", "success")
    ]
    assert [(e.type, e.data) for e in resp.events] == [("handoff", {"to": "billing"})]


def test_an_event_flood_is_capped() -> None:
    events = [{"type": "tool_call", "name": f"t{i}"} for i in range(2000)]
    resp = AgentResponse()
    apply_events(resp, events)
    assert len(resp.tool_calls) == 500


def test_chat_and_task_modes_declare_different_capabilities(tmp_path: Path) -> None:
    chat = adapter_for(tmp_path, sandbox=DisabledSandboxProvider(), mode="chat")
    task = adapter_for(tmp_path, sandbox=DisabledSandboxProvider(), mode="task")
    assert chat.capabilities.conversational and chat.capabilities.sessions
    assert not task.capabilities.conversational and not task.capabilities.sessions
    assert chat.workdir() == "/agent", "a chat agent starts next to its own code"
    assert task.workdir() == "/workspace", "a coding agent starts in the workspace"
    assert adapter_for(tmp_path, sandbox=None, mode="task", workdir="/work").workdir() == "/work"


def test_the_default_working_directory_survives_a_trip_through_a_job_queue_or_the_database(tmp_path: Path) -> None:
    """A target submitted through the API is serialised and read back, which writes every field. The default used to be
    decided by "was workdir written?", so a chat agent ran in /workspace, where its code is not."""
    made = adapter_for(tmp_path, sandbox=DisabledSandboxProvider(), mode="chat")
    stored = TargetSpec.model_validate_json(made.spec.model_dump_json())
    assert stored.command is not None and "workdir" in stored.command.model_fields_set
    again = CommandAdapter(stored, AdapterContext(config=AgentLabConfig(), extras=made.ctx.extras))
    assert again.workdir() == "/agent"
    explicit = CommandConfig(command=["x"], workdir="/somewhere")
    assert CommandConfig.model_validate_json(explicit.model_dump_json()).workdir == "/somewhere"


async def test_a_task_runner_is_not_asked_questions(tmp_path: Path) -> None:
    task = adapter_for(tmp_path, sandbox=DisabledSandboxProvider(), mode="task")
    with pytest.raises(UnsupportedCapability, match="coding agent"):
        await task.send(AgentRequest(input="hi", session_id="s"))


async def test_without_an_isolating_sandbox_the_adapter_does_not_open(tmp_path: Path) -> None:
    marker = tmp_path / "ran-on-the-host"
    adapter = adapter_for(tmp_path, sandbox=DisabledSandboxProvider())
    (tmp_path / "agent-repo" / "agent.py").write_text(f"open({str(marker)!r}, 'w').write('x')\n")
    with pytest.raises(SandboxUnavailable, match="refusing to run untrusted code"):
        await adapter.open()
    with pytest.raises(SandboxUnavailable):
        await adapter.send(AgentRequest(input="hi", session_id="s"))
    assert not marker.exists()
    none = adapter_for(tmp_path, sandbox=None)
    with pytest.raises(SandboxUnavailable, match="no sandbox provider"):
        await none.open()


def test_a_timeout_and_a_nonzero_exit_become_errors_on_the_response(tmp_path: Path) -> None:
    adapter = adapter_for(tmp_path, sandbox=None)
    late = adapter.to_response(ExecResult(exit_code=137, timed_out=True), 30000)
    assert late.error == "timeout after 30s" and late.observed["exit_code"] == 137
    bad = adapter.to_response(ExecResult(exit_code=2, stderr="Traceback ...\nValueError: x"), 5)
    assert bad.error == "command exited with status 2" and "ValueError" in (bad.raw or "")
    fine = adapter.to_response(ExecResult(exit_code=0, stdout="all good\n"), 5)
    assert fine.error is None and fine.output == "all good"


@needs_docker
async def test_a_chat_agent_answers_remembers_per_session_and_is_removed_afterwards(tmp_path: Path) -> None:
    before = agentlab_containers()
    adapter = adapter_for(tmp_path, sandbox=DockerSandboxProvider())
    await adapter.open()
    try:
        a, b = await adapter.new_session(), await adapter.new_session()
        hello = await adapter.send(AgentRequest(input="My name is Alice.", session_id=a))
        assert hello.output == "Nice to meet you." and hello.error is None
        again = await adapter.send(AgentRequest(input="What is my name?", session_id=a))
        assert again.output == "Your name is Alice."
        other = await adapter.send(AgentRequest(input="What is my name?", session_id=b))
        assert other.output == "I do not know your name.", "a second session does not see the first one's files"
        tool = await adapter.send(AgentRequest(input="use a tool", session_id=a))
        assert tool.output == "The answer is 42." and [(c.name, c.result) for c in tool.tool_calls] == [
            ("lookup", "42")
        ]
        crash = await adapter.send(AgentRequest(input="crash", session_id=a))
        assert crash.error == "command exited with status 7"
        assert len(agentlab_containers() - before) == 1, "one sandbox serves the whole conversation"
    finally:
        await adapter.close()
    assert agentlab_containers() - before == set()
