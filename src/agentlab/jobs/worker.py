"""The worker: takes jobs from a queue and runs them, a few at a time.

The same class is what ``agentlab serve`` embeds (with the in-memory queue) and what ``agentlab worker`` runs on its own
(with Redis). Stopping it lets running jobs finish when they can and records the ones that could not."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from agentlab.jobs.queue import JobQueue
from agentlab.jobs.runner import JobRunner, reap_forever
from agentlab.services import Services

log = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        services: Services,
        queue: JobQueue,
        *,
        concurrency: int | None = None,
        poll_seconds: float = 1.0,
    ) -> None:
        self.services = services
        self.queue = queue
        self.concurrency = concurrency or services.config.queue.max_concurrent_runs
        self.runner = JobRunner(services, queue, poll_seconds=poll_seconds)
        self._stop = asyncio.Event()
        self._tasks: set[asyncio.Task[None]] = set()
        self._main: asyncio.Task[None] | None = None
        self._reaper: asyncio.Task[None] | None = None

    # ----------------------------------------------------------------------------------------------- lifecycle
    def start(self) -> None:
        """Start working in the background of the running event loop."""
        if self._main is None:
            self._stop.clear()
            self._main = asyncio.create_task(self._loop(), name="agentlab-worker")
            self._reaper = asyncio.create_task(reap_forever(self.services, self.queue), name="agentlab-reaper")

    async def stop(self, *, drain_seconds: float = 0.0, wind_down_seconds: float = 10.0) -> None:
        """Stop taking jobs. Runs in progress get ``drain_seconds`` to finish on their own; the ones still going are then
        asked to stop at a safe point (the step in progress ends, the rest is skipped, what ran is analysed and reported)
        and get ``wind_down_seconds`` for that; whatever is still going after it is cancelled."""
        self._stop.set()
        if self._reaper is not None:
            self._reaper.cancel()
        if self._main is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(self._main, timeout=5.0 + self.queue_poll)
        pending = [t for t in self._tasks if not t.done()]
        if pending and drain_seconds > 0:
            await asyncio.wait(pending, timeout=drain_seconds)
        pending = [t for t in self._tasks if not t.done()]
        if pending:
            for run_id in self.runner.active:
                self.runner.cancel_local(run_id, "the worker is stopping")
            if wind_down_seconds > 0:
                await asyncio.wait(pending, timeout=wind_down_seconds)
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._main = self._reaper = None

    async def run_forever(self) -> None:
        """Work until cancelled (what ``agentlab worker`` does)."""
        self.start()
        try:
            assert self._main is not None
            await self._main
        finally:
            await self.stop(drain_seconds=0)

    @property
    def queue_poll(self) -> float:
        return 1.0

    @property
    def busy(self) -> int:
        return len([t for t in self._tasks if not t.done()])

    # --------------------------------------------------------------------------------------------------- loops
    async def _loop(self) -> None:
        slots = asyncio.Semaphore(self.concurrency)
        failures = 0
        while not self._stop.is_set():
            await slots.acquire()
            if self._stop.is_set():
                slots.release()
                break
            try:
                job = await self.queue.claim(timeout=self.queue_poll)
                failures = 0
            except Exception as exc:  # the queue is unreachable: keep trying, slowly, and say so once in a while
                slots.release()
                failures += 1
                if failures in (1, 10) or failures % 60 == 0:
                    log.error("cannot read the job queue (%s); retrying", exc)
                await asyncio.sleep(min(5.0, 0.5 * failures))
                continue
            if job is None:
                slots.release()
                continue
            task = asyncio.create_task(self._run_one(job, slots), name=f"run-{job.run_id}")
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run_one(self, job: object, slots: asyncio.Semaphore) -> None:
        from agentlab.jobs.models import JobSpec

        assert isinstance(job, JobSpec)
        try:
            await self.runner.run(job)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("job %s crashed the runner", job.job_id)
        finally:
            slots.release()
