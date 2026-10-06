"""Test case model (spec section 7) and assertion specification."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from agentlab.core.enums import AgentType, RiskClass, Severity, TestStatus, normalize_score_category
from agentlab.core.models.base import Model


class Turn(Model):
    """One user turn. ``session`` lets a test address isolated sessions (A/B)."""

    input: str
    session: str = "default"
    attachments: list[str] = Field(default_factory=list)
    assertions: list[AssertionSpec] = Field(default_factory=list)


class AssertionSpec(Model):
    """A deterministic check. ``type`` selects the evaluator plug-in."""

    type: str
    params: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None
    severity: Severity | None = None
    weight: float = 1.0
    metric: str | None = None
    required: bool = True
    turn: int | None = Field(default=None, description="Apply to this turn only (0-based); default: last turn")


class JudgeCriterion(Model):
    metric: str
    rubric: str
    weight: float = 1.0
    threshold: float = 0.6


class ExpectedToolCall(Model):
    name: str
    arguments: dict[str, Any] | None = None
    match: Literal["exact", "subset", "name_only"] = "subset"


class BrowserStep(Model):
    """Declarative browser step executed by the BrowserExecutionEngine."""

    action: Literal[
        "goto",
        "click",
        "fill",
        "press",
        "select",
        "check",
        "upload",
        "expect_text",
        "expect_url",
        "expect_visible",
        "wait",
        "screenshot",
        "download",
        "dialog_accept",
        "dialog_dismiss",
        "chat",
    ]
    target: str | None = None
    value: str | None = None
    role: str | None = None
    name: str | None = None
    timeout_ms: int = 10_000


class TestCase(Model):
    __test__ = False

    id: str
    name: str
    category: str
    subcategory: str = "general"
    objective: str
    rationale: str = Field(default="", description="Why this test was generated (explainability)")
    skill: str | None = None
    skill_version: str | None = None
    risk_level: RiskClass = RiskClass.SAFE
    severity_on_failure: Severity = Severity.MEDIUM
    preconditions: list[str] = Field(default_factory=list)
    required_credentials: list[str] = Field(default_factory=list)
    required_interfaces: list[str] = Field(default_factory=list)
    input: str | None = None
    turns: list[Turn] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    expected_behavior: str = ""
    expected_output: str | None = None
    expected_tool_calls: list[ExpectedToolCall] = Field(default_factory=list)
    forbidden_behavior: list[str] = Field(default_factory=list)
    assertions: list[AssertionSpec] = Field(default_factory=list)
    judge: list[JudgeCriterion] = Field(default_factory=list)
    evaluation_metrics: list[str] = Field(default_factory=list)
    browser_steps: list[BrowserStep] = Field(default_factory=list)
    timeout: float = 60.0
    max_steps: int = 20
    max_cost: float = 0.5
    max_tokens: int = 20_000
    repetitions: int | None = None
    cleanup_strategy: str = "none"
    evidence_requirements: list[str] = Field(default_factory=list)
    applicable_agent_types: list[AgentType] = Field(default_factory=list)
    isolation_key: str | None = Field(
        default=None, description="Tests sharing a key share mutable state and never run in parallel"
    )
    status: TestStatus = TestStatus.DRAFT
    tags: list[str] = Field(default_factory=list)
    score_category: str = "functional_quality"

    @field_validator("score_category")
    @classmethod
    def _score_category(cls, v: str) -> str:
        return normalize_score_category(v)

    def all_turns(self) -> list[Turn]:
        if self.turns:
            return self.turns
        if self.input is not None:
            return [Turn(input=self.input)]
        return []


Turn.model_rebuild()
