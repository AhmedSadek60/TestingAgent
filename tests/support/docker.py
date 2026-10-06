"""Skip helpers for tests that need a running Docker daemon (marker: docker)."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from typing import Any

import pytest


def docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    r = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"], capture_output=True)  # noqa: S603,S607
    return r.returncode == 0


_skip_without_docker = pytest.mark.skipif(not docker_ok(), reason="Docker daemon not available")


def needs_docker(test: Callable[..., Any]) -> Callable[..., Any]:
    """Marks a test as one that starts containers (``-m "not docker"`` deselects it) and skips it without a daemon."""
    return pytest.mark.docker(_skip_without_docker(test))


def agentlab_containers() -> set[str]:
    """Names of the containers AgentLab created and has not removed (the label is set by the Docker provider)."""
    r = subprocess.run(  # noqa: S603
        ["docker", "ps", "-a", "--filter", "label=agentlab=1", "--format", "{{.Names}}"],  # noqa: S607
        capture_output=True,
        text=True,
    )
    return set(r.stdout.split())
