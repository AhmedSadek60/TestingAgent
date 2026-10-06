"""Docker sandbox provider using the ``docker`` CLI.

Hardening applied to every container (verified by ``tests/security/test_sandbox_escape.py``):

* ``--network none`` by default (``internal`` = a private, non-routable network; an allow-list mode
  needs an egress proxy and is reported as unsupported rather than faked)
* ``--read-only`` root filesystem; ``/workspace`` and ``/tmp`` are size-limited ``tmpfs``
  mounts (``noexec`` on /tmp)
* ``--cap-drop ALL``, ``--security-opt no-new-privileges``, non-root user, ``--pids-limit``,
  memory limit with swap disabled, CPU limit, ``nofile`` ulimit
* no Docker socket, no host mounts: files move in and out as tar streams over ``docker exec``
* every command runs under ``timeout -s KILL`` inside the container, with a host-side watchdog
  that removes the whole container if the client hangs
* containers are labelled ``agentlab=1`` and removed on close / at interpreter exit
"""

from __future__ import annotations

import asyncio
import atexit
import io
import os
import shutil
import subprocess
import tarfile
import time
import uuid
from pathlib import Path

from agentlab.core.errors import SandboxError, SandboxUnavailable
from agentlab.sandbox.base import SANDBOX_PROVIDERS, ExecResult, Sandbox, SandboxProvider, SandboxSpec

_LIVE: set[str] = set()


def _cleanup_all() -> None:  # pragma: no cover - interpreter shutdown
    docker = shutil.which("docker")
    if not docker:
        return
    for cid in list(_LIVE):
        subprocess.run([docker, "rm", "-f", cid], capture_output=True, timeout=30, check=False)  # noqa: S603


atexit.register(_cleanup_all)


async def _run(
    args: list[str], *, stdin: bytes | None = None, timeout: float = 60.0, max_bytes: int = 2_000_000
) -> tuple[int, bytes, bytes, bool, bool]:
    """Run a docker CLI command. Returns (code, stdout, stderr, timed_out, truncated)."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def _read(stream: asyncio.StreamReader | None) -> tuple[bytes, bool]:
        buf = bytearray()
        trunc = False
        while stream is not None:
            chunk = await stream.read(65536)
            if not chunk:
                break
            if len(buf) < max_bytes:
                buf += chunk[: max_bytes - len(buf)]
            else:
                trunc = True
        return bytes(buf), trunc

    async def _feed() -> None:
        if stdin is not None and proc.stdin is not None:
            try:
                proc.stdin.write(stdin)
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                proc.stdin.close()

    try:
        (out, t1), (err, t2), _ = await asyncio.wait_for(
            asyncio.gather(_read(proc.stdout), _read(proc.stderr), _feed()), timeout=timeout
        )
        code = await asyncio.wait_for(proc.wait(), timeout=5)
        return code, out, err, False, t1 or t2
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, b"", b"host-side watchdog timeout", True, False


class DockerSandbox(Sandbox):
    def __init__(self, provider: DockerSandboxProvider, spec: SandboxSpec, cid: str, network: str | None) -> None:
        self.provider = provider
        self.spec = spec
        self.id = cid
        self._network = network
        self._closed = False

    @property
    def _docker(self) -> str:
        return self.provider.docker

    async def exec(
        self,
        command: list[str],
        *,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
        stdin: bytes | None = None,
        workdir: str | None = None,
    ) -> ExecResult:
        if self._closed:
            raise SandboxError("sandbox is closed")
        timeout = timeout or self.spec.timeout_seconds
        args = [self._docker, "exec", "-i", "-w", workdir or self.spec.workdir]
        for k, v in {**self.spec.env, **(env or {})}.items():
            args += ["-e", f"{k}={v}"]
        wrapped = ["timeout", "-s", "KILL", str(max(1, int(timeout))), *command]
        args += [self.id, *wrapped]
        t0 = time.perf_counter()
        code, out, err, host_timeout, trunc = await _run(
            args, stdin=stdin, timeout=timeout + 15, max_bytes=self.spec.max_output_bytes
        )
        timed_out = host_timeout or code in (124, 137)
        if host_timeout:  # the client hung: the container is no longer trustworthy, remove it entirely
            await self.close()
        return ExecResult(
            exit_code=code,
            stdout=out.decode("utf-8", "replace"),
            stderr=err.decode("utf-8", "replace"),
            timed_out=timed_out,
            duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            truncated=trunc,
            command=command,
        )

    def attach_argv(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        workdir: str | None = None,
        timeout: float | None = None,
    ) -> list[str]:
        args = [self._docker, "exec", "-i", "-w", workdir or self.spec.workdir]
        for k, v in {**self.spec.env, **(env or {})}.items():
            args += ["-e", f"{k}={v}"]
        limit = max(1, int(timeout or self.spec.timeout_seconds))
        return [*args, self.id, "timeout", "-s", "KILL", str(limit), *command]

    async def put_dir(
        self, local: Path, dest: str | None = None, *, max_files: int = 5000, max_bytes: int = 100 * 1024 * 1024
    ) -> int:
        buf = io.BytesIO()
        count = total = 0
        root = local.resolve()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for path in sorted(root.rglob("*")):
                if path.is_symlink() or not path.is_file():
                    continue  # never copy symlinks (they could point at host files)
                rel = path.relative_to(root)
                if ".git" in rel.parts:
                    continue
                size = path.stat().st_size
                total += size
                count += 1
                if count > max_files or total > max_bytes:
                    raise SandboxError(
                        f"directory exceeds sandbox import limits ({max_files} files / {max_bytes} bytes)"
                    )
                info = tar.gettarinfo(str(path), arcname=str(rel))
                info.uid = info.gid = 65534
                info.uname = info.gname = ""
                with path.open("rb") as fh:
                    tar.addfile(info, fh)
        r = await self._tar_in(buf.getvalue(), dest or self.spec.workdir)
        if not r.ok:
            raise SandboxError(f"failed to copy files into sandbox: {r.stderr[:300]}")
        return count

    async def _tar_in(self, data: bytes, dest: str) -> ExecResult:
        return await self.exec(
            ["tar", "-xf", "-", "-C", dest, "--no-same-owner", "--no-same-permissions"], stdin=data, timeout=120
        )

    async def put_file(self, dest: str, data: bytes, mode: int = 0o644) -> None:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name=dest.lstrip("/"))
            info.size = len(data)
            info.mode = mode
            info.uid = info.gid = 65534
            tar.addfile(info, io.BytesIO(data))
        r = await self.exec(
            ["tar", "-xf", "-", "-C", "/", "--no-same-owner"], stdin=buf.getvalue(), timeout=60, workdir="/"
        )
        if not r.ok:
            raise SandboxError(f"failed to write {dest}: {r.stderr[:300]}")

    async def get_file(self, path: str, max_bytes: int = 5 * 1024 * 1024) -> bytes:
        r = await self.exec(["head", "-c", str(max_bytes), path], timeout=60)
        if not r.ok:
            raise SandboxError(f"cannot read {path}: {r.stderr[:200]}")
        return r.stdout.encode("utf-8", "surrogateescape")

    async def export_dir(self, path: str | None, local: Path, max_bytes: int = 100 * 1024 * 1024) -> int:
        """Copy a sandbox directory out as regular files only (links and devices are dropped)."""
        code, out, err, _t, trunc = await _run(
            [self._docker, "exec", "-w", "/", self.id, "tar", "-cf", "-", "-C", path or self.spec.workdir, "."],
            timeout=120,
            max_bytes=max_bytes,
        )
        if code != 0 or trunc:
            raise SandboxError(f"export failed: {err.decode('utf-8', 'replace')[:200] or 'output too large'}")
        local = local.resolve()
        local.mkdir(parents=True, exist_ok=True)
        n = 0
        with tarfile.open(fileobj=io.BytesIO(out), mode="r:") as tar:
            for m in tar.getmembers():
                target = (local / m.name).resolve()
                if local not in target.parents and target != local:
                    continue  # path traversal attempt inside the archive
                if m.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif m.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    f = tar.extractfile(m)
                    if f is not None:
                        target.write_bytes(f.read())
                        os.chmod(target, 0o600)
                        n += 1
        return n

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await _run([self._docker, "rm", "-f", self.id], timeout=60)
        _LIVE.discard(self.id)
        if self._network:
            await _run([self._docker, "network", "rm", self._network], timeout=30)


class DockerSandboxProvider(SandboxProvider):
    name = "docker"

    def __init__(self) -> None:
        self.docker = shutil.which("docker") or "docker"

    async def available(self) -> tuple[bool, str]:
        if not shutil.which("docker"):
            return False, "the 'docker' CLI is not installed"
        code, out, err, to, _ = await _run([self.docker, "version", "--format", "{{.Server.Version}}"], timeout=15)
        if code != 0 or to:
            return False, "the Docker daemon is not reachable: " + (
                err.decode("utf-8", "replace").strip()[:160] or "timeout"
            )
        return True, f"Docker {out.decode().strip()}"

    async def create(self, spec: SandboxSpec) -> Sandbox:
        ok, why = await self.available()
        if not ok:
            raise SandboxUnavailable(f"{why}; refusing to run untrusted code without isolation")
        if spec.network == "allowlist":
            raise SandboxUnavailable(
                "network 'allowlist' mode is not supported by this build (it would need an egress proxy); "
                "use 'none' or 'internal'"
            )
        code, _o, _e, _t, _ = await _run([self.docker, "image", "inspect", spec.image], timeout=30)
        if code != 0:
            if not spec.allow_pull:
                raise SandboxUnavailable(f"image {spec.image} is not present and pulling is disabled")
            code, _o, e, _t, _ = await _run([self.docker, "pull", "-q", spec.image], timeout=600)
            if code != 0:
                raise SandboxUnavailable(f"cannot pull image {spec.image}: {e.decode('utf-8', 'replace')[:200]}")
        name = f"agentlab-{uuid.uuid4().hex[:12]}"
        network_name: str | None = None
        net_args = ["--network", "none"]
        if spec.network == "internal":
            network_name = f"{name}-net"
            code, _o, e, _t, _ = await _run([self.docker, "network", "create", "--internal", network_name], timeout=30)
            if code != 0:
                raise SandboxError(f"cannot create internal network: {e.decode('utf-8', 'replace')[:200]}")
            net_args = ["--network", network_name]
        mem = f"{spec.memory_mb}m"
        args = [
            self.docker,
            "create",
            "--name",
            name,
            *net_args,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(spec.pids_limit),
            "--memory",
            mem,
            "--memory-swap",
            mem,
            "--cpus",
            str(spec.cpus),
            "--user",
            spec.user,
            "--ulimit",
            "nofile=1024:1024",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m,mode=1777",  # noqa: S108 - container-local tmpfs
            "--tmpfs",
            f"{spec.workdir}:rw,nosuid,size={spec.disk_mb}m,mode=1777",
            *[
                arg
                for path in spec.extra_tmpfs
                for arg in ("--tmpfs", f"{path}:rw,nosuid,size={spec.disk_mb}m,mode=1777")
            ],
            "-w",
            spec.workdir,
            "--label",
            "agentlab=1",
            "--label",
            f"agentlab.run={spec.labels.get('run', '')}",
            "-e",
            "HOME=/tmp",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            spec.image,
            "sleep",
            "infinity",
        ]
        code, _o, e, _t, _ = await _run(args, timeout=60)
        if code != 0:
            if network_name:
                await _run([self.docker, "network", "rm", network_name], timeout=30)
            raise SandboxError(f"docker create failed: {e.decode('utf-8', 'replace')[:300]}")
        _LIVE.add(name)
        code, _o, e, _t, _ = await _run([self.docker, "start", name], timeout=60)
        if code != 0:
            await _run([self.docker, "rm", "-f", name], timeout=30)
            _LIVE.discard(name)
            raise SandboxError(f"docker start failed: {e.decode('utf-8', 'replace')[:300]}")
        return DockerSandbox(self, spec, name, network_name)


class DisabledSandboxProvider(SandboxProvider):
    """Used when ``security.sandbox.provider: disabled``. Every request fails closed."""

    name = "disabled"

    async def available(self) -> tuple[bool, str]:
        return False, "sandbox provider is disabled in configuration"

    async def create(self, spec: SandboxSpec) -> Sandbox:
        raise SandboxUnavailable("sandbox is disabled; untrusted code is never executed on the host")


SANDBOX_PROVIDERS.register("docker", DockerSandboxProvider, replace=True)
SANDBOX_PROVIDERS.register("disabled", DisabledSandboxProvider, replace=True)


def create_sandbox_provider(name: str) -> SandboxProvider:
    return SANDBOX_PROVIDERS.get(name)()
