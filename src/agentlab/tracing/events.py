"""Structured event model and in-process event bus (spec section 48).

Every event carries ``event_id, run_id, test_id, timestamp, type, payload,
redaction_status``. Payloads are redacted when the event is created, so no
subscriber (database writer, live UI stream, log) ever sees raw secrets.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import Field

from agentlab.core.enums import EventType, RedactionStatus
from agentlab.core.ids import new_id, utcnow
from agentlab.core.models.base import Model
from agentlab.security.redactor import get_redactor

log = logging.getLogger(__name__)


class Event(Model):
    event_id: str = Field(default_factory=new_id)
    run_id: str
    test_id: str | None = None
    timestamp: datetime = Field(default_factory=utcnow)
    type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)
    redaction_status: RedactionStatus = RedactionStatus.NOT_SCANNED

    @classmethod
    def create(cls, run_id: str, type: EventType, payload: dict[str, Any] | None = None,
               test_id: str | None = None) -> Event:
        result = get_redactor().redact(payload or {})
        return cls(run_id=run_id, test_id=test_id, type=type, payload=result.value,
                   redaction_status=result.status)


Subscriber = Callable[[Event], None]


class EventBus:
    """Synchronous fan-out bus with optional asyncio queues for streaming consumers."""

    def __init__(self) -> None:
        self._subs: list[Subscriber] = []
        self._queues: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[Event]]] = []
        self._lock = threading.Lock()
        self.history: list[Event] = []
        self.keep_history = True

    def subscribe(self, fn: Subscriber) -> None:
        with self._lock:
            self._subs.append(fn)

    def stream(self) -> asyncio.Queue[Event]:
        loop = asyncio.get_running_loop()
        q: asyncio.Queue[Event] = asyncio.Queue()
        with self._lock:
            self._queues.append((loop, q))
            for e in self.history:
                q.put_nowait(e)
        return q

    def publish(self, event: Event) -> Event:
        with self._lock:
            subs = list(self._subs)
            queues = list(self._queues)
            if self.keep_history:
                self.history.append(event)
        for fn in subs:
            try:
                fn(event)
            except Exception:  # a broken subscriber must not break a run
                log.exception("event subscriber failed")
        for loop, q in queues:
            try:
                loop.call_soon_threadsafe(q.put_nowait, event)
            except RuntimeError:
                pass
        return event

    def emit(self, run_id: str, type: EventType, payload: dict[str, Any] | None = None,
             test_id: str | None = None) -> Event:
        return self.publish(Event.create(run_id, type, payload, test_id))
