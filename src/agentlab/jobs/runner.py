"""Doing a job: design a plan, or design and execute a run, with safe cancellation and an honest record if it goes wrong.

The orchestrator already records its own failures. What this adds is the part that belongs to whoever *hosts* a run: a
cancellation request that arrives from another process, a liveness mark so an abandoned run can be recognised, and the
guarantee that a run never stays "running" because something unexpected escaped."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging

from agentlab.core.enums import RunStatus
from agentlab.core.errors import AgentLabError
from agentlab.core.ids import as_utc, utcnow
from agentlab.jobs.models import JobSpec
from agentlab.jobs.queue import JobQueue
from agentlab.orchestrator import TestOrchestratorAgent
from agentlab.orchestrator.options import RunOutcome
from agentlab.security.redactor import redact
from agentlab.services import Services
from agentlab.tracing import EventBus

log = logging.getLogger(__name__)

TERMINAL = {
    RunStatus.COMPLETED.value,
    RunStatus.FAILED.value,
    RunStatus.CANCELLED.value,
    RunStatus.STOPPED_DUE_TO_COST.value,
    RunStatus.STOPPED_DUE_TO_TIMEOUT.value,
    RunStatus.STOPPED_DUE_TO_STEP_LIMIT.value,
}


class JobRunner:
    """Executes jobs one at a time per call; a :class:`~agentlab.jobs.worker.Worker` calls it concurrently."""

    def __init__(self, services: Services, queue: JobQueue, *, poll_seconds: float = 1.0) -> None:
        self.services = services
        self.queue = queue
        self.poll_seconds = poll_seconds
        self._active: dict[str, TestOrchestratorAgent] = {}

    @property
    def active(self) -> list[str]:
        return sorted(self._active)

    # ------------------------------------------------------------------------------------------------ one job
    async def run(self, job: JobSpec) -> RunOutcome | None:
        run_id = job.run_id
        reason = await self.queue.cancel_requested(run_id)
        if reason is not None:  # cancelled while it waited: nothing was started, so there is nothing to wind down
            self._finish(run_id, RunStatus.CANCELLED, note=f"cancelled before it started: {reason}")
            await self.queue.release(run_id)
            return None

        with contextlib.suppress(Exception):  # "pending" means waiting for a worker; this one has it now
            self.services.store.update_run(run_id, status=RunStatus.RUNNING.value)
        sv = self._services_for(job)
        bus = EventBus()
        orch = TestOrchestratorAgent(sv, bus=bus)
        self._active[run_id] = orch
        watcher = asyncio.create_task(self._watch(run_id, orch), name=f"watch-{run_id}")
        try:
            return await self._execute(job, orch)
        except asyncio.CancelledError:  # the worker is shutting down
            self._finish(run_id, RunStatus.CANCELLED, note="the worker stopped while the run was in progress")
            raise
        except AgentLabError as exc:  # the orchestrator has recorded it; this is the log line
            log.warning("run %s ended with an error: %s", run_id, redact(str(exc)))
            self._finish(run_id, RunStatus.FAILED, note=str(exc))
            return None
        except Exception as exc:
            log.exception("run %s failed unexpectedly", run_id)
            self._finish(run_id, RunStatus.FAILED, note=f"{type(exc).__name__}: {str(exc)[:300]}")
            return None
        finally:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await watcher
            self._active.pop(run_id, None)
            await self.queue.release(run_id)
            with contextlib.suppress(Exception):
                await sv.close_browser()

    async def _execute(self, job: JobSpec, orch: TestOrchestratorAgent) -> RunOutcome:
        options = job.run_options()
        prepared = await orch.prepare(job.target, options)
        try:
            if job.kind == "plan":
                return orch.finish_plan_only(prepared)
            return await orch.execute(prepared, cancel=orch.token_for(job.run_id))
        finally:
            await prepared.aclose()

    def _services_for(self, job: JobSpec) -> Services:
        """The shared services with this job's configuration. Store, artifacts, credentials, providers and sandbox are
        shared; the browser is the run's own, and it is the only thing a run closes."""
        config = job.overrides.apply(self.services.config)
        sv = dataclasses.replace(self.services, config=config, _browser_pool=None)
        sv.web_discoverer = sv.discover_web
        return sv

    # ------------------------------------------------------------------------------------------ cancellation
    async def _watch(self, run_id: str, orch: TestOrchestratorAgent) -> None:
        """While a run is in progress: say so (so it is not mistaken for an abandoned one) and pass a cancellation request
        from any process on to the run. The run stops safely: the step in progress finishes, the rest is skipped, and what
        ran is analysed and reported."""
        cancelled = False
        while True:
            try:
                await self.queue.heartbeat(run_id)
                if not cancelled:
                    reason = await self.queue.cancel_requested(run_id)
                    if reason is not None:
                        cancelled = orch.cancel(run_id, reason)
            except Exception as exc:  # a hiccup of the queue must not stop the run
                log.warning("queue unreachable while watching run %s: %s", run_id, exc)
            await asyncio.sleep(self.poll_seconds)

    def cancel_local(self, run_id: str, reason: str) -> bool:
        orch = self._active.get(run_id)
        return bool(orch and orch.cancel(run_id, reason))

    # ------------------------------------------------------------------------------------------------- records
    def _finish(self, run_id: str, status: RunStatus, *, note: str) -> None:
        """Make sure the run row ends in a terminal state. A run the orchestrator already closed is left as it is."""
        store = self.services.store
        try:
            row = store.get_run(run_id)
            if row["status"] in TERMINAL:
                return
            store.update_run(
                run_id,
                status=status.value,
                finished_at=utcnow(),
                error=None
                if status == RunStatus.CANCELLED
                else {"kind": "INFRASTRUCTURE_ERROR", "message": str(redact(note))[:500]},
            )
        except Exception:
            log.exception("could not record the end of run %s", run_id)


async def reap_orphans(services: Services, queue: JobQueue, *, grace_seconds: float | None = None) -> list[str]:
    """Close the runs a stopped worker left behind.

    A run that is ``running`` but whose worker has stopped reporting (and a ``pending`` run whose job is gone from the queue
    long enough ago) will never finish by itself; it is marked failed, with the reason, instead of looking busy forever.
    ``grace_seconds`` is how recently a run must have changed to be left alone (default: the queue's worker timeout).
    Returns the ids closed."""
    grace_seconds = queue.heartbeat_ttl if grace_seconds is None else grace_seconds
    closed: list[str] = []
    now = utcnow()
    waiting = await queue.depth()
    for row in services.store.list_runs(limit=500):
        status = row["status"]
        if status not in {RunStatus.RUNNING.value, RunStatus.PENDING.value}:
            continue
        run_id = str(row["id"])
        if await queue.alive(run_id):
            continue
        touched = row.get("updated_at") or row.get("created_at")
        if touched is not None and (now - as_utc(touched)).total_seconds() < grace_seconds:
            continue  # changed recently: starting up, or between two phases
        if status == RunStatus.PENDING.value and waiting:
            continue  # it may simply be waiting its turn
        services.store.update_run(
            run_id,
            status=RunStatus.FAILED.value,
            finished_at=now,
            error={
                "kind": "INFRASTRUCTURE_ERROR",
                "message": "the worker stopped before the run finished (no sign of life for "
                f"{int(grace_seconds)} seconds); start it again to retry",
            },
        )
        closed.append(run_id)
    return closed


async def reap_forever(services: Services, queue: JobQueue) -> None:
    """Look for runs nobody is working on, now and then, until cancelled.

    A worker does this, and so does an API process that has none of its own: whichever of them is still up closes what a
    dead worker left behind, so a run does not look busy for ever."""
    every = max(2.0, queue.heartbeat_ttl / 3)
    while True:
        try:
            for run_id in await reap_orphans(services, queue):
                log.warning("run %s was abandoned by its worker and has been marked failed", run_id)
        except Exception:
            log.exception("could not look for abandoned runs")
        await asyncio.sleep(every)
