"""Sandbox behaviour against a real Docker daemon (marker: docker)."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from agentlab.core.errors import SandboxUnavailable
from agentlab.sandbox import DisabledSandboxProvider, DockerSandboxProvider, SandboxSpec

pytestmark = pytest.mark.docker


def _docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    r = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"], capture_output=True)  # noqa: S603,S607
    return r.returncode == 0


needs_docker = pytest.mark.skipif(not _docker_ok(), reason="Docker daemon not available")


async def test_disabled_provider_fails_closed():
    with pytest.raises(SandboxUnavailable):
        await DisabledSandboxProvider().create(SandboxSpec())


@needs_docker
async def test_exec_roundtrip_and_isolation(tmp_path):
    prov = DockerSandboxProvider()
    (tmp_path / "hello.py").write_text("print('hi from sandbox')\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_text("a")
    (tmp_path / "link").symlink_to("/etc/passwd")
    sb = await prov.create(SandboxSpec(timeout_seconds=30))
    try:
        assert await sb.put_dir(tmp_path) == 2  # the symlink is not copied
        r = await sb.exec(["python", "hello.py"])
        assert r.ok and r.stdout.strip() == "hi from sandbox"
        assert not (await sb.exec(["ls", "link"])).ok
        # no network
        net = await sb.exec(["python", "-c", "import socket;socket.create_connection(('1.1.1.1',80),2)"], timeout=10)
        assert not net.ok
        # read-only root, non-root user, no capabilities
        assert not (await sb.exec(["sh", "-c", "echo x > /etc/agentlab_test"])).ok
        who = await sb.exec(["id", "-u"])
        assert who.stdout.strip() == "65534"
        # files round-trip out
        await sb.exec(["sh", "-c", "echo changed > sub/a.txt"])
        out = tmp_path / "out"
        assert await sb.export_dir(None, out) >= 2
        assert (out / "sub" / "a.txt").read_text().strip() == "changed"
    finally:
        await sb.close()


@needs_docker
async def test_timeout_and_memory_limit_are_enforced():
    prov = DockerSandboxProvider()
    sb = await prov.create(SandboxSpec(timeout_seconds=30, memory_mb=128))
    try:
        r = await sb.exec(["sleep", "30"], timeout=2)
        assert r.timed_out
        hog = await sb.exec(["python", "-c", "x = bytearray(600*1024*1024); print(len(x))"], timeout=20)
        assert not hog.ok  # OOM-killed
        fork = await sb.exec(["sh", "-c", "for i in $(seq 1 600); do sleep 5 & done; wait"], timeout=10)
        assert not fork.ok or "Resource temporarily unavailable" in fork.stderr or fork.timed_out
    finally:
        await sb.close()


@needs_docker
async def test_container_removed_on_close():
    prov = DockerSandboxProvider()
    sb = await prov.create(SandboxSpec())
    cid = sb.id
    await sb.close()
    r = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name={cid}", "--format", "{{.Names}}"],  # noqa: S603,S607
        capture_output=True,
        text=True,
    )
    assert cid not in r.stdout


@needs_docker
async def test_allowlist_network_is_reported_unsupported():
    with pytest.raises(SandboxUnavailable):
        await DockerSandboxProvider().create(SandboxSpec(network="allowlist"))
