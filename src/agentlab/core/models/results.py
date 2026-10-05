"""Evaluation outputs: assertion/judge results, test results, findings and scorecards."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from agentlab.core.enums import ErrorKind, RootCause, Severity, TestStatus
from agentlab.core.ids import new_id, utcnow
from agentlab.core.models.base import Model


class AssertionResult(Model):
    type: str
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    message: str
    metric: str | None = None
    weight: float = 1.0
    evidence: dict[str, Any] = Field(default_factory=dict)
    turn_index: int | None = None
    severity: Severity | None = None


class JudgeVote(Model):
    judge: str
    provider: str
    model: str
    score: float
    passed: bool
    confidence: float
    reasoning: str
    uncertain: bool = False


class JudgeResult(Model):
    metric: str
    score: float = Field(ge=0.0, le=1.0)
    passed: bool
    confidence: float = Field(ge=0.0, le=1.0)
    rubric: str
    votes: list[JudgeVote] = Field(default_factory=list)
    strategy: str = "single"
    agreement: float = 1.0
    error: str | None = None


class AttemptResult(Model):
    """One repetition of a test."""

    attempt: int
    status: TestStatus
    assertions: list[AssertionResult] = Field(default_factory=list)
    judge: list[JudgeResult] = Field(default_factory=list)
    trajectory: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float = 0.0
    tokens: int = 0
    cost_usd: float = 0.0
    steps: int = 0
    error: str | None = None
    error_kind: ErrorKind | None = None
    outputs: list[str] = Field(default_factory=list)
    trace_id: str | None = None


class ReliabilityStats(Model):
    repetitions: int
    passes: int
    pass_rate: float
    flaky: bool
    deterministic_failure: bool
    timeout_rate: float = 0.0
    error_rate: float = 0.0
    output_variance: float = 0.0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0


class TestResult(Model):
    __test__ = False

    id: str = Field(default_factory=new_id)
    run_id: str
    test_id: str
    test_name: str
    category: str
    score_category: str
    status: TestStatus
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    attempts: list[AttemptResult] = Field(default_factory=list)
    reliability: ReliabilityStats | None = None
    blocked_reason: str | None = None
    error_kind: ErrorKind | None = None
    root_cause: RootCause | None = None
    root_cause_confidence: float | None = None
    severity: Severity | None = None
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    latency_ms: float = 0.0
    tokens: int = 0
    cost_usd: float = 0.0
    evidence: list[str] = Field(default_factory=list, description="Artifact ids")
    trace_ids: list[str] = Field(default_factory=list)
    review: dict[str, Any] | None = None


class Finding(Model):
    """A specific, evidenced defect (spec section 30). Never 'agent is bad'."""

    id: str = Field(default_factory=new_id)
    run_id: str
    test_id: str
    title: str
    category: str
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    expected: str
    observed: str
    impact: str
    evidence: list[str] = Field(default_factory=list)
    reproduction: str
    recommendation: str
    root_cause: RootCause = RootCause.UNKNOWN
    root_cause_confidence: float = 0.3
    is_security: bool = False
    fact_kind: str = Field(default="observed", description="observed | inference | judgment")
    status: str = "open"
    original: dict[str, Any] | None = None


class CategoryScore(Model):
    category: str
    score: float | None = Field(description="0-100, None when N/A")
    weight: float
    confidence: float
    tests: int
    passed: int
    applicable: bool = True
    note: str | None = None


class Scorecard(Model):
    profile: str
    categories: list[CategoryScore]
    overall: float | None
    overall_confidence: float
    security_cap_applied: bool = False
    cap_reason: str | None = None
    grade: str | None = None
