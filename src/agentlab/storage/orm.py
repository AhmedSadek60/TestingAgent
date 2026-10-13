"""Relational schema (spec section 25).

Every table carries ``id`` (UUID string), ``created_at``, ``updated_at``, ``version`` and
``status``. Large payloads (traces, screenshots, videos, reports) live in the artifact
store; tables keep references and queryable summaries.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, TypeDecorator, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeEngine

from agentlab.core.ids import new_id, utcnow
from agentlab.security.redactor import redact_strings


class RedactedJSON(TypeDecorator[Any]):
    """JSON whose string values are scrubbed of secrets on the way in: whatever a target said or a request carried, no
    credential reaches the database (the artifact store applies the same redactor to files)."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        return dialect.type_descriptor(JSONB() if dialect.name == "postgresql" else JSON())

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        return redact_strings(value) if value is not None else None


class RedactedText(TypeDecorator[str]):
    """Free text (a reason, an error message) scrubbed of secrets on the way in."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:
        return redact_strings(value) if value is not None else None


JSONType = RedactedJSON()


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
    blocked_reason: Mapped[str | None] = mapped_column(RedactedText, nullable=True)
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


# =============================================================================================== accounts
class UserRow(Entity):
    """A person who signs in. ``status`` is ``active``, ``pending`` (waiting for an admin) or ``disabled``.

    The password is never stored: only a salted, memory-hard hash with its parameters (``accounts/passwords.py``)."""

    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    role: Mapped[str] = mapped_column(String(20), default="reporter", index=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    must_change_password: Mapped[bool] = mapped_column(default=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class UserSessionRow(Entity):
    """A sign-in. The cookie holds a random value; only its SHA-256 is kept here."""

    __tablename__ = "user_sessions"
    user_id: Mapped[str] = _fk("users")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    client: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(200), default="")


class AccountEventRow(Entity):
    """What happened to accounts: who did what to whom, from where. Never a password, a hash or a token."""

    __tablename__ = "account_events"
    event: Mapped[str] = mapped_column(String(40), index=True)
    actor: Mapped[str] = mapped_column(String(80), default="")
    subject: Mapped[str] = mapped_column(String(80), default="", index=True)
    client: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


# ================================================================================================= feedback
class SubmissionRow(Entity):
    """A feature request or an issue report. ``status`` is one of the configured workflow statuses.

    Free text goes through ``RedactedText``: a secret pasted into a description is masked before it is stored."""

    __tablename__ = "submissions"
    human_id: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    type: Mapped[str] = mapped_column(String(10), index=True)  # feature | issue
    year: Mapped[int] = mapped_column(Integer)
    seq: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(RedactedText())
    description: Mapped[str] = mapped_column(RedactedText(), default="")
    category: Mapped[str] = mapped_column(String(80), index=True)
    requested_priority: Mapped[str | None] = mapped_column(String(24), nullable=True)
    assigned_priority: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    severity: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    visibility: Mapped[str] = mapped_column(String(12), default="normal", index=True)  # normal | security
    reporter_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    reporter_label: Mapped[str] = mapped_column(String(80), default="")
    assignee_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    related_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    related_test_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    related_trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    related_result_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    related_report_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    related_target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    build: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    last_staff_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reporter_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("type", "year", "seq", name="uq_submission_number"),)


class FeatureDetailsRow(Entity):
    __tablename__ = "feature_request_details"
    submission_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("submissions.id", ondelete="CASCADE"), unique=True, index=True
    )
    problem_statement: Mapped[str] = mapped_column(RedactedText())
    expected_behavior: Mapped[str] = mapped_column(RedactedText(), default="")
    primary_use_case: Mapped[str] = mapped_column(RedactedText(), default="")
    why_important: Mapped[str] = mapped_column(RedactedText(), default="")
    users_affected: Mapped[str] = mapped_column(RedactedText(), default="")
    business_impact: Mapped[str] = mapped_column(RedactedText(), default="")
    use_frequency: Mapped[str] = mapped_column(String(120), default="")
    current_workaround: Mapped[str] = mapped_column(RedactedText(), default="")
    proposed_solution: Mapped[str] = mapped_column(RedactedText(), default="")
    acceptance_criteria: Mapped[str] = mapped_column(RedactedText(), default="")
    related_area: Mapped[str] = mapped_column(String(200), default="")
    dependencies: Mapped[str] = mapped_column(RedactedText(), default="")


class IssueDetailsRow(Entity):
    __tablename__ = "issue_report_details"
    submission_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("submissions.id", ondelete="CASCADE"), unique=True, index=True
    )
    impact_summary: Mapped[str] = mapped_column(RedactedText())
    expected_behavior: Mapped[str] = mapped_column(RedactedText(), default="")
    actual_behavior: Mapped[str] = mapped_column(RedactedText(), default="")
    reproduction_steps: Mapped[str] = mapped_column(RedactedText(), default="")
    not_reproducible: Mapped[bool] = mapped_column(default=False)
    frequency: Mapped[str] = mapped_column(String(20), default="unknown", index=True)
    first_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consistent: Mapped[bool | None] = mapped_column(nullable=True)
    blocks_work: Mapped[bool | None] = mapped_column(nullable=True)
    workaround: Mapped[str] = mapped_column(RedactedText(), default="")
    reported_impact: Mapped[str] = mapped_column(String(30), default="other", index=True)
    ui_screen: Mapped[str] = mapped_column(String(200), default="")
    worker_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_config_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SubmissionCommentRow(Entity):
    """A line of the discussion. ``visibility`` is ``public`` (the reporter sees it) or ``internal`` (maintainers only)."""

    __tablename__ = "submission_comments"
    submission_id: Mapped[str] = _fk("submissions")
    author_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    author_label: Mapped[str] = mapped_column(String(80), default="")
    author_is_staff: Mapped[bool] = mapped_column(default=False)
    visibility: Mapped[str] = mapped_column(String(10), default="public", index=True)
    kind: Mapped[str] = mapped_column(String(20), default="comment")  # comment | info_request | system
    body: Mapped[str] = mapped_column(RedactedText())


class SubmissionHistoryRow(Entity):
    """Every status change, never overwritten."""

    __tablename__ = "submission_status_history"
    submission_id: Mapped[str] = _fk("submissions")
    old_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    new_status: Mapped[str] = mapped_column(String(40))
    changed_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    changed_by_label: Mapped[str] = mapped_column(String(80), default="")
    by_staff: Mapped[bool] = mapped_column(default=False)
    comment: Mapped[str] = mapped_column(RedactedText(), default="")


class SubmissionLabelRow(Entity):
    __tablename__ = "submission_labels"
    submission_id: Mapped[str] = _fk("submissions")
    label: Mapped[str] = mapped_column(String(60), index=True)
    __table_args__ = (UniqueConstraint("submission_id", "label", name="uq_submission_label"),)


class SubmissionRelationRow(Entity):
    __tablename__ = "submission_relations"
    from_id: Mapped[str] = _fk("submissions")
    to_id: Mapped[str] = _fk("submissions")
    kind: Mapped[str] = mapped_column(String(24))  # duplicate_of | related | caused_by | implements
    created_by_label: Mapped[str] = mapped_column(String(80), default="")
    __table_args__ = (UniqueConstraint("from_id", "to_id", "kind", name="uq_submission_relation"),)


class ExternalIssueLinkRow(Entity):
    """The issue a submission was published to in another system. ``status`` is ``publishing`` (the call was started),
    ``published`` or ``failed``. One per submission and provider: a retry cannot make a second one."""

    __tablename__ = "external_issue_links"
    submission_id: Mapped[str] = _fk("submissions")
    provider: Mapped[str] = mapped_column(String(20))
    key: Mapped[str] = mapped_column(String(64))  # names the publication, so a retry finds an issue that was made
    external_id: Mapped[str] = mapped_column(String(64), default="")
    external_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    url: Mapped[str] = mapped_column(String(300), default="")
    external_state: Mapped[str] = mapped_column(String(10), default="open")
    error_kind: Mapped[str] = mapped_column(String(20), default="")
    error_detail: Mapped[str] = mapped_column(String(300), default="")
    submission_version: Mapped[int] = mapped_column(Integer, default=1)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    repository: Mapped[str] = mapped_column(String(200), default="")
    published_by_label: Mapped[str] = mapped_column(String(80), default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synced_status: Mapped[str] = mapped_column(String(40), default="")
    __table_args__ = (UniqueConstraint("submission_id", "provider", name="uq_external_issue_link"),)


class SubmissionCounterRow(Base):
    """The highest number given out for a type and a year, so that a number of a deleted submission is never given again."""

    __tablename__ = "submission_counters"
    type: Mapped[str] = mapped_column(String(10), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_seq: Mapped[int] = mapped_column(Integer)


class SubmissionAuditRow(Entity):
    """Important actions on submissions. No foreign key: the record outlives the submission it is about."""

    __tablename__ = "submission_audit_events"
    submission_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    event: Mapped[str] = mapped_column(String(40), index=True)
    actor_label: Mapped[str] = mapped_column(String(80), default="")
    actor_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    client: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class SubmissionAttachmentRow(Entity):
    """A file attached to a submission. The bytes are in the artifact store, restricted; this row is what is known about them.

    ``status`` is ``available`` or ``removed``. A removed attachment keeps its row (for the audit trail) and loses its bytes
    when nothing else uses them."""

    __tablename__ = "submission_attachments"
    submission_id: Mapped[str] = _fk("submissions")
    uploader_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    uploader_label: Mapped[str] = mapped_column(String(80), default="")
    uploader_is_staff: Mapped[bool] = mapped_column(default=False)
    visibility: Mapped[str] = mapped_column(String(10), default="public")  # public | internal
    original_name: Mapped[str] = mapped_column(String(200), default="")
    extension: Mapped[str] = mapped_column(String(16), default="")
    declared_type: Mapped[str] = mapped_column(String(120), default="")
    detected_type: Mapped[str] = mapped_column(String(120), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    artifact_id: Mapped[str] = mapped_column(String(80))
    sanitized_artifact_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    text_like: Mapped[bool] = mapped_column(default=False)
    secrets_status: Mapped[str] = mapped_column(String(16), default="not_applicable")
    secrets_report: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    scan_status: Mapped[str] = mapped_column(String(16), default="not_scanned")
    scan_detail: Mapped[str] = mapped_column(String(200), default="")
    archive_summary: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    removed_by_label: Mapped[str] = mapped_column(String(80), default="")


# ===================================================================================================== diagnostics
class LogEventRow(Base):
    """One line AgentLab logged, kept briefly and already redacted, so it can be attached to an issue report.

    Not an ``Entity``: there are many, they are never updated, and an integer key is what a bounded table needs."""

    __tablename__ = "log_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    level: Mapped[str] = mapped_column(String(10))
    logger: Mapped[str] = mapped_column(String(120))
    process: Mapped[str] = mapped_column(String(12), index=True)  # api | worker
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    request_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    principal: Mapped[str] = mapped_column(String(80), default="", index=True)
    message: Mapped[str] = mapped_column(Text)
    exception: Mapped[str | None] = mapped_column(Text, nullable=True)


class DiagnosticCollectionRow(Entity):
    """What one person chose to collect for a report, kept sanitized, until it is attached to a submission or expires.

    ``status`` is ``draft``, ``attached`` or ``expired``."""

    __tablename__ = "diagnostic_collections"
    owner_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    owner_label: Mapped[str] = mapped_column(String(80), default="")
    submission_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("submissions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    requested: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)  # categories, window, ids
    window_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    window_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consent_by: Mapped[str] = mapped_column(String(80), default="")


class DiagnosticItemRow(Entity):
    """One thing collected: a log extract, an environment record, a trace. Only the sanitized text is kept."""

    __tablename__ = "diagnostic_items"
    collection_id: Mapped[str] = _fk("diagnostic_collections")
    category: Mapped[str] = mapped_column(String(32), index=True)
    path: Mapped[str] = mapped_column(String(120), default="")  # where it sits in the bundle
    source: Mapped[str] = mapped_column(String(120), default="")
    media_type: Mapped[str] = mapped_column(String(60), default="text/plain")
    time_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    time_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str] = mapped_column(String(16), default="collected")  # collected | unavailable | failed
    detail: Mapped[str] = mapped_column(String(300), default="")
    artifact_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    size: Mapped[int] = mapped_column(Integer, default=0)
    truncated: Mapped[bool] = mapped_column(default=False)
    included: Mapped[bool] = mapped_column(default=True)
    redaction_status: Mapped[str] = mapped_column(String(16), default="clean")
    redaction_report: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class EngineeringHandoffRow(Entity):
    """A package built for an engineer from one submission, as the submission was at that version.

    The package is a restricted artifact; this row says what is in it and who asked for it. ``status`` is ``generated``."""

    __tablename__ = "engineering_handoffs"
    submission_id: Mapped[str] = _fk("submissions")
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_label: Mapped[str] = mapped_column(String(80), default="")
    submission_version: Mapped[int] = mapped_column(Integer, default=1)
    options: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    artifact_id: Mapped[str] = mapped_column(String(80))
    task_artifact_id: Mapped[str] = mapped_column(String(80))
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    external_sharing_blocked: Mapped[bool] = mapped_column(default=False)
    includes_unsanitized: Mapped[bool] = mapped_column(default=False)
