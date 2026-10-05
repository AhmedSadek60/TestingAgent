"""Services: the shared, configured components every entry point (CLI, API, tests) builds a run from.

One place creates the database, artifact store, credential manager, provider manager and sandbox provider from an
:class:`AgentLabConfig`, so the CLI and the REST API behave identically. Any component can be injected, which is how
tests run without touching the file system or the network.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentlab.core.config import AgentLabConfig
from agentlab.execution.environment import browser_status
from agentlab.providers import ProviderManager
from agentlab.sandbox import SandboxProvider, create_sandbox_provider
from agentlab.security.credentials import CredentialManager, EncryptedSecretStore
from agentlab.storage.artifacts import ArtifactStore, LocalArtifactStore
from agentlab.storage.db import Database, Store, open_store

log = logging.getLogger(__name__)

WebDiscoverer = Callable[[Any], Awaitable[dict[str, Any]]]


def load_plugin_modules(modules: list[str]) -> list[str]:
    """Import the plug-in modules named in the *owner's* configuration (never anything found in a target).
    Returns warnings for modules that could not be imported."""
    warnings: list[str] = []
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as exc:  # a broken plug-in must not stop AgentLab
            warnings.append(f"plug-in module '{name}' could not be imported ({type(exc).__name__}: {exc})")
    return warnings


@dataclass
class Services:
    config: AgentLabConfig
    store: Store
    artifacts: ArtifactStore
    credentials: CredentialManager
    providers: ProviderManager
    sandbox: SandboxProvider
    base_dir: Path = field(default_factory=Path.cwd)
    web_discoverer: WebDiscoverer | None = None
    startup_warnings: list[str] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        config: AgentLabConfig | None = None,
        *,
        base_dir: Path | None = None,
        store: Store | None = None,
        artifacts: ArtifactStore | None = None,
        credentials: CredentialManager | None = None,
        providers: ProviderManager | None = None,
        sandbox: SandboxProvider | None = None,
        migrate: bool = True,
    ) -> Services:
        """``migrate=False`` leaves the database untouched (it is not even created)."""
        cfg = config or AgentLabConfig.load()
        base = base_dir or Path.cwd()
        warnings = load_plugin_modules(cfg.plugins)
        creds = credentials or CredentialManager(EncryptedSecretStore(_under(base, cfg.storage.secrets_file)))
        return cls(
            config=cfg,
            store=store or _open_store(_db_url(base, cfg.storage.database_url), migrate),
            artifacts=artifacts or LocalArtifactStore(_under(base, cfg.storage.artifacts_dir)),
            credentials=creds,
            providers=providers or ProviderManager(cfg, creds),
            sandbox=sandbox or create_sandbox_provider(cfg.security.sandbox.provider),
            base_dir=base,
            startup_warnings=warnings,
        )

    # ------------------------------------------------------------------ environment questions
    async def docker_status(self) -> tuple[bool, str]:
        return await self.sandbox.available()

    def browser_status(self) -> tuple[bool, str]:
        return browser_status(self.config)

    def workdir(self, run_id: str) -> Path:
        path = self.base_dir / ".agentlab" / "work" / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def aclose(self) -> None:
        await self.providers.aclose()
        self.store.db.dispose()


def _open_store(url: str, migrate: bool) -> Store:
    """``migrate=False`` is for commands that never touch stored data (``discover``, ``providers``, ``doctor``): the
    database file is not created and its schema is left alone."""
    return open_store(url, migrate=True) if migrate else Store(Database(url))


def _under(base: Path, p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else base / path


def _db_url(base: Path, url: str) -> str:
    """Relative SQLite paths are relative to the project directory, not to wherever the process was started."""
    prefix = "sqlite:///"
    if url.startswith(prefix) and not url.startswith("sqlite:////") and url != prefix + ":memory:":
        return prefix + str(_under(base, url[len(prefix) :]))
    return url
