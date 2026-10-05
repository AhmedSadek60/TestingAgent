"""Relational schema (spec section 25).

Every table carries ``id`` (UUID string), ``created_at``, ``updated_at``, ``version`` and
``status``. Large payloads (traces, screenshots, videos, reports) live in the artifact
store; tables keep references and queryable summaries.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from agentlab.core.ids import new_id, utcnow

JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    __test__ = False  # keep pytest from collecting Test* ORM classes


class Entity(Base):
    __abstract__ = True

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(40), default="active")


def _fk(table: str, nullable: bool = False, ondelete: str = "CASCADE") -> Mapped[Any]:
    return mapped_column(String(36), ForeignKey(f"{table}.id", ondelete=ondelete), nullable=nullable, index=True)


class Project(Entity):
    __tablename__ = "projects"
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    objective: Mapped[str] = mapped_column(Text, default="")
    settings: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    targets: Mapped[list[Target]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Target(Entity):
    __tablename__ = "targets"
    project_id: Mapped[str] = _fk("projects")
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40), default="unknown")
    target_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    project: Mapped[Project] = relationship(back_populates="targets")
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_target_project_name"),)


class Repository(Entity):
    __tablename__ = "repositories"
    target_id: Mapped[str] = _fk("targets")
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot_artifact_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    analysis: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class AgentProfileRow(Entity):
    __tablename__ = "agent_profiles"
    target_id: Mapped[str] = _fk("targets")
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
    profile: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class CredentialProfileRow(Entity):
    """Metadata only. Secret values live in the encrypted secret store, never here."""

    __tablename__ = "credential_profiles"
    project_id: Mapped[str | None] = _fk("projects", nullable=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(40))
    scopes: Mapped[list[str]] = mapped_column(JSONType, default=list)
    test_only: Mapped[bool] = mapped_column(default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    secret_version: Mapped[int] = mapped_column(Integer, default=1)
    description: Mapped[str] = mapped_column(Text, default="")


class Document(Entity):
    __tablename__ = "documents"
    project_id: Mapped[str] = _fk("projects")
    name: Mapped[str] = mapped_column(String(300))
    media_type: Mapped[str] = mapped_column(String(120), default="")
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    versions: Mapped[list[DocumentVersion]] = relationship(back_populates="document", cascade="all, delete-orphan")


class DocumentVersion(Entity):
    __tablename__ = "document_versions"
    document_id: Mapped[str] = _fk("documents")
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size: Mapped[int] = mapped_column(Integer, default=0)
    artifact_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    parsed: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    document: Mapped[Document] = relationship(back_populates="versions")


class SkillRow(Entity):
    __tablename__ = "skills"
    name: Mapped[str] = mapped_column(String(120))
    skill_version: Mapped[str] = mapped_column(String(40))
    origin: Mapped[str] = mapped_column(String(40), default="builtin")
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    __table_args__ = (UniqueConstraint("name", "skill_version", name="uq_skill_name_version"),)


class TestSuiteRow(Entity):
    __tablename__ = "test_suites"
    project_id: Mapped[str] = _fk("projects")
    target_id: Mapped[str | None] = _fk("targets", nullable=True, ondelete="SET NULL")
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40), default="full")
    suite_hash: Mapped[str] = mapped_column(String(64), index=True)
    plan: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)  # selection, explanations, skills
    cases: Mapped[list[TestCaseRow]] = relationship(back_populates="suite", cascade="all, delete-orphan")


class TestCaseRow(Entity):
    __tablename__ = "test_cases"
    suite_id: Mapped[str] = _fk("test_suites")
    test_key: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(80))
    risk_level: Mapped[str] = mapped_column(String(20))
    definition: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    suite: Mapped[TestSuiteRow] = relationship(back_populates="cases")
    __table_args__ = (UniqueConstraint("suite_id", "test_key", name="uq_case_suite_key"),)


class ModelProviderRow(Entity):
    __tablename__ = "model_providers"
    name: Mapped[str] = mapped_column(String(120), unique=True)
    type: Mapped[str] = mapped_column(String(60))
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)  # key *references* only


class ModelConfigurationRow(Entity):
    __tablename__ = "model_configurations"
    provider_id: Mapped[str] = _fk("model_providers")
    model: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(40), default="judge")
    config: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class TestRunRow(Entity):
    __tablename__ = "test_runs"
    project_id: Mapped[str] = _fk("projects")
    target_id: Mapped[str | None] = _fk("targets", nullable=True, ondelete="SET NULL")
    suite_id: Mapped[str | None] = _fk("test_suites", nullable=True, ondelete="SET NULL")
    mode: Mapped[str] = mapped_column(String(40), default="full")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    limits: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    totals: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    results: Mapped[list[TestResultRow]] = relationship(back_populates="run", cascade="all, delete-orphan")


class TestResultRow(Entity):
    __tablename__ = "test_results"
    run_id: Mapped[str] = _fk("test_runs")
    test_key: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(80))
    score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True)
    error_kind: Mapped[str | None] = mapped_column(String(40), nullable=True)
    root_cause: Mapped[str | None] = mapped_column(String(60), nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    result: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)  # full original TestResult
    run: Mapped[TestRunRow] = relationship(back_populates="results")
    __table_args__ = (
        UniqueConstraint("run_id", "test_key", name="uq_result_run_key"),
        Index("ix_result_run_status", "run_id", "status"),
    )


class TraceRow(Entity):
    __tablename__ = "traces"
    run_id: Mapped[str] = _fk("test_runs")
    test_key: Mapped[str] = mapped_column(String(120))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    artifact_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class EventRow(Base):
    __tablename__ = "events"
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = _fk("test_runs")
    test_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    type: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    redaction_status: Mapped[str] = mapped_column(String(20), default="not_scanned")


class ArtifactRow(Entity):
    __tablename__ = "artifacts"
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(60))
    media_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)
    sensitivity: Mapped[str] = mapped_column(String(20), default="normal")
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    test_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    uri: Mapped[str] = mapped_column(String(500), default="")
    meta: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class EvaluatorRow(Entity):
    __tablename__ = "evaluators"
    name: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(40))
    evaluator_version: Mapped[str] = mapped_column(String(40), default="1")
    config: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    __table_args__ = (UniqueConstraint("name", "evaluator_version", name="uq_evaluator_name_version"),)


class EvaluationRow(Entity):
    __tablename__ = "evaluations"
    result_id: Mapped[str] = _fk("test_results")
    evaluator_id: Mapped[str | None] = _fk("evaluators", nullable=True, ondelete="SET NULL")
    metric: Mapped[str] = mapped_column(String(120))
    layer: Mapped[str] = mapped_column(String(20), default="deterministic")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    passed: Mapped[bool] = mapped_column(default=False)
    rubric_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class FindingRow(Entity):
    __tablename__ = "findings"
    run_id: Mapped[str] = _fk("test_runs")
    test_key: Mapped[str] = mapped_column(String(120))
    severity: Mapped[str] = mapped_column(String(20), index=True)
    category: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(300))
    is_security: Mapped[bool] = mapped_column(default=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    finding: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)  # original, never overwritten


class ScorecardRow(Entity):
    __tablename__ = "scorecards"
    run_id: Mapped[str] = _fk("test_runs")
    profile: Mapped[str] = mapped_column(String(80))
    overall: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    scorecard: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class ReportRow(Entity):
    __tablename__ = "reports"
    run_id: Mapped[str] = _fk("test_runs")
    report_version: Mapped[int] = mapped_column(Integer, default=1)
    formats: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)  # format -> artifact id
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class BrowserSessionRow(Entity):
    __tablename__ = "browser_sessions"
    run_id: Mapped[str] = _fk("test_runs")
    test_key: Mapped[str] = mapped_column(String(120))
    browser: Mapped[str] = mapped_column(String(20), default="chromium")
    trace_artifact_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    video_artifact_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    screenshot_artifact_ids: Mapped[list[str]] = mapped_column(JSONType, default=list)
    actions: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class ReviewRow(Entity):
    """Human review. The original result/finding is never modified (spec section 59)."""

    __tablename__ = "reviews"
    run_id: Mapped[str] = _fk("test_runs")
    subject_type: Mapped[str] = mapped_column(String(20))  # result | finding
    subject_id: Mapped[str] = mapped_column(String(36), index=True)
    decision: Mapped[str] = mapped_column(String(30))
    reviewer: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(Text, default="")
    original: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    reviewed: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    comment: Mapped[str] = mapped_column(Text, default="")
