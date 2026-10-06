"""Serving fixture apps: on an ephemeral port in a background thread (self-tests) or in the foreground (``agentlab
fixtures serve``). Fixtures only ever bind to loopback unless the caller explicitly asks otherwise."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import uvicorn


class ThreadedServer:
    """An ASGI app on its own thread and event loop, listening on a free port of ``host``."""

    def __init__(self, app: Any, host: str = "127.0.0.1", port: int = 0) -> None:
        sock = socket.socket()
        sock.bind((host, port))
        self.port = sock.getsockname()[1]
        self.host = host
        self.server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
        self._sock = sock
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        asyncio.run(self.server.serve(sockets=[self._sock]))

    def start(self) -> ThreadedServer:
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started and time.time() < deadline:
            time.sleep(0.02)
        if not self.server.started:
            raise RuntimeError("the fixture server failed to start")
        return self

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


@contextmanager
def serve(app: Any) -> Iterator[ThreadedServer]:
    srv = ThreadedServer(app).start()
    try:
        yield srv
    finally:
        srv.stop()


def serve_forever(app: Any, host: str = "127.0.0.1", port: int = 8765) -> None:
    uvicorn.run(app, host=host, port=port, log_level="warning", lifespan="off")
