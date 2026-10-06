"""Build a :class:`TargetSpec` from a ``target.yaml`` and/or command-line flags (flags win)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from agentlab.core.enums import RiskClass
from agentlab.core.errors import UserError
from agentlab.core.models import (
    ApiConfig,
    CommandConfig,
    LlmTargetConfig,
    McpConfig,
    MockAgentConfig,
    RepositorySource,
    TargetSpec,
    WebConfig,
)
from agentlab.security import safeyaml


def load_target_file(path: Path) -> TargetSpec:
    if not path.is_file():
        raise UserError(f"target file not found: {path}")
    try:
        data = safeyaml.load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise UserError(f"{path}: cannot be read as YAML ({exc})") from exc
    if not isinstance(data, dict):
        raise UserError(f"{path}: expected a mapping at the top level")
    base = path.resolve().parent
    # paths in a target file are relative to the file, so a project can be run from anywhere
    data["documents"] = [_rel(base, d) for d in data.get("documents") or []]
    repo = data.get("repository")
    if isinstance(repo, dict):
        for key in ("path", "archive"):
            if repo.get(key):
                repo[key] = _rel(base, repo[key])
    data.setdefault("name", path.stem)
    try:
        return TargetSpec.model_validate(data)
    except Exception as exc:
        raise UserError(f"{path}: invalid target definition: {exc}") from exc


def _rel(base: Path, ref: str) -> str:
    p = Path(ref)
    if p.is_absolute() or ref.startswith(("http://", "https://")):
        return ref
    return str((base / p).resolve()) if (base / p).exists() else ref


def _fallback_name(spec: TargetSpec) -> str:
    if spec.llm is not None:
        return re.sub(r"[^A-Za-z0-9._-]+", "-", f"{spec.llm.provider}-{spec.llm.model or 'default'}")
    if spec.mock is not None:
        return "mock-agent"
    if spec.command is not None:
        return re.sub(r"[^A-Za-z0-9._-]+", "-", Path(spec.command.command[0]).name)
    return "target"


def derive_name(spec_hint: str | None, *sources: str | None, fallback: str = "target") -> str:
    if spec_hint:
        return spec_hint
    for src in sources:
        if not src:
            continue
        if src.startswith(("http://", "https://", "git@")):
            host = urlparse(src).hostname or src
            tail = urlparse(src).path.strip("/").split("/")[-1] if "/" in urlparse(src).path.strip("/") else ""
            return re.sub(r"[^A-Za-z0-9._-]+", "-", (tail or host).removesuffix(".git"))
        return re.sub(r"[^A-Za-z0-9._-]+", "-", Path(src).resolve().name)
    return fallback


def build_target(
    *,
    target_file: Path | None = None,
    name: str | None = None,
    repo: str | None = None,
    ref: str | None = None,
    url: str | None = None,
    api_url: str | None = None,
    openapi: str | None = None,
    mcp_url: str | None = None,
    mcp_command: str | None = None,
    command: str | None = None,
    llm: str | None = None,
    system_prompt: str | None = None,
    docs: list[str] | None = None,
    description: str | None = None,
    objective: str | None = None,
    credentials: list[str] | None = None,
    mock: list[str] | None = None,
    authorize: list[str] | None = None,
    authorization_note: str | None = None,
    disposable: bool = False,
    production: bool = False,
) -> TargetSpec:
    spec = load_target_file(target_file) if target_file else TargetSpec(name="target")
    data: dict[str, Any] = {}
    if repo:
        is_remote = repo.startswith(("http://", "https://", "git@"))
        data["repository"] = RepositorySource(url=repo, ref=ref) if is_remote else RepositorySource(path=repo, ref=ref)
    if url:
        data["web"] = WebConfig(url=url)
    if api_url or openapi:
        data["api"] = ApiConfig(url=api_url or openapi or "", openapi_url=openapi)
    if mcp_url:
        data["mcp"] = McpConfig(transport="streamable_http", url=mcp_url)
    elif mcp_command:
        data["mcp"] = McpConfig(transport="stdio", command=mcp_command.split())
    if command:
        data["command"] = CommandConfig(command=command.split())
    if mock:
        data["mock"] = MockAgentConfig(behaviors=mock)
    if llm:
        data["llm"] = _llm_target(llm, system_prompt, spec.llm)
    if docs:
        data["documents"] = [*spec.documents, *docs]
    if description:
        data["description"] = description
    if objective:
        data["objective"] = objective
    spec = spec.model_copy(update=data)
    if not target_file or name:
        spec.name = derive_name(name, repo, url, api_url, openapi, mcp_url, fallback=_fallback_name(spec))
    if credentials:
        spec.credentials = list(dict.fromkeys([*spec.credentials, *credentials]))
        # the first credential authenticates the interfaces that do not name one explicitly
        for iface in (spec.api, spec.web, spec.mcp):
            if iface is not None and getattr(iface, "auth_credential", None) is None:
                iface.auth_credential = credentials[0]
    _apply_authorization(spec, authorize, authorization_note, disposable, production)
    return spec


def _llm_target(ref: str, system_prompt: str | None, current: LlmTargetConfig | None) -> LlmTargetConfig:
    """``provider`` or ``provider:model`` (model names may contain colons, e.g. ``ollama:qwen2.5:0.5b``). The system
    prompt may be given inline or as ``@file``."""
    provider, _, model = ref.partition(":")
    if not provider:
        raise UserError("--llm expects PROVIDER or PROVIDER:MODEL")
    cfg = current.model_copy() if current else LlmTargetConfig(provider=provider)
    cfg.provider, cfg.model = provider, model or cfg.model
    if system_prompt:
        if system_prompt.startswith("@"):
            path = Path(system_prompt[1:])
            if not path.is_file():
                raise UserError(f"--system-prompt file not found: {path}")
            system_prompt = path.read_text(encoding="utf-8")
        cfg.system_prompt = system_prompt
    return cfg


def _apply_authorization(
    spec: TargetSpec, authorize: list[str] | None, note: str | None, disposable: bool, production: bool
) -> None:
    """What the *owner* authorises. Nothing here is inferred: HIGH_IMPACT tests only run when it is stated."""
    if authorize:
        try:
            wanted = {RiskClass(a.lower()) for a in authorize}
        except ValueError as exc:
            raise UserError("--authorize accepts: controlled, high_impact") from exc
        current = set(spec.safety.authorized_risk_classes) | {RiskClass.SAFE}
        spec.safety.authorized_risk_classes = sorted(current | wanted, key=lambda r: r.value)
    if note:
        spec.safety.authorization_note = note
    if disposable:
        spec.safety.disposable_environment = True
    if production:
        spec.safety.production = True
