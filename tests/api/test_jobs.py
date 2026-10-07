"""Jobs: the queue, the runner that executes one, the worker that takes them, and what happens to a run whose worker dies."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentlab.core.config import AgentLabConfig, QueueConfig
from agentlab.core.errors import PolicyBlocked
from agentlab.core.ids import utcnow
from agentlab.core.models import TargetSpec
from agentlab.jobs import (
    InlineQueue,
    JobOptions,
    JobOverrides,
    JobRunner,
    JobSpec,
    Worker,
    create_queue,
    reap_orphans,
)
from agentlab.orchestrator import TestOrchestratorAgent
from agentlab.services import Services
from tests.support.api import TERMINAL, api_config, mock_target, product_logging, queue_job, running_api

SECRET_TAIL = "Zq81LmN4" + "xW7sR2pK" + "9vB3cD6e"  # a made-up key, never written out in one piece


@pytest.fixture
def services(tmp_path: Path):
    sv = Services.create(api_config(tmp_path), base_dir=tmp_path)
    yield sv
    sv.store.db.dispose()


async def settle(services: Services, run_id: str, timeout: float = 60.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        row = services.store.get_run(run_id)
        if row["status"] in TERMINAL:
            return row
        assert asyncio.get_running_loop().time() < deadline, f"run did not end: {row['status']}"
        await asyncio.sleep(0.05)


# ======================================================================================================= the queue
async def test_the_in_process_queue_hands_jobs_out_in_order_and_waits_for_the_next(services: Services) -> None:
    q = InlineQueue()
    first = await queue_job(services, q)
    second = await queue_job(services, q)
    assert await q.depth() == 2
    assert (await q.claim(0.1)).run_id == first.run_id
    assert (await q.claim(0.1)).run_id == second.run_id
    assert await q.claim(0.05) is None, "nothing waiting: it gives up after the timeout"

    waiting = asyncio.create_task(q.claim(5.0))
    await asyncio.sleep(0.05)
    assert not waiting.done()
    third = await queue_job(services, q)
    assert (await asyncio.wait_for(waiting, 2.0)).run_id == third.run_id, (
        "a waiting worker is woken as soon as a job arrives"
    )


async def test_a_job_nobody_has_taken_can_be_taken_back_and_one_that_was_taken_cannot(services: Services) -> None:
    q = InlineQueue()
    a, b = await queue_job(services, q), await queue_job(services, q)
    assert await q.withdraw(b.run_id) is True and await q.depth() == 1
    assert await q.withdraw(b.run_id) is False
    taken = await q.claim(0.1)
    assert taken.run_id == a.run_id
    assert await q.withdraw(a.run_id) is False, "a job a worker holds is cancelled through the run, not the queue"


async def test_a_worker_that_goes_quiet_stops_looking_alive_after_its_timeout() -> None:
    q = InlineQueue(heartbeat_ttl=0.3)
    await q.heartbeat("r1")
    assert await q.alive("r1") is True
    await asyncio.sleep(0.4)
    assert await q.alive("r1") is False, "a worker silent for too long is not alive"
    assert create_queue(QueueConfig(worker_timeout_seconds=7)).heartbeat_ttl == 7, "the timeout is configurable"


# ===================================================================================== what a job carries
def test_a_job_survives_being_written_down_and_read_back_and_carries_no_secret(services: Services) -> None:
    job = JobSpec(
        kind="run",
        run_id="r1",
        target=TargetSpec(**mock_target()),
        options=JobOptions(intensity="quick", only_tests=["MEM-*"], requirements=["never refund over 100"]),
        overrides=JobOverrides(max_cost_usd=1.5, repetitions=2, report_formats=["json"]),
    )
    again = JobSpec.model_validate_json(job.model_dump_json())
    assert again == job
    assert again.run_options().only_tests == ["MEM-*"] and again.run_options().run_id == "r1"
    assert again.run_options().plan_only is False
    assert JobSpec(kind="plan", run_id="p", target=job.target).run_options().plan_only is True


def test_overrides_change_limits_for_one_run_and_never_touch_security_or_the_servers_own_settings() -> None:
    base = AgentLabConfig()
    changed = JobOverrides(
        max_cost_usd=2.0, max_execution_time_seconds=60, max_parallel=2, repetitions=3, report_formats=[]
    ).apply(base)
    assert changed.limits.max_cost_usd == 2.0 and changed.limits.max_execution_time_seconds == 60
    assert changed.max_parallel == 2 and changed.evaluation.repetitions == 3 and changed.reporting.formats == []
    assert base.limits.max_cost_usd != 2.0 and base.evaluation.repetitions == 1, (
        "the server's configuration is not modified"
    )
    for forbidden in (
        "sandbox_required",
        "allow_production_targets",
        "block_metadata_endpoints",
        "security",
        "storage",
        "server",
        "providers",
    ):
        with pytest.raises(ValidationError):
            JobOverrides(**{forbidden: True})
    for bad in (
        {"max_cost_usd": 0},
        {"max_parallel": 0},
        {"max_parallel": 1000},
        {"repetitions": 0},
        {"repetitions": 99},
    ):
        with pytest.raises(ValidationError):
            JobOverrides(**bad)


# ===================================================================================================== the runner
async def test_the_runner_designs_a_plan_and_executes_a_run_and_leaves_nothing_behind(services: Services) -> None:
    q = InlineQueue()
    runner = JobRunner(services, q, poll_seconds=0.05)
    plan_job = await queue_job(services, q, kind="plan")
    outcome = await runner.run(await q.claim(0.1))
    assert outcome is not None
    row = services.store.get_run(plan_job.run_id)
    assert row["status"] == "completed" and row["totals"]["plan_only"] is True and row["totals"]["warnings"] is not None
    assert services.store.list_results(plan_job.run_id) == []

    run_job = await queue_job(services, q)
    await runner.run(await q.claim(0.1))
    done = services.store.get_run(run_job.run_id)
    assert done["status"] == "completed" and done["started_at"] and done["finished_at"]
    assert services.store.list_results(run_job.run_id)
    assert runner.active == [], "nothing is left registered as running"
    assert await q.alive(run_job.run_id) is False and await q.cancel_requested(run_job.run_id) is None


async def test_a_run_cancelled_while_it_waited_never_starts(services: Services) -> None:
    q = InlineQueue()
    runner = JobRunner(services, q, poll_seconds=0.05)
    job = await queue_job(services, q)
    await q.request_cancel(job.run_id, "changed my mind")
    assert await runner.run(await q.claim(0.1)) is None
    row = services.store.get_run(job.run_id)
    assert row["status"] == "cancelled" and row["started_at"] is None
    assert services.store.list_events(job.run_id) == [] and services.store.list_results(job.run_id) == []
    assert await q.cancel_requested(job.run_id) is None, "the request is cleared"


async def test_a_run_that_breaks_unexpectedly_is_recorded_as_failed_not_left_running(
    services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "sk-" + "proj-" + SECRET_TAIL

    async def explode(self: object, *_a: object, **_k: object) -> None:
        raise RuntimeError("the orchestrator broke while holding " + secret)

    monkeypatch.setattr(TestOrchestratorAgent, "prepare", explode)
    q = InlineQueue()
    job = await queue_job(services, q)
    with product_logging() as logged:
        assert await JobRunner(services, q).run(await q.claim(0.1)) is None
    row = services.store.get_run(job.run_id)
    assert row["status"] == "failed" and row["finished_at"]
    assert "RuntimeError" in row["error"]["message"] and row["error"]["kind"] == "INFRASTRUCTURE_ERROR"
    assert SECRET_TAIL not in str(row["error"]), "the record of the failure carries no secret"
    text = logged.getvalue()
    assert "failed unexpectedly" in text and "RuntimeError" in text, "the operator can see that it happened and why"
    assert SECRET_TAIL not in text and "[REDACTED" in text, "the log shows the failure without the secret it held"


async def test_a_run_that_ends_with_a_known_error_is_recorded_with_that_error_masked(
    services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "sk-" + "proj-" + SECRET_TAIL

    async def refuse(self: object, *_a: object, **_k: object) -> None:
        raise PolicyBlocked("the endpoint rejected " + secret)

    monkeypatch.setattr(TestOrchestratorAgent, "prepare", refuse)
    q = InlineQueue()
    job = await queue_job(services, q)
    with product_logging() as logged:
        await JobRunner(services, q).run(await q.claim(0.1))
    row = services.store.get_run(job.run_id)
    assert row["status"] == "failed" and "rejected" in row["error"]["message"]
    assert SECRET_TAIL not in str(row["error"]) and SECRET_TAIL not in logged.getvalue()


async def test_a_run_gets_its_own_configuration_and_its_own_browser(services: Services) -> None:
    q = InlineQueue()
    runner = JobRunner(services, q)
    job = await queue_job(services, q, overrides={"max_cost_usd": 0.25, "repetitions": 2})
    mine = runner._services_for(job)
    assert mine.config.limits.max_cost_usd == 0.25 and mine.config.evaluation.repetitions == 2
    assert services.config.limits.max_cost_usd != 0.25, "the shared configuration is untouched"
    assert mine.store is services.store and mine.credentials is services.credentials, (
        "storage and credentials are shared"
    )
    assert mine._browser_pool is None and mine is not services


# ===================================================================================================== the worker
async def test_a_worker_takes_jobs_in_turn_and_never_more_than_its_concurrency(services: Services) -> None:
    q = InlineQueue()
    worker = Worker(services, q, concurrency=1, poll_seconds=0.05)
    jobs = [await queue_job(services, q, mock_target("demo", "success", "slow")) for _ in range(2)]
    worker.start()
    try:
        seen = 0
        while any(services.store.get_run(j.run_id)["status"] not in TERMINAL for j in jobs):
            seen = max(seen, worker.busy)
            await asyncio.sleep(0.02)
    finally:
        await worker.stop()
    assert seen == 1, "one run at a time"
    first, second = (services.store.get_run(j.run_id) for j in jobs)
    assert first["status"] == second["status"] == "completed"
    assert first["finished_at"] <= second["started_at"], "the second began after the first ended"


async def test_a_worker_with_room_runs_jobs_side_by_side(services: Services) -> None:
    q = InlineQueue()
    worker = Worker(services, q, concurrency=2, poll_seconds=0.05)
    jobs = [await queue_job(services, q, mock_target("demo", "success", "slow")) for _ in range(2)]
    worker.start()
    try:
        peak = 0
        while any(services.store.get_run(j.run_id)["status"] not in TERMINAL for j in jobs):
            peak = max(peak, worker.busy)
            await asyncio.sleep(0.01)
    finally:
        await worker.stop()
    assert peak == 2


async def start_slow_run(services: Services, q: InlineQueue, worker: Worker) -> JobSpec:
    """A run with one test at a time, each a little slow, that has got going (three tests have finished)."""
    job = await queue_job(
        services,
        q,
        mock_target("demo", "success", "slow"),
        options={"intensity": "quick", "second_wave": False, "max_tests": None},
        overrides={"max_parallel": 1},
    )
    worker.start()
    for _ in range(600):
        done = [e for e in services.store.list_events(job.run_id) if e["type"] == "TestCompleted"]
        if len(done) >= 3:
            return job
        await asyncio.sleep(0.02)
    raise AssertionError("the run never got going")


async def test_stopping_a_worker_ends_its_runs_at_a_safe_point_and_what_ran_is_still_reported(
    services: Services,
) -> None:
    q = InlineQueue()
    worker = Worker(services, q, concurrency=1, poll_seconds=0.05)
    job = await start_slow_run(services, q, worker)
    await worker.stop(drain_seconds=0, wind_down_seconds=60)
    row = services.store.get_run(job.run_id)
    assert row["status"] == "cancelled" and row["finished_at"], "a stopped worker never leaves the run 'running'"
    assert worker.busy == 0 and worker.runner.active == []
    statuses = {r.status.value for r in services.store.list_results(job.run_id)}
    assert "skipped" in statuses and len(statuses) > 1, "what ran is kept, what had not is recorded as skipped"
    assert services.store.list_reports(job.run_id), "the run was analysed and reported although it was cut short"
    cancelled = [e for e in services.store.list_events(job.run_id) if e["type"] == "RunCancelled"]
    assert len(cancelled) == 1 and "worker is stopping" in str(cancelled[0]["payload"])


async def test_a_worker_that_cannot_wind_a_run_down_in_time_still_closes_it(services: Services) -> None:
    q = InlineQueue()
    worker = Worker(services, q, concurrency=1, poll_seconds=0.05)
    job = await start_slow_run(services, q, worker)
    await worker.stop(drain_seconds=0, wind_down_seconds=0)
    row = services.store.get_run(job.run_id)
    assert row["status"] == "cancelled" and row["finished_at"], (
        "even when it had to be cut off, the run is not left running"
    )
    assert worker.busy == 0 and worker.runner.active == []


class RunThatLastsUntilAsked:
    """Stands in for the runner: one run that goes on until it is asked to stop, as a long run does."""

    def __init__(self) -> None:
        self.active: list[str] = []
        self.asked: list[tuple[str, str]] = []
        self._ended = asyncio.Event()

    async def run(self, job: JobSpec) -> None:
        self.active.append(job.run_id)
        try:
            await self._ended.wait()
        finally:
            self.active.remove(job.run_id)

    def cancel_local(self, run_id: str, reason: str) -> bool:
        self.asked.append((run_id, reason))
        self._ended.set()
        return True


async def test_a_worker_with_every_slot_busy_asks_its_runs_to_stop_without_waiting_for_them(
    services: Services,
) -> None:
    """The loop that takes jobs is parked on the one busy slot. It holds no job, so stopping must not wait for the run
    to end before asking it to stop: with ``drain_seconds=0`` the request is made at once, and what the run does with it
    does not depend on how fast the machine is."""
    q = InlineQueue()
    worker = Worker(services, q, concurrency=1, poll_seconds=0.05)
    runner = RunThatLastsUntilAsked()
    worker.runner = runner  # type: ignore[assignment]
    await queue_job(services, q)
    worker.start()
    for _ in range(100):
        if runner.active:
            break
        await asyncio.sleep(0.02)
    assert runner.active, "the run got going and holds the only slot"
    began = asyncio.get_running_loop().time()
    await worker.stop(drain_seconds=0, wind_down_seconds=5)
    took = asyncio.get_running_loop().time() - began
    assert [reason for _, reason in runner.asked] == ["the worker is stopping"]
    assert took < 3.0, f"stopping waited {took:.1f} s before it asked the run to stop"
    assert worker.busy == 0 and runner.active == []


async def test_a_worker_keeps_working_when_the_queue_is_briefly_unreachable(services: Services) -> None:
    class Flaky(InlineQueue):
        def __init__(self) -> None:
            super().__init__()
            self.fail = 2

        async def claim(self, timeout: float):  # type: ignore[no-untyped-def]
            if self.fail:
                self.fail -= 1
                raise ConnectionError("queue unreachable")
            return await super().claim(timeout)

    q = Flaky()
    worker = Worker(services, q, concurrency=1, poll_seconds=0.05)
    job = await queue_job(services, q)
    worker.start()
    try:
        row = await settle(services, job.run_id)
    finally:
        await worker.stop()
    assert row["status"] == "completed"


# ===================================================================================== a worker that died
async def test_a_run_whose_worker_has_gone_is_closed_with_the_reason(services: Services) -> None:
    q = InlineQueue()
    job = await queue_job(services, q)
    await q.claim(0.1)  # taken by a worker that then died: nothing will ever finish it
    services.store.update_run(job.run_id, status="running", started_at=utcnow() - timedelta(minutes=10))
    assert await reap_orphans(services, q, grace_seconds=0) == [job.run_id]
    row = services.store.get_run(job.run_id)
    assert row["status"] == "failed" and "worker stopped" in row["error"]["message"] and row["finished_at"]
    assert await reap_orphans(services, q, grace_seconds=0) == [], "a run is closed once"


async def test_a_run_that_is_alive_or_recent_or_waiting_its_turn_is_left_alone(services: Services) -> None:
    q = InlineQueue()
    alive = await queue_job(services, q)
    await q.claim(0.1)
    services.store.update_run(alive.run_id, status="running")
    await q.heartbeat(alive.run_id)
    silent = await queue_job(services, q)
    await q.claim(0.1)
    services.store.update_run(silent.run_id, status="running")  # no sign of life, but it changed a moment ago
    waiting = await queue_job(services, q)  # still in the queue: it is only waiting its turn

    assert await reap_orphans(services, q, grace_seconds=3600) == [], "a recent change is never an abandoned run"
    assert await reap_orphans(services, q, grace_seconds=0) == [silent.run_id], "only the one that went silent"
    assert services.store.get_run(alive.run_id)["status"] == "running", "it reports in, so it is working"
    assert services.store.get_run(waiting.run_id)["status"] == "pending", "it is only waiting its turn"
    assert services.store.get_run(silent.run_id)["status"] == "failed"


async def test_a_waiting_run_whose_job_was_lost_with_the_queue_is_closed_too(services: Services) -> None:
    q = InlineQueue()
    job = await queue_job(services, q)
    assert await q.withdraw(job.run_id)  # what a restart of the API does to an in-process queue
    assert await q.depth() == 0
    assert await reap_orphans(services, q, grace_seconds=0) == [job.run_id]
    row = services.store.get_run(job.run_id)
    assert row["status"] == "failed" and "start it again" in row["error"]["message"]


async def test_the_api_and_a_separate_worker_can_share_a_queue(tmp_path: Path) -> None:
    q = InlineQueue()
    async with running_api(tmp_path, queue=q, start_worker=False) as api:
        accepted = await api.client.post(
            "/test-runs",
            json={"target": mock_target(), "options": {"intensity": "quick", "max_tests": 3, "second_wave": False}},
        )
        run_id = accepted.json()["run_id"]
        await asyncio.sleep(0.3)
        assert (await api.client.get(f"/test-runs/{run_id}")).json()["status"] == "pending", (
            "nobody is working the queue"
        )
        elsewhere = Worker(api.services, q, concurrency=1, poll_seconds=0.05)  # what `agentlab worker` is
        elsewhere.start()
        try:
            final = await api.wait(run_id)
        finally:
            await elsewhere.stop()
        assert final["status"] == "completed"
        assert (await api.client.get(f"/test-runs/{run_id}/results")).json(), (
            "the worker's results are what the API serves"
        )
