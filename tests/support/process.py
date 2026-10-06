"""Running ``agentlab`` as a real operating-system process, the way it is run in production: its own interpreter, its own
environment, output to a log file, stopped with a signal."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import yaml

from agentlab.core.config import AgentLabConfig


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def write_config(root: Path, config: AgentLabConfig) -> Path:
    """The configuration as ``agentlab.yaml`` (what ``--config`` reads)."""
    path = root / "agentlab.yaml"
    path.write_text(yaml.safe_dump(config.model_dump(mode="json")), encoding="utf-8")
    return path


@dataclass
class Proc:
    popen: subprocess.Popen[bytes]
    log: Path
    args: list[str] = field(default_factory=list)

    @property
    def output(self) -> str:
        return self.log.read_text(encoding="utf-8", errors="replace") if self.log.exists() else ""

    @property
    def running(self) -> bool:
        return self.popen.poll() is None

    def stop(self, sig: int = signal.SIGTERM, timeout: float = 30.0) -> int:
        """Signal the process and wait for it; returns its exit code (a process that ignores the signal is killed, and the
        caller sees that as a failure through the code)."""
        if self.running:
            self.popen.send_signal(sig)
        try:
            return self.popen.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.popen.kill()
            self.popen.wait(timeout=10)
            raise AssertionError(
                f"did not stop within {timeout}s of {signal.Signals(sig).name}\n{self.output}"
            ) from None

    def wait(self, timeout: float = 30.0) -> int:
        try:
            return self.popen.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.popen.kill()
            raise AssertionError(f"still running after {timeout}s\n{self.output}") from None

    def kill(self) -> None:
        """The process dies at once and gets no chance to clean up."""
        if self.running:
            self.popen.kill()
        self.popen.wait(timeout=10)


def start_agentlab(args: list[str], *, cwd: Path, env: dict[str, str] | None = None, name: str = "agentlab") -> Proc:
    """``agentlab <args>`` with an environment of its own: nothing of the developer's AgentLab settings leaks in."""
    base = {k: v for k, v in os.environ.items() if not k.startswith("AGENTLAB_")}
    base.update({"PYTHONUNBUFFERED": "1", "HOME": str(cwd), "NO_COLOR": "1", "COLUMNS": "200", **(env or {})})
    log = cwd / f"{name}.log"
    handle = log.open("wb")
    try:
        popen = subprocess.Popen(  # noqa: S603 - the interpreter of this test run and arguments of the test
            [sys.executable, "-m", "agentlab.cli.main", *args],
            cwd=cwd,
            env=base,
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
    finally:
        handle.close()
    return Proc(popen=popen, log=log, args=args)


def wait_until_up(url: str, proc: Proc, timeout: float = 40.0) -> None:
    """Wait for ``url`` to answer 200, failing at once (with the log) if the process ends first."""
    deadline = time.monotonic() + timeout
    while True:
        if not proc.running:
            raise AssertionError(f"exited with {proc.popen.returncode} before it was up\n{proc.output}")
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            proc.kill()
            raise AssertionError(f"not up after {timeout}s\n{proc.output}")
        time.sleep(0.1)


def poll(check, *, timeout: float = 60.0, every: float = 0.1, what: str = "the condition"):  # type: ignore[no-untyped-def]
    """Call ``check`` until it returns something truthy; returns that."""
    deadline = time.monotonic() + timeout
    while True:
        value = check()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"{what} did not happen within {timeout}s")
        time.sleep(every)
