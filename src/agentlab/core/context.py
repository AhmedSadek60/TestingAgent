"""Identifiers that follow a unit of work through the code.

A log line says what happened; these say *for what*. They are set where the work starts (a request enters the API, a run
starts in a worker) and read where a log line is kept (``agentlab.feedback.logcapture``), so what was logged while a run
executed can be found again without every call that logs having to pass the id along."""

from __future__ import annotations

from contextvars import ContextVar

#: The run being executed, set by the worker for the length of the run.
run_id: ContextVar[str | None] = ContextVar("agentlab_run_id", default=None)
#: The API request being served. Made by the server, never taken from the client (a client could name another person's).
request_id: ContextVar[str | None] = ContextVar("agentlab_request_id", default=None)
