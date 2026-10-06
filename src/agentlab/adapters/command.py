"""CommandAdapter: an agent that is a command, run inside the sandbox (spec sections 10, 19 and 20).

Two modes, chosen by ``command.mode`` in the target file:

* ``chat``: the command answers one message per run (the message on standard input, the answer on standard output), like
  a command-line assistant. Several turns of one conversation share one sandbox, so an agent that keeps state in files
  keyed by the ``AGENTLAB_SESSION`` environment variable can remember.
* ``task``: a coding agent. It is started on a disposable copy of a project with the task on standard input and is judged
  by what it leaves behind (see :mod:`agentlab.execution.workspace`). It is not asked questions.

Untrusted code is never started on the evaluator host. The adapter refuses to open when no sandbox that can guarantee
isolation is available, so every test that needs it is BLOCKED with that reason, never run unprotected.

A command may report what it did by printing lines of the form ``AGENTLAB_EVENT {"type": "tool_call", "name": ...}``:
tool calls become the response's tool calls, other types become events. Everything else on standard output is the answer.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from pathlib import Path
from typing import Any

from agentlab.adapters.base import ADAPTERS, AdapterCapabilities, AdapterContext, AgentAdapter
from agentlab.core.errors import SandboxUnavailable, TargetError, UnsupportedCapability
from agentlab.core.models import AgentEvent, AgentRequest, AgentResponse, CommandConfig, TargetSpec, ToolCall
from agentlab.sandbox.base import ExecResult, Sandbox, SandboxSpec

AGENT_DIR = "/agent"
WORKSPACE_DIR = "/workspace"
EVENT_PREFIX = "AGENTLAB_EVENT "
MAX_OUTPUT_CHARS = 200_000


def split_events(stdout: str) -> tuple[str, list[dict[str, Any]]]:
    """The answer and the structured events a command printed. A malformed event line stays in the answer."""
    text: list[str] = []
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if line.startswith(EVENT_PREFIX):
            try:
                obj = json.loads(line[len(EVENT_PREFIX) :])
            except ValueError:
                obj = None
            if isinstance(obj, dict) and isinstance(obj.get("type"), str):
                events.append(obj)
                continue
        text.append(line)
    return "\n".join(text).strip(), events


def apply_events(resp: AgentResponse, events: list[dict[str, Any]]) -> None:
    for ev in events[:500]:
        if ev["type"] == "tool_call" and ev.get("name"):
            args = ev.get("arguments")
            resp.tool_calls.append(
                ToolCall(
                    name=str(ev["name"]),
                    arguments=args if isinstance(args, dict) else {},
                    result=ev.get("result"),
                    status=str(ev.get("status", "success")),
                )
            )
        else:
            resp.events.append(AgentEvent(type=str(ev["type"]), data={k: v for k, v in ev.items() if k != "type"}))


def _clip(text: str) -> str:
    return (
        text if len(text) <= MAX_OUTPUT_CHARS else text[:MAX_OUTPUT_CHARS] + f"...[{len(text) - MAX_OUTPUT_CHARS} more]"
    )


class CommandAdapter(AgentAdapter):
    kind = "command"

    def __init__(self, spec: TargetSpec, ctx: AdapterContext) -> None:
        super().__init__(spec, ctx)
        if spec.command is None:
            raise TargetError("command adapter requires target.command")
        self.cfg: CommandConfig = spec.command
        chat = self.cfg.mode == "chat"
        self.capabilities = AdapterCapabilities(
            conversational=chat,
            sessions=chat,
            parallel_sessions=not chat,  # a chat agent may keep state in the shared sandbox; workspaces are per test
            reports_tool_calls=False,
            reports_events=False,
            notes=[
                "the command runs inside the sandbox; tool calls and events are visible only if it prints "
                f"{EVENT_PREFIX!r} lines"
            ],
        )
        self._sandbox: Sandbox | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------------------------------------ sandbox
    async def open(self) -> None:
        provider = self.ctx.sandbox
        if provider is None:
            raise SandboxUnavailable("no sandbox provider is configured; untrusted code is never run on the host")
        ok, why = await provider.available()
        if not ok:
            raise SandboxUnavailable(f"{why}; refusing to run untrusted code without isolation")

    def _repo_path(self) -> Path | None:
        raw = self.ctx.extras.get("repo_path") or (self.spec.repository.path if self.spec.repository else None)
        return Path(str(raw)) if raw and Path(str(raw)).is_dir() else None

    def workdir(self) -> str:
        """Where the command starts: what the owner set, otherwise next to the agent's own code in chat mode and in the
        workspace in task mode."""
        if "workdir" in self.cfg.model_fields_set:
            return self.cfg.workdir
        return AGENT_DIR if self.cfg.mode == "chat" and self._repo_path() else WORKSPACE_DIR

    async def create_sandbox(
        self, *, env: dict[str, str] | None = None, with_agent: bool = True, image: str | None = None
    ) -> Sandbox:
        """A new sandbox. With ``with_agent`` (the default) it holds a copy of the agent's code at ``/agent`` and runs
        with the network and environment the owner configured; without it, it is a clean room (no agent code, no
        network, no owner environment) used to check a result. The caller closes it."""
        provider = self.ctx.sandbox
        if provider is None:
            raise SandboxUnavailable("no sandbox provider is configured; untrusted code is never run on the host")
        sandbox = await provider.create(
            SandboxSpec.from_config(
                self.ctx.config.security.sandbox,
                image=(self.cfg.image if with_agent else None) or image or self.ctx.config.security.sandbox.image,
                workdir=WORKSPACE_DIR,
                env={**(self.cfg.env if with_agent else {}), **(env or {})},
                timeout_seconds=self.cfg.timeout_seconds,
                network=self.cfg.network if with_agent else "none",
                allow_hosts=self.cfg.allow_hosts if with_agent else [],
                extra_tmpfs=[AGENT_DIR] if with_agent else [],
                labels={"run": self.ctx.run_id or ""},
            )
        )
        try:
            repo = self._repo_path()
            if with_agent and repo is not None:
                await sandbox.put_dir(repo, AGENT_DIR)
        except BaseException:
            await sandbox.close()
            raise
        return sandbox

    async def close(self) -> None:
        sandbox, self._sandbox = self._sandbox, None
        if sandbox is not None:
            with contextlib.suppress(Exception):
                await sandbox.close()

    # ------------------------------------------------------------------------------------------------ requests
    async def send(self, request: AgentRequest) -> AgentResponse:
        if self.cfg.mode != "chat":
            raise UnsupportedCapability(
                "this command is a coding agent (command.mode: task): it is given a task on a workspace, not messages"
            )
        async with self._lock:
            if self._sandbox is None:
                self._sandbox = await self.create_sandbox()
            sandbox = self._sandbox
        t0 = time.perf_counter()
        result = await sandbox.exec(
            self.cfg.command,
            stdin=request.input.encode("utf-8"),
            env={"AGENTLAB_SESSION": request.session_id, "AGENTLAB_TURN": str(request.metadata.get("turn", 0))},
            timeout=self.cfg.timeout_seconds,
            workdir=self.workdir(),
        )
        return self.to_response(result, (time.perf_counter() - t0) * 1000)

    def to_response(self, result: ExecResult, latency_ms: float) -> AgentResponse:
        answer, events = split_events(result.stdout)
        resp = AgentResponse(output=_clip(answer), latency_ms=latency_ms, status_code=None)
        apply_events(resp, events)
        resp.observed = {"exit_code": result.exit_code, "truncated": result.truncated}
        if result.timed_out:
            resp.error = f"timeout after {self.cfg.timeout_seconds:g}s"
        elif result.exit_code != 0:
            resp.error = f"command exited with status {result.exit_code}"
            resp.raw = result.stderr[-500:]
        return resp

    def describe(self) -> dict[str, Any]:
        return {**super().describe(), "mode": self.cfg.mode, "command": self.cfg.command, "image": self.cfg.image}


ADAPTERS.register("command", CommandAdapter, replace=True)
