"""Run manifest (spec sections 41 and 44): everything needed to know *what a run measured* and whether two runs can be
compared. It records versions, skill content hashes, the plan hash, judges, the scoring profile, limits and the
environment. Secrets never appear: provider keys are references, headers and credentials are not copied.
"""

from __future__ import annotations

import platform
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal

from agentlab import __version__
from agentlab.core.config import AgentLabConfig
from agentlab.core.ids import utcnow
from agentlab.core.models import AgentProfile, TargetSpec
from agentlab.core.models.base import Model
from agentlab.design.models import TestPlan
from agentlab.evaluation.scoring import ScoringProfile
from agentlab.skills.model import Skill
from agentlab.storage.db import canonical_hash

SCHEMA_VERSION = 1


def config_fingerprint(config: AgentLabConfig) -> dict[str, Any]:
    """The settings that change what a run measures. Providers appear as name/type/model; keys, headers and URLs
    with credentials are not copied."""
    ev = config.evaluation
    return {
        "providers": sorted(
            ({"name": p.name, "type": p.type, "model": p.model} for p in config.providers), key=lambda d: d["name"]
        ),
        "evaluation": {
            "judges": [j.model_dump(mode="json") for j in ev.judges],
            "judge_strategy": ev.judge_strategy,
            "judge_enabled": ev.judge_enabled,
            "repetitions": ev.repetitions,
            "repetitions_by_risk": dict(sorted(ev.repetitions_by_risk.items())),
            "reliability_repetitions": ev.reliability_repetitions,
            "pass_threshold": ev.pass_threshold,
            "timeout_seconds": ev.timeout_seconds,
            "latency_budget_ms": ev.latency_budget_ms,
            "max_tests_per_skill": ev.max_tests_per_skill,
        },
        "limits": config.limits.model_dump(mode="json"),
        "planning": config.planning.model_dump(mode="json"),
        "security": {
            "sandbox_required": config.security.sandbox_required,
            "allow_production_targets": config.security.allow_production_targets,
            "block_metadata_endpoints": config.security.block_metadata_endpoints,
            "allow_private_networks": config.security.allow_private_networks,
            "canary_prefix": config.security.canary_prefix,
            "sandbox": config.security.sandbox.model_dump(mode="json"),
        },
        "max_parallel": config.max_parallel,
    }


def target_fingerprint(spec: TargetSpec) -> str:
    """Identity of the target *definition* (not of the target's behaviour); header values and credentials excluded."""
    data = spec.model_dump(mode="json")
    for key in ("api", "mcp"):
        if isinstance(data.get(key), dict) and "headers" in data[key]:
            data[key]["headers"] = sorted(data[key]["headers"])  # names only
    return canonical_hash(data)


def skill_entry(skill: Skill) -> dict[str, str]:
    return {
        "name": skill.name,
        "version": skill.version,
        "trust": skill.manifest.trust,
        "content_hash": skill.content_hash,
    }


def build_manifest(
    *,
    run_id: str,
    config: AgentLabConfig,
    spec: TargetSpec,
    profile: AgentProfile,
    plan: TestPlan,
    skills: Iterable[Skill],
    scoring: ScoringProfile,
    judge: Mapping[str, Any],
    environment: Mapping[str, Any],
    options: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "agentlab_version": __version__,
        "python": platform.python_version(),
        "platform": platform.system().lower(),
        "run_id": run_id,
        "created_at": utcnow().isoformat(),
        "target": {
            "name": spec.name,
            "version": spec.version,
            "interfaces": spec.interfaces(),
            "spec_hash": target_fingerprint(spec),
            "production": spec.safety.production,
        },
        "profile": {
            "hash": plan.profile_hash,
            "types": [{"type": t.type.value, "confidence": round(t.confidence, 3)} for t in profile.types[:6]],
            "tools": len(profile.tools),
            "modes": [m.value for m in profile.modes],
        },
        "plan": {
            "id": plan.id,
            "hash": plan.plan_hash,
            "suite": plan.suite,
            "intensity": plan.intensity,
            "tests": len(plan.selected_tests()),
            "runnable": len(plan.runnable()),
            "predicted_blocked": len(plan.predicted_blocked()),
        },
        "skills": sorted((skill_entry(s) for s in skills), key=lambda d: d["name"]),
        "scoring_profile": {"name": scoring.name, "hash": canonical_hash(scoring.model_dump(mode="json"))[:16]},
        "judges": dict(judge),
        "environment": dict(environment),
        "options": dict(options),
        "config": config_fingerprint(config),
        "config_hash": canonical_hash(config_fingerprint(config))[:16],
    }


# ================================================================================ comparing two manifests
Impact = Literal["blocks_comparison", "caveat", "info"]


class ManifestDifference(Model):
    field: str
    base: Any = None
    current: Any = None
    impact: Impact = "caveat"
    note: str = ""


def _get(m: Mapping[str, Any], path: str) -> Any:
    cur: Any = m
    for part in path.split("."):
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(part)
    return cur


def _major_minor(v: str | None) -> tuple[str, str]:
    parts = (v or "0.0").split(".")
    return parts[0], parts[1] if len(parts) > 1 else "0"


def replays(base: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    """True when ``current`` was started as a replay of the run ``base`` (``agentlab test --baseline``): it re-runs the
    tests ``base`` started with, under a plan of its own."""
    base_id = _get(base, "run_id")
    return bool(base_id) and _get(current, "options.baseline_run_id") == base_id


def compare_manifests(base: Mapping[str, Any], current: Mapping[str, Any]) -> list[ManifestDifference]:
    """Why two runs may not be comparable. ``blocks_comparison``: scores are not comparable; ``caveat``: comparable
    with a stated reservation; ``info``: expected (for a regression run the target itself is meant to differ)."""
    out: list[ManifestDifference] = []

    def add(field: str, b: Any, c: Any, impact: Impact, note: str) -> None:
        if b != c:
            out.append(ManifestDifference(field=field, base=b, current=c, impact=impact, note=note))

    bv, cv = _get(base, "agentlab_version"), _get(current, "agentlab_version")
    if bv != cv:
        major_differs = _major_minor(bv)[0] != _major_minor(cv)[0]
        same_minor = _major_minor(bv) == _major_minor(cv)
        out.append(
            ManifestDifference(
                field="agentlab_version",
                base=bv,
                current=cv,
                impact="blocks_comparison" if major_differs else ("info" if same_minor else "caveat"),
                note="a different AgentLab version may score or evaluate differently",
            )
        )
    add(
        "target.name",
        _get(base, "target.name"),
        _get(current, "target.name"),
        "caveat",
        "different targets are compared",
    )
    add(
        "target.version",
        _get(base, "target.version"),
        _get(current, "target.version"),
        "info",
        "the target changed between the runs (expected for a regression comparison)",
    )
    add(
        "target.spec_hash",
        _get(base, "target.spec_hash"),
        _get(current, "target.spec_hash"),
        "info",
        "the target definition (interfaces, endpoints, tools) changed",
    )
    # a replay is a plan of its own (a new id, the "regression" suite) that holds the same tests, so those differences
    # are expected and say nothing about whether the runs can be compared
    replay = replays(base, current)
    add(
        "plan.hash",
        _get(base, "plan.hash"),
        _get(current, "plan.hash"),
        "info" if replay else "caveat",
        "the second run replays the first one's tests under a plan of its own"
        if replay
        else "the test plans differ: only tests present in both runs can be compared one to one",
    )
    add(
        "plan.intensity", _get(base, "plan.intensity"), _get(current, "plan.intensity"), "caveat", "different intensity"
    )
    add(
        "plan.suite",
        _get(base, "plan.suite"),
        _get(current, "plan.suite"),
        "info" if replay else "caveat",
        "a replay runs as the regression suite" if replay else "different suites",
    )
    add(
        "scoring_profile",
        _get(base, "scoring_profile"),
        _get(current, "scoring_profile"),
        "blocks_comparison",
        "scores use different weights, so overall scores are not comparable (category results still are)",
    )

    bs = {s["name"]: s for s in base.get("skills", [])}
    cs = {s["name"]: s for s in current.get("skills", [])}
    for name in sorted(set(bs) | set(cs)):
        b, c = bs.get(name), cs.get(name)
        if b is None or c is None:
            out.append(
                ManifestDifference(
                    field=f"skills.{name}",
                    base=b["version"] if b else None,
                    current=c["version"] if c else None,
                    impact="caveat",
                    note="the skill was used in only one of the runs",
                )
            )
        elif b["version"] != c["version"] or b.get("content_hash") != c.get("content_hash"):
            out.append(
                ManifestDifference(
                    field=f"skills.{name}",
                    base=f"{b['version']}@{str(b.get('content_hash', ''))[:8]}",
                    current=f"{c['version']}@{str(c.get('content_hash', ''))[:8]}",
                    impact="caveat",
                    note="the skill changed, so its tests may differ for reasons other than the target",
                )
            )
    add("judges", _get(base, "judges.judges"), _get(current, "judges.judges"), "caveat", "different LLM judges")
    add(
        "judges.enabled",
        _get(base, "judges.enabled"),
        _get(current, "judges.enabled"),
        "caveat",
        "judging was on in only one run",
    )
    add(
        "config.evaluation",
        _get(base, "config.evaluation"),
        _get(current, "config.evaluation"),
        "caveat",
        "evaluation settings (repetitions, thresholds, judge strategy) differ",
    )
    add(
        "config.limits",
        _get(base, "config.limits"),
        _get(current, "config.limits"),
        "caveat",
        "budgets differ, which can stop tests earlier in one run",
    )
    for key in ("docker", "browser"):
        add(
            f"environment.{key}",
            _get(base, f"environment.{key}"),
            _get(current, f"environment.{key}"),
            "caveat",
            f"{key} availability differs, so tests that need it were BLOCKED in only one run",
        )
    return out


def comparable(differences: Sequence[ManifestDifference]) -> bool:
    return not any(d.impact == "blocks_comparison" for d in differences)
