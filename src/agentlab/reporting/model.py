"""The report as data (spec sections 29, 30 and 49).

A report is built once, as a :class:`ReportData`, and every format (JSON, Markdown, HTML, PDF) is a rendering of it.
That keeps the formats consistent and makes the JSON the single source of truth ("raw machine-readable results").

Principles that are visible in the structure:

* a finding says what was **observed** (facts, with evidence), what is **inferred**, what a **judge** thought and what is
  **recommended** - never "the agent is bad";
* ``BLOCKED`` is not ``FAILED``: tests that could not run are listed as coverage gaps with the reason, never as defects;
* a score always travels with the qualifiers that limit it (security only partly tested, judge unavailable, ...);
* the original evaluation is never overwritten by a human review: reviewed values sit next to the originals.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from agentlab.core.ids import utcnow
from agentlab.core.models.base import Model

REPORT_SCHEMA = "agentlab.report"
REPORT_SCHEMA_VERSION = 1

FactKind = Literal["observed", "inference", "judgment", "recommendation"]


class Versioning(Model):
    """Everything needed to reproduce the report's numbers (spec section 49)."""

    agentlab_version: str
    agentlab_commit: str | None = None
    python: str = ""
    platform: str = ""
    target: str = ""
    target_version: str | None = None
    target_commit: str | None = None
    target_spec_hash: str | None = None
    providers: list[dict[str, Any]] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    judges: list[dict[str, Any]] = Field(default_factory=list)
    judge_enabled: bool = False
    judge_independent: bool | None = None
    test_suite: dict[str, Any] = Field(default_factory=dict)
    skills: list[dict[str, Any]] = Field(default_factory=list)
    evaluation_profile: dict[str, Any] = Field(default_factory=dict)
    environment_fingerprint: str = ""
    config_hash: str = ""
    timestamp: datetime = Field(default_factory=utcnow)


class RunInfo(Model):
    run_id: str
    status: str
    target: str
    suite: str = ""
    intensity: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_s: float | None = None
    waves: int = 1
    error: str | None = None
    baseline_run_id: str | None = None
    complete: bool = True
    incomplete_reason: str | None = None


class KeyPoint(Model):
    kind: Literal["strength", "concern", "gap", "note"]
    text: str


class ExecutiveSummary(Model):
    headline: str
    verdict: str
    overall: float | None = None
    grade: str | None = None
    points: list[KeyPoint] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


class CategoryRow(Model):
    category: str
    label: str
    score: float | None
    weight: float
    confidence: float
    tests: int
    passed: int
    applicable: bool = True
    note: str | None = None


class ScorecardView(Model):
    profile: str
    profile_description: str = ""
    overall: float | None = None
    raw_overall: float | None = None
    grade: str | None = None
    confidence: float = 0.0
    security_cap_applied: bool = False
    cap_reason: str | None = None
    categories: list[CategoryRow] = Field(default_factory=list)
    qualifiers: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    scoring_note: str = (
        "Scores are a summary of the tests that ran. Categories with no applicable test are shown as N/A and do not "
        "count for or against the agent; BLOCKED tests are excluded from every score."
    )


class RiskSummary(Model):
    severity_counts: dict[str, int] = Field(default_factory=dict)
    open_findings: int = 0
    security_findings: int = 0
    security_posture: str = "not_tested"
    security_summary: str = ""
    top_risks: list[str] = Field(default_factory=list)
    unassessed_areas: list[str] = Field(default_factory=list)


class TargetOverview(Model):
    name: str
    version: str | None = None
    description: str = ""
    interfaces: list[str] = Field(default_factory=list)
    authentication: str = ""
    models: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    languages: dict[str, int] = Field(default_factory=dict)
    repository: dict[str, Any] = Field(default_factory=dict)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    data_sources: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    production: bool = False
    modes: list[str] = Field(default_factory=list)


class ArchitectureView(Model):
    nodes: list[dict[str, str]] = Field(default_factory=list)
    edges: list[dict[str, str]] = Field(default_factory=list)
    mermaid: str = ""
    note: str = ""


class ClassificationRow(Model):
    type: str
    confidence: float
    evidence: list[str] = Field(default_factory=list)


class CapabilityRow(Model):
    capability: str
    detected: bool
    testable: str
    reason: str = ""


class EnvironmentView(Model):
    docker: bool = False
    docker_note: str = ""
    browser: bool = False
    browser_note: str = ""
    judge: bool = False
    judge_note: str = ""
    judge_independent: bool | None = None
    sandbox_provider: str = ""
    interfaces: list[str] = Field(default_factory=list)
    interface_errors: dict[str, str] = Field(default_factory=dict)
    unreachable: dict[str, str] = Field(default_factory=dict)
    parallelism: int = 1
    canary_seeding: bool = False
    warnings: list[str] = Field(default_factory=list)


class MethodologyView(Model):
    summary: str
    steps: list[str] = Field(default_factory=list)
    evaluation_layers: list[str] = Field(default_factory=list)
    safety_rules: list[str] = Field(default_factory=list)
    scoring: list[str] = Field(default_factory=list)
    severity_model: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class InventoryRow(Model):
    key: str
    label: str = ""
    tests: int = 0
    passed: int = 0
    failed: int = 0
    blocked: int = 0
    other: int = 0


class CoverageRow(Model):
    key: str
    name: str
    status: str
    tests: int = 0
    executed: int = 0
    blocked: int = 0
    note: str = ""


class SuiteInventory(Model):
    total_planned: int = 0
    total_selected: int = 0
    executed: int = 0
    by_skill: list[InventoryRow] = Field(default_factory=list)
    by_category: list[InventoryRow] = Field(default_factory=list)
    by_origin: list[InventoryRow] = Field(default_factory=list)
    coverage: list[CoverageRow] = Field(default_factory=list)
    security_coverage: list[CoverageRow] = Field(default_factory=list)
    deselected: list[dict[str, str]] = Field(default_factory=list)
    plan_hash: str = ""
    plan_summary: str = ""
    waves: list[dict[str, Any]] = Field(default_factory=list)
    skills: list[dict[str, Any]] = Field(default_factory=list)


class CheckView(Model):
    type: str
    passed: bool
    message: str
    metric: str | None = None
    required: bool = True
    evaluator_error: bool = False
    turn_index: int | None = None


class AttemptView(Model):
    attempt: int
    status: str
    latency_ms: float = 0.0
    tokens: int = 0
    cost_usd: float = 0.0
    steps: int = 0
    error: str | None = None
    outputs: list[str] = Field(default_factory=list)
    checks: list[CheckView] = Field(default_factory=list)
    judge: list[dict[str, Any]] = Field(default_factory=list)
    trace_id: str | None = None


class ReviewOverlay(Model):
    """A human's opinion, shown *next to* the machine result and never in place of it."""

    id: str
    decision: str
    reviewer: str
    subject_type: str = ""  # result | finding
    subject: str = ""  # the test id (result) or finding id (finding) the review is about
    reason: str = ""
    comment: str = ""
    created_at: datetime | None = None
    original: dict[str, Any] = Field(default_factory=dict)
    reviewed: dict[str, Any] = Field(default_factory=dict)


class ResultRow(Model):
    test_id: str
    name: str
    category: str
    score_category: str
    skill: str | None = None
    status: str
    effective_status: str | None = None  # after human review, when different
    score: float = 0.0
    confidence: float = 0.0
    severity: str | None = None
    root_cause: str | None = None
    latency_ms: float = 0.0
    tokens: int = 0
    cost_usd: float = 0.0
    attempts: int = 1
    pass_rate: float | None = None
    flaky: bool = False
    blocked_reason: str | None = None
    error_kind: str | None = None
    objective: str = ""
    expected: str = ""
    inputs: list[str] = Field(default_factory=list)
    failed_checks: list[str] = Field(default_factory=list)
    finding_id: str | None = None
    taxonomy: list[str] = Field(default_factory=list)
    security_categories: list[str] = Field(default_factory=list)
    reviews: list[ReviewOverlay] = Field(default_factory=list)


class FailedTest(Model):
    test_id: str
    name: str
    category: str
    status: str
    severity: str | None = None
    objective: str = ""
    expected: str = ""
    inputs: list[str] = Field(default_factory=list)
    attempts: list[AttemptView] = Field(default_factory=list)
    why_it_failed: list[str] = Field(default_factory=list)
    reproduction: str = ""
    finding_id: str | None = None
    evidence: list[str] = Field(default_factory=list)


class FindingView(Model):
    id: str
    test_id: str
    title: str
    category: str
    severity: str
    effective_severity: str | None = None
    confidence: float
    is_security: bool = False
    status: str = "open"
    expected: str
    observed: str
    impact: str
    reproduction: str
    recommendation: str
    root_cause: str = "unknown"
    root_cause_confidence: float = 0.0
    facts: list[str] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)
    judgments: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    severity_breakdown: dict[str, Any] = Field(default_factory=dict)
    reviews: list[ReviewOverlay] = Field(default_factory=list)
    priority: int = 0


class SecurityCategoryView(Model):
    code: str
    name: str
    verdict: str
    tests: int = 0
    passed: int = 0
    failed: int = 0
    blocked: int = 0
    failing_tests: list[str] = Field(default_factory=list)
    blocked_tests: list[str] = Field(default_factory=list)
    note: str = ""


class SecuritySection(Model):
    tested: bool = False
    posture: str = "not_tested"
    summary: str = ""
    rating_note: str = ""
    caveats: list[str] = Field(default_factory=list)
    categories: list[SecurityCategoryView] = Field(default_factory=list)
    attacks_succeeded: list[dict[str, Any]] = Field(default_factory=list)
    verdict_counts: dict[str, int] = Field(default_factory=dict)
    findings: list[str] = Field(default_factory=list)  # finding ids
    canary_note: str = (
        "Security tests are authorized, non-destructive and canary based: a leak means a seeded canary or a planted "
        "secret appeared in an output; nothing real was attacked or exfiltrated."
    )


class DomainSection(Model):
    """RAG, tool, memory, browser, multi-agent, ... (sections 14-18)."""

    key: str
    title: str
    applicable: bool
    summary: str = ""
    tests: int = 0
    passed: int = 0
    failed: int = 0
    blocked: int = 0
    score: float | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    failures: list[str] = Field(default_factory=list)
    blocked_tests: list[dict[str, str]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ReliabilityFlaky(Model):
    test_id: str
    name: str
    passes: int
    repetitions: int
    pass_rate: float


class ReliabilitySection(Model):
    verdict: str = "not_measured"
    measured_tests: int = 0
    single_run_tests: int = 0
    stable_passes: int = 0
    consistency: float | None = None
    deterministic_failures: list[str] = Field(default_factory=list)
    flaky: list[ReliabilityFlaky] = Field(default_factory=list)
    timeout_tests: list[str] = Field(default_factory=list)
    error_tests: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class LatencyPoint(Model):
    test_id: str
    name: str
    latency_ms: float


class PerformanceSection(Model):
    measured: int = 0
    p50_ms: float | None = None
    p95_ms: float | None = None
    max_ms: float | None = None
    mean_ms: float | None = None
    budget_ms: float | None = None
    over_budget: list[str] = Field(default_factory=list)
    slowest: list[LatencyPoint] = Field(default_factory=list)
    histogram: list[dict[str, Any]] = Field(default_factory=list)
    timeouts: int = 0
    notes: list[str] = Field(default_factory=list)


class CostRow(Model):
    key: str
    label: str
    tokens: int = 0
    cost_usd: float = 0.0
    tests: int = 0


class CostSection(Model):
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    target_tokens: int = 0
    judge_tokens: int = 0
    judge_cost_usd: float = 0.0
    cost_known: bool = False
    by_category: list[CostRow] = Field(default_factory=list)
    most_expensive: list[CostRow] = Field(default_factory=list)
    limits: dict[str, Any] = Field(default_factory=dict)
    estimated: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class TrendPoint(Model):
    run_id: str
    started_at: datetime | None = None
    overall: float | None = None
    failed: int = 0
    tests: int = 0
    findings: int = 0
    comparable: bool = True


class TrendSection(Model):
    """Earlier runs against the same target, oldest first, to show how failures change over time."""

    points: list[TrendPoint] = Field(default_factory=list)
    note: str = ""


class EvidenceItem(Model):
    id: str
    kind: str
    name: str
    media_type: str = ""
    size: int = 0
    sha256: str = ""
    sensitivity: str = ""
    test_key: str | None = None
    href: str | None = None


class BrowserStepView(Model):
    index: int
    action: str
    target: str = ""
    ok: bool = True
    detail: str = ""
    at_ms: float | None = None
    screenshot: str | None = None


class BrowserSessionView(Model):
    test_id: str
    browser: str = ""
    trace: str | None = None
    video: str | None = None
    screenshots: list[str] = Field(default_factory=list)
    actions: list[BrowserStepView] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class RepositoryEvidence(Model):
    test_id: str
    files: list[str] = Field(default_factory=list)
    diff: str | None = None
    test_output: str | None = None
    notes: list[str] = Field(default_factory=list)


class EvidenceSection(Model):
    items: list[EvidenceItem] = Field(default_factory=list)
    browser: list[BrowserSessionView] = Field(default_factory=list)
    repository: list[RepositoryEvidence] = Field(default_factory=list)
    trace_ids: list[str] = Field(default_factory=list)
    redaction_note: str = (
        "Evidence is stored after secret redaction. Excerpts in this report are truncated; the full redacted "
        "artifacts are referenced by id and checksum."
    )


class Recommendation(Model):
    priority: int
    title: str
    why: str
    action: str
    severity: str | None = None
    confidence: float | None = None
    findings: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    effort: Literal["small", "medium", "large", "unknown"] = "unknown"
    kind: Literal["fix", "coverage", "verification", "configuration"] = "fix"


class AppendixSection(Model):
    skills: list[dict[str, Any]] = Field(default_factory=list)
    phases: list[dict[str, Any]] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)
    checksums_note: str = ""


class ReportData(Model):
    schema_id: str = Field(default=REPORT_SCHEMA, alias="schema")
    schema_version: int = REPORT_SCHEMA_VERSION
    report_version: int = 1
    generated_at: datetime = Field(default_factory=utcnow)
    title: str = ""
    run: RunInfo
    versioning: Versioning
    executive: ExecutiveSummary
    scorecard: ScorecardView
    reviewed_scorecard: ScorecardView | None = None  # only when a human reviewed results or findings
    risk: RiskSummary
    target: TargetOverview
    architecture: ArchitectureView
    classification: list[ClassificationRow] = Field(default_factory=list)
    capabilities: list[CapabilityRow] = Field(default_factory=list)
    environment: EnvironmentView
    methodology: MethodologyView
    inventory: SuiteInventory
    results: list[ResultRow] = Field(default_factory=list)
    failed_tests: list[FailedTest] = Field(default_factory=list)
    blocked_tests: list[ResultRow] = Field(default_factory=list)
    findings: list[FindingView] = Field(default_factory=list)
    security: SecuritySection
    domains: list[DomainSection] = Field(default_factory=list)
    reliability: ReliabilitySection
    performance: PerformanceSection
    cost: CostSection
    trend: TrendSection = Field(default_factory=TrendSection)
    regression: dict[str, Any] | None = None  # a rendered Comparison (see reporting.compare), when a baseline exists
    evidence: EvidenceSection = Field(default_factory=EvidenceSection)
    recommendations: list[Recommendation] = Field(default_factory=list)
    appendix: AppendixSection = Field(default_factory=AppendixSection)
    reviews: list[ReviewOverlay] = Field(default_factory=list)
    reviewed: bool = False

    model_config = {"extra": "forbid", "populate_by_name": True}

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


__all__ = [name for name in dir() if name[:1].isupper() or name.startswith("REPORT_")]
