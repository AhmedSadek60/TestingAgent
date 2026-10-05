"""Execution engines: *how* one attempt of a test is driven against a target.

The default :class:`ConversationEngine` sends the test's turns through an agent adapter, one
isolated session per named session. Browser and workspace engines register themselves in
``ENGINES`` (plug-in registry) and are selected by the shape of the test, so adding a new way to
drive a target never touches the executor.
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentlab.adapters.base import AgentAdapter
from agentlab.core.enums import EventType
from agentlab.core.errors import AgentLabError, CredentialError, PolicyBlocked, TargetError, UserError
from agentlab.core.models import AgentRequest, AgentResponse, Attachment, TestCase
from agentlab.core.plugins import Registry
from agentlab.evaluation.context import PlaceholderResolver
from agentlab.execution.limits import CancellationToken, LimitReached, LimitTracker, TestBudget
from agentlab.tracing import TraceRecorder

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


@dataclass
class AttemptEnv:
    """Everything an engine needs for one attempt."""

    run_id: str
    attempt: int
    adapter: AgentAdapter
    trace: TraceRecorder
    budget: TestBudget
    limits: LimitTracker
    cancel: CancellationToken
    resolver: PlaceholderResolver
    extras: dict[str, Any] = field(default_factory=dict)  # engines, workdir, artifacts, credentials, ...


@dataclass
class AttemptOutcome:
    inputs: list[str] = field(default_factory=list)
    sessions: list[str] = field(default_factory=list)
    responses: list[AgentResponse] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    stopped: LimitReached | None = None
    timed_out: bool = False
    error: AgentLabError | None = None

    @property
    def tool_calls(self) -> list:
        return [c for r in self.responses for c in r.tool_calls]


class ExecutionEngine(ABC):
    name = "abstract"

    @abstractmethod
    def handles(self, test: TestCase) -> bool: ...

    @abstractmethod
    async def run(self, test: TestCase, env: AttemptEnv) -> AttemptOutcome: ...


ENGINES: Registry[type[ExecutionEngine]] = Registry("engines")


def load_attachment(ref: str, base: Path | None) -> Attachment:
    """Attachments are read from the fixture directory only (never arbitrary host paths)."""
    root = (base or Path.cwd()).resolve()
    path = (root / ref).resolve()
    if root not in path.parents and path != root:
        raise PolicyBlocked(f"attachment '{ref}' escapes the fixtures directory")
    if not path.is_file():
        raise UserError(f"attachment '{ref}' not found under {root}")
    data = path.read_bytes()
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise UserError(f"attachment '{ref}' is larger than {MAX_ATTACHMENT_BYTES} bytes")
    mt = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return Attachment(name=path.name, media_type=mt, content_b64=base64.b64encode(data).decode())


def _request_metadata(test: TestCase, env: AttemptEnv, turn: int) -> dict[str, Any]:
    """Per-request hints. ``inject_knowledge`` / ``poison_tool_output`` are only honoured by interfaces that
    declare the matching capability; the executor blocks tests that need them elsewhere."""
    meta: dict[str, Any] = {"test_id": test.id, "turn": turn, "attempt": env.attempt}
    for key in ("inject_knowledge", "poison_tool_output"):
        if test.context.get(key):
            meta[key] = env.resolver.resolve_obj(test.context[key])
    return meta


class ConversationEngine(ExecutionEngine):
    name = "conversation"

    def handles(self, test: TestCase) -> bool:
        return not test.browser_steps

    async def run(self, test: TestCase, env: AttemptEnv) -> AttemptOutcome:
        out = AttemptOutcome()
        sessions: dict[str, str] = {}
        adapter = env.adapter
        # generated fixtures live in a per-run directory the orchestrator provides; a test never stores a host path
        fixtures_ref = test.context.get("fixtures_dir") or env.extras.get("fixtures_dir")
        fixtures = Path(fixtures_ref) if fixtures_ref else None
        try:
            for i, turn in enumerate(test.all_turns()):
                env.cancel.raise_if_cancelled()
                env.limits.check_run()
                if turn.session not in sessions:
                    sessions[turn.session] = await adapter.new_session()
                text = env.resolver.resolve(turn.input)
                req = AgentRequest(
                    input=text,
                    session_id=sessions[turn.session],
                    attachments=[load_attachment(a, fixtures) for a in turn.attachments],
                    credential=test.required_credentials[0] if test.required_credentials else None,
                    metadata=_request_metadata(test, env, i),
                )
                env.trace.record(
                    EventType.AGENT_REQUEST,
                    {
                        "turn": i,
                        "session": turn.session,
                        "input": text,
                        "attachments": [a.name for a in req.attachments],
                    },
                )
                out.inputs.append(text)
                out.sessions.append(turn.session)
                t0 = time.perf_counter()
                try:
                    resp = await asyncio.wait_for(adapter.send(req), timeout=max(0.05, env.budget.remaining_time()))
                except TimeoutError:
                    out.timed_out = True
                    env.trace.record(
                        EventType.ERROR,
                        {"turn": i, "kind": "TIMEOUT", "message": f"no response within {env.budget.timeout:g}s"},
                    )
                    break
                except (CredentialError, PolicyBlocked, UserError):
                    raise
                except TargetError as exc:
                    resp = AgentResponse(error=str(exc))
                    out.error = exc
                resp.latency_ms = resp.latency_ms or round((time.perf_counter() - t0) * 1000, 2)
                out.responses.append(resp)
                env.trace.record_response(i, turn.session, resp)
                steps = 1 + len(resp.tool_calls)
                env.limits.record(
                    env.budget,
                    tokens=resp.usage.total_tokens,
                    cost=resp.usage.cost_usd,
                    steps=steps,
                    category=test.category,
                )
                try:
                    LimitTracker.check_test(env.budget)
                    env.limits.check_run()
                except LimitReached as lr:
                    out.stopped = lr
                    env.trace.record(
                        EventType.LIMIT_REACHED, {"status": lr.status.value, "message": str(lr), "scope": lr.scope}
                    )
                    break
        finally:
            for sid in sessions.values():
                try:
                    await adapter.end_session(sid)
                except Exception:  # noqa: S110 - best-effort cleanup
                    pass
        return out


ENGINES.register("conversation", ConversationEngine, replace=True)


def pick_engine(test: TestCase, engines: dict[str, ExecutionEngine]) -> ExecutionEngine:
    """The most specific engine that claims the test; the conversation engine is the fallback."""
    for name, eng in engines.items():
        if name != "conversation" and eng.handles(test):
            return eng
    return engines["conversation"]
