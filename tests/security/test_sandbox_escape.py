"""Untrusted code (a repository's tests, a coding agent, a command-line target) only ever runs inside a container that cannot
reach the network, the host's files, its secrets or its Docker socket, and when no such container can be made AgentLab
refuses rather than running the code on the host (spec section 10, "fail securely").

The first half needs no Docker: it checks what AgentLab *asks* Docker for. The second half (marker ``docker``) starts real
containers, asks Docker what it built and then behaves like malicious code trying to get out."""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

import agentlab.sandbox.docker as docker_module
from agentlab.core.errors import SandboxUnavailable
from agentlab.core.models import CommandConfig, TargetSpec
from agentlab.orchestrator import RunOptions
from agentlab.sandbox import DisabledSandboxProvider, DockerSandboxProvider, SandboxSpec
from tests.support.docker import agentlab_containers, needs_docker
from tests.support.lab import Lab


# ================================================================================ what AgentLab asks Docker for
def fake_docker(monkeypatch: pytest.MonkeyPatch, *, daemon: bool = True) -> list[list[str]]:
    """A Docker CLI that records every command and succeeds (or, with ``daemon=False``, cannot reach a daemon)."""
    seen: list[list[str]] = []

    async def run(args: list[str], **_kw: Any) -> tuple[int, bytes, bytes, bool, bool]:
        seen.append(list(args))
        if args[1:2] == ["version"] and not daemon:
            return 1, b"", b"Cannot connect to the Docker daemon", False, False
        return 0, b"29.0.0", b"", False, False

    monkeypatch.setattr(docker_module, "_run", run)
    monkeypatch.setattr(docker_module.shutil, "which", lambda _name: "/usr/bin/docker")
    return seen


async def test_without_a_docker_cli_nothing_is_run_and_the_refusal_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker_module.shutil, "which", lambda _name: None)
    with pytest.raises(SandboxUnavailable, match="refusing to run untrusted code without isolation") as caught:
        await DockerSandboxProvider().create(SandboxSpec())
    assert "'docker' CLI is not installed" in str(caught.value)


async def test_with_a_docker_cli_but_no_daemon_nothing_is_run_either(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = fake_docker(monkeypatch, daemon=False)
    with pytest.raises(SandboxUnavailable, match="daemon is not reachable"):
        await DockerSandboxProvider().create(SandboxSpec())
    assert not [a for a in seen if a[1:2] in (["create"], ["run"], ["start"], ["exec"])], (
        "no container was even requested"
    )


async def test_a_disabled_sandbox_refuses_everything() -> None:
    provider = DisabledSandboxProvider()
    assert (await provider.available())[0] is False
    with pytest.raises(SandboxUnavailable, match="never executed on the host"):
        await provider.create(SandboxSpec())


async def test_an_egress_allow_list_is_refused_rather_than_pretended(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = fake_docker(monkeypatch)
    with pytest.raises(SandboxUnavailable, match="not supported"):
        await DockerSandboxProvider().create(SandboxSpec(network="allowlist", allow_hosts=["api.example.com"]))
    assert not [a for a in seen if a[1:2] == ["create"]]


async def test_every_container_is_created_with_the_full_set_of_restrictions(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = fake_docker(monkeypatch)
    spec = SandboxSpec(memory_mb=256, pids_limit=64, cpus=0.5, disk_mb=32, labels={"run": "r1"})
    await DockerSandboxProvider().create(spec)
    (create,) = [a for a in seen if a[1:2] == ["create"]]
    joined = " ".join(create)

    def following(flag: str) -> list[str]:
        return [create[i + 1] for i, a in enumerate(create) if a == flag]

    assert following("--network") == ["none"], "no network unless explicitly asked for"
    assert "--read-only" in create and following("--cap-drop") == ["ALL"]
    assert following("--security-opt") == ["no-new-privileges"]
    assert following("--user") == ["65534:65534"], "never root"
    assert following("--pids-limit") == ["64"] and following("--cpus") == ["0.5"]
    assert following("--memory") == ["256m"] and following("--memory-swap") == ["256m"], "no swap to hide in"
    assert any(t.startswith("/tmp:") and "noexec" in t for t in following("--tmpfs")), "nothing is run from /tmp"
    assert any(t.startswith("/workspace:") and "size=32m" in t for t in following("--tmpfs")), "disk is capped"
    assert following("--ulimit") == ["nofile=1024:1024"]
    assert "agentlab=1" in following("--label") and "agentlab.run=r1" in following("--label")
    forbidden = {
        "--privileged",
        "-v",
        "--volume",
        "--mount",
        "--cap-add",
        "--device",
        "--pid",
        "--ipc",
        "--userns",
        "--uts",
    }
    assert not forbidden & set(create), f"never: {sorted(forbidden & set(create))}"
    assert "docker.sock" not in joined and "host" not in following("--network")
    assert create[-3:] == [spec.image, "sleep", "infinity"], "the container does nothing until it is given work"


async def test_an_internal_network_is_created_isolated_and_removed_with_the_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = fake_docker(monkeypatch)
    sandbox = await DockerSandboxProvider().create(SandboxSpec(network="internal"))
    (net,) = [a for a in seen if a[1:3] == ["network", "create"]]
    assert "--internal" in net, "an internal network has no route out"
    await sandbox.close()
    assert [a for a in seen if a[1:3] == ["network", "rm"]], "and it does not outlive the sandbox"


async def test_command_targets_are_never_executed_on_the_host_when_there_is_no_sandbox(tmp_path: Path) -> None:
    marker = tmp_path / "RAN-ON-THE-HOST"
    script = f"import pathlib; pathlib.Path({str(marker)!r}).write_text('x')"
    async with Lab(tmp_path / "lab") as lab:  # sandbox provider "disabled"
        out = await lab.run(
            TargetSpec(name="cli-agent", command=CommandConfig(command=["python", "-c", script])),
            RunOptions(intensity="quick", suite="functional", second_wave=False),
        )
    assert not marker.exists(), "the command ran on the evaluator host"
    assert out.results and {r.status.value for r in out.results} == {"blocked"} and not out.findings
    assert all(
        "sandbox" in (r.blocked_reason or "").lower() or "docker" in (r.blocked_reason or "").lower()
        for r in out.results
    )


# =============================================================================== real containers, hostile code
@contextmanager
def host_listener() -> Iterator[int]:
    """A TCP server on every host interface, standing in for a service on the machine that runs AgentLab."""
    srv = socket.socket()
    srv.bind(("0.0.0.0", 0))  # noqa: S104 - a test double that must be reachable from a container if the sandbox leaks
    srv.listen(5)
    stop = threading.Event()

    def serve() -> None:
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
                conn.close()
            except OSError:
                continue

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    try:
        yield srv.getsockname()[1]
    finally:
        stop.set()
        t.join(timeout=2)
        srv.close()


def inspect(container: str) -> dict[str, Any]:
    out = subprocess.run(["docker", "inspect", container], capture_output=True, text=True, check=True)  # noqa: S603,S607
    return json.loads(out.stdout)[0]


@needs_docker
async def test_the_container_docker_built_has_every_restriction() -> None:
    sandbox = await DockerSandboxProvider().create(
        SandboxSpec(memory_mb=256, pids_limit=64, cpus=0.5, labels={"run": "t"})
    )
    try:
        info = inspect(sandbox.id)
        host = info["HostConfig"]
        assert host["NetworkMode"] == "none" and host["ReadonlyRootfs"] is True and host["Privileged"] is False
        assert host["CapDrop"] == ["ALL"] and not host["CapAdd"]
        assert any(o.startswith("no-new-privileges") for o in host["SecurityOpt"])
        assert host["PidsLimit"] == 64 and host["Memory"] == host["MemorySwap"] == 256 * 1024 * 1024
        assert host["NanoCpus"] == 500_000_000
        assert not host.get("Binds") and not info["Mounts"], "no host directory and no socket is mounted"
        assert host["PidMode"] == "" and host["IpcMode"] in {"private", ""} and not host.get("Devices")
        assert info["Config"]["User"] == "65534:65534" and info["Config"]["Labels"]["agentlab"] == "1"
    finally:
        await sandbox.close()


@needs_docker
async def test_code_in_the_container_cannot_reach_the_host_or_the_internet() -> None:
    probe = (
        "import socket, sys\n"
        "for host in sys.argv[1:]:\n"
        "    try:\n"
        "        socket.create_connection((host.split(':')[0], int(host.split(':')[1])), 2).close()\n"
        "        print('REACHED', host)\n"
        "    except OSError as exc:\n"
        "        print('refused', host, type(exc).__name__)\n"
    )
    with host_listener() as port:
        for network in ("none", "internal"):
            sandbox = await DockerSandboxProvider().create(SandboxSpec(network=network))  # type: ignore[arg-type]
            try:
                targets = [
                    f"127.0.0.1:{port}",
                    f"172.17.0.1:{port}",
                    f"host.docker.internal:{port}",
                    "1.1.1.1:80",
                    "8.8.8.8:53",
                ]
                result = await sandbox.exec(["python", "-c", probe, *targets], timeout=60)
                assert "REACHED" not in result.stdout, f"({network}) {result.stdout}"
                assert result.stdout.count("refused") == len(targets), result.stdout + result.stderr
                dns = await sandbox.exec(
                    ["python", "-c", "import socket; socket.gethostbyname('example.com')"], timeout=20
                )
                assert not dns.ok, f"({network}) a name was resolved: nothing outside should answer"
            finally:
                await sandbox.close()


@needs_docker
async def test_the_container_sees_nothing_of_the_host_and_holds_no_privilege(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host_file = tmp_path / "host-only-file.txt"
    host_file.write_text("host secret")
    monkeypatch.setenv("AGENTLAB_HOST_SECRET", "must-not-be-visible-in-the-container")
    sandbox = await DockerSandboxProvider().create(SandboxSpec())
    try:

        async def sh(script: str) -> Any:
            return await sandbox.exec(["sh", "-c", script], timeout=30)

        assert (await sh("id -u")).stdout.strip() == "65534"
        assert "must-not-be-visible" not in (await sh("env; cat /proc/1/environ 2>/dev/null")).stdout
        assert not (await sh(f"ls {host_file}")).ok and not (await sh("ls /var/run/docker.sock /run/docker.sock")).ok
        assert (
            not (await sh("ls /home/user /root")).ok
            or "TestingAgent" not in (await sh("ls -R /home /root 2>/dev/null")).stdout
        )
        caps = (await sh("grep -E '^Cap(Eff|Prm|Bnd|Inh)' /proc/self/status")).stdout
        assert caps.count("0000000000000000") == 4, caps
        assert "NoNewPrivs:\t1" in (await sh("grep NoNewPrivs /proc/self/status")).stdout
        assert not (await sh("mount -t tmpfs none /mnt")).ok, "no mounting"
        assert not (await sh("chmod u+s /bin/sh")).ok and not (await sh("echo x > /etc/agentlab-probe")).ok, (
            "read-only system"
        )
        assert not (await sh("cp /bin/true /tmp/true && /tmp/true")).ok, "nothing runs from /tmp"
        assert (await sh("echo ok > /workspace/f && cat /workspace/f")).stdout.strip() == "ok", (
            "the workspace is writable"
        )
    finally:
        await sandbox.close()
    assert host_file.read_text() == "host secret"


BOMB = """
import os, time
children = 0
for _ in range(500):
    try:
        pid = os.fork()
    except OSError:
        break
    if pid == 0:
        time.sleep(3)
        os._exit(0)
    children += 1
print("forked", children)
"""


@needs_docker
async def test_a_process_bomb_cannot_exceed_its_limit_and_the_sandbox_recovers() -> None:
    sandbox = await DockerSandboxProvider().create(SandboxSpec(pids_limit=32))
    try:
        bomb = await sandbox.exec(["python", "-c", BOMB], timeout=30)
        assert bomb.stdout.startswith("forked "), bomb.stdout + bomb.stderr
        assert int(bomb.stdout.split()[1]) < 32, "the kernel refused every process past the limit"
        await asyncio.sleep(4)  # the children were told to exit after three seconds
        assert (await sandbox.exec(["echo", "alive"])).ok, "the sandbox is usable again once they are gone"
    finally:
        await sandbox.close()


@needs_docker
async def test_a_memory_hog_is_killed_at_its_limit() -> None:
    sandbox = await DockerSandboxProvider().create(SandboxSpec(memory_mb=128))
    try:
        hog = await sandbox.exec(["python", "-c", "x = bytearray(900*1024*1024); print('allocated')"], timeout=30)
        assert not hog.ok and "allocated" not in hog.stdout, hog.stdout + hog.stderr
        assert (await sandbox.exec(["echo", "alive"])).ok, "only the offender died, not the sandbox"
    finally:
        await sandbox.close()


@needs_docker
async def test_an_output_flood_is_capped_on_the_way_out() -> None:
    sandbox = await DockerSandboxProvider().create(SandboxSpec(max_output_bytes=10_000))
    try:
        flood = await sandbox.exec(["python", "-c", "print('x' * 50_000_000)"], timeout=60)
        assert flood.truncated and len(flood.stdout) <= 10_000, (flood.truncated, len(flood.stdout))
    finally:
        await sandbox.close()


@needs_docker
async def test_a_command_that_ignores_its_timeout_is_killed_and_a_closed_sandbox_leaves_nothing_behind() -> None:
    before = agentlab_containers()
    sandbox = await DockerSandboxProvider().create(SandboxSpec())
    created = sandbox.id
    assert created in agentlab_containers() - before
    stuck = await sandbox.exec(["sh", "-c", "trap '' TERM INT; while :; do :; done"], timeout=2)
    assert stuck.timed_out and not stuck.ok, "SIGKILL cannot be trapped"
    await sandbox.close()
    assert created not in agentlab_containers(), "the container is removed, not stopped and forgotten"
    await sandbox.close()  # closing twice is harmless


@needs_docker
async def test_files_cross_the_boundary_as_plain_regular_files_only(tmp_path: Path) -> None:
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "keep.txt").write_text("kept")
    (tmp_path / "in" / "outside-link").symlink_to("/etc/passwd")
    sandbox = await DockerSandboxProvider().create(SandboxSpec())
    try:
        assert await sandbox.put_dir(tmp_path / "in") == 1, "a link is never copied in"
        assert not (await sandbox.exec(["ls", "outside-link"])).ok
        await sandbox.exec(
            ["sh", "-c", "ln -s /etc/passwd leak && ln -s ../../etc escape && mkdir -p d && echo made > d/new.txt"]
        )
        out = tmp_path / "out"
        assert await sandbox.export_dir(None, out) == 2
        names = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file() or p.is_symlink()}
        assert names == {"keep.txt", "d/new.txt"}, "links created by the code are dropped on the way out"
        assert not any(p.is_symlink() for p in out.rglob("*"))
    finally:
        await sandbox.close()
