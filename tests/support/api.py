"""Running AgentLab's REST API for tests: in-process (``httpx`` straight into the ASGI app) or on a real loopback port."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI

from agentlab.api.app import create_app
from agentlab.api.state import ApiState
from agentlab.core.config import AgentLabConfig
from agentlab.jobs.queue import JobQueue
from agentlab.netserve import ThreadedServer
from agentlab.services import Services
from tests.support.lab import make_config

TERMINAL = {
    "completed",
    "failed",
    "cancelled",
    "stopped_due_to_cost",
    "stopped_due_to_timeout",
    "stopped_due_to_step_limit",
}
HR_TOOLS = ["send_email", "get_weather", "calculator", "delete_file"]
HR_KNOWLEDGE = {"leave.md": "Employees receive 25 days of paid annual leave."}
SMALL_RUN = {"intensity": "quick", "max_tests": 6, "second_wave": False}  # about 20 tests, a second or two


def mock_target(name: str = "demo", *behaviors: str, **extra: Any) -> dict[str, Any]:
    """A deterministic in-process agent: no network and no Docker."""
    return {
        "name": name,
        "description": "HR assistant that answers from policy documents and can send email",
        "mock": {"behaviors": list(behaviors) or ["success"], "tools": HR_TOOLS, "knowledge": HR_KNOWLEDGE},
        **extra,
    }


def api_config(root: Path, **overrides: Any) -> AgentLabConfig:
    """Everything under ``root``; Docker is never assumed; reports are written as JSON and Markdown."""
    overrides.setdefault("formats", ["json", "md"])
    return make_config(root, **overrides)


@dataclass
class Api:
    client: httpx.AsyncClient
    services: Services
    state: ApiState
    app: FastAPI
    token: str | None = None

    async def wait(self, run_id: str, *, timeout: float = 90.0) -> dict[str, Any]:
        """Poll a run until it has ended; returns its final ``GET /test-runs/{id}``."""
        deadline = time.monotonic() + timeout
        while True:
            r = await self.client.get(f"/test-runs/{run_id}")
            assert r.status_code == 200, r.text
            body: dict[str, Any] = r.json()
            if body["status"] in TERMINAL:
                return body
            if time.monotonic() > deadline:
                raise AssertionError(f"run {run_id} did not finish in {timeout}s: {body['status']} {body['progress']}")
            await asyncio.sleep(0.1)

    async def run(self, target: dict[str, Any] | None = None, **body: Any) -> dict[str, Any]:
        """Start a small run and wait for it."""
        payload = {"target": target or mock_target(), "options": SMALL_RUN, **body}
        r = await self.client.post("/test-runs", json=payload)
        assert r.status_code == 202, r.text
        return await self.wait(r.json()["run_id"])

    async def plan(self, target: dict[str, Any] | None = None, **body: Any) -> dict[str, Any]:
        payload = {"target": target or mock_target(), "options": SMALL_RUN, "wait_seconds": 30, **body}
        r = await self.client.post("/test-plans", json=payload)
        assert r.status_code in (200, 202), r.text
        out: dict[str, Any] = r.json()
        assert out["status"] == "completed", out
        return out


@contextlib.asynccontextmanager
async def running_api(
    root: Path,
    *,
    token: str | None = None,
    config: AgentLabConfig | None = None,
    queue: JobQueue | None = None,
    start_worker: bool | None = None,
    allowed_hosts: tuple[str, ...] | None = ("testserver",),
    headers: dict[str, str] | None = None,
) -> AsyncIterator[Api]:
    """The API started the way ``agentlab serve`` starts it (lifespan included) and a client wired straight to it."""
    services = Services.create(config or api_config(root), base_dir=root)
    app = create_app(
        services, queue=queue, token=token, start_worker=start_worker, allowed_hosts=allowed_hosts, serve_ui=False
    )
    sent = dict(headers or {})
    if token:
        sent.setdefault("Authorization", f"Bearer {token}")
    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver", headers=sent, timeout=120
            ) as client:
                yield Api(client=client, services=services, state=app.state.agentlab, app=app, token=token)
    finally:
        services.store.db.dispose()


@contextlib.contextmanager
def product_logging() -> Iterator[io.StringIO]:
    """Logging set up the way the command line and the server set it up (a handler that masks secrets on the root logger),
    captured in a buffer, and put back afterwards."""
    from agentlab.cli.main import configure_logging

    root, package = logging.getLogger(), logging.getLogger("agentlab")
    saved = (root.handlers[:], root.level, package.level)
    out = io.StringIO()
    configure_logging(False)
    handler = root.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    handler.setStream(out)
    try:
        yield out
    finally:
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
        package.setLevel(saved[2])


@contextlib.contextmanager
def live_api(
    root: Path, *, token: str | None = None, config: AgentLabConfig | None = None, start_worker: bool | None = None
) -> Iterator[ThreadedServer]:
    """The API on a real loopback port in its own thread (needed to follow the event stream: httpx's ASGI transport
    returns a response only when it is complete)."""
    services = Services.create(config or api_config(root), base_dir=root)
    app = create_app(services, token=token, start_worker=start_worker, serve_ui=False)
    server = ThreadedServer(app, lifespan="on").start()
    try:
        yield server
    finally:
        server.stop()
        services.store.db.dispose()


@dataclass
class Frame:
    """One server-sent event as a client reads it."""

    event: str = "message"
    id: str | None = None
    data: str = ""
    retry: int | None = None
    comment: str | None = None

    def json(self) -> dict[str, Any]:
        out: dict[str, Any] = json.loads(self.data)
        return out


async def frames(response: httpx.Response) -> AsyncIterator[Frame]:
    """The events of a ``text/event-stream`` response, one at a time, as they arrive."""
    frame, seen = Frame(), False
    async for line in response.aiter_lines():
        if line == "":
            if seen:
                yield frame
            frame, seen = Frame(), False
            continue
        seen = True
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if not field:
            frame.comment = value
        elif field == "event":
            frame.event = value
        elif field == "id":
            frame.id = value
        elif field == "data":
            frame.data += value
        elif field == "retry":
            frame.retry = int(value)


async def queue_job(
    services: Services,
    queue: JobQueue,
    target: dict[str, Any] | None = None,
    *,
    kind: str = "run",
    options: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
) -> Any:
    """What the API does when it accepts a run: store the target, create the pending row and queue the job."""
    from agentlab.core.ids import new_id
    from agentlab.core.models import TargetSpec
    from agentlab.jobs.models import JobOptions, JobOverrides, JobSpec

    spec = TargetSpec(**(target or mock_target()))
    project = services.store.ensure_project("default")
    row = services.store.add_target(project["id"], spec)
    run_id = new_id()
    services.store.create_run(project["id"], row["id"], None, "full", {"queued": {}}, {}, run_id=run_id)
    services.store.update_run(run_id, totals={"queued_kind": kind})
    job = JobSpec(
        kind=kind,  # type: ignore[arg-type]
        run_id=run_id,
        project="default",
        target=spec,
        options=JobOptions(**{**SMALL_RUN, **(options or {})}),
        overrides=JobOverrides(**(overrides or {})),
    )
    await queue.submit(job)
    return job
