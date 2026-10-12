"""The log AgentLab keeps for issue reports: bounded, redacted, correlated, and unable to hurt the server.

Every secret below is invented and assembled from fragments so no scanner mistakes this file for a leak."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from agentlab.core import context
from agentlab.core.config import DiagnosticsConfig
from agentlab.feedback.logcapture import LogCapture, install_log_capture, uninstall_log_capture
from agentlab.storage import orm
from agentlab.storage.db import Database

OPENAI = "sk-" + "proj" + "-" + "Zq81LmN4xW7sR2pK9vB3cD6e"


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/log.db")
    database.migrate()
    return database


def record(
    message: str, *, level: int = logging.WARNING, name: str = "agentlab.test", **extra: object
) -> logging.LogRecord:
    made = logging.LogRecord(name, level, __file__, 1, message, None, None)
    for key, value in extra.items():
        setattr(made, key, value)
    return made


def rows(db: Database) -> list[orm.LogEventRow]:
    with db.session() as s:
        found = list(s.scalars(select(orm.LogEventRow).order_by(orm.LogEventRow.id)))
        s.expunge_all()
        return found


def test_a_line_is_kept_with_its_level_logger_process_and_what_it_belongs_to(db: Database) -> None:
    handler = LogCapture(db, process="worker")
    token = context.run_id.set("run-42")
    try:
        handler.emit(record("the target did not answer", level=logging.ERROR, name="agentlab.jobs.runner"))
    finally:
        context.run_id.reset(token)
    handler.emit(record("a plain warning"))
    assert handler.write_batch() == 2
    first, second = rows(db)
    assert (first.level, first.logger, first.process, first.run_id) == (
        "ERROR",
        "agentlab.jobs.runner",
        "worker",
        "run-42",
    )
    assert (second.run_id, second.request_id, second.principal) == (None, None, "")
    assert first.message == "the target did not answer"


def test_what_is_logged_is_masked_before_it_is_kept_message_and_traceback_alike(db: Database) -> None:
    handler = LogCapture(db, process="api")
    try:
        raise RuntimeError(f"provider said: bad key {OPENAI}")
    except RuntimeError:
        import sys

        made = record(f"call failed with Authorization: Bearer {OPENAI}", level=logging.ERROR)
        made.exc_info = sys.exc_info()
    handler.emit(made)
    handler.write_batch()
    (kept,) = rows(db)
    assert OPENAI not in kept.message and "Zq81LmN4xW7sR2pK9vB3cD6e" not in kept.message
    assert kept.exception and "RuntimeError" in kept.exception and OPENAI not in kept.exception


def test_only_lines_at_or_above_the_level_and_never_its_own_are_kept(db: Database) -> None:
    handler = LogCapture(db, process="api", level=logging.WARNING)
    logger = logging.getLogger("agentlab.capture.level.test")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.addHandler(handler)
    try:
        logger.info("fine")
        logger.warning("worth keeping")
        logging.getLogger("agentlab.feedback.logcapture").warning("its own noise")
        handler.emit(record("its own", name="agentlab.feedback.logcapture.writer"))
    finally:
        logger.removeHandler(handler)
    handler.write_batch()
    assert [r.message for r in rows(db)] == ["worth keeping"]


def test_a_burst_beyond_the_queue_drops_the_oldest_and_counts_them(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentlab.feedback import logcapture

    monkeypatch.setattr(logcapture, "QUEUE_LIMIT", 10)
    handler = LogCapture(db, process="api")
    assert handler._queue.maxlen == 10
    for n in range(25):
        handler.emit(record(f"line {n}"))
    assert handler.dropped == 15 and len(handler._queue) == 10
    handler.write_batch()
    assert [r.message for r in rows(db)] == [f"line {n}" for n in range(15, 25)], "the newest are what is kept"


def test_a_long_message_and_traceback_are_cut(db: Database) -> None:
    handler = LogCapture(db, process="api")
    handler.emit(record("x" * 10_000))
    handler.write_batch()
    assert len(rows(db)[0].message) == 2_000


def test_the_table_is_pruned_by_age_and_by_count(db: Database) -> None:
    now = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
    handler = LogCapture(db, process="api", retention_hours=2, max_rows=5, clock=lambda: now)
    with db.session() as s:
        for n in range(8):
            s.add(
                orm.LogEventRow(
                    ts=now - timedelta(minutes=10 * n), level="WARNING", logger="a", process="api", message=f"m{n}"
                )
            )
        s.add(orm.LogEventRow(ts=now - timedelta(hours=5), level="WARNING", logger="a", process="api", message="old"))
    handler.prune()
    kept = [r.message for r in rows(db)]
    assert "old" not in kept and len(kept) == 5, kept
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(orm.LogEventRow)) == 5


def test_a_database_that_fails_costs_the_lines_and_never_raises(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    handler = LogCapture(db, process="api")
    handler.emit(record("will not be written"))

    def broken() -> None:
        raise RuntimeError("database is gone")

    monkeypatch.setattr(db, "session", broken)
    assert handler.write_batch() == 1 and handler.failed == 1 and handler.written == 0
    handler.prune()  # also silent
    handler.emit(record("and logging still works"))  # nothing raised


def test_the_writer_thread_writes_in_the_background_and_close_writes_what_is_left(db: Database) -> None:
    handler = LogCapture(db, process="api", flush_seconds=0.05).start()
    handler.emit(record("background"))
    deadline = time.time() + 5
    while time.time() < deadline and not rows(db):
        time.sleep(0.05)
    assert [r.message for r in rows(db)] == ["background"]
    handler.emit(record("at the end"))
    handler.close()
    assert [r.message for r in rows(db)] == ["background", "at the end"]


def test_installing_attaches_to_the_package_logger_and_uninstalling_detaches_it(db: Database) -> None:
    package = logging.getLogger("agentlab")
    before = list(package.handlers)
    assert install_log_capture(db, DiagnosticsConfig(capture_logs=False), process="api") is None
    handler = install_log_capture(db, DiagnosticsConfig(), process="api")
    assert handler is not None and handler in package.handlers
    logging.getLogger("agentlab.something").warning("kept %s", "this")
    uninstall_log_capture(handler)
    assert handler not in package.handlers and package.handlers == before
    assert [r.message for r in rows(db)] == ["kept this"]
    uninstall_log_capture(None)  # harmless
