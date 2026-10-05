"""Inputs and outputs of an orchestrated run."""

from __future__ import annotations

import contextlib
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from agentlab.adapters.base import TargetRuntime
from agentlab.core.enums import Phase, RunStatus, TestStatus
from agentlab.core.ids import utcnow
from agentlab.core.models import AgentProfile, Finding, Scorecard, TargetSpec, TestCase, TestResult
from agentlab.core.models.base import Model
from agentlab.design.models import TestPlan
from agentlab.discovery.agent import DiscoveryResult, IngestedTarget
from agentlab.evaluation.context import PlaceholderResolver
from agentlab.evaluation.judge import JudgeEngine
from agentlab.evaluation.scoring import ScoringProfile
from agentlab.orchestrator.analysis import CrossTestAnalysis, ReliabilityAnalysis, SecurityAnalysis
from agentlab.security.gate import AuthorizationGate
from agentlab.skills.context import SkillContext
from agentlab.tracing import EventBus


@dataclass
class RunOptions:
    """What the user asked for. Everything has a safe default; nothing here can widen the target's authorization."""

    suite: str = "full"
    intensity: str = "standard"
    include_skills: list[str] | None = None
    exclude_skills: list[str] | None = None
    user_test_files: list[Path] = field(default_factory=list)
    user_tests: list[TestCase] = field(default_factory=list)
    scoring_profile: str | None = None
    second_wave: bool = True
    max_tests: int | None = None
    judge: bool = True
    objective: str | None = None
    requirements: list[str] = field(default_factory=list)
    probe: bool = True
    seed: int = 0
    project: str = "default"
    plan_only: bool = False
    run_id: str | None = None
    plan: TestPlan | None = None  # an approved/edited plan to execute instead of designing one
    baseline_run_id: str | None = None  # regression: re-run the tests of this earlier run
    keep_workspace: bool = False


class PhaseRecord(Model):
    phase: Phase
    index: int
    status: Literal["completed", "skipped", "failed"] = "completed"
    streamed: bool = False
    duration_s: float = 0.0
    detail: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None


class EnvironmentReport(Model):
    """What phase 4 found: nothing here is assumed, everything was checked."""

    docker: bool = False
    docker_note: str = ""
    browser: bool = False
    browser_note: str = ""
    judge: bool = False
    judge_note: str = ""
    judge_independent: bool | None = None
    sandbox_provider: str = "docker"
    interfaces: list[str] = Field(default_factory=list)
    interface_errors: dict[str, str] = Field(default_factory=dict)
    unreachable: dict[str, str] = Field(default_factory=dict)
    parallelism: int = 1
    canary_seeding: bool = False
    warnings: list[str] = Field(default_factory=list)


@dataclass
class PreparedRun:
    """Phases 1-7 are done: the target is understood, the environment is ready and the plan exists. Nothing has been
    sent to the target yet except SAFE discovery probes. Call :meth:`aclose` when finished."""

    run_id: str
    spec: TargetSpec
    options: RunOptions
    project_id: str
    target_id: str
    profile: AgentProfile
    discovery: DiscoveryResult
    ingested: IngestedTarget
    ctx: SkillContext
    plan: TestPlan
    runtime: TargetRuntime
    gate: AuthorizationGate
    resolver: PlaceholderResolver
    judge: JudgeEngine | None
    scoring: ScoringProfile
    environment: EnvironmentReport
    bus: EventBus
    workdir: Path
    manifest: dict[str, Any]
    phases: list[PhaseRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    suite_id: str | None = None
    selected_skills: list[Any] = field(default_factory=list)
    started_at: datetime = field(default_factory=utcnow)
    _closed: bool = False

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            await self.runtime.close()
        self.ingested.cleanup()
        if not self.options.keep_workspace:
            shutil.rmtree(self.workdir, ignore_errors=True)


@dataclass
class RunOutcome:
    run_id: str
    status: RunStatus
    target: str
    profile: AgentProfile
    plans: list[TestPlan]
    tests: list[TestCase]
    results: list[TestResult]
    findings: list[Finding]
    scorecard: Scorecard | None
    cross_test: CrossTestAnalysis | None
    security: SecurityAnalysis | None
    reliability: ReliabilityAnalysis | None
    manifest: dict[str, Any]
    environment: EnvironmentReport
    limits: dict[str, Any]
    warnings: list[str]
    phases: list[PhaseRecord]
    scoring: ScoringProfile | None = None
    error: str | None = None
    report: Any = None  # the ReportBundle, when report generation ran
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.results:
            out[r.status.value] = out.get(r.status.value, 0) + 1
        return out

    @property
    def executed(self) -> int:
        return sum(1 for r in self.results if r.status in {TestStatus.PASSED, TestStatus.FAILED, TestStatus.TIMEOUT})

    @property
    def tested(self) -> bool:
        """False when nothing could be run (for example the target was unreachable): there is no verdict to give."""
        return self.executed > 0

    def summary(self) -> dict[str, Any]:
        sc = self.scorecard
        return {
            "run_id": self.run_id,
            "status": self.status.value,
            "target": self.target,
            "tests": len(self.results),
            "counts": self.counts,
            "findings": len(self.findings),
            "overall": sc.overall if sc else None,
            "grade": sc.grade if sc else None,
            "security_posture": self.security.posture if self.security else None,
            "reliability": self.reliability.verdict if self.reliability else None,
            "cost_usd": self.limits.get("cost_usd"),
            "elapsed_s": self.limits.get("elapsed_s"),
            "waves": len(self.plans),
        }
