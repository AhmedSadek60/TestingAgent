"""What the API's handlers share: the configured services, the queue and the worker."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from fastapi import Request

from agentlab.api.security import TokenGuard
from agentlab.jobs.queue import JobQueue
from agentlab.jobs.worker import Worker
from agentlab.services import Services


@dataclass
class ApiState:
    services: Services
    queue: JobQueue
    guard: TokenGuard
    worker: Worker | None = None
    uploads_dir: Path = field(default_factory=lambda: Path(".agentlab/uploads"))
    allowed_roots: list[Path] = field(default_factory=list)
    max_upload_bytes: int = 25 * 1024 * 1024
    ui_dir: Path | None = None

    @property
    def store(self):  # type: ignore[no-untyped-def]
        return self.services.store


def state_of(request: Request) -> ApiState:
    st: ApiState = request.app.state.agentlab
    return st
