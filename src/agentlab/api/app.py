"""The REST API application (spec section 41) and, when it has been built, the web interface (section 27)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from agentlab import __version__
from agentlab.api import errors, openapi_docs
from agentlab.api.routes import catalog, projects, reports, runs
from agentlab.api.security import UI_CSP, RequestGuard, TokenGuard, is_loopback, resolve_token
from agentlab.api.state import ApiState
from agentlab.core.config import AgentLabConfig
from agentlab.core.errors import PolicyBlocked
from agentlab.jobs.queue import JobQueue, RedisQueue, create_queue
from agentlab.jobs.runner import reap_forever
from agentlab.jobs.worker import Worker
from agentlab.services import Services

log = logging.getLogger(__name__)

LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1")

TAGS = [
    {"name": "Server", "description": "Health, the environment this server can use, and its configuration."},
    {"name": "Projects", "description": "Projects group targets, documents and runs."},
    {"name": "Targets", "description": "The agents under test."},
    {"name": "Credentials", "description": "Encrypted, scoped test credentials. Values are write-only."},
    {"name": "Documents", "description": "Files to use with a target: knowledge, repository archives, test cases."},
    {"name": "Discovery", "description": "Understand a target before testing it."},
    {"name": "Test plans", "description": "Explainable plans: what will be tested, why, and what cannot run."},
    {"name": "Test runs", "description": "Execute a plan safely; follow, cancel and read the run."},
    {"name": "Human review", "description": "Reviewer decisions that never destroy the original evaluation."},
    {"name": "Reports", "description": "Versioned reports in JSON, Markdown, HTML and PDF; regression comparison."},
    {"name": "Artifacts", "description": "The evidence a run stored."},
    {"name": "Providers", "description": "LLM providers and the models they offer."},
    {"name": "Skills", "description": "The versioned test skills and the scoring profiles."},
]

DESCRIPTION = """
AgentLab tests, evaluates and security-tests AI agents. This is its REST API; the command line (`agentlab`) and the web
interface use the same engine.

**Authentication.** When the server was started with a token, send `Authorization: Bearer <token>` (or `X-API-Key`). A
server without a token only listens on this machine.

**Errors** always have the shape `{"error": {"kind": ..., "message": ..., "details": [...]}}`; messages never contain a
secret.

**Runs are asynchronous.** `POST /test-runs` answers 202 with the run id; follow it with `GET /test-runs/{id}` or the live
event stream, and stop it with `POST /test-runs/{id}/cancel`.
"""


def build_ui_routes(app: FastAPI, ui_dir: Path) -> None:
    """Serve the built web interface at ``/`` (it uses hash routes, so no other path is needed)."""
    index = ui_dir / "index.html"

    @app.get("/", include_in_schema=False)
    async def ui_index() -> Response:
        return FileResponse(index, headers={"Content-Security-Policy": UI_CSP, "Cache-Control": "no-cache"})

    assets = ui_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="ui-assets")
    for extra in ui_dir.iterdir():
        if extra.is_file() and extra.name != "index.html" and extra.suffix in {".svg", ".ico", ".png", ".webmanifest"}:

            def make(path: Path) -> Any:
                async def serve_file() -> Response:
                    return FileResponse(path, headers={"Cache-Control": "public, max-age=3600"})

                return serve_file

            app.add_api_route(f"/{extra.name}", make(extra), include_in_schema=False)


def default_ui_dir() -> Path | None:
    """Where the built interface is: ``AGENTLAB_WEB_DIR``, else the folder the build writes next to this package."""
    candidates = [os.environ.get("AGENTLAB_WEB_DIR"), str(Path(__file__).parent / "static")]
    for c in candidates:
        if c and (Path(c) / "index.html").is_file():
            return Path(c)
    return None


def get_openapi_schema(app: FastAPI) -> dict[str, Any]:
    return get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        description=app.description,
        routes=app.routes,
        tags=app.openapi_tags,
        license_info=app.license_info,
    )


def create_app(
    services: Services | None = None,
    *,
    config: AgentLabConfig | None = None,
    base_dir: Path | None = None,
    queue: JobQueue | None = None,
    token: str | None = None,
    start_worker: bool | None = None,
    allowed_hosts: Sequence[str] | None = None,
    ui_dir: Path | None = None,
    serve_ui: bool | None = None,
) -> FastAPI:
    """Build the API. Nothing is opened until the application starts (its lifespan creates the services, the queue and the
    worker), so importing or describing the application has no side effects.

    ``services`` / ``queue`` / ``token`` can be injected (tests, embedding); otherwise they come from the configuration."""
    cfg_hint = services.config if services is not None else config

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owns = services is None
        svc = services or Services.create(config or AgentLabConfig.load(), base_dir=base_dir)
        scfg = svc.config.server
        api_token = token or resolve_token(scfg.token_ref, svc.credentials)
        if api_token is None and not is_loopback(scfg.host):
            raise PolicyBlocked(
                f"the server is set to listen on {scfg.host}, which other machines can reach, and no API token is "
                "configured. Set server.token_ref (env:AGENTLAB_API_TOKEN) or listen on 127.0.0.1"
            )
        q = queue or create_queue(svc.config.queue, redis_url=os.environ.get("AGENTLAB_REDIS_URL"))
        if isinstance(q, RedisQueue):
            await q.ping()
        run_worker = (q.name == "inline") if start_worker is None else start_worker
        worker = Worker(svc, q) if run_worker else None
        uploads = svc.base_dir / ".agentlab" / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        resolved_ui = (ui_dir or default_ui_dir()) if (serve_ui if serve_ui is not None else scfg.serve_ui) else None
        app.state.agentlab = ApiState(
            services=svc,
            queue=q,
            guard=TokenGuard(api_token),
            worker=worker,
            uploads_dir=uploads,
            allowed_roots=[Path(p if Path(p).is_absolute() else svc.base_dir / p) for p in scfg.allowed_paths],
            max_upload_bytes=scfg.max_upload_mb * 1024 * 1024,
            ui_dir=resolved_ui,
        )
        reaper: asyncio.Task[None] | None = None
        if worker is not None:
            worker.start()  # a worker also watches for runs that were abandoned
        else:
            reaper = asyncio.create_task(reap_forever(svc, q), name="agentlab-api-reaper")
        for warning in svc.startup_warnings:
            log.warning("%s", warning)
        try:
            yield
        finally:
            if reaper is not None:
                reaper.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await reaper
            if worker is not None:
                await worker.stop(drain_seconds=5.0)
            await q.close()
            if owns:
                await svc.aclose()

    app = FastAPI(
        title="AgentLab API",
        version=__version__,
        description=DESCRIPTION,
        openapi_tags=TAGS,
        lifespan=lifespan,
        license_info={"name": "Apache-2.0"},
    )
    errors.install(app)
    describe_once: dict[str, Any] = {}

    def openapi() -> dict[str, Any]:
        if not describe_once:
            describe_once.update(openapi_docs.describe(get_openapi_schema(app)))
        return describe_once

    app.openapi = openapi  # type: ignore[method-assign]

    hosts = allowed_hosts
    if hosts is None and token is None and not (cfg_hint and cfg_hint.server.token_ref):
        hosts = (
            LOOPBACK_HOSTS  # no token: only this machine's names, so a web page cannot reach the API by DNS rebinding
        )
    cors = cfg_hint.server.cors_origins if cfg_hint else []
    app.add_middleware(RequestGuard, allowed_hosts=hosts, allowed_origins=cors)
    if cors:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["authorization", "content-type", "x-api-key", "last-event-id"],
            expose_headers=["X-Next-Cursor"],
            allow_credentials=False,
        )

    app.include_router(catalog.public)
    for module in (projects, runs, reports, catalog):
        app.include_router(module.router)

    resolved_ui = (
        (ui_dir or default_ui_dir())
        if (serve_ui if serve_ui is not None else (cfg_hint.server.serve_ui if cfg_hint else True))
        else None
    )
    if resolved_ui is not None:
        build_ui_routes(app, resolved_ui)
    return app
