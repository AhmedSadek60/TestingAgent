"""``evaluation.timeout_seconds``: the longest any single test may take.

The setting used to be recorded in the run manifest and nothing else, so lowering it changed nothing.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi import FastAPI

from agentlab.core.config import EvaluationConfig, LimitsConfig
from agentlab.core.enums import TestStatus
from agentlab.core.models import ApiConfig, TargetSpec, TestCase
from agentlab.execution.limits import LimitTracker
from agentlab.orchestrator import RunOptions
from tests.support.lab import Lab
from tests.support.servers import serve


def budget(own: float, cap: float | None) -> float:
    test = TestCase(id="T", name="t", category="functional_quality", objective="o", timeout=own)
    return LimitTracker(LimitsConfig(), test_timeout=cap).budget_for(test).timeout


def test_the_cap_lowers_a_tests_own_timeout_and_never_raises_it() -> None:
    assert budget(own=60, cap=5) == 5
    assert budget(own=2, cap=5) == 2
    assert budget(own=60, cap=None) == 60, "no cap, the test's own timeout"


def slow_agent(seconds: float) -> FastAPI:
    app = FastAPI()

    @app.post("/chat")
    async def chat() -> dict[str, str]:
        await asyncio.sleep(seconds)
        return {"output": "hello"}

    return app


async def test_a_test_that_outlasts_the_configured_timeout_ends_as_a_timeout(tmp_path: Path) -> None:
    with serve(slow_agent(20)) as server:
        evaluation = EvaluationConfig(timeout_seconds=1.0)
        async with Lab(tmp_path, evaluation=evaluation) as lab:
            started = time.monotonic()
            outcome = await lab.run(
                TargetSpec(name="slow", api=ApiConfig(url=server.url + "/chat", timeout_seconds=30)),
                RunOptions(
                    intensity="quick",
                    suite="functional",
                    second_wave=False,
                    probe=False,
                    only_tests=["CONV-GREETING-001"],
                ),
            )
            took = time.monotonic() - started
    [result] = [r for r in outcome.results if r.test_id == "CONV-GREETING-001"]
    assert result.status == TestStatus.TIMEOUT, result.status
    assert took < 10, f"the run took {took:.0f}s although a test may take 1s and the agent needs 20s"
