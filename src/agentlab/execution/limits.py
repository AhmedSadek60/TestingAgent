"""Run/test budgets and safe cancellation (spec sections 28 and 38).

Limits never produce a bare "failure": reaching one yields a ``STOPPED_DUE_TO_*`` status so the
report distinguishes "the agent misbehaved" from "AgentLab stopped the test to protect the budget".
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field

from agentlab.core.config import AgentLabConfig, LimitsConfig
from agentlab.core.enums import RiskClass, RunStatus, TestStatus
from agentlab.core.errors import AgentLabError
from agentlab.core.models import TestCase


def repetitions_for(config: AgentLabConfig, test: TestCase, risk: RiskClass) -> int:
    """How many times a test runs: an explicit test setting > reliability tests > per-risk override > global default.

    Shared by the executor (what actually runs) and the designer (what the plan predicts it will cost)."""
    ev = config.evaluation
    if test.repetitions:
        return max(1, test.repetitions)
    if test.category.lower() == "reliability" or "reliability" in test.tags:
        return max(1, ev.reliability_repetitions)
    return max(1, ev.repetitions_by_risk.get(risk.value, ev.repetitions))


class LimitReached(AgentLabError):
    """Raised inside a test when a configured budget is exhausted."""

    def __init__(self, status: TestStatus, message: str, scope: str = "test") -> None:
        super().__init__(message)
        self.status = status
        self.scope = scope  # "test" or "run"


class CancelledByUser(AgentLabError):
    pass


class CancellationToken:
    """Cooperative cancellation shared by the orchestrator, scheduler, executor and adapters."""

    def __init__(self) -> None:
        self._flag = threading.Event()
        self.reason: str = ""

    def cancel(self, reason: str = "cancelled by user") -> None:
        self.reason = reason
        self._flag.set()

    @property
    def cancelled(self) -> bool:
        return self._flag.is_set()

    def raise_if_cancelled(self) -> None:
        if self._flag.is_set():
            raise CancelledByUser(self.reason or "cancelled")

    async def wait(self, poll: float = 0.05) -> None:
        while not self._flag.is_set():
            await asyncio.sleep(poll)


@dataclass
class TestBudget:
    __test__ = False
    max_cost: float
    max_tokens: int
    max_steps: int
    max_browser_actions: int
    timeout: float
    cost: float = 0.0
    tokens: int = 0
    steps: int = 0
    browser_actions: int = 0
    started: float = field(default_factory=time.monotonic)

    def remaining_time(self) -> float:
        return max(0.0, self.timeout - (time.monotonic() - self.started))


class LimitTracker:
    """Thread-safe accounting for the whole run plus per-test budgets."""

    def __init__(self, limits: LimitsConfig, *, test_timeout: float | None = None) -> None:
        self.limits = limits
        self.test_timeout = test_timeout  # evaluation.timeout_seconds: no single test may take longer than this
        self._lock = threading.Lock()
        self.started = time.monotonic()
        self.cost = 0.0
        self.tokens = 0
        self.steps = 0
        self.browser_actions = 0
        self.by_category: dict[str, float] = {}
        self.by_model: dict[str, float] = {}
        self.stopped: RunStatus | None = None
        self.stop_reason: str | None = None

    # ------------------------------------------------------------------ budgets
    def budget_for(self, test: TestCase) -> TestBudget:
        return TestBudget(
            max_cost=min(test.max_cost, self.limits.max_test_cost_usd),
            max_tokens=min(test.max_tokens, self.limits.max_tokens),
            max_steps=min(test.max_steps, self.limits.max_steps),
            max_browser_actions=self.limits.max_browser_actions,
            timeout=test.timeout if self.test_timeout is None else min(test.timeout, self.test_timeout),
        )

    def record(
        self,
        budget: TestBudget | None,
        *,
        tokens: int = 0,
        cost: float = 0.0,
        steps: int = 0,
        browser_actions: int = 0,
        category: str | None = None,
        model: str | None = None,
    ) -> None:
        with self._lock:
            self.cost += cost
            self.tokens += tokens
            self.steps += steps
            self.browser_actions += browser_actions
            if category:
                self.by_category[category] = self.by_category.get(category, 0.0) + cost
            if model:
                self.by_model[model] = self.by_model.get(model, 0.0) + cost
        if budget is not None:
            budget.cost += cost
            budget.tokens += tokens
            budget.steps += steps
            budget.browser_actions += browser_actions

    # ------------------------------------------------------------------ checks
    def check_run(self) -> None:
        """Run-level budgets: total cost, total tokens and wall-clock time."""
        lim = self.limits
        elapsed = time.monotonic() - self.started
        if self.cost > lim.max_cost_usd:
            raise LimitReached(
                TestStatus.STOPPED_DUE_TO_COST,
                f"run cost ${self.cost:.4f} exceeds max_cost_usd ${lim.max_cost_usd:g}",
                "run",
            )
        if self.tokens > lim.max_tokens:
            raise LimitReached(
                TestStatus.STOPPED_DUE_TO_COST, f"run used {self.tokens} tokens; max_tokens is {lim.max_tokens}", "run"
            )
        if elapsed > lim.max_execution_time_seconds:
            raise LimitReached(
                TestStatus.STOPPED_DUE_TO_TIMEOUT,
                f"run time {elapsed:.0f}s exceeds max_execution_time_seconds {lim.max_execution_time_seconds:g}",
                "run",
            )

    @staticmethod
    def check_test(b: TestBudget) -> None:
        if b.cost > b.max_cost:
            raise LimitReached(
                TestStatus.STOPPED_DUE_TO_COST, f"test cost ${b.cost:.4f} exceeds max_cost ${b.max_cost:g}"
            )
        if b.tokens > b.max_tokens:
            raise LimitReached(
                TestStatus.STOPPED_DUE_TO_COST, f"test used {b.tokens} tokens; max_tokens is {b.max_tokens}"
            )
        if b.steps > b.max_steps:
            raise LimitReached(
                TestStatus.STOPPED_DUE_TO_STEP_LIMIT,
                f"test used {b.steps} steps (turns + tool calls); max_steps is {b.max_steps}",
            )
        if b.browser_actions > b.max_browser_actions:
            raise LimitReached(
                TestStatus.STOPPED_DUE_TO_STEP_LIMIT,
                f"test performed {b.browser_actions} browser actions; limit is {b.max_browser_actions}",
            )

    def summary(self) -> dict[str, object]:
        return {
            "cost_usd": round(self.cost, 6),
            "tokens": self.tokens,
            "steps": self.steps,
            "browser_actions": self.browser_actions,
            "elapsed_s": round(time.monotonic() - self.started, 2),
            "cost_by_category": {k: round(v, 6) for k, v in self.by_category.items()},
            "cost_by_model": {k: round(v, 6) for k, v in self.by_model.items()},
            "limits": self.limits.model_dump(),
        }
