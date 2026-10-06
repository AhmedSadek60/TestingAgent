"""A throw-away AgentLab installation for end-to-end tests: its own database, artifacts and secrets under ``root``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentlab.core.config import (
    AgentLabConfig,
    EvaluationConfig,
    ProviderConfig,
    ReportingConfig,
    SandboxConfig,
    SecurityConfig,
    StorageConfig,
)
from agentlab.core.models import TargetSpec
from agentlab.orchestrator import RunOptions, TestOrchestratorAgent
from agentlab.orchestrator.options import RunOutcome
from agentlab.services import Services

REPOSITORIES = Path(__file__).resolve().parents[2] / "fixtures" / "repositories"


def make_config(
    root: Path,
    *,
    sandbox: str = "disabled",
    providers: list[ProviderConfig] | None = None,
    evaluation: EvaluationConfig | None = None,
    formats: list[str] | None = None,
    **overrides: Any,
) -> AgentLabConfig:
    """Docker is never assumed (``sandbox="disabled"``) and no report is written unless ``formats`` asks for one."""
    base: dict[str, Any] = {
        "storage": StorageConfig(
            database_url=f"sqlite:///{root}/lab.db",
            artifacts_dir=str(root / "artifacts"),
            secrets_file=str(root / "secrets.enc"),
            reports_dir=str(root / "reports"),
        ),
        "security": SecurityConfig(sandbox=SandboxConfig(provider=sandbox)),  # type: ignore[arg-type]
        "reporting": ReportingConfig(formats=formats or []),
        "providers": providers or [],
    }
    if evaluation is not None:
        base["evaluation"] = evaluation
    base.update(overrides)
    return AgentLabConfig(**base)


class Lab:
    """One installation. ``await lab.run(target)`` is what ``agentlab test`` does for a target."""

    def __init__(self, root: Path, **config: Any) -> None:
        self.root = root
        self.services = Services.create(make_config(root, **config), base_dir=root)

    async def run(
        self, target: TargetSpec | dict[str, Any], options: RunOptions | None = None, **kw: Any
    ) -> RunOutcome:
        spec = target if isinstance(target, TargetSpec) else TargetSpec(**target)
        opts = options or RunOptions(**{"intensity": "quick", "second_wave": False, **kw})
        return await TestOrchestratorAgent(self.services).run(spec, opts)

    @property
    def store(self) -> Any:
        return self.services.store

    async def aclose(self) -> None:
        await self.services.aclose()

    async def __aenter__(self) -> Lab:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    def files(self) -> list[Path]:
        """Every file this installation wrote (database, artifacts, reports): what a leaked secret could be found in."""
        return [p for p in self.root.rglob("*") if p.is_file()]


def everything_written(lab: Lab) -> bytes:
    """All bytes the installation persisted, for a "this value must appear nowhere" check."""
    return b"\n".join(p.read_bytes() for p in lab.files() if p.stat().st_size < 50_000_000)
