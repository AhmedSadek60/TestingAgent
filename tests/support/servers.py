"""Run ASGI apps on an ephemeral localhost port in a background thread (test helper)."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import uvicorn


class ThreadedServer:
    def __init__(self, app, host: str = "127.0.0.1", port: int = 0) -> None:  # type: ignore[no-untyped-def]
        sock = socket.socket()
        sock.bind((host, port))
        self.port = sock.getsockname()[1]
        self.host = host
        config = uvicorn.Config(app, log_level="error", lifespan="off")
        self.server = uvicorn.Server(config)
        self._sock = sock
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        import asyncio

        asyncio.run(self.server.serve(sockets=[self._sock]))

    def start(self) -> ThreadedServer:
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started and time.time() < deadline:
            time.sleep(0.02)
        if not self.server.started:
            raise RuntimeError("server failed to start")
        return self

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


@contextmanager
def serve(app) -> Iterator[ThreadedServer]:  # type: ignore[no-untyped-def]
    srv = ThreadedServer(app).start()
    try:
        yield srv
    finally:
        srv.stop()
