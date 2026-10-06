"""LoadEngine: a bounded, polite concurrency check (spec section 8P; never a stress test).

A test whose ``context["load"]`` is ``{"sessions": N, "rounds": R}`` sends its first turn from N independent sessions at
the same time, R times each, and records how many succeeded and how long they took. The numbers are *observed facts*
the ``load_stats`` assertion judges; the engine itself never decides pass or fail.

The size is capped (a test cannot ask for more than ``MAX_SESSIONS`` x ``MAX_ROUNDS`` requests) and every request is
counted against the test and run budgets, so a runaway agent still ends in a STOPPED status.
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any

from agentlab.core.enums import EventType
from agentlab.core.errors import AgentLabError, CredentialError, PolicyBlocked, TargetError, UserError
from agentlab.core.models import AgentRequest, AgentResponse, TestCase
from agentlab.execution.engines import ENGINES, AttemptEnv, AttemptOutcome, ExecutionEngine
from agentlab.execution.limits import LimitReached, LimitTracker

MAX_SESSIONS = 20
MAX_ROUNDS = 5


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (no interpolation: the number reported is a latency that really happened)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def succeeded(resp: AgentResponse) -> bool:
    return (
        resp.error is None and (resp.status_code is None or 200 <= resp.status_code < 300) and bool(resp.output.strip())
    )


class LoadEngine(ExecutionEngine):
    name = "load"

    def handles(self, test: TestCase) -> bool:
        return bool(test.context.get("load")) and bool(test.all_turns())

    async def run(self, test: TestCase, env: AttemptEnv) -> AttemptOutcome:
        cfg: dict[str, Any] = test.context.get("load") or {}
        sessions = max(1, min(int(cfg.get("sessions", 3)), MAX_SESSIONS))
        rounds = max(1, min(int(cfg.get("rounds", 1)), MAX_ROUNDS))
        text = env.resolver.resolve(test.all_turns()[0].input)
        out = AttemptOutcome()
        latencies: list[float] = []
        errors: list[str] = []
        ok = 0
        lock = asyncio.Lock()
        started = time.perf_counter()

        async def one(index: int) -> None:
            nonlocal ok
            sid = await env.adapter.new_session()
            try:
                for _ in range(rounds):
                    if out.stopped or out.timed_out:
                        return
                    env.cancel.raise_if_cancelled()
                    req = AgentRequest(
                        input=text,
                        session_id=sid,
                        credential=test.required_credentials[0] if test.required_credentials else None,
                        metadata={"test_id": test.id, "load_session": index, "attempt": env.attempt},
                    )
                    t0 = time.perf_counter()
                    try:
                        resp = await asyncio.wait_for(
                            env.adapter.send(req), timeout=max(0.05, env.budget.remaining_time())
                        )
                    except TimeoutError:
                        async with lock:
                            out.timed_out = True
                            errors.append("timeout")
                        return
                    except (CredentialError, PolicyBlocked, UserError):
                        raise
                    except TargetError as exc:
                        resp = AgentResponse(error=str(exc))
                    resp.latency_ms = resp.latency_ms or round((time.perf_counter() - t0) * 1000, 2)
                    async with lock:
                        position = len(out.responses)
                        out.inputs.append(text)
                        out.sessions.append(f"load-{index}")
                        out.responses.append(resp)
                        env.trace.record_response(position, f"load-{index}", resp)
                        latencies.append(resp.latency_ms)
                        if succeeded(resp):
                            ok += 1
                        else:
                            errors.append(resp.error or "empty reply")
                        env.limits.record(
                            env.budget,
                            tokens=resp.usage.total_tokens,
                            cost=resp.usage.cost_usd,
                            steps=1,
                            category=test.category,
                        )
                        try:
                            LimitTracker.check_test(env.budget)
                            env.limits.check_run()
                        except LimitReached as lr:
                            out.stopped = lr
                            env.trace.record(
                                EventType.LIMIT_REACHED,
                                {"status": lr.status.value, "message": str(lr), "scope": lr.scope},
                            )
                            return
            finally:
                try:
                    await env.adapter.end_session(sid)
                except Exception:  # noqa: S110 - best-effort cleanup
                    pass

        results = await asyncio.gather(*(one(i) for i in range(sessions)), return_exceptions=True)
        for r in results:
            if isinstance(r, AgentLabError):
                raise r
            if isinstance(r, BaseException):
                errors.append(f"{type(r).__name__}")
        out.state["load"] = {
            "sessions": sessions,
            "rounds": rounds,
            "ok": ok,
            "errors": errors[:5],
            "p50_ms": percentile(latencies, 50),
            "p95_ms": percentile(latencies, 95),
            "max_ms": max(latencies, default=0.0),
            "wall_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        return out


ENGINES.register("load", LoadEngine, replace=True)
