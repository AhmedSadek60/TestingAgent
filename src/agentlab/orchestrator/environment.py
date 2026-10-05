"""Phase 4, environment preparation: open the target's interfaces, check what the machine can provide and decide
whether an independent judge can be used. Every check is a real check; nothing is assumed available.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

from agentlab.adapters.base import AdapterContext, TargetRuntime
from agentlab.core.config import AgentLabConfig
from agentlab.core.errors import AgentLabError
from agentlab.core.models import AgentProfile, TargetSpec
from agentlab.discovery.agent import DiscoveryResult, IngestedTarget
from agentlab.evaluation.context import PlaceholderResolver
from agentlab.evaluation.judge import JudgeEngine
from agentlab.orchestrator.options import EnvironmentReport, RunOptions
from agentlab.security.egress import EgressPolicy
from agentlab.services import Services
from agentlab.skills.context import SkillContext

PROBE_TIMEOUT_S = 20.0


def document_paths(spec: TargetSpec) -> dict[str, Path]:
    """File name -> the document the user supplied (skills may attach them to conversations)."""
    out: dict[str, Path] = {}
    for ref in spec.documents:
        p = Path(ref)
        files = sorted(x for x in p.rglob("*") if x.is_file())[:200] if p.is_dir() else [p]
        for f in files:
            out.setdefault(f.name, f)
    return out


def target_models(spec: TargetSpec) -> set[tuple[str, str]]:
    """(provider, model) pairs the *target* is known to use, so a judge can never be the target itself."""
    out: set[tuple[str, str]] = set()
    if spec.llm is not None:
        model = spec.llm.model or ""
        out.add((spec.llm.provider, model))
    return out


async def open_runtime(
    services: Services,
    spec: TargetSpec,
    *,
    run_id: str,
    resolver: PlaceholderResolver,
    workdir: Path,
    extras: dict[str, Any] | None = None,
) -> TargetRuntime:
    cfg = services.config
    ctx = AdapterContext(
        config=cfg,
        credentials=services.credentials,
        egress=EgressPolicy(
            block_metadata=cfg.security.block_metadata_endpoints, allow_private=cfg.security.allow_private_networks
        ),
        artifacts=services.artifacts,
        providers=services.providers,
        sandbox=services.sandbox,
        workdir=workdir,
        run_id=run_id,
        extras={"canary_secret": resolver.canary("system_secret"), **(extras or {})},
    )
    return await TargetRuntime(spec, ctx).open()


async def check_reachability(runtime: TargetRuntime, report: EnvironmentReport) -> None:
    """An interface that cannot be reached is removed from the runtime (with the reason), so the tests that need it
    are BLOCKED with that reason instead of failing one by one."""
    for kind, adapter in list(runtime.adapters.items()):
        reason: str | None = None
        try:
            info = await asyncio.wait_for(adapter.probe(), timeout=PROBE_TIMEOUT_S)
            if isinstance(info, dict) and info.get("reachable") is False:
                reason = str(info.get("error") or "the target reported itself unreachable")
        except TimeoutError:
            reason = f"no answer to a reachability check within {PROBE_TIMEOUT_S:g}s"
        except AgentLabError as exc:
            reason = f"{exc.kind.value}: {exc}"
        except Exception as exc:  # an adapter defect must not stop the run
            reason = f"{type(exc).__name__}: {exc}"
        if reason:
            runtime.adapters.pop(kind, None)
            runtime.errors[kind] = reason
            report.unreachable[kind] = reason
            with contextlib.suppress(Exception):
                await adapter.close()


def prepare_judge(
    services: Services,
    spec: TargetSpec,
    profile: AgentProfile,
    options: RunOptions,
    report: EnvironmentReport,
) -> JudgeEngine | None:
    """The independent judge, or ``None`` with the reason recorded. A judge that is (or may be) the target itself is
    never used: the target must not control the evaluator."""
    cfg = services.config
    if not options.judge or not cfg.evaluation.judge_enabled:
        report.judge_note = "LLM judging is switched off for this run"
        return None
    if not cfg.evaluation.judges:
        report.judge_note = "no judge is configured (evaluation.judges); only deterministic checks run"
        return None
    judge = JudgeEngine(services.providers, cfg.evaluation, target_models=target_models(spec))
    try:
        judge.validate_independence()
    except AgentLabError as exc:
        report.judge_independent = False
        report.judge_note = f"judge disabled: {exc}"
        report.warnings.append(report.judge_note)
        return None
    except Exception as exc:  # unknown provider, missing key reference, ...
        report.judge_note = f"judge disabled: {type(exc).__name__}: {exc}"
        report.warnings.append(report.judge_note)
        return None
    report.judge_independent = True
    detected = {m.lower() for m in profile.models}
    for j in cfg.evaluation.judges:
        model = (j.model or cfg.provider(j.provider).model or "").lower()
        if model and model in detected:
            report.warnings.append(
                f"judge model '{model}' is also a model the target appears to use (found in its files); a judge from "
                "the same model may share the target's blind spots. Prefer a different model family."
            )
    report.judge = True
    report.judge_note = "judges: " + ", ".join(
        f"{j.provider}/{j.model or cfg.provider(j.provider).model or 'default'}" for j in cfg.evaluation.judges
    )
    return judge


def build_skill_context(
    *,
    services: Services,
    spec: TargetSpec,
    profile: AgentProfile,
    ingested: IngestedTarget,
    discovery: DiscoveryResult,
    runtime: TargetRuntime,
    report: EnvironmentReport,
    options: RunOptions,
    fixtures_dir: Path,
    config: AgentLabConfig,
) -> SkillContext:
    return SkillContext(
        profile=profile,
        target=spec,
        config=config,
        repo=ingested.repo_analysis,
        documents=list(ingested.documents),
        interfaces=runtime.available(),
        adapter_capabilities={k: a.capabilities for k, a in runtime.adapters.items()},
        judge_available=report.judge,
        docker_available=report.docker,
        browser_available=report.browser,
        credential_names=[c for c in spec.credentials if services.credentials.has(c)],
        intensity=options.intensity,
        fixtures_dir=fixtures_dir,
        doc_paths=document_paths(spec),
        seed=options.seed,
        user_requirements=list(options.requirements),
    )
