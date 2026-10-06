"""What AgentLab has to offer and what this server can do: providers, models, skills, scoring profiles, environment, settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Request

from agentlab import __version__
from agentlab.api.routes.common import RESPONSES, require_auth
from agentlab.api.schemas import (
    EnvironmentCheck,
    EnvironmentOut,
    Health,
    ModelOut,
    ProviderCheck,
    ProviderCheckRequest,
    ProviderOut,
    ScoringProfileOut,
    SettingsOut,
    SkillDetail,
    SkillSummary,
)
from agentlab.api.state import ApiState, state_of
from agentlab.core.errors import NotFoundError, UserError
from agentlab.diagnostics import check_provider, key_status, provider_works, run_checks
from agentlab.evaluation.scoring import list_profiles, load_profile
from agentlab.security.redactor import redact_strings
from agentlab.skills import SkillRegistry

public = APIRouter()
router = APIRouter(dependencies=[Depends(require_auth)], responses=RESPONSES)


# ===================================================================================================== health
@public.get(
    "/health",
    response_model=Health,
    tags=["Server"],
    summary="Is the server up",
    description="Answers without a token. It reports counts only: runs in progress and jobs waiting.",
)
async def health(request: Request) -> Health:
    st = state_of(request)
    return Health(
        version=__version__,
        auth_required=st.guard.required,
        queue=st.queue.name,
        running=st.store.count_runs("running"),
        queued=await st.queue.depth(),
    )


# =================================================================================================== providers
@router.get(
    "/providers",
    response_model=list[ProviderOut],
    tags=["Providers"],
    summary="List providers",
    description=(
        "The configured LLM providers with their endpoint, default model, declared capabilities and whether their key "
        "reference resolves. Nothing is sent to any provider and keys are never shown."
    ),
)
def list_providers(request: Request) -> Any:
    st = state_of(request)
    svc = st.services
    judges = {j.provider for j in svc.config.evaluation.judges}
    out = []
    for row in svc.providers.describe():
        ref = row.get("api_key_ref")
        caps = row.get("capabilities")
        base_url, model, problem = row.get("base_url"), row.get("model"), row.get("configured_error")
        out.append(
            ProviderOut(
                name=str(row["name"]),
                type=str(row["type"]),
                base_url=str(base_url) if base_url else None,
                model=str(model) if model else None,
                capabilities=[str(c) for c in caps] if isinstance(caps, list) else [],
                key=key_status(svc, ref if isinstance(ref, str) else None),
                judge=str(row["name"]) in judges,
                configured_error=str(problem) if problem else None,
            )
        )
    return out


@router.post(
    "/providers/{name}/check",
    response_model=ProviderCheck,
    tags=["Providers"],
    summary="Check a provider",
    description="Contact the provider (a model listing and, unless disabled, one tiny completion) to verify the endpoint, the key and the model. The completion may be billed, so this only runs when asked.",
)
async def check(name: str, request: Request, body: ProviderCheckRequest | None = None) -> Any:
    st = state_of(request)
    body = body or ProviderCheckRequest()
    st.services.config.provider(name)  # unknown name -> 422
    st.services.credentials.reload()
    res = await check_provider(st.services, name, model=body.model, complete=body.complete)
    return ProviderCheck(ok=provider_works(res), **{k: v for k, v in res.items() if k in ProviderCheck.model_fields})


@router.get(
    "/models",
    response_model=list[ModelOut],
    tags=["Providers"],
    summary="List models",
    description="The models each configured provider reports (this asks the provider's model-listing endpoint). A provider that cannot be reached is listed with the reason instead of failing the request.",
)
async def list_models(request: Request, provider: str | None = None) -> Any:
    st = state_of(request)
    svc = st.services
    names = svc.providers.names()
    if provider and provider not in names:
        raise UserError(f"provider '{provider}' is not configured (known: {', '.join(names)})")
    svc.credentials.reload()
    rows: list[ModelOut] = []
    for name in [provider] if provider else names:
        try:
            models = await svc.providers.discover_models(name)
        except Exception as exc:  # one unreachable provider must not hide the others
            from agentlab.security.redactor import redact

            rows.append(ModelOut(provider=name, model=None, error=redact(f"{type(exc).__name__}: {exc}")[:200]))
            continue
        rows += [
            ModelOut(
                provider=name, model=m.id, context=m.context_length, capabilities=[c.value for c in m.capabilities]
            )
            for m in models
        ] or [ModelOut(provider=name, model=None, error="no models reported")]
    return rows


# ===================================================================================================== skills
def _registry(st: ApiState) -> SkillRegistry:
    base = st.services.base_dir
    dirs = [d if Path(d).is_absolute() else str(base / d) for d in st.services.config.skill_dirs]
    return SkillRegistry.default(dirs)


def _skill_summary(skill: Any) -> dict[str, Any]:
    m = skill.manifest
    return {
        "name": m.name,
        "version": m.version,
        "title": m.title,
        "description": m.description,
        "trust": m.trust,
        "status": m.status,
        "kind": m.kind,
        "taxonomy": list(m.taxonomy),
        "risk_class": m.risk_class.value,
        "category": m.category,
        "content_hash": skill.content_hash,
        "problems": list(skill.problems),
    }


@router.get(
    "/skills",
    response_model=list[SkillSummary],
    tags=["Skills"],
    summary="List skills",
    description="The versioned test skills AgentLab can use, with the taxonomy letters they cover, their trust level and risk class.",
)
def list_skills(request: Request) -> Any:
    return [_skill_summary(s) for s in _registry(state_of(request)).all() if not s.draft]


@router.get(
    "/skills/{name}",
    response_model=SkillDetail,
    tags=["Skills"],
    summary="Get a skill",
    description="One skill's manifest (when it applies, what it needs, how it generates and evaluates tests) and its SKILL.md, including what it cannot do.",
)
def get_skill(name: str, request: Request) -> Any:
    reg = _registry(state_of(request))
    try:
        skill = reg.get(name)
    except UserError as exc:
        raise NotFoundError(str(exc)) from exc
    return {**_skill_summary(skill), "manifest": skill.manifest.model_dump(mode="json"), "doc": skill.doc}


@router.get(
    "/scoring-profiles",
    response_model=list[ScoringProfileOut],
    tags=["Skills"],
    summary="List scoring profiles",
    description="The profiles that weight the score categories for a kind of agent (general, coding, rag, security, production, ...).",
)
def scoring_profiles() -> Any:
    out = []
    for name in list_profiles():
        prof = load_profile(name)
        out.append(ScoringProfileOut(name=prof.name, description=prof.description, weights=dict(prof.weights)))
    return out


# =============================================================================================== environment
@router.get(
    "/environment",
    response_model=EnvironmentOut,
    tags=["Server"],
    summary="What this server can and cannot do",
    description=(
        "The checks behind `agentlab doctor`: Docker for the sandbox, a browser for UI tests, providers, storage, the queue and "
        "skills. A missing capability is reported here and shows up as BLOCKED, never FAILED, in the tests that need it."
    ),
)
async def environment(request: Request, live: bool = False) -> Any:
    st = state_of(request)
    st.services.credentials.reload()
    checks = await run_checks(st.services, None, live=live)
    return EnvironmentOut(
        ok=not any(c.level == "fail" for c in checks),
        version=__version__,
        checks=[EnvironmentCheck(level=c.level, name=c.name, detail=c.detail, fix=c.fix) for c in checks],
    )


def mask_password(url: str) -> str:
    """``scheme://user:***@host/...``: a connection string without its password."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "***"
    if parts.password is None:
        return url
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{parts.username or ''}:***@{host}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


@router.get(
    "/settings",
    response_model=SettingsOut,
    tags=["Server"],
    summary="The server's configuration",
    description="The effective configuration with passwords in connection strings masked. Keys are references (`env:NAME`), never values. It cannot be changed through the API.",
)
def settings(request: Request) -> Any:
    st = state_of(request)
    cfg = redact_strings(st.services.config.model_dump(mode="json"))
    for section, key in (("storage", "database_url"), ("queue", "redis_url")):
        cfg[section][key] = mask_password(getattr(st.services.config, section).model_dump()[key])
    return SettingsOut(version=__version__, config=cfg, startup_warnings=list(st.services.startup_warnings))
