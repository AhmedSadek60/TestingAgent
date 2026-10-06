"""What this machine and this configuration can and cannot do: the checks behind ``agentlab doctor`` and the API's
``GET /environment``, and the live check of one provider.

Every check is a real check (a file is written, a daemon is asked, a socket is opened). Nothing is assumed available, and a
check that cannot run says so. Remote providers are *not* contacted unless asked; their keys are only checked for being set.
"""

from __future__ import annotations

import os
import platform
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from sqlalchemy import text

from agentlab import __version__
from agentlab.core.config import ProviderConfig
from agentlab.core.errors import AgentLabError
from agentlab.providers import CompletionRequest, Message
from agentlab.security.redactor import redact
from agentlab.services import Services
from agentlab.skills import SkillRegistry
from agentlab.storage.migrate import current_head

Level = Literal["ok", "info", "warn", "fail"]


@dataclass
class Check:
    level: Level
    name: str
    detail: str
    fix: str = ""


def key_status(services: Services, ref: str | None) -> str:
    """Whether an API-key reference resolves. The value is never read into the output."""
    if not ref:
        return "not needed"
    if ref.startswith("env:"):
        return "set" if os.environ.get(ref[4:]) else f"MISSING ({ref[4:]} is not set)"
    if ref.startswith("secret:"):
        return "set" if services.credentials.has(ref[7:]) else f"MISSING (no credential '{ref[7:]}')"
    return "invalid reference (use env:NAME or secret:NAME)"


LOCAL_TYPES = {"ollama", "lmstudio", "vllm", "llamacpp"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}  # noqa: S104 - compared against, never bound


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


async def check_provider(
    services: Services, name: str, *, model: str | None = None, complete: bool = True
) -> dict[str, Any]:
    """Contact one provider (a model listing and, unless ``complete`` is false, one tiny completion) to verify the
    endpoint, the key and the model. It sends a request with the provider's credentials, which may be billed, so it only
    runs when asked for. The result never contains the key."""
    out: dict[str, Any] = {"provider": name, "key": key_status(services, services.config.provider(name).api_key_ref)}
    try:
        prov = services.providers.get(name)
        started = time.perf_counter()
        try:
            models = await prov.discover()
            out["models"] = len(models)
            out["discovery_ms"] = round((time.perf_counter() - started) * 1000)
        except Exception as exc:  # discovery is optional: say why it did not work
            out["models"] = None
            out["discovery_error"] = redact(f"{type(exc).__name__}: {exc}")
        if complete:
            started = time.perf_counter()
            try:
                resp = await prov.complete(
                    CompletionRequest(
                        messages=[Message(role="user", content="Reply with the single word: ok")],
                        model=model,
                        max_tokens=8,
                        temperature=0.0,
                    )
                )
                out["completion"] = "ok"
                out["model"] = resp.model
                out["latency_ms"] = round((time.perf_counter() - started) * 1000)
                out["tokens"] = resp.usage.input_tokens + resp.usage.output_tokens
            except Exception as exc:
                out["completion"] = "failed"
                out["completion_error"] = redact(f"{type(exc).__name__}: {exc}")
    except Exception as exc:
        out["error"] = redact(f"{type(exc).__name__}: {exc}")
    return out


def provider_works(result: dict[str, Any]) -> bool:
    return "error" not in result and result.get("completion", "ok") == "ok"
