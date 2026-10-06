"""Everything a report is built from, loaded from the stores.

A report never depends on the process that ran the tests: ``agentlab report --run ID`` weeks later reads the same
rows and artifacts the orchestrator wrote, so the first report and the hundredth are identical.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from agentlab.core.errors import UserError
from agentlab.core.models import AgentProfile, Finding, Scorecard, TargetSpec, TestResult
from agentlab.design.models import PlannedTest, TestPlan
from agentlab.services import Services

log = logging.getLogger(__name__)


@dataclass
class RunMaterial:
    run: dict[str, Any]
    manifest: dict[str, Any]
    target: TargetSpec | None
    profile: AgentProfile | None
    plans: list[TestPlan]
    results: list[TestResult]
    findings: list[Finding]
    scorecard: Scorecard | None
    analysis: dict[str, Any]
    artifacts: list[dict[str, Any]]
    browser_sessions: list[dict[str, Any]]
    reviews: list[dict[str, Any]]
    traces: list[dict[str, Any]]
    history: list[dict[str, Any]] = field(default_factory=list)
    report_version: int = 1
    notes: list[str] = field(default_factory=list)

    @property
    def run_id(self) -> str:
        return str(self.run["id"])

    def planned(self) -> dict[str, PlannedTest]:
        """Every planned test of every wave, by id (a later wave never replaces a test of an earlier one)."""
        out: dict[str, PlannedTest] = {}
        for plan in self.plans:
            for p in plan.tests:
                out.setdefault(p.id, p)
        return out


def _artifact_json(sv: Services, rows: list[dict[str, Any]], kind: str, name: str) -> Any | None:
    for row in reversed(rows):
        if row.get("kind") == kind and row.get("name") == name:
            with contextlib.suppress(Exception):
                return sv.artifacts.get_json(f"sha256-{row['sha256']}")
    return None


def load_material(
    sv: Services, run_id: str, *, history_limit: int = 12, final: Mapping[str, Any] | None = None
) -> RunMaterial:
    """Load a finished (or stopped, or failed) run. Raises :class:`UserError` when the run does not exist.

    ``final`` is for the orchestrator's own report phase: the run row only becomes terminal once its report exists (so that
    nothing reading the row ever sees "completed" without the report), and the report must still say how the run ended.
    ``final`` gives that state (``status``, ``finished_at``); it is laid over the stored row."""
    store = sv.store
    run = {**store.get_run(run_id), **(final or {})}
    if run.get("status") in {"pending", "running"} and not store.list_results(run_id):
        raise UserError(f"run '{run_id}' has not produced any result yet (status: {run.get('status')})")
    artifacts = store.list_artifacts(run_id)
    notes: list[str] = []

    plans: list[TestPlan] = []
    for row in sorted(
        (a for a in artifacts if a.get("kind") == "plan" and str(a.get("name", "")).endswith(".json")),
        key=lambda a: str(a.get("name")),
    ):
        try:
            plans.append(TestPlan.model_validate(sv.artifacts.get_json(f"sha256-{row['sha256']}")))
        except Exception as exc:  # a damaged plan artifact degrades the report, it does not stop it
            notes.append(f"plan artifact {row.get('name')} could not be read ({type(exc).__name__})")
    analysis = _artifact_json(sv, artifacts, "analysis", "analysis.json") or {}
    if not analysis:
        notes.append("analysis.json is missing: cross-test, security and reliability sections are rebuilt from results")
    manifest = dict(run.get("manifest") or {}) or (_artifact_json(sv, artifacts, "manifest", "manifest.json") or {})

    target: TargetSpec | None = None
    if run.get("target_id"):
        with contextlib.suppress(Exception):
            _, target = store.get_target(str(run["target_id"]))
    profile = store.profile_for_run(run_id)
    previous = store.latest_report(run_id)

    history: list[dict[str, Any]] = []
    started = str(run.get("started_at") or run.get("created_at") or "")
    if run.get("target_id"):
        for other in store.list_runs(run.get("project_id"), limit=200):
            if other["id"] == run_id or other.get("target_id") != run.get("target_id"):
                continue
            if other.get("status") in {"pending", "running"}:
                continue
            totals = other.get("totals") or {}
            history.append(
                {
                    "run_id": other["id"],
                    "started_at": other.get("started_at") or other.get("created_at"),
                    "overall": totals.get("overall"),
                    "counts": totals.get("counts") or {},
                    "tests": totals.get("tests") or 0,
                    "findings": totals.get("findings") or 0,
                    "plan_hash": (other.get("manifest") or {}).get("plan", {}).get("hash"),
                    "scoring_profile": (other.get("manifest") or {}).get("scoring_profile"),
                }
            )
        history.sort(key=lambda h: str(h["started_at"]))
        # the trend is what led up to this run: a report of an older run does not change when newer runs are added
        history = [h for h in history if not started or str(h["started_at"]) < started][-history_limit:]

    return RunMaterial(
        run=run,
        manifest=manifest,
        target=target,
        profile=profile,
        plans=plans,
        results=store.list_results(run_id),
        findings=store.list_findings(run_id),
        scorecard=store.latest_scorecard(run_id),
        analysis=analysis,
        artifacts=artifacts,
        browser_sessions=store.list_browser_sessions(run_id),
        reviews=store.list_reviews(run_id),
        traces=store.list_traces(run_id),
        history=history,
        report_version=(int(previous["report_version"]) + 1) if previous else 1,
        notes=notes,
    )
