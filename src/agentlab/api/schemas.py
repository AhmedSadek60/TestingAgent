"""Request and response bodies of the REST API (spec section 41: every request and response is documented).

Bodies reuse AgentLab's own models where they exist (a target, a test plan, a finding, a scorecard), so the OpenAPI
document describes exactly what the engine works with. Requests reject unknown fields: a misspelt option is an error, not
silently ignored."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, get_args

from pydantic import Field

from agentlab.core.enums import ReviewDecision, Severity
from agentlab.core.models import AgentProfile, TargetSpec
from agentlab.core.models.base import Model
from agentlab.design.models import TestPlan
from agentlab.jobs.models import JobOptions, JobOverrides

NAME = r"^[A-Za-z0-9][A-Za-z0-9_.\- ]{0,198}$"
CREDENTIAL_NAME = r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$"


# ===================================================================================================== generic
class ErrorBody(Model):
    kind: str = Field(description="A stable machine-readable name: user_error, not_found, policy_blocked, ...")
    message: str = Field(description="What went wrong and what to do about it. Never contains a secret.")
    details: list[dict[str, Any]] | None = Field(default=None, description="Per-field problems of an invalid request")


class ErrorResponse(Model):
    error: ErrorBody


class Health(Model):
    status: Literal["ok"] = "ok"
    version: str = Field(description="AgentLab version")
    auth_required: bool = Field(description="Whether requests must carry the API token")
    queue: str = Field(description="Queue backend: inline or redis")
    running: int = Field(description="Runs in progress, whichever process works on them (from the database)")
    queued: int = Field(description="Jobs waiting for a worker")


# ================================================================================================== projects
class ProjectCreate(Model):
    name: str = Field(pattern=NAME, description="Unique project name", examples=["support-bot"])
    description: str = Field(default="", max_length=2000)
    objective: str = Field(default="", max_length=2000, description="What the owner wants to learn about the agents")


class ProjectOut(Model):
    id: str
    name: str
    description: str = ""
    objective: str = ""
    created_at: datetime


# =================================================================================================== targets
class TargetCreate(Model):
    project: str = Field(default="default", description="Project name or id; created if it does not exist")
    spec: TargetSpec = Field(description="The target definition (the same document `agentlab test --target` reads)")


class TargetOut(Model):
    id: str
    project_id: str
    name: str
    kind: str = Field(description="The first interface of the target: api, web, command, mcp, llm, mock or unknown")
    target_version: str | None = None
    spec: TargetSpec
    created_at: datetime
    updated_at: datetime


# ============================================================================================== credentials
CredentialKindName = Literal["bearer", "api_key", "basic", "headers", "cookies", "oauth_token", "browser_state", "env"]


class CredentialCreate(Model):
    """Store a test credential, encrypted. The secret values are accepted here and never returned by any endpoint."""

    name: str = Field(pattern=CREDENTIAL_NAME, description="The name targets refer to it by", examples=["test-user"])
    kind: CredentialKindName = "bearer"
    description: str = Field(default="", max_length=500)
    scopes: list[str] = Field(
        default_factory=list,
        description="Hosts (or URL prefixes) the credential may be sent to. Required unless `unscoped` is true",
    )
    unscoped: bool = Field(default=False, description="Allow any host the target definition names (not recommended)")
    header_name: str | None = Field(default=None, description="api_key only: the header (default X-API-Key)")
    expires_at: datetime | None = Field(default=None, description="After this moment the credential is refused")
    secrets: dict[str, str] = Field(
        default_factory=dict,
        description="Field name to value: token (bearer, oauth_token), key (api_key), username and password (basic), "
        "any header or cookie names (headers, cookies), state (browser_state). Write-only",
    )
    references: dict[str, str] = Field(
        default_factory=dict,
        description="Field name to env:NAME. The value is read from the server's environment when it is used; "
        "nothing is stored",
    )


class CredentialRotate(Model):
    secrets: dict[str, str] = Field(description="The new values, by field name. Write-only")


class CredentialOut(Model):
    name: str
    kind: str
    description: str = ""
    scopes: list[str] = Field(default_factory=list)
    header_name: str | None = None
    expires_at: datetime | None = None
    secret_version: int = 1
    fields: list[str] = Field(default_factory=list, description="Names of the stored fields, never their values")
    references: dict[str, str] = Field(default_factory=dict)
    test_only: bool = True


# =============================================================================================== documents
class DocumentSummary(Model):
    pages: int = 0
    text_chars: int = 0
    knowledge_items: int = 0
    headings: int = 0
    tables: int = 0
    warnings: list[str] = Field(default_factory=list)
    injection_indicators: list[str] = Field(
        default_factory=list, description="Instruction-like text found in the document (treated as data, never obeyed)"
    )
    hidden_content: int = Field(default=0, description="Pieces of text a reader would not see")
    unsupported: str | None = Field(default=None, description="Why the file could not be read, if it could not")


class DocumentOut(Model):
    ref: str = Field(
        description="`upload:<id>`: put this in a target's `documents` (or `repository.archive`, or a run's "
        "`user_test_files`) to use the file",
        examples=["upload:3f1c0e9a-0000-4000-8000-000000000000"],
    )
    id: str
    version: int
    new_version: bool = Field(description="False when the same content was already uploaded")
    project: str
    name: str
    media_type: str
    size: int
    sha256: str
    summary: DocumentSummary


# ============================================================================================== discovery
class DiscoverRequest(Model):
    project: str = "default"
    target_id: str | None = Field(default=None, description="A stored target; give this or `target`")
    target: TargetSpec | None = Field(default=None, description="A target definition; give this or `target_id`")
    probe: bool = Field(default=True, description="Send harmless probes to the target (false: analyse files only)")


class DiscoverResponse(Model):
    target_id: str
    profile: AgentProfile
    warnings: list[str] = Field(default_factory=list)


# ============================================================================================ plans & runs
class PlanRequest(Model):
    """Design a test plan without running it. Only harmless discovery probes are sent to the target."""

    project: str = "default"
    target_id: str | None = Field(default=None, description="A stored target; give this or `target`")
    target: TargetSpec | None = Field(default=None, description="A target definition; give this or `target_id`")
    options: JobOptions = Field(default_factory=JobOptions)
    overrides: JobOverrides = Field(default_factory=JobOverrides)
    wait_seconds: int = Field(
        default=0, ge=0, le=120, description="Wait up to this long for the plan and answer 200 with it (0: answer 202)"
    )


class RunRequest(Model):
    """Start a run. Either design a plan and run it, or run the plan you approved (`plan_id`)."""

    project: str = "default"
    target_id: str | None = Field(default=None, description="A stored target; give this or `target`")
    target: TargetSpec | None = Field(default=None, description="A target definition; give this or `target_id`")
    plan_id: str | None = Field(
        default=None,
        description="Id of a plan (from POST /test-plans) to execute exactly as approved. The target of the plan "
        "is used when neither `target_id` nor `target` is given",
    )
    deselect: list[str] = Field(
        default_factory=list, description="Test ids to leave out of the approved plan (they stay listed, deselected)"
    )
    options: JobOptions = Field(default_factory=JobOptions)
    overrides: JobOverrides = Field(default_factory=JobOverrides)


class RunAccepted(Model):
    run_id: str
    kind: Literal["plan", "run"]
    status: str = Field(description="pending while it waits for a worker")
    queue: str
    links: dict[str, str] = Field(description="Where to follow it: run, events, stream, results, ...")


class CancelRequest(Model):
    reason: str = Field(default="cancelled from the API", max_length=300)


class CancelResponse(Model):
    run_id: str
    status: str = Field(description="cancelled (it had not started) or cancelling (it stops at the next safe point)")
    message: str


class RunProgress(Model):
    tests_total: int = 0
    tests_done: int = 0
    passed: int = 0
    failed: int = 0
    blocked: int = 0
    errors: int = 0
    skipped: int = 0
    stopped: int = 0
    findings: int = 0
    security_alerts: int = 0
    phase: str | None = Field(default=None, description="The orchestrator phase in progress")
    phases_done: int = 0
    running_tests: list[str] = Field(default_factory=list, description="Ids of the tests being executed")
    tool_calls: int = 0
    browser_actions: int = 0
    llm_calls: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    latency_ms_avg: float | None = None


class RunSummary(Model):
    id: str
    project_id: str
    project: str | None = None
    target_id: str | None = None
    target: str | None = Field(default=None, description="Name of the target")
    kind: Literal["plan", "run"] = "run"
    suite: str = Field(description="discovery, functional, security, browser, full or regression")
    status: str = Field(
        description="pending, running, completed, failed, cancelled, stopped_due_to_cost, "
        "stopped_due_to_timeout or stopped_due_to_step_limit"
    )
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: dict[str, Any] | None = None
    overall: float | None = Field(default=None, description="Overall score 0-100, when the run was scored")
    grade: str | None = None
    totals: dict[str, Any] = Field(default_factory=dict)


class RunDetail(RunSummary):
    progress: RunProgress
    limits: dict[str, Any] = Field(default_factory=dict)
    manifest: dict[str, Any] = Field(default_factory=dict, description="What is needed to reproduce the run")
    suite_id: str | None = None
    links: dict[str, str] = Field(default_factory=dict)


class PlanOut(Model):
    run_id: str
    status: str
    plan: TestPlan | None = Field(default=None, description="Null until the plan has been designed")
    profile: AgentProfile | None = None
    warnings: list[str] = Field(default_factory=list)
    error: dict[str, Any] | None = None


class EventOut(Model):
    event_id: str
    run_id: str
    test_id: str | None = None
    timestamp: datetime
    type: str
    payload: dict[str, Any] = Field(default_factory=dict, description="Redacted when the event was created")
    redaction_status: str = "not_scanned"


class TraceSummary(Model):
    id: str
    run_id: str
    test_id: str = Field(description="Id of the test the trace belongs to")
    attempt: int
    event_count: int
    artifact_id: str | None = None
    event_types: list[str] = Field(default_factory=list)


class TraceOut(Model):
    id: str
    run_id: str
    test_id: str
    attempt: int
    timestamp: datetime
    events: list[EventOut]


class ArtifactOut(Model):
    id: str
    sha256: str
    kind: str
    media_type: str
    size: int
    sensitivity: Literal["normal", "restricted"] = "normal"
    name: str | None = None
    run_id: str | None = None
    test_id: str | None = None
    url: str


# ============================================================================================ reviews
class ReviewRequest(Model):
    """A human decision about a test result or a finding. The original evaluation is never changed: the review is kept
    beside it, with who decided and why."""

    subject: Literal["result", "finding"]
    subject_id: str = Field(description="The id of the test (or result) or of the finding")
    decision: ReviewDecision
    reviewer: str = Field(min_length=1, max_length=120, description="Who is deciding")
    reason: str = Field(default="", max_length=2000, description="Required for every decision except approve")
    comment: str = Field(default="", max_length=2000)
    score: float | None = Field(default=None, ge=0, le=1, description="override_score only")
    severity: Severity | None = Field(default=None, description="change_severity and false_negative")


class ReviewOut(Model):
    id: str
    run_id: str
    subject_type: str
    subject_id: str
    subject_label: str | None = Field(default=None, description="The test id a person recognises")
    decision: str
    reviewer: str
    reason: str = ""
    comment: str = ""
    original: dict[str, Any] = Field(default_factory=dict)
    reviewed: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


# ============================================================================================== reports
class ReportFileOut(Model):
    format: Literal["json", "md", "html", "pdf"]
    artifact_id: str
    media_type: str
    size: int
    url: str = Field(description="Download this file")


class ReportOut(Model):
    id: str
    run_id: str
    report_version: int
    created_at: datetime
    formats: list[ReportFileOut]
    bundle_id: str | None = Field(default=None, description="Checksum of the bundle (`agentlab report --verify`)")
    generated_at: str | None = None
    baseline_run_id: str | None = None
    redactions: dict[str, int] = Field(default_factory=dict, description="Secret-like values masked, by kind")
    warnings: list[str] = Field(default_factory=list)


ReportFormatName = Literal["json", "md", "html", "pdf"]


class ReportCreate(Model):
    formats: list[Literal["json", "md", "html", "pdf"]] = Field(
        default_factory=lambda: list(get_args(ReportFormatName))
    )
    include_sensitive: bool = Field(
        default=False, description="Embed restricted evidence (screenshots taken signed in)"
    )
    baseline_run_id: str | None = Field(default=None, description="Compare with this run in the report")


class ExportRequest(Model):
    format: Literal["json", "md", "html", "pdf"]
    include_sensitive: bool = False


class ViewLink(Model):
    url: str = Field(description="Path on this server that opens the report; it needs no token")
    expires_in: int = Field(description="Seconds the link stays valid")


class ExportResponse(Model):
    report: ReportOut
    file: ReportFileOut
    created_new_version: bool = Field(
        description="True when the format did not exist in this report, so a new report version was generated"
    )


# ========================================================================================== catalogue
class ProviderOut(Model):
    name: str
    type: str
    base_url: str | None = None
    model: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    key: str = Field(description="Whether the key reference resolves: set, MISSING (...), not needed. Never the value")
    judge: bool = Field(description="Whether it is configured as an LLM judge")
    configured_error: str | None = Field(default=None, description="Why the adapter cannot be built, if it cannot")


class ProviderCheckRequest(Model):
    model: str | None = None
    complete: bool = Field(default=True, description="Also send one tiny completion (may be billed)")


class ProviderCheck(Model):
    provider: str
    ok: bool
    key: str
    models: int | None = None
    discovery_ms: int | None = None
    discovery_error: str | None = None
    completion: str | None = None
    model: str | None = None
    latency_ms: int | None = None
    tokens: int | None = None
    completion_error: str | None = None
    error: str | None = None


class ModelOut(Model):
    provider: str
    model: str | None
    context: int | None = None
    capabilities: list[str] = Field(default_factory=list)
    error: str | None = None


class SkillSummary(Model):
    name: str
    version: str
    title: str
    description: str
    trust: str
    status: str
    kind: str
    taxonomy: list[str]
    risk_class: str
    category: str
    content_hash: str
    problems: list[str] = Field(default_factory=list)


class SkillDetail(SkillSummary):
    manifest: dict[str, Any]
    doc: str = Field(description="SKILL.md: when the skill applies, how it tests, what it cannot do")


class ScoringProfileOut(Model):
    name: str
    description: str = ""
    weights: dict[str, float] = Field(default_factory=dict)


class EnvironmentCheck(Model):
    level: Literal["ok", "info", "warn", "fail"]
    name: str
    detail: str
    fix: str = ""


class EnvironmentOut(Model):
    ok: bool
    version: str
    checks: list[EnvironmentCheck]


class SettingsOut(Model):
    """The server's configuration as the interface may show it: nothing secret, and nothing the API can change."""

    version: str
    config: dict[str, Any]
    startup_warnings: list[str] = Field(default_factory=list)
