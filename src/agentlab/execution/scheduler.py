"""Scheduler: parallel execution where safe, serial where tests share mutable state (spec section 39).

Tests are grouped by an *isolation key*. Tests in one group run sequentially (they share a user
session, account, repository, database or mutable memory); different groups run in parallel up to
``max_parallel``. When the target adapter cannot serve parallel sessions the whole run is serial.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from agentlab.core.enums import RiskClass, TestStatus
from agentlab.core.models import TestCase, TestResult
from agentlab.execution.executor import TestExecutor

SERIAL_CATEGORIES = {"memory", "coding", "browser"}


def isolation_key(test: TestCase) -> str | None:
    """Explicit key wins; otherwise infer one from shared mutable resources the test declares."""
    if test.isolation_key:
        return test.isolation_key
    if test.required_credentials:
        return f"credential:{test.required_credentials[0]}"
    if test.context.get("workspace"):
        return f"workspace:{test.context['workspace']}"
    if test.risk_level == RiskClass.HIGH_IMPACT:
        return "high_impact"
    if test.category.lower() in SERIAL_CATEGORIES and test.context.get("shared_state", False):
        return f"shared:{test.category.lower()}"
    return None


def plan_groups(tests: list[TestCase]) -> list[list[TestCase]]:
    groups: dict[str, list[TestCase]] = {}
    singles: list[list[TestCase]] = []
    for t in tests:
        k = isolation_key(t)
        if k is None:
            singles.append([t])
        else:
            groups.setdefault(k, []).append(t)
    return [*groups.values(), *singles]


class Scheduler:
    def __init__(
        self,
        executor: TestExecutor,
        *,
        max_parallel: int = 4,
        on_result: Callable[[TestResult], Awaitable[None] | None] | None = None,
    ) -> None:
        self.executor = executor
        self.max_parallel = max(1, max_parallel)
        self.on_result = on_result

    def effective_parallelism(self) -> int:
        adapters = self.executor.d.runtime.adapters.values()
        if any(not a.capabilities.parallel_sessions for a in adapters):
            return 1
        return self.max_parallel

    async def run(self, tests: list[TestCase]) -> list[TestResult]:
        sem = asyncio.Semaphore(self.effective_parallelism())
        results: dict[str, TestResult] = {}

        async def run_group(group: list[TestCase]) -> None:
            for t in group:
                async with sem:
                    res = await self.executor.run_test(t)
                results[t.id] = res
                if self.on_result:
                    maybe = self.on_result(res)
                    if asyncio.iscoroutine(maybe):
                        await maybe

        groups = plan_groups(tests)
        tasks = [asyncio.create_task(run_group(g)) for g in groups]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return [results[t.id] for t in tests if t.id in results]


def is_stopped_run(results: list[TestResult]) -> TestStatus | None:
    """If a run-level limit ended the run early, report which one."""
    for r in results:
        if r.status.is_stopped:
            return r.status
    return None
