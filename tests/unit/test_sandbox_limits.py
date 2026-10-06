"""The limits an operator sets under ``security.sandbox`` reach every sandbox AgentLab asks for.

A resource limit that is written in the configuration but never applied is worse than none: whoever reads the file believes
the sandbox is smaller than it is. These tests use a provider that only records what it was asked for, so they need no Docker.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from agentlab.adapters.base import AdapterContext
from agentlab.adapters.command import CommandAdapter
from agentlab.adapters.mcp import McpAdapter
from agentlab.core.config import AgentLabConfig, SandboxConfig
from agentlab.core.errors import SandboxUnavailable
from agentlab.core.models import CommandConfig, RepositorySource, TargetSpec
from agentlab.sandbox.base import Sandbox, SandboxProvider, SandboxSpec

TIGHT = {
    "image": "registry.example/agent-box:1",
    "cpus": 0.5,
    "memory_mb": 256,
    "pids_limit": 64,
    "disk_mb": 128,
    "user": "1234:1234",
}


class Recording(SandboxProvider):
    """Remembers the specs it was asked for and creates nothing."""

    name = "recording"

    def __init__(self) -> None:
        self.specs: list[SandboxSpec] = []

    async def available(self) -> tuple[bool, str]:
        return True, "recording only"

    async def create(self, spec: SandboxSpec) -> Sandbox:
        self.specs.append(spec)
        raise SandboxUnavailable("recorded; nothing is created")


def tight_config() -> AgentLabConfig:
    config = AgentLabConfig()
    config.security.sandbox = SandboxConfig(**TIGHT)
    return config


def asked_for(spec: SandboxSpec) -> dict[str, object]:
    return {key: getattr(spec, key) for key in TIGHT}


def test_a_spec_made_from_the_configuration_carries_every_limit_and_lets_the_caller_override() -> None:
    spec = SandboxSpec.from_config(SandboxConfig(**TIGHT), workdir="/work", network="internal", memory_mb=512)
    assert asked_for(spec) == {**TIGHT, "memory_mb": 512}
    assert spec.workdir == "/work" and spec.network == "internal"


def test_the_provider_choice_is_not_part_of_a_spec() -> None:
    assert "provider" not in SandboxSpec.from_config(SandboxConfig(provider="disabled")).model_dump()


async def test_a_command_target_runs_in_the_sandbox_the_operator_sized(tmp_path: Path) -> None:
    repo = tmp_path / "agent"
    repo.mkdir()
    provider = Recording()
    spec = TargetSpec(
        name="cli",
        repository=RepositorySource(path=str(repo)),
        command=CommandConfig(command=["python", "/agent/agent.py"], timeout_seconds=30),
    )
    adapter = CommandAdapter(spec, AdapterContext(config=tight_config(), sandbox=provider))
    with pytest.raises(SandboxUnavailable, match="recorded"):
        await adapter.create_sandbox()
    assert asked_for(provider.specs[0]) == TIGHT
    assert provider.specs[0].timeout_seconds == 30, "the target's own timeout still wins over the default"


async def test_a_target_may_name_its_own_image_but_not_loosen_the_other_limits(tmp_path: Path) -> None:
    provider = Recording()
    spec = TargetSpec(
        name="cli",
        command=CommandConfig(command=["run"], image="registry.example/their-image:2"),
    )
    adapter = CommandAdapter(spec, AdapterContext(config=tight_config(), sandbox=provider))
    with pytest.raises(SandboxUnavailable):
        await adapter.create_sandbox()
    asked = provider.specs[0]
    assert asked.image == "registry.example/their-image:2"
    assert {key: getattr(asked, key) for key in TIGHT if key != "image"} == {
        k: v for k, v in TIGHT.items() if k != "image"
    }


async def test_the_checking_sandbox_is_sized_the_same_way(tmp_path: Path) -> None:
    provider = Recording()
    spec = TargetSpec(name="cli", command=CommandConfig(command=["run"]))
    adapter = CommandAdapter(spec, AdapterContext(config=tight_config(), sandbox=provider))
    with pytest.raises(SandboxUnavailable):
        await adapter.create_sandbox(with_agent=False)
    asked = provider.specs[0]
    assert asked_for(asked) == TIGHT and asked.network == "none"


async def test_an_mcp_server_over_stdio_runs_in_the_sandbox_the_operator_sized() -> None:
    pytest.importorskip("mcp")
    provider = Recording()
    spec = TargetSpec(name="stdio", mcp={"transport": "stdio", "command": ["python", "server.py"]})  # type: ignore[arg-type]
    adapter = McpAdapter(spec, AdapterContext(config=tight_config(), sandbox=provider))
    async with contextlib.AsyncExitStack() as stack:
        with pytest.raises(SandboxUnavailable, match="recorded"):
            await adapter._stdio_transport(stack)
    assert asked_for(provider.specs[0]) == TIGHT
    assert provider.specs[0].network == "none"
