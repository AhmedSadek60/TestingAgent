"""What every job queue promises, checked against the in-process queue and (with a server) the Redis one."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator

import pytest

from agentlab.core.errors import InfrastructureError
from agentlab.core.ids import new_id
from agentlab.core.models import TargetSpec
from agentlab.jobs import InlineQueue, JobOptions, JobQueue, JobSpec, RedisQueue
from tests.support.api import mock_target
from tests.support.redis import needs_redis, redis_param, redis_test_url


def a_job(**extra: object) -> JobSpec:
    return JobSpec(
        kind="run",
        run_id=new_id(),
        target=TargetSpec(**mock_target()),
        options=JobOptions(intensity="quick", only_tests=["MEM-*"]),
        **extra,  # type: ignore[arg-type]
    )


async def purge(queue: RedisQueue, prefix: str) -> None:
    keys = [key async for key in queue._redis.scan_iter(f"{prefix}:*")]
    if keys:
        await queue._redis.delete(*keys)


@pytest.fixture(params=["inline", redis_param])
async def queue(request: pytest.FixtureRequest) -> AsyncIterator[JobQueue]:
    if request.param == "inline":
        q: JobQueue = InlineQueue()
        yield q
        await q.close()
        return
    prefix = f"agentlab-test-{uuid.uuid4().hex[:10]}"
    redis_queue = RedisQueue(redis_test_url(), prefix=prefix)
    await redis_queue.ping()
    try:
        yield redis_queue
    finally:
        await purge(redis_queue, prefix)
        await redis_queue.close()


async def test_jobs_come_out_in_the_order_they_went_in_and_arrive_whole(queue: JobQueue) -> None:
    first, second = a_job(), a_job()
    await queue.submit(first)
    await queue.submit(second)
    assert await queue.depth() == 2
    got = await queue.claim(1)
    assert got == first, "the job is the one that was submitted, every field of it"
    assert got is not None and got.options.only_tests == ["MEM-*"] and got.target.mock is not None
    assert await queue.claim(1) == second
    assert await queue.depth() == 0


async def test_claiming_waits_for_a_job_and_gives_up_after_the_timeout(queue: JobQueue) -> None:
    started = time.monotonic()
    assert await queue.claim(1) is None, "nothing waiting"
    assert 0.8 <= time.monotonic() - started < 3

    waiting = asyncio.create_task(queue.claim(10))
    await asyncio.sleep(0.2)
    assert not waiting.done()
    job = a_job()
    await queue.submit(job)
    assert await asyncio.wait_for(waiting, 3) == job, "a worker that is waiting gets the job as soon as it arrives"


async def test_a_job_nobody_has_taken_can_be_taken_back_once(queue: JobQueue) -> None:
    a, b, c = a_job(), a_job(), a_job()
    for job in (a, b, c):
        await queue.submit(job)
    assert await queue.withdraw(b.run_id) is True
    assert await queue.withdraw(b.run_id) is False
    assert await queue.withdraw("never-queued") is False
    assert await queue.depth() == 2
    assert await queue.claim(1) == a
    assert await queue.withdraw(a.run_id) is False, "a job a worker holds is cancelled through its run, not the queue"
    assert await queue.claim(1) == c


async def test_each_job_goes_to_exactly_one_of_several_workers(queue: JobQueue) -> None:
    jobs = [a_job() for _ in range(12)]
    for job in jobs:
        await queue.submit(job)
    taken: dict[str, list[str]] = {"w1": [], "w2": [], "w3": []}

    async def work(name: str) -> None:
        while (job := await queue.claim(1)) is not None:
            taken[name].append(job.run_id)
            await asyncio.sleep(0.01)

    await asyncio.gather(*(work(n) for n in taken))
    everything = [run_id for ids in taken.values() for run_id in ids]
    assert sorted(everything) == sorted(j.run_id for j in jobs), "all of them, none twice"
    assert sum(1 for ids in taken.values() if ids) >= 2, "the work was shared"


async def test_a_cancellation_request_is_remembered_until_the_run_is_released(queue: JobQueue) -> None:
    run_id = new_id()
    assert await queue.cancel_requested(run_id) is None
    await queue.request_cancel(run_id, "wrong target: ünïcode ✓")
    assert await queue.cancel_requested(run_id) == "wrong target: ünïcode ✓"
    assert await queue.cancel_requested(new_id()) is None, "only for that run"
    await queue.release(run_id)
    assert await queue.cancel_requested(run_id) is None


async def test_a_sign_of_life_lasts_until_the_run_is_released(queue: JobQueue) -> None:
    run_id = new_id()
    assert await queue.alive(run_id) is False
    await queue.heartbeat(run_id)
    assert await queue.alive(run_id) is True and await queue.alive(new_id()) is False
    await queue.release(run_id)
    assert await queue.alive(run_id) is False
    await queue.release(run_id)  # releasing twice is harmless


# ============================================================================================ Redis in particular
@needs_redis
async def test_what_redis_remembers_expires_by_itself() -> None:
    prefix = f"agentlab-test-{uuid.uuid4().hex[:10]}"
    queue = RedisQueue(redis_test_url(), prefix=prefix, ttl_seconds=600, heartbeat_ttl=20)
    try:
        run_id = new_id()
        await queue.request_cancel(run_id, "stop")
        await queue.heartbeat(run_id)
        cancel_ttl = await queue._redis.ttl(f"{prefix}:cancel:{run_id}")
        beat_ttl = await queue._redis.ttl(f"{prefix}:beat:{run_id}")
        assert 0 < cancel_ttl <= 600, "a request nobody reads does not stay for ever"
        assert 0 < beat_ttl <= 20, "a worker that dies stops looking alive"
    finally:
        await purge(queue, prefix)
        await queue.close()


@needs_redis
async def test_withdrawing_skips_entries_that_are_not_jobs() -> None:
    prefix = f"agentlab-test-{uuid.uuid4().hex[:10]}"
    queue = RedisQueue(redis_test_url(), prefix=prefix)
    try:
        await queue._redis.lpush(f"{prefix}:jobs", "{not json")
        job = a_job()
        await queue.submit(job)
        assert await queue.withdraw(job.run_id) is True
    finally:
        await purge(queue, prefix)
        await queue.close()


@needs_redis
async def test_two_installations_with_different_prefixes_do_not_see_each_others_jobs() -> None:
    one, two = (f"agentlab-test-{uuid.uuid4().hex[:10]}" for _ in range(2))
    a, b = RedisQueue(redis_test_url(), prefix=one), RedisQueue(redis_test_url(), prefix=two)
    try:
        await a.submit(a_job())
        assert await b.depth() == 0 and await b.claim(1) is None
        assert await a.depth() == 1
    finally:
        await purge(a, one)
        await purge(b, two)
        await a.close()
        await b.close()


async def test_an_unreachable_redis_is_reported_in_plain_words_without_the_password() -> None:
    password = "hunter2-" + "not-a-real-password"
    queue = RedisQueue(f"redis://queue-user:{password}@127.0.0.1:1/0")
    try:
        with pytest.raises(InfrastructureError) as caught:
            await queue.ping()
        assert "cannot reach the Redis queue" in str(caught.value) and password not in str(caught.value)
        with pytest.raises(InfrastructureError) as submit_failed:
            await queue.submit(a_job())
        assert password not in str(submit_failed.value) and "127.0.0.1" not in str(submit_failed.value)
        with pytest.raises(InfrastructureError):
            await queue.claim(1)
    finally:
        await queue.close()
