"""A job: one run (or one plan) waiting for a worker, described by plain data so it can cross a process boundary.

Everything a worker needs is in the job. Nothing in it is a secret: credentials are named, and the worker looks them up in
its own encrypted store."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from agentlab.core.config import AgentLabConfig
from agentlab.core.ids import new_id, utcnow
from agentlab.core.models import TargetSpec, TestCase
from agentlab.core.models.base import Model
from agentlab.design.models import TestPlan
from agentlab.orchestrator.options import RunOptions

JobKind = Literal["plan", "run"]


class JobOptions(Model):
    """The serialisable part of :class:`RunOptions`: what the person asked for."""

    suite: str = Field(default="full", description="discovery, functional, security, browser, full or regression")
    intensity: str = Field(default="standard", description="quick, standard or deep")
    include_skills: list[str] | None = Field(default=None, description="Use only these skills")
    exclude_skills: list[str] | None = Field(default=None, description="Never use these skills")
    scoring_profile: str | None = Field(default=None, description="Name of a scoring profile")
    second_wave: bool = Field(default=True, description="Add follow-up tests for what the first wave found")
    max_tests: int | None = Field(default=None, ge=1, description="Upper bound on the number of tests")
    judge: bool = Field(default=True, description="Use the LLM judge where deterministic checks cannot decide")
    objective: str | None = Field(default=None, description="What the owner wants to learn")
    requirements: list[str] = Field(default_factory=list, description="Business rules the agent must follow")
    probe: bool = Field(default=True, description="Send harmless discovery probes to the target")
    seed: int = Field(default=0, description="Seed for test generation")
    baseline_run_id: str | None = Field(default=None, description="Regression: replay the tests of this earlier run")
    only_tests: list[str] = Field(default_factory=list, description="Run just these test ids (or MEM-* patterns)")
    user_tests: list[TestCase] = Field(default_factory=list, description="The person's own test cases")
    user_test_files: list[str] = Field(
        default_factory=list, description="Server paths of uploaded files with test cases (set by the API)"
    )
    keep_workspace: bool = Field(default=False, description="Keep the temporary work folder for debugging")


class JobOverrides(Model):
    """Per-run changes to the server's configuration (limits and reporting only; never security settings)."""

    max_cost_usd: float | None = Field(default=None, gt=0, description="Stop at this cost in USD")
    max_execution_time_seconds: float | None = Field(default=None, gt=0, description="Stop after this many seconds")
    max_parallel: int | None = Field(default=None, ge=1, le=64, description="Tests that run at the same time")
    repetitions: int | None = Field(default=None, ge=1, le=20, description="Repeat every test this many times")
    report_formats: list[str] | None = Field(default=None, description="json, md, html, pdf (empty: no report)")

    def apply(self, config: AgentLabConfig) -> AgentLabConfig:
        """A copy of ``config`` with the overrides applied; the original is not touched."""
        cfg = config.model_copy(deep=True)
        if self.max_cost_usd is not None:
            cfg.limits.max_cost_usd = self.max_cost_usd
        if self.max_execution_time_seconds is not None:
            cfg.limits.max_execution_time_seconds = self.max_execution_time_seconds
        if self.max_parallel is not None:
            cfg.max_parallel = self.max_parallel
        if self.repetitions is not None:
            cfg.evaluation.repetitions = self.repetitions
        if self.report_formats is not None:
            from agentlab.reporting.bundle import normalise_formats

            cfg.reporting.formats = normalise_formats(self.report_formats) if self.report_formats else []  # type: ignore[assignment]
        return cfg


class JobSpec(Model):
    job_id: str = Field(default_factory=new_id)
    kind: JobKind
    run_id: str
    project: str = "default"
    target: TargetSpec
    options: JobOptions = Field(default_factory=JobOptions)
    overrides: JobOverrides = Field(default_factory=JobOverrides)
    plan: TestPlan | None = Field(default=None, description="The approved plan to execute (kind=run)")
    submitted_at: datetime = Field(default_factory=utcnow)
    submitted_by: str | None = None

    def run_options(self) -> RunOptions:
        o = self.options
        return RunOptions(
            suite=o.suite,
            intensity=o.intensity,
            include_skills=o.include_skills,
            exclude_skills=o.exclude_skills,
            user_test_files=[Path(p) for p in o.user_test_files],
            user_tests=list(o.user_tests),
            scoring_profile=o.scoring_profile,
            second_wave=o.second_wave,
            max_tests=o.max_tests,
            judge=o.judge,
            objective=o.objective,
            requirements=list(o.requirements),
            probe=o.probe,
            seed=o.seed,
            project=self.project,
            plan_only=self.kind == "plan",
            run_id=self.run_id,
            plan=self.plan,
            baseline_run_id=o.baseline_run_id,
            only_tests=list(o.only_tests),
            keep_workspace=o.keep_workspace,
        )


def job_summary(job: JobSpec) -> dict[str, Any]:
    return {"job_id": job.job_id, "kind": job.kind, "run_id": job.run_id, "target": job.target.name}
