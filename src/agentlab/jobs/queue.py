"""Job queues (spec section 47: "Redis + worker abstraction").

Two implementations of one small contract:

* :class:`InlineQueue` keeps jobs in memory and is worked by the process that submitted them. It needs nothing installed and
  is what ``agentlab serve`` uses by default.
* :class:`RedisQueue` keeps jobs in Redis, so any number of ``agentlab worker`` processes can work them, the API stays
  responsive while runs execute, and a queued run survives a restart of the API.

The queue only moves jobs, cancellation requests and liveness marks. The run itself is stored in the database, which is
what every reader (the API, the CLI, the web interface) looks at.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from typing import Any, Protocol, runtime_checkable

from agentlab.core.config import QueueConfig
from agentlab.core.errors import InfrastructureError
from agentlab.jobs.models import JobSpec

HEARTBEAT_TTL_SECONDS = 45.0  # default: a run whose worker has been silent this long is considered orphaned


@runtime_checkable
class JobQueue(Protocol):
    """What the API and the workers need from a queue."""

    name: str
    heartbeat_ttl: float
    """Seconds without a sign of life after which a run is no longer considered to be worked on."""

    async def submit(self, job: JobSpec) -> None: ...

    async def claim(self, timeout: float) -> JobSpec | None:
        """The next job, waiting up to ``timeout`` seconds for one; ``None`` if there was none."""
        ...

    async def withdraw(self, run_id: str) -> bool:
        """Take a job back that no worker has claimed. ``True`` if it was still waiting."""
        ...

    async def request_cancel(self, run_id: str, reason: str) -> None: ...

    async def cancel_requested(self, run_id: str) -> str | None:
        """The reason given when cancellation of ``run_id`` was requested, else ``None``."""
        ...

    async def heartbeat(self, run_id: str) -> None: ...

    async def alive(self, run_id: str) -> bool:
        """Whether a worker has reported on ``run_id`` recently."""
        ...

    async def release(self, run_id: str) -> None:
        """A run is over: forget its cancellation request and heartbeat."""
        ...

    async def depth(self) -> int: ...

    async def close(self) -> None: ...


# ==================================================================================================== in memory
class InlineQueue:
    """In-process queue. Safe to use from several tasks of one event loop."""

    name = "inline"

    def __init__(self, *, heartbeat_ttl: float = HEARTBEAT_TTL_SECONDS) -> None:
        self.heartbeat_ttl = heartbeat_ttl
        self._jobs: deque[JobSpec] = deque()
        self._wakeup = asyncio.Event()
        self._cancel: dict[str, str] = {}
        self._beats: dict[str, float] = {}

    async def submit(self, job: JobSpec) -> None:
        self._jobs.append(job)
        self._wakeup.set()

    async def claim(self, timeout: float) -> JobSpec | None:
        deadline = time.monotonic() + timeout
        while True:
            if self._jobs:
                job = self._jobs.popleft()
                if not self._jobs:
                    self._wakeup.clear()
                return job
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._wakeup.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wakeup.wait(), remaining)

    async def withdraw(self, run_id: str) -> bool:
        for job in list(self._jobs):
            if job.run_id == run_id:
                self._jobs.remove(job)
                return True
        return False

    async def request_cancel(self, run_id: str, reason: str) -> None:
        self._cancel[run_id] = reason

    async def cancel_requested(self, run_id: str) -> str | None:
        return self._cancel.get(run_id)

    async def heartbeat(self, run_id: str) -> None:
        self._beats[run_id] = time.monotonic()

    async def alive(self, run_id: str) -> bool:
        seen = self._beats.get(run_id)
        return seen is not None and time.monotonic() - seen < self.heartbeat_ttl

    async def release(self, run_id: str) -> None:
        self._cancel.pop(run_id, None)
        self._beats.pop(run_id, None)

    async def depth(self) -> int:
        return len(self._jobs)

    async def close(self) -> None:
        self._jobs.clear()


# ======================================================================================================== Redis
class RedisQueue:
    """Jobs in a Redis list; cancellation requests and heartbeats are keys with an expiry, so nothing lingers."""

    name = "redis"

    def __init__(
        self,
        url: str,
        *,
        ttl_seconds: int = 86_400,
        prefix: str = "agentlab",
        heartbeat_ttl: float = HEARTBEAT_TTL_SECONDS,
    ) -> None:
        self.heartbeat_ttl = heartbeat_ttl
        try:
            import redis.asyncio as aioredis
        except ImportError as exc:
            raise InfrastructureError(
                "the redis queue needs the 'redis' package: pip install 'agentlab[redis]' (or set queue.backend: inline)"
            ) from exc
        self._redis: Any = aioredis.from_url(url, decode_responses=True)
        self._ttl = ttl_seconds
        self._list = f"{prefix}:jobs"
        self._prefix = prefix

    def _key(self, kind: str, run_id: str) -> str:
        return f"{self._prefix}:{kind}:{run_id}"

    async def ping(self) -> None:
        try:
            await self._redis.ping()
        except Exception as exc:
            raise InfrastructureError(f"cannot reach the Redis queue: {type(exc).__name__}") from exc

    async def submit(self, job: JobSpec) -> None:
        try:
            await self._redis.lpush(self._list, job.model_dump_json())
        except Exception as exc:
            raise InfrastructureError(f"cannot queue the job in Redis: {type(exc).__name__}") from exc

    async def claim(self, timeout: float) -> JobSpec | None:
        try:
            popped = await self._redis.brpop(self._list, timeout=max(1, int(timeout)))
        except Exception as exc:
            raise InfrastructureError(f"cannot read the Redis queue: {type(exc).__name__}") from exc
        if not popped:
            return None
        return JobSpec.model_validate_json(popped[1])

    async def withdraw(self, run_id: str) -> bool:
        for raw in await self._redis.lrange(self._list, 0, -1):
            try:
                job = JobSpec.model_validate_json(raw)
            except ValueError:
                continue
            if job.run_id == run_id:
                return bool(await self._redis.lrem(self._list, 1, raw))
        return False

    async def request_cancel(self, run_id: str, reason: str) -> None:
        await self._redis.set(self._key("cancel", run_id), reason, ex=self._ttl)

    async def cancel_requested(self, run_id: str) -> str | None:
        value = await self._redis.get(self._key("cancel", run_id))
        return str(value) if value is not None else None

    async def heartbeat(self, run_id: str) -> None:
        await self._redis.set(self._key("beat", run_id), "1", ex=max(1, int(self.heartbeat_ttl)))

    async def alive(self, run_id: str) -> bool:
        return bool(await self._redis.exists(self._key("beat", run_id)))

    async def release(self, run_id: str) -> None:
        await self._redis.delete(self._key("cancel", run_id), self._key("beat", run_id))

    async def depth(self) -> int:
        return int(await self._redis.llen(self._list))

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self._redis.aclose()


def create_queue(config: QueueConfig, *, redis_url: str | None = None) -> JobQueue:
    """The queue the configuration asks for. ``redis_url`` (from ``AGENTLAB_REDIS_URL``) wins over the file."""
    if config.backend == "redis":
        return RedisQueue(
            redis_url or config.redis_url,
            ttl_seconds=config.job_ttl_seconds,
            prefix=config.key_prefix,
            heartbeat_ttl=config.worker_timeout_seconds,
        )
    return InlineQueue(heartbeat_ttl=config.worker_timeout_seconds)
