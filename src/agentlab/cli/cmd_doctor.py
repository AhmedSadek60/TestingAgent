"""``agentlab doctor``: check what this machine can and cannot do, so a missing capability is known before a run.

Every check is a real check (a file is written, a daemon is asked, a socket is opened). Nothing is assumed available, and
a check that cannot run says so. Remote providers are *not* contacted unless ``--live`` is given; their keys are only
checked for being set.
"""

from __future__ import annotations

import os
import platform
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

import typer
from rich import box
from rich.table import Table
from sqlalchemy import text

from agentlab import __version__
from agentlab.cli.cmd_providers import key_status
from agentlab.cli.common import console, emit_json, make_services, run_async, state
from agentlab.core.config import ProviderConfig
from agentlab.core.errors import AgentLabError
from agentlab.security.redactor import redact
from agentlab.services import Services
from agentlab.skills import SkillRegistry
from agentlab.storage.migrate import current_head

Level = Literal["ok", "info", "warn", "fail"]
MARK = {
    "ok": "[green]ok[/green]",
    "info": "[dim]info[/dim]",
    "warn": "[yellow]warn[/yellow]",
    "fail": "[red]FAIL[/red]",
}
LOCAL_TYPES = {"ollama", "lmstudio", "vllm", "llamacpp"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}  # noqa: S104 - compared against, never bound


@dataclass
class Check:
    level: Level
    name: str
    detail: str
    fix: str = ""


def _is_local(p: ProviderConfig) -> bool:
    if p.type in LOCAL_TYPES:
        return True
    host = urlparse(p.base_url or "").hostname
    return host in LOCAL_HOSTS if host else False


def _writable(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".doctor-", delete=True):
            pass
        return True, str(path)
    except OSError as exc:
        return False, f"{path}: {exc.strerror or exc}"


def _database_check(services: Services) -> Check:
    url = services.config.storage.database_url
    kind = url.split("://")[0]
    try:
        with services.store.db.engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        projects = len(services.store.list_projects())
    except Exception:
        return Check("info", "database", f"{kind}: not created yet; it is created and migrated by the first run")
    head = current_head()
    if version != head:
        return Check("warn", "database", f"schema {version}, this version expects {head}; the next run migrates it")
    return Check("ok", "database", f"{kind} reachable, schema {version}, {projects} project(s)")


async def _provider_checks(services: Services, live: bool) -> list[Check]:
    out: list[Check] = []
    judges = {j.provider for j in services.config.evaluation.judges}
    for p in services.config.providers:
        label = f"provider {p.name} ({p.type})" + (" [judge]" if p.name in judges else "")
        key = key_status(services, p.api_key_ref)
        needs_key = p.type not in LOCAL_TYPES and p.type != "mock" and not _is_local(p)
        if key.startswith(("MISSING", "invalid")):
            out.append(
                Check(
                    "warn" if needs_key else "info",
                    label,
                    f"key reference: {key}",
                    "set the environment variable or store a credential",
                )
            )
            continue
        if not (_is_local(p) or live):
            out.append(Check("info", label, f"key {key}; not contacted (use --live to check the endpoint)"))
            continue
        try:
            prov = services.providers.get(p.name)
            models = await prov.discover()
            out.append(Check("ok", label, f"reachable, {len(models)} model(s) listed; key {key}"))
        except AgentLabError as exc:
            out.append(
                Check(
                    "warn",
                    label,
                    redact(f"{exc.kind.value}: {exc}")[:200],
                    "check base_url, the key and that the server is running",
                )
            )
        except Exception as exc:
            out.append(Check("warn", label, redact(f"{type(exc).__name__}: {exc}")[:200]))
    return out


async def run_checks(services: Services, config_path: Path | None, *, live: bool) -> list[Check]:
    cfg = services.config
    checks: list[Check] = []
    ok_py = sys.version_info >= (3, 12)
    checks.append(
        Check(
            "ok" if ok_py else "fail",
            "python",
            f"{platform.python_version()} · agentlab {__version__}",
            "" if ok_py else "AgentLab needs Python 3.12 or newer",
        )
    )
    checks.append(
        Check("ok", "configuration", str(config_path))
        if config_path
        else Check("info", "configuration", "no agentlab.yaml: using the defaults (`agentlab init` writes one)")
    )

    # storage
    checks.append(_database_check(services))
    for label, rel in (("artifacts", cfg.storage.artifacts_dir), ("reports", cfg.storage.reports_dir)):
        path = Path(rel) if Path(rel).is_absolute() else services.base_dir / rel
        ok, detail = _writable(path)
        checks.append(
            Check("ok" if ok else "fail", f"{label} folder", ("writable: " if ok else "not writable: ") + detail)
        )
    key_file = (services.base_dir / cfg.storage.secrets_file).with_suffix(".key")
    if os.environ.get("AGENTLAB_MASTER_KEY"):
        checks.append(Check("ok", "secret store key", "from AGENTLAB_MASTER_KEY"))
    elif key_file.exists():
        loose = key_file.stat().st_mode & 0o077
        checks.append(
            Check("fail", "secret store key", f"{key_file} is readable by other users", f"chmod 600 {key_file}")
            if loose
            else Check("ok", "secret store key", f"{key_file} (owner-only)")
        )
    else:
        checks.append(
            Check(
                "info",
                "secret store key",
                "no key yet; one is generated (owner-only) when the first credential is stored",
            )
        )

    # isolation
    docker_ok, docker_note = await services.docker_status()
    checks.append(
        Check("ok", "docker (sandbox)", docker_note)
        if docker_ok
        else Check(
            "warn",
            "docker (sandbox)",
            docker_note,
            "without it, tests that execute a repository or a coding agent are BLOCKED (never run on the host)",
        )
    )
    browser_ok, browser_note = services.browser_status()
    checks.append(
        Check("ok", "browser (Playwright)", browser_note)
        if browser_ok
        else Check(
            "warn", "browser (Playwright)", browser_note, "browser/UI tests are BLOCKED until a browser is available"
        )
    )

    # models
    checks += await _provider_checks(services, live)
    if not cfg.evaluation.judges:
        checks.append(
            Check(
                "info",
                "LLM judge",
                "none configured: only deterministic checks run, and subjective criteria are reported as not judged",
            )
        )
    else:
        names = ", ".join(
            f"{j.provider}/{j.model or cfg.provider(j.provider).model or 'default'}" for j in cfg.evaluation.judges
        )
        checks.append(Check("ok", "LLM judge", names))

    # queue
    if cfg.queue.backend == "redis":
        try:
            import redis

            redis.Redis.from_url(cfg.queue.redis_url, socket_connect_timeout=2).ping()
            checks.append(Check("ok", "queue (redis)", "reachable"))
        except Exception as exc:
            checks.append(
                Check(
                    "fail",
                    "queue (redis)",
                    redact(f"{type(exc).__name__}: {exc}")[:160],
                    "start Redis or set queue.backend: inline",
                )
            )
    else:
        checks.append(Check("info", "queue", "inline (runs in the API process)"))

    # skills and plug-ins
    reg = SkillRegistry.default([d if Path(d).is_absolute() else str(services.base_dir / d) for d in cfg.skill_dirs])
    checks.append(
        Check("warn", "skills", f"{len(reg.all())} loaded, {len(reg.problems)} problem(s): {reg.problems[0]}")
        if reg.problems
        else Check("ok", "skills", f"{len(reg.all())} loaded, no problems")
    )
    for w in services.startup_warnings:
        checks.append(Check("warn", "plug-ins", w))
    return checks


def doctor(
    ctx: typer.Context,
    live: Annotated[
        bool, typer.Option("--live", help="Also contact remote providers (a model-listing request with their keys).")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Check the environment: Docker, browser, providers, storage, skills. Exit code 1 when something is broken."""
    st = state(ctx)
    services = make_services(st, migrate=False)
    config_path = st.config_path or (Path("agentlab.yaml") if Path("agentlab.yaml").exists() else None)

    async def go() -> list[Check]:
        try:
            return await run_checks(services, config_path, live=live)
        finally:
            await services.aclose()

    checks = run_async(go())
    failed = any(c.level == "fail" for c in checks)
    if as_json:
        emit_json({"ok": not failed, "checks": [c.__dict__ for c in checks]})
    else:
        t = Table("", "Check", "Result", box=box.SIMPLE_HEAD)
        for c in checks:
            t.add_row(MARK[c.level], c.name, c.detail + (f"\n[dim]-> {c.fix}[/dim]" if c.fix else ""))
        console.print(t)
        warns = sum(c.level == "warn" for c in checks)
        console.print(
            "[red]problems found[/red]"
            if failed
            else "[green]ready[/green]"
            + (
                f", {warns} warning(s): the features named above will be reported as BLOCKED, not failed"
                if warns
                else ""
            )
        )
    if failed:
        raise typer.Exit(1)
