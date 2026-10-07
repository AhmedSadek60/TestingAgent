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

from agentlab.browser.discover import discover_web
from agentlab.browser.environment import browser_status
from agentlab.browser.pool import BrowserPool
from agentlab.core.config import AgentLabConfig
from agentlab.core.models import TargetSpec
from agentlab.providers import ProviderManager
from agentlab.sandbox import SandboxProvider, create_sandbox_provider
from agentlab.security.credentials import CredentialManager, EncryptedSecretStore
from agentlab.storage.artifacts import ArtifactStore, create_artifact_store
from agentlab.storage.db import Database, Store, open_store

log = logging.getLogger(__name__)

WebDiscoverer = Callable[[Any], Awaitable[dict[str, Any]]]
Reporter = Callable[[Any, "Services"], Any]  # called at the end of a run with (outcome, services); may be async


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


def _check_report_formats(cfg: AgentLabConfig) -> None:
    """Refuse a report format nothing provides now, not when the report phase of a long run reaches it. This runs after
    the plug-ins are loaded, because a plug-in may be what adds the format."""
    if cfg.reporting.formats:
        from agentlab.reporting.bundle import normalise_formats  # local: the reporting package builds on Services

        normalise_formats(cfg.reporting.formats)


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
    reporter: Reporter | None = None
    startup_warnings: list[str] = field(default_factory=list)
    _browser_pool: BrowserPool | None = field(default=None, repr=False)

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
        reporter: Reporter | None = None,
        migrate: bool = True,
    ) -> Services:
        """``migrate=False`` leaves the database untouched (it is not even created). The report phase of a run writes
        the formats listed in ``reporting.formats`` (an empty list turns it off) unless a ``reporter`` is given."""
        cfg = config or AgentLabConfig.load()
        base = base_dir or Path.cwd()
        warnings = load_plugin_modules(cfg.plugins)
        _check_report_formats(cfg)
        creds = credentials or CredentialManager(EncryptedSecretStore(_under(base, cfg.storage.secrets_file)))
        if reporter is None and cfg.reporting.formats:
            from agentlab.reporting.bundle import default_reporter  # local: the reporting package builds on Services

            reporter = default_reporter
        services = cls(
            config=cfg,
            store=store or _open_store(_db_url(base, cfg.storage.database_url), migrate),
            artifacts=artifacts
            or create_artifact_store(
                cfg.storage.artifact_store, _under(base, cfg.storage.artifacts_dir), cfg.storage.artifact_store_options
            ),
            credentials=creds,
            providers=providers or ProviderManager(cfg, creds),
            sandbox=sandbox or create_sandbox_provider(cfg.security.sandbox.provider),
            base_dir=base,
            reporter=reporter,
            startup_warnings=warnings,
        )
        services.web_discoverer = services.discover_web
        return services

    # ------------------------------------------------------------------ environment questions
    async def docker_status(self) -> tuple[bool, str]:
        return await self.sandbox.available()

    def browser_status(self) -> tuple[bool, str]:
        return browser_status(self.config)

    @property
    def browser_pool(self) -> BrowserPool:
        """The run's shared Chromium (started on first use, so a run that never opens a page never launches one)."""
        if self._browser_pool is None:
            self._browser_pool = BrowserPool(self.config)
        return self._browser_pool

    async def discover_web(self, spec: TargetSpec) -> dict[str, Any]:
        return await discover_web(self.browser_pool, spec, self.credentials)

    async def discover(self, spec: TargetSpec, *, probe: bool = True) -> Any:
        """Fingerprint a target (what ``agentlab discover`` does): returns a ``DiscoveryResult``. Sends harmless probes
        only, and none at all with ``probe=False``."""
        from agentlab.discovery.agent import TargetDiscoveryAgent

        docker_ok, _ = await self.docker_status()
        browser_ok, _ = self.browser_status()
        agent = TargetDiscoveryAgent(
            self.config,
            providers=self.providers,
            credentials=self.credentials,
            artifacts=self.artifacts,
            docker_available=docker_ok,
            browser_available=browser_ok,
            web_discoverer=self.web_discoverer,
            sandbox=self.sandbox,
            extras={"browser_pool": self.browser_pool},
        )
        return await agent.discover(spec, probe=probe)

    async def close_browser(self) -> None:
        """Stop Chromium and Playwright. Call it on the event loop that used them, once a run is over."""
        if self._browser_pool is not None:
            await self._browser_pool.aclose()

    def workdir(self, run_id: str) -> Path:
        path = _under(self.base_dir, self.config.storage.work_dir) / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def uploads_dir(self) -> Path:
        """Where documents and archives sent to the API are kept (created when the API starts)."""
        return _under(self.base_dir, self.config.storage.uploads_dir)

    async def aclose(self) -> None:
        await self.close_browser()
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
