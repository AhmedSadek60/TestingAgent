"""The workspace engine: run a coding agent on a disposable project and judge what it leaves behind (taxonomy K).

One attempt does, in this order:

1. build the project the test names (``context.workspace``: a bundled project plus what the test plants in it) and keep
   a copy of it as the *baseline*;
2. start a **sandbox** (Docker; no network unless the owner configured one), put the agent's code at ``/agent`` and the
   project at ``/workspace``, and run the agent's command with the task on standard input;
3. copy ``/workspace`` out as plain files and **destroy the sandbox**: whatever the agent started is gone;
4. in a second, **clean sandbox** (no agent code, no network), run the project's own tests on the agent's result with the
   protected files (the original tests) put back, and the held-out tests added; and
5. compare the result with the baseline *on the host, without running ``git`` on it*.

The verdict is data for assertions (``tests_pass``, ``diff_not_touches``, ``file_exists`` ...), never a judgement made
here. When no isolating sandbox exists the engine does nothing: coding-agent code is never run on the host.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from typing import Any

from agentlab.adapters.command import WORKSPACE_DIR, CommandAdapter
from agentlab.coding import diff_trees, get_project, materialize, read_tree
from agentlab.coding.diffing import MAX_CAPTURED_TEXT, decode, is_text
from agentlab.coding.projects import WorkspaceProject
from agentlab.core.enums import EventType
from agentlab.core.errors import UnsupportedCapability, UserError
from agentlab.core.models import TestCase
from agentlab.evaluation.assertions_workspace import matches_any
from agentlab.execution.engines import ENGINES, AttemptEnv, AttemptOutcome, ExecutionEngine, save_artifact
from agentlab.execution.limits import LimitReached, LimitTracker
from agentlab.sandbox.base import ExecResult, Sandbox

TEST_TIMEOUT_SECONDS = 120.0
OUTPUT_TAIL = 4000
MAX_CAPTURED_FILES = 20


def write_tree(root: Path, files: dict[str, bytes]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _tail(text: str, limit: int = OUTPUT_TAIL) -> str:
    return text if len(text) <= limit else "..." + text[-limit:]


def restore_protected(
    after: dict[str, bytes], baseline: dict[str, bytes], protected: tuple[str, ...]
) -> tuple[dict[str, bytes], list[str]]:
    """The agent's result with every protected file put back as it was (and protected files it added removed).

    Returns the files to verify and the paths that were put back, so the report can say that it happened."""
    out = dict(after)
    restored: list[str] = []
    for path in list(out):
        if matches_any(path, list(protected)) and path not in baseline:
            del out[path]
            restored.append(path)
    for path, data in baseline.items():
        if matches_any(path, list(protected)) and out.get(path) != data:
            out[path] = data
            restored.append(path)
    return out, sorted(restored)


def captured_contents(
    after: dict[str, bytes], changed: list[str], wanted: list[str], limit: int = MAX_CAPTURED_FILES
) -> dict[str, str]:
    """Text of the files an assertion may need to read: what changed, plus every file a test names explicitly."""
    out: dict[str, str] = {}
    for path in [*wanted, *changed]:
        data = after.get(path)
        if data is None or path in out or len(out) >= limit or not is_text(data):
            continue
        out[path] = decode(data[:MAX_CAPTURED_TEXT])
    return out


def _named_files(test: TestCase) -> list[str]:
    return [str(a.params["path"]) for a in test.assertions if a.type == "file_contains" and a.params.get("path")]


class WorkspaceEngine(ExecutionEngine):
    name = "workspace"

    def handles(self, test: TestCase) -> bool:
        return bool(test.context.get("workspace"))

    async def run(self, test: TestCase, env: AttemptEnv) -> AttemptOutcome:
        adapter = env.adapter
        if not isinstance(adapter, CommandAdapter) or adapter.cfg.mode != "task":
            raise UnsupportedCapability(
                "coding-agent tests need a target that declares `command` with `mode: task` (an agent started in the sandbox)"
            )
        cfg = env.resolver.resolve_obj(dict(test.context["workspace"]))
        project = get_project(str(cfg.get("fixture", "")))
        plant, canary_env, sentinel = cfg.get("plant"), cfg.get("canary_env"), cfg.get("sentinel")
        if not all(isinstance(v, (dict, type(None))) for v in (plant, canary_env)):
            raise UserError("workspace.plant and workspace.canary_env must be mappings")
        baseline = materialize(project, plant=plant, canary_env=canary_env, sentinel=sentinel)
        task = env.resolver.resolve(test.input or test.name)

        out = AttemptOutcome(inputs=[task], sessions=["workspace"])
        env.trace.record(
            EventType.AGENT_REQUEST,
            {"turn": 0, "session": "workspace", "input": task, "project": project.name, "files": len(baseline)},
        )
        with tempfile.TemporaryDirectory(prefix="agentlab-workspace-") as raw:
            tmp = Path(raw)
            write_tree(tmp / "before", baseline)
            env.cancel.raise_if_cancelled()
            agent_result, after, agent_ms = await self._run_agent(adapter, env, test, task, tmp)
            out.timed_out = agent_result.timed_out
            diff = diff_trees(baseline, after)
            env.cancel.raise_if_cancelled()
            verified, restored = restore_protected(after, baseline, project.protected)
            tests = await self._verify(adapter, env, project, verified, tmp, hidden=False)
            hidden = await self._verify(adapter, env, project, verified, tmp, hidden=True) if project.hidden else None

        response = adapter.to_response(agent_result, agent_ms)
        out.responses.append(response)
        env.trace.record_response(0, "workspace", response)
        out.state["workspace"] = {
            "fixture": project.name,
            "files": sorted(after),
            "changed_files": diff.changed_files,
            "added": diff.added,
            "modified": diff.modified,
            "deleted": diff.deleted,
            "diff": diff.diff,
            "diff_lines": diff.diff_lines,
            "contents": captured_contents(after, diff.changed_files, _named_files(test)),
            "agent": {
                "exit_code": agent_result.exit_code,
                "timed_out": agent_result.timed_out,
                "duration_ms": agent_ms,
            },
            "tests": tests,
            "hidden_tests": hidden,
            "restored_protected_files": restored,
            "sandbox": {
                "image": adapter.cfg.image or "default",
                "network": adapter.cfg.network,
                "verification": "a second sandbox without the agent's code or network",
            },
        }
        artifact = save_artifact(
            env,
            {
                "test_id": test.id,
                "project": project.name,
                "files": diff.changed_files,
                "diff": diff.diff,
                "test_output": (tests or {}).get("output", ""),
                "notes": self._notes(project, restored, tests, hidden, agent_result),
            },
            kind="workspace",
            name=f"workspace-{test.id}.json",
            test_id=test.id,
        )
        if artifact:
            out.artifacts.append(artifact)
        steps = 1 + len(response.tool_calls)
        env.limits.record(env.budget, tokens=0, cost=0.0, steps=steps, category=test.category)
        try:
            LimitTracker.check_test(env.budget)
            env.limits.check_run()
        except LimitReached as lr:
            out.stopped = lr
            env.trace.record(
                EventType.LIMIT_REACHED, {"status": lr.status.value, "message": str(lr), "scope": lr.scope}
            )
        return out

    # ------------------------------------------------------------------------------------------------ the agent
    async def _run_agent(
        self, adapter: CommandAdapter, env: AttemptEnv, test: TestCase, task: str, tmp: Path
    ) -> tuple[ExecResult, dict[str, bytes], float]:
        sandbox = await adapter.create_sandbox()
        try:
            await sandbox.put_dir(tmp / "before", WORKSPACE_DIR)
            budget = max(1.0, min(env.budget.remaining_time(), adapter.cfg.timeout_seconds))
            t0 = time.perf_counter()
            result = await asyncio.wait_for(
                sandbox.exec(
                    adapter.cfg.command,
                    stdin=task.encode("utf-8"),
                    env={"AGENTLAB_WORKSPACE": WORKSPACE_DIR},
                    timeout=budget,
                    workdir=adapter.workdir(),
                ),
                timeout=budget + 30,
            )
            elapsed = round((time.perf_counter() - t0) * 1000, 2)
            self._record_command(env, "agent", adapter.cfg.command, result)
            await sandbox.export_dir(WORKSPACE_DIR, tmp / "after")
            return result, read_tree(tmp / "after"), elapsed
        finally:
            await sandbox.close()

    # ---------------------------------------------------------------------------------------- the clean room
    async def _verify(
        self,
        adapter: CommandAdapter,
        env: AttemptEnv,
        project: WorkspaceProject,
        files: dict[str, bytes],
        tmp: Path,
        *,
        hidden: bool,
    ) -> dict[str, Any]:
        """Run the project's tests (or the held-out ones) on the agent's result in a sandbox that never saw the agent."""
        command = list(project.hidden_command if hidden else project.test_command)
        label = "hidden_tests" if hidden else "tests"
        tree = dict(files)
        if hidden:
            tree.update({path: text.encode("utf-8") for path, text in project.hidden.items()})
        root = tmp / f"verify-{label}"
        write_tree(root, tree)
        sandbox: Sandbox = await adapter.create_sandbox(with_agent=False, image=project.image)
        try:
            await sandbox.put_dir(root, WORKSPACE_DIR)
            result = await sandbox.exec(command, workdir=WORKSPACE_DIR, timeout=TEST_TIMEOUT_SECONDS)
        finally:
            await sandbox.close()
        self._record_command(env, label, command, result)
        return {
            "ran": True,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "duration_ms": result.duration_ms,
            "output": _tail((result.stderr + result.stdout).strip()),
        }

    @staticmethod
    def _record_command(env: AttemptEnv, phase: str, command: list[str], result: ExecResult) -> None:
        env.trace.record(
            EventType.SANDBOX_COMMAND,
            {
                "phase": phase,
                "command": " ".join(command)[:200],
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "duration_ms": result.duration_ms,
                "stdout": _tail(result.stdout, 1500),
                "stderr": _tail(result.stderr, 1500),
            },
        )

    @staticmethod
    def _notes(
        project: WorkspaceProject,
        restored: list[str],
        tests: dict[str, Any] | None,
        hidden: dict[str, Any] | None,
        agent: ExecResult,
    ) -> list[str]:
        notes = [
            f"project: {project.name}",
            "the agent ran in one sandbox; the tests ran in a second, clean sandbox on a copy of its result",
        ]
        if restored:
            notes.append(f"protected files put back before the tests ran: {', '.join(restored)}")
        if agent.timed_out:
            notes.append("the agent did not finish within its time limit and was stopped")
        elif agent.exit_code != 0:
            notes.append(f"the agent exited with status {agent.exit_code}")
        if tests is not None and tests.get("exit_code") != 0:
            notes.append("the project's own tests fail on the agent's result")
        if hidden is not None and hidden.get("exit_code") != 0:
            notes.append("the held-out tests (never shown to the agent) fail on the agent's result")
        return notes


ENGINES.register("workspace", WorkspaceEngine, replace=True)
