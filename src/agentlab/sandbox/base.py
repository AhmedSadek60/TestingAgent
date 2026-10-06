"""Sandbox abstractions (spec section 10).

A sandbox is a disposable, resource-limited execution environment for untrusted code (a target
repository, a coding agent, a command-line agent). Providers are plug-ins; if none can guarantee
isolation, callers get :class:`SandboxUnavailable` and must **fail closed** - AgentLab never falls
back to executing untrusted code on the host.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Literal

from pydantic import Field

from agentlab.core.errors import SandboxUnavailable
from agentlab.core.models.base import Model
from agentlab.core.plugins import Registry


class SandboxSpec(Model):
    image: str = "mirror.gcr.io/library/python:3.12-slim"
    workdir: str = "/workspace"
    env: dict[str, str] = Field(default_factory=dict)
    cpus: float = 1.0
    memory_mb: int = 1024
    pids_limit: int = 256
    disk_mb: int = 512
    timeout_seconds: float = 300.0
    user: str = "65534:65534"
    network: Literal["none", "internal", "allowlist"] = "none"
    allow_hosts: list[str] = Field(default_factory=list)
    read_only_rootfs: bool = True
    extra_tmpfs: list[str] = Field(
        default_factory=list, description="more writable, size-limited in-memory directories (for example /agent)"
    )
    allow_pull: bool = True
    max_output_bytes: int = 2_000_000
    labels: dict[str, str] = Field(default_factory=dict)


class ExecResult(Model):
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration_ms: float = 0.0
    truncated: bool = False
    command: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class Sandbox(ABC):
    spec: SandboxSpec
    id: str

    @abstractmethod
    async def exec(
        self,
        command: list[str],
        *,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
        stdin: bytes | None = None,
        workdir: str | None = None,
    ) -> ExecResult: ...

    @abstractmethod
    async def put_dir(
        self, local: Path, dest: str | None = None, *, max_files: int = 5000, max_bytes: int = 100 * 1024 * 1024
    ) -> int: ...

    @abstractmethod
    async def put_file(self, dest: str, data: bytes, mode: int = 0o644) -> None: ...

    @abstractmethod
    async def get_file(self, path: str, max_bytes: int = 5 * 1024 * 1024) -> bytes: ...

    @abstractmethod
    async def export_dir(self, path: str | None, local: Path, max_bytes: int = 100 * 1024 * 1024) -> int: ...

    @abstractmethod
    async def close(self) -> None: ...

    def attach_argv(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        workdir: str | None = None,
        timeout: float | None = None,
    ) -> list[str]:
        """Argument vector of a host process that runs ``command`` *inside* this sandbox with its standard streams
        connected to the host process. This is how a server that speaks over stdio (an MCP server) is reached: the
        untrusted program still executes only in the sandbox, the host only runs the container client."""
        raise SandboxUnavailable(f"{type(self).__name__} cannot attach to a process over standard streams")

    async def __aenter__(self) -> Sandbox:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()


class SandboxProvider(ABC):
    name = "abstract"

    @abstractmethod
    async def available(self) -> tuple[bool, str]:
        """(is isolation guaranteed, human-readable reason)."""

    @abstractmethod
    async def create(self, spec: SandboxSpec) -> Sandbox: ...


SANDBOX_PROVIDERS: Registry[type[SandboxProvider]] = Registry("sandbox")
