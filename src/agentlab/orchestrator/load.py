"""Rebuild a finished run from storage (``agentlab runs show``, ``report --run``, the REST API and the web UI).

A run is stored as rows (run, results, findings, scorecard, profile) plus two kinds of artifact: one plan per wave and
``analysis.json`` with everything that is not a row of its own. Nothing is recomputed: what was concluded at the time
is what is reported, so a report is reproducible from the stored evidence.
"""

from __future__ import annotations

from typing import Any

from agentlab.core.enums import RunStatus
from agentlab.core.errors import UserError
from agentlab.core.ids import utcnow
from agentlab.core.models import AgentProfile, TestCase
from agentlab.design.models import TestPlan
from agentlab.evaluation.scoring import ScoringProfile
from agentlab.orchestrator.analysis import CrossTestAnalysis, ReliabilityAnalysis, SecurityAnalysis
from agentlab.orchestrator.options import EnvironmentReport, PhaseRecord, RunOutcome
from agentlab.services import Services


def _artifact_json(services: Services, rows: list[dict[str, Any]], name: str) -> Any | None:
    hit = next((r for r in rows if r.get("name") == name), None)
    if hit is None:
        return None
    try:
        return services.artifacts.get_json(f"sha256-{hit['sha256']}")
    except Exception:  # a missing blob must not make the whole run unreadable
        return None


def load_outcome(services: Services, run_id: str) -> RunOutcome:
    store = services.store
    run = store.get_run(run_id)
    if run["status"] in {"pending", "running"}:
        raise UserError(f"run '{run_id}' has not finished (status: {run['status']})")
    rows = store.list_artifacts(run_id)
    plans: list[TestPlan] = []
    for wave in range(1, 6):
        data = _artifact_json(services, rows, f"plan-wave{wave}.json")
        if data is None:
            break
        plans.append(TestPlan.model_validate(data))
    tests: list[TestCase] = [t for p in plans for t in p.test_cases()]
    extra = _artifact_json(services, rows, "analysis.json") or {}
    profile = store.profile_for_run(run_id) or AgentProfile(
        target_name=str(run["manifest"].get("target", {}).get("name"))
    )
    target_name = run["manifest"].get("target", {}).get("name") or profile.target_name
    scoring = ScoringProfile.model_validate(extra["scoring_profile"]) if extra.get("scoring_profile") else None

    def model(cls: Any, key: str) -> Any:
        return cls.model_validate(extra[key]) if extra.get(key) else None

    return RunOutcome(
        run_id=run_id,
        status=RunStatus(run["status"]),
        target=target_name,
        profile=profile,
        plans=plans,
        tests=tests,
        results=store.list_results(run_id),
        findings=store.list_findings(run_id),
        scorecard=store.latest_scorecard(run_id),
        cross_test=model(CrossTestAnalysis, "cross_test"),
        security=model(SecurityAnalysis, "security"),
        reliability=model(ReliabilityAnalysis, "reliability"),
        manifest=run["manifest"],
        environment=EnvironmentReport.model_validate(extra.get("environment") or {}),
        limits=extra.get("limits") or {},
        warnings=list(extra.get("warnings") or []),
        phases=[PhaseRecord.model_validate(p) for p in extra.get("phases") or []],
        scoring=scoring,
        error=extra.get("error") or (run.get("error") or {}).get("message"),
        started_at=_parse(run.get("started_at")),
        finished_at=_parse(run.get("finished_at")),
    )


def _parse(value: Any) -> Any:
    from datetime import datetime

    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value)) if value else utcnow()
    except ValueError:
        return utcnow()
