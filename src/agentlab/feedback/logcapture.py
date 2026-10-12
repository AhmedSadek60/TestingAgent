"""A bounded, redacted copy of what AgentLab logged, kept so it can be attached to an issue report.

AgentLab writes its log to the process's standard error and keeps none. An issue report that says "it failed at 10:42" needs
the lines around 10:42, so a handler on the ``agentlab`` logger keeps the lines at or above a level in the database, with the
run and request they belong to. It is deliberately small and cannot hurt the server:

* ``emit`` only queues a record; a thread writes batches, and a record the queue has no room for is dropped (and counted);
* every message and traceback is redacted before it is queued, and cut to a fixed size;
* the table is pruned by age and by count, so it cannot grow without bound;
* nothing here raises into the code that logged, or logs by itself (no recursion).

Nothing outside AgentLab's own logger is kept: the lines of other libraries, request bodies and responses never are.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select

from agentlab.core import context
from agentlab.core.config import DiagnosticsConfig
from agentlab.core.ids import utcnow
from agentlab.security.redactor import get_redactor
from agentlab.storage import orm
from agentlab.storage.db import Database

MESSAGE_LIMIT = 2_000
EXCEPTION_LIMIT = 6_000
QUEUE_LIMIT = 5_000
PRUNE_EVERY_SECONDS = 60.0
OWN = "agentlab.feedback.logcapture"


class LogCapture(logging.Handler):
    def __init__(
        self,
        db: Database,
        *,
        process: str,
        level: int = logging.WARNING,
        retention_hours: int = 48,
        max_rows: int = 50_000,
        flush_seconds: float = 1.0,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        super().__init__(level)
        self.db = db
        self.process = process[:12]
        self.retention = timedelta(hours=retention_hours)
        self.max_rows = max_rows
        self.flush_seconds = flush_seconds
        self._now = clock
        self._queue: deque[dict[str, Any]] = deque(maxlen=QUEUE_LIMIT)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_prune = 0.0
        self.dropped = 0
        self.failed = 0
        self.written = 0

    # ------------------------------------------------------------------------------------------------ logging
    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(OWN):
            return
        try:
            redactor = get_redactor()
            message = redactor.redact_text(record.getMessage())[0][:MESSAGE_LIMIT]
            exception = None
            if record.exc_info:
                exception = redactor.redact_text(logging.Formatter().formatException(record.exc_info))[0][
                    :EXCEPTION_LIMIT
                ]
            entry = {
                "ts": datetime.fromtimestamp(record.created, tz=self._now().tzinfo),
                "level": record.levelname[:10],
                "logger": record.name[:120],
                "process": self.process,
                "run_id": getattr(record, "run_id", None) or context.run_id.get(),
                "request_id": getattr(record, "request_id", None) or context.request_id.get(),
                "principal": str(getattr(record, "principal", "") or "")[:80],
                "message": message,
                "exception": exception,
            }
            if len(self._queue) == self._queue.maxlen:
                self.dropped += 1
            self._queue.append(entry)
            if len(self._queue) >= 200:
                self._wake.set()
        except Exception:  # noqa: S110 - logging must never fail because the capture did
            pass

    # ---------------------------------------------------------------------------------------------- the writer
    def start(self) -> LogCapture:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="agentlab-log-capture", daemon=True)
            self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.flush_seconds)
            self._wake.clear()
            self.write_batch()

    def write_batch(self) -> int:
        """Write what is queued. Returns how many lines were written."""
        batch: list[dict[str, Any]] = []
        while self._queue and len(batch) < 1_000:
            try:
                batch.append(self._queue.popleft())
            except IndexError:
                break
        if batch:
            try:
                with self.db.session() as s:
                    s.add_all(orm.LogEventRow(**entry) for entry in batch)
                self.written += len(batch)
            except Exception:  # noqa: BLE001 - a database that is busy or gone costs these lines, not the server
                self.failed += len(batch)
        if time.monotonic() - self._last_prune >= PRUNE_EVERY_SECONDS:
            self._last_prune = time.monotonic()
            self.prune()
        return len(batch)

    def prune(self) -> None:
        """Delete what is older than the retention and what is beyond the row limit (the oldest go first)."""
        try:
            with self.db.session() as s:
                L = orm.LogEventRow
                s.execute(delete(L).where(L.ts < self._now() - self.retention))
                newest = s.scalar(select(func.max(L.id)))
                if newest is not None:
                    s.execute(delete(L).where(L.id <= newest - self.max_rows))
        except Exception:  # noqa: S110, BLE001 - the next flush tries again
            pass

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        while self._queue:
            if self.write_batch() == 0:
                break
        super().close()


def install_log_capture(db: Database, config: DiagnosticsConfig, *, process: str) -> LogCapture | None:
    """Start keeping the log of this process, if the owner has not turned it off. Returns the handler to uninstall."""
    if not config.capture_logs:
        return None
    level = logging.INFO if config.log_level == "INFO" else logging.WARNING
    handler = LogCapture(
        db, process=process, level=level, retention_hours=config.log_retention_hours, max_rows=config.log_max_rows
    ).start()
    package = logging.getLogger("agentlab")
    package.addHandler(handler)
    if package.level == logging.NOTSET or package.level > level:
        package.setLevel(level)
    return handler


def uninstall_log_capture(handler: LogCapture | None) -> None:
    if handler is None:
        return
    logging.getLogger("agentlab").removeHandler(handler)
    handler.close()
