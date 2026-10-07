"""Plan models: an explainable, editable description of what will be tested and why, before anything runs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from agentlab.core.enums import Intensity, RiskClass, Suite
from agentlab.core.ids import short_id, utcnow
from agentlab.core.models import TestCase
from agentlab.core.models.base import Model
from agentlab.skills.model import SkillMatch

Origin = Literal["skill", "user", "llm", "adaptive"]


class PlannedTest(Model):
    test: TestCase
    skill: str
    skill_version: str
    wave: int = 1
    taxonomy: list[str] = Field(default_factory=list, description="taxonomy letters A-Q")
    security_categories: list[str] = Field(default_factory=list, description="N1-N28 codes")
    reasons: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    risk: RiskClass = RiskClass.SAFE
    gate_reasons: list[str] = Field(default_factory=list)
    predicted: Literal["runnable", "blocked"] = "runnable"
    blocked_kind: str | None = None
    blocked_reason: str | None = None
    selected: bool = True
    deselected_reason: str | None = None
    est_attempts: int = 1
    est_calls: int = 1
    est_judge_calls: int = 0
    est_seconds: float = 0.0
    est_tokens: int = 0
    origin: Origin = "skill"

    @property
    def id(self) -> str:
        return self.test.id


CoverageStatus = Literal["covered", "partial", "not_covered", "not_applicable"]


class CoverageEntry(Model):
    key: str  # "A".."Q" or "N1".."N28"
    name: str
    status: CoverageStatus
    tests: int = 0
    runnable: int = 0
    blocked: int = 0
    skills: list[str] = Field(default_factory=list)
    note: str = ""


class BudgetEstimate(Model):
    tests: int = 0
    attempts: int = 0
    target_calls: int = 0
    judge_calls: int = 0
    est_tokens: int = 0
    est_cost_usd: float | None = None
    cost_note: str = ""
    serial_seconds: float = 0.0
    est_wall_seconds: float = 0.0
    within_limits: bool = True
    notes: list[str] = Field(default_factory=list)


class PlanWarning(Model):
    level: Literal["info", "warning", "blocker"] = "warning"
    code: str
    message: str


class TestPlan(Model):
    __test__ = False

    id: str = Field(default_factory=lambda: short_id("plan_"))
    target: str
    created_at: datetime = Field(default_factory=utcnow)
    wave: int = 1
    parent_plan_id: str | None = None
    suite: Suite = "full"
    intensity: Intensity = "standard"
    profile_hash: str = ""
    summary: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict, description="what the plan was built from (no secrets)")
    skills: list[SkillMatch] = Field(default_factory=list)
    tests: list[PlannedTest] = Field(default_factory=list)
    coverage: list[CoverageEntry] = Field(default_factory=list)
    security_coverage: list[CoverageEntry] = Field(default_factory=list)
    budget: BudgetEstimate = Field(default_factory=BudgetEstimate)
    warnings: list[PlanWarning] = Field(default_factory=list)
    skill_notes: dict[str, list[str]] = Field(default_factory=dict)
    proposed_skills: list[str] = Field(
        default_factory=list, description="draft skills the forge wrote for uncovered capabilities"
    )
    assumptions: list[str] = Field(default_factory=list)
    limits: dict[str, Any] = Field(default_factory=dict)
    plan_hash: str = ""

    # ------------------------------------------------------------------ views
    def selected_tests(self) -> list[PlannedTest]:
        return [t for t in self.tests if t.selected]

    def runnable(self) -> list[PlannedTest]:
        return [t for t in self.selected_tests() if t.predicted == "runnable"]

    def predicted_blocked(self) -> list[PlannedTest]:
        return [t for t in self.selected_tests() if t.predicted == "blocked"]

    def get(self, test_id: str) -> PlannedTest:
        for t in self.tests:
            if t.id == test_id:
                return t
        raise KeyError(test_id)

    def test_cases(self, *, include_blocked: bool = True) -> list[TestCase]:
        src = self.selected_tests() if include_blocked else self.runnable()
        return [p.test for p in src]

    def compute_hash(self) -> str:
        """Identity of the plan content: used to decide whether two runs are comparable."""
        parts = sorted(
            (p.id, p.skill, p.skill_version, p.test.model_dump_json(exclude={"status", "rationale"}))
            for p in self.tests
            if p.selected
        )
        return hashlib.sha256(json.dumps([self.suite, self.intensity, parts]).encode()).hexdigest()[:24]

    def deselect(self, test_ids: Iterable[str], reason: str = "deselected by the user") -> list[str]:
        """Remove tests from what will run (they stay in the plan, visibly deselected). Returns the ids changed."""
        changed = []
        for tid in test_ids:
            p = self.get(tid)
            if p.selected:
                p.selected, p.deselected_reason = False, reason
                changed.append(tid)
        self.plan_hash = self.compute_hash()
        return changed

    def select(self, test_ids: Iterable[str]) -> list[str]:
        changed = []
        for tid in test_ids:
            p = self.get(tid)
            if not p.selected:
                p.selected, p.deselected_reason = True, None
                changed.append(tid)
        self.plan_hash = self.compute_hash()
        return changed

    def block_summary(self) -> dict[str, int]:
        """Predicted-BLOCKED tests grouped by reason."""
        out: dict[str, int] = {}
        for p in self.predicted_blocked():
            out[p.blocked_reason or "unknown"] = out.get(p.blocked_reason or "unknown", 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def to_markdown(self, *, detail: bool = False) -> str:
        from agentlab.design.render import plan_markdown

        return plan_markdown(self, detail=detail)

    def counts(self) -> dict[str, int]:
        sel = self.selected_tests()
        return {
            "tests": len(sel),
            "runnable": sum(1 for t in sel if t.predicted == "runnable"),
            "blocked": sum(1 for t in sel if t.predicted == "blocked"),
            "skills_selected": sum(1 for s in self.skills if s.selected),
        }
