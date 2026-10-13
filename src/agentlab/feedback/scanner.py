"""Malware scanning of an attachment, when the owner has a scanner.

AgentLab does not ship a scanner. ``feedback.attachments.scanner_command`` can name any command-line scanner that follows the
convention of ``clamscan`` (exit 0: clean, 1: infected, anything else: it failed). With none configured an attachment is
recorded as ``not_scanned`` and is described that way everywhere: AgentLab never says a file was scanned when no scanner ran.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agentlab.security.redactor import redact

ScanStatus = Literal["not_scanned", "clean", "infected", "error"]


@dataclass(frozen=True)
class ScanResult:
    status: ScanStatus
    detail: str = ""


class MalwareScanner:
    def __init__(self, command: list[str], timeout_seconds: int = 60) -> None:
        self.command = list(command)
        self.timeout = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.command)

    def scan(self, data: bytes) -> ScanResult:
        """Scan ``data``. The file is written under a fixed name in a private folder that is removed afterwards, so nothing a
        person supplied reaches the command line."""
        if not self.configured:
            return ScanResult("not_scanned", "no malware scanner is configured on this server")
        with tempfile.TemporaryDirectory(prefix="agentlab-scan-") as folder:
            os.chmod(folder, 0o700)
            path = Path(folder) / "sample"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            argv = [str(path) if part == "{path}" else part for part in self.command]
            if "{path}" not in self.command:
                argv.append(str(path))
            try:
                done = subprocess.run(  # noqa: S603 - an argument list from the owner's configuration, never a shell
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    check=False,
                    cwd=folder,
                    env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": folder, "LC_ALL": "C"},
                )
            except subprocess.TimeoutExpired:
                return ScanResult("error", f"the scanner did not finish within {self.timeout} seconds")
            except OSError as exc:
                return ScanResult("error", f"the scanner could not be started: {type(exc).__name__}")
        said = redact((done.stdout or done.stderr or "").strip())[:160]
        if done.returncode == 0:
            return ScanResult("clean", said)
        if done.returncode == 1:
            return ScanResult("infected", said)
        return ScanResult("error", f"the scanner exited with status {done.returncode}: {said}")
