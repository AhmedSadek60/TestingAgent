"""AgentLab configuration (``agentlab.yaml``, spec section 40).

Configuration never contains raw secrets: provider keys are *references* such as
``env:GEMINI_API_KEY`` or ``secret:gemini``, resolved by the CredentialManager at
call time.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, field_validator, model_validator

from agentlab.core.errors import UserError
from agentlab.core.models.base import Model
from agentlab.security import safeyaml


class Pricing(Model):
    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0


class ProviderConfig(Model):
    name: str
    type: str = Field(
        description="Adapter type: mock, openai, openai_compatible, openrouter, gemini, "
        "anthropic, ollama, lmstudio, vllm, llamacpp"
    )
    base_url: str | None = None
    api_key_ref: str | None = Field(default=None, description="env:NAME or secret:alias, never a raw key")
    model: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: float = 60.0
    max_retries: int = 2
    capabilities: list[str] | None = Field(
        default=None, description="Explicit capability override; otherwise negotiated by the adapter"
    )
    pricing: dict[str, Pricing] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)


class JudgeConfig(Model):
    provider: str
    model: str | None = None
    weight: float = 1.0


class EvaluationConfig(Model):
    judges: list[JudgeConfig] = Field(default_factory=list)
    judge_strategy: Literal["single", "average", "vote", "min"] = "average"
    judge_enabled: bool = True
    repetitions: int = 1
    repetitions_by_risk: dict[str, int] = Field(
        default_factory=dict, description="Override repetitions per risk class, e.g. {high_impact: 5}"
    )
    reliability_repetitions: int = 3
    pass_threshold: float = Field(default=1.0, description="Fraction of repetitions that must pass")
    timeout_seconds: float = Field(
        default=300.0,
        gt=0,
        description="The longest any single test may take. A test's own timeout (60 seconds unless its skill says "
        "otherwise) is lowered to this, never raised",
    )
    scoring_profile: str | None = None
    llm_test_generation: bool = False
    latency_budget_ms: float = Field(default=8000.0, description="Latency a single reply should stay under")
    max_tests_per_skill: int = Field(default=40, description="Upper bound on tests one skill may add to a plan")


class SandboxConfig(Model):
    provider: str = Field(
        default="docker",
        description="Sandbox plug-in that runs untrusted code: docker, disabled (nothing runs: every test that needs a "
        "sandbox is blocked) or the name of one a plug-in registers (docs/plugins.md)",
    )
    image: str = "mirror.gcr.io/library/python:3.12-slim"
    cpus: float = 1.0
    memory_mb: int = 1024
    pids_limit: int = 256
    disk_mb: int = 512
    timeout_seconds: float = 300.0
    user: str = "65534:65534"


class SecurityConfig(Model):
    sandbox_required: bool = Field(
        default=True,
        description="Repositories, coding agents and MCP servers started by a command run only inside a sandbox. This "
        "cannot be turned off, so false is refused: when Docker is unavailable the tests that need it are blocked, "
        "never run on this machine",
    )
    allow_production_targets: bool = False
    block_metadata_endpoints: bool = True
    allow_private_networks: bool = True
    custom_secret_patterns: list[str] = Field(default_factory=list)
    canary_prefix: str = "AGENTLAB_CANARY"
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)

    @field_validator("sandbox_required")
    @classmethod
    def _never_off(cls, value: bool) -> bool:
        if not value:
            raise ValueError(
                "security.sandbox_required cannot be false: AgentLab never runs untrusted code outside a sandbox. "
                "Without Docker, the tests that need it are blocked"
            )
        return value


class BrowserConfig(Model):
    enabled: bool = True
    browsers: list[str] = Field(
        default_factory=lambda: ["chromium"],
        description="Browsers to drive. Only chromium is supported: Firefox and WebKit have not been verified, so they are "
        "refused here instead of being accepted and ignored",
    )
    headless: bool = True
    executable_path: str | None = None
    record_video: bool = False
    record_trace: bool = True
    default_timeout_ms: int = 15_000

    @field_validator("browsers")
    @classmethod
    def _chromium_only(cls, value: list[str]) -> list[str]:
        unsupported = sorted({b for b in value if b != "chromium"})
        if unsupported:
            raise ValueError(
                f"{', '.join(unsupported)} is not supported in this build: AgentLab drives Chromium only "
                "(Firefox and WebKit have not been verified)"
            )
        return value


class LimitsConfig(Model):
    max_cost_usd: float = 10.0
    max_test_cost_usd: float = 1.0
    max_tokens: int = 500_000
    max_steps: int = 100
    max_browser_actions: int = 200
    max_execution_time_seconds: float = 3600.0
    max_retries: int = Field(
        default=2,
        ge=0,
        description="The most a call is repeated after a transient failure: a model call that was rate limited or "
        "unavailable, or a request to the agent whose connection could not be made (so it never arrived). A request "
        "that may have arrived is never repeated. 0 switches retries off, and it caps providers[].max_retries",
    )


class StorageConfig(Model):
    """Where AgentLab keeps what it writes. A relative path is relative to the folder of the configuration file (to the
    working folder when there is no file), so a configuration that lives somewhere read-only, such as ``/etc``, has to
    point every one of these somewhere writable."""

    database_url: str = "sqlite:///.agentlab/agentlab.db"
    artifact_store: str = Field(
        default="local",
        description="Artifact store plug-in: local (files under artifacts_dir), memory (lost when the process ends), or "
        "the name of one a plug-in registers (docs/plugins.md)",
    )
    artifact_store_options: dict[str, Any] = Field(
        default_factory=dict,
        description="Settings handed to a plug-in artifact store (none for local); never put a secret here",
    )
    artifacts_dir: str = ".agentlab/artifacts"
    secrets_file: str = ".agentlab/secrets.enc"
    reports_dir: str = ".agentlab/reports"
    work_dir: str = ".agentlab/work"
    uploads_dir: str = ".agentlab/uploads"
    skill_drafts_dir: str = ".agentlab/skills/drafts"


_ALL_FORMATS: tuple[str, ...] = ("json", "md", "html", "pdf")


class ReportingConfig(Model):
    """What ``agentlab test`` writes at the end of a run. ``agentlab report`` can render any format later."""

    formats: list[str] = Field(
        default_factory=lambda: list(_ALL_FORMATS),
        description="Report formats written after every run (the folder is storage.reports_dir): json, md, html, pdf, "
        "all, or the name of a format a plug-in adds. A name no installed plug-in provides is refused at start-up",
    )
    include_sensitive_artifacts: bool = Field(
        default=False,
        description="Embed restricted evidence (screenshots taken while signed in) in the report files",
    )


class PlanningConfig(Model):
    """Planning defaults. The numbers are *planning estimates* used to size a plan before anything runs, not measurements."""

    max_tests: int = Field(
        default=400, description="Upper bound on tests in one plan; extra tests are trimmed with coverage kept"
    )
    seconds_per_call: float = Field(default=3.0, description="Assumed wall time of one target call")
    tokens_per_call: int = Field(default=800, description="Assumed prompt+completion tokens of one target call")
    judge_tokens_per_call: int = Field(default=1000, description="Assumed tokens of one judge call")
    adaptive_max_tests: int = Field(default=60, description="Upper bound on tests the adaptive second wave may add")
    adaptive_repetitions: int = Field(default=5, description="Repetitions for the re-check of a flaky test")
    adaptive_variants_per_failure: int = Field(default=3, description="Rephrased variants added per failed test")


class QueueConfig(Model):
    """Where runs wait for a worker (spec section 47). ``inline`` runs them inside the API process; ``redis`` hands them to
    ``agentlab worker`` processes, so the API stays responsive and runs survive an API restart."""

    backend: Literal["inline", "redis"] = Field(
        default="inline", description="inline (this process) or redis (workers)"
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis server of the redis backend (put a password in AGENTLAB_REDIS_URL, not in this file)",
    )
    key_prefix: str = Field(
        default="agentlab",
        pattern=r"^[A-Za-z0-9_.:-]{1,64}$",
        description="Prefix of the Redis keys, so several installations can share one Redis server",
    )
    max_concurrent_runs: int = Field(default=2, ge=1, le=64, description="Runs one process works on at the same time")
    worker_timeout_seconds: int = Field(
        default=45,
        ge=5,
        le=3600,
        description="How long a worker may say nothing before the runs it held are considered abandoned and closed as failed",
    )
    job_ttl_seconds: int = Field(
        default=86_400, ge=60, description="How long a queued job may wait for a worker before it is given up"
    )


class ServerConfig(Model):
    """``agentlab serve``: the REST API and the web interface (spec sections 27 and 41)."""

    host: str = Field(default="127.0.0.1", description="Address to listen on; anything but loopback needs a token")
    port: int = Field(default=8080, ge=1, le=65535)
    token_ref: str | None = Field(
        default=None,
        description="env:NAME or secret:NAME holding the API token every request must present. Required unless the "
        "server listens on loopback only",
    )
    cors_origins: list[str] = Field(
        default_factory=list,
        description="Origins that may call the API from a browser page of their own. The bundled interface is served "
        "from the API itself and needs none",
    )
    max_upload_mb: int = Field(default=25, ge=1, le=512, description="Largest document or archive an upload may carry")
    max_body_mb: int = Field(
        default=4,
        ge=1,
        le=256,
        description="Largest request body the server reads, an upload excepted. A larger one is refused with 413 before "
        "it is parsed, so one oversized request cannot exhaust the server's memory",
    )
    allowed_paths: list[str] = Field(
        default_factory=list,
        description="Folders on the server a request may point a target at (a repository path, a document). "
        "Uploads are always allowed. Empty means API clients cannot name server paths at all",
    )
    serve_ui: bool = Field(default=True, description="Serve the web interface at / when it has been built")


class AccountsConfig(Model):
    """People who sign in to the web interface: who may register, how long a session lasts, how passwords are held to account."""

    registration: Literal["open", "approval", "closed"] = Field(
        default="open",
        description="Who may create an account: `open` (anyone who reaches the server, as a reporter), `approval` (anyone may "
        "ask; an admin activates the account) or `closed` (an admin creates every account)",
    )
    default_role: Literal["reporter", "maintainer"] = Field(
        default="reporter",
        description="The role of an account made by registration. A reporter may use Feedback & Support only; a maintainer "
        "has everything the shared API token has. Keep `reporter` on a server that anyone can reach",
    )
    session_idle_minutes: int = Field(
        default=60, ge=5, le=10_080, description="A session ends after this long without a request"
    )
    session_absolute_hours: int = Field(
        default=12, ge=1, le=720, description="A session ends this long after sign-in, whatever it does"
    )
    min_password_length: int = Field(default=12, ge=8, le=128, description="The shortest password accepted")
    max_failed_attempts: int = Field(
        default=5, ge=3, le=20, description="Wrong passwords in a row before an account is locked"
    )
    lock_minutes: int = Field(default=15, ge=1, le=1_440, description="How long a locked account stays locked")
    registrations_per_hour: int = Field(
        default=5, ge=1, le=1_000, description="Registrations accepted per client address per hour"
    )
    cookie_secure: Literal["auto", "always", "never"] = Field(
        default="auto",
        description="Whether the session cookie is marked Secure: `auto` when the request came over HTTPS (or a proxy said "
        "so with X-Forwarded-Proto), `always`, or `never` (a plain-HTTP deployment on a trusted network)",
    )


def _all_notifications() -> list[Literal["comment", "information_request", "status_change"]]:
    return ["comment", "information_request", "status_change"]


#: The file types an attachment may be. An owner can only switch these on or off: each has a signature check behind it.
ATTACHMENT_TYPES = (
    "png",
    "jpg",
    "jpeg",
    "webp",
    "pdf",
    "docx",
    "txt",
    "md",
    "csv",
    "json",
    "jsonl",
    "yaml",
    "yml",
    "log",
    "zip",
)


def _default_attachment_types() -> list[str]:
    return list(ATTACHMENT_TYPES)


class AttachmentsConfig(Model):
    """What may be attached to a submission, and how it is checked."""

    allowed_extensions: list[str] = Field(
        default_factory=_default_attachment_types,
        description="The file types that may be attached, by extension. Each is checked against its file signature or its "
        "text; a type outside this list cannot be enabled",
    )
    max_files: int = Field(default=10, ge=1, le=100, description="Attachments one submission may have")
    max_file_mb: int = Field(default=25, ge=1, le=512, description="The largest single attachment")
    max_total_mb: int = Field(
        default=100, ge=1, le=4096, description="The most attachments of one submission may add up to"
    )
    max_uploads_per_day: int = Field(
        default=50, ge=1, le=10_000, description="Attachments one person with the role reporter may add in 24 hours"
    )
    zip_max_entries: int = Field(default=200, ge=1, le=10_000, description="The most entries an archive may list")
    zip_max_expanded_mb: int = Field(
        default=200,
        ge=1,
        le=8192,
        description="The most an archive may expand to, by what it declares (it is never extracted)",
    )
    zip_max_ratio: int = Field(
        default=100,
        ge=2,
        le=10_000,
        description="The largest ratio of expanded size to stored size, for the archive or an entry",
    )
    sanitize_max_mb: int = Field(
        default=10,
        ge=1,
        le=256,
        description="The largest text attachment that is scanned for secrets and given a sanitized copy",
    )
    scanner_command: list[str] = Field(
        default_factory=list,
        description="A command that scans a file for malware: its arguments, with {path} where the file goes (the path is "
        "appended when there is none). Exit 0 means clean and 1 means infected, as `clamscan` does. Empty: no scanner, and "
        "attachments are recorded as not scanned",
    )
    scanner_timeout_seconds: int = Field(
        default=60, ge=1, le=3600, description="How long the scanner may take for one file"
    )
    scanner_required: bool = Field(
        default=False,
        description="Refuse an attachment when the scanner is configured but fails or times out (otherwise it is kept and "
        "recorded as not scanned)",
    )

    @model_validator(mode="after")
    def _known_types(self) -> AttachmentsConfig:
        unknown = sorted({e.lower().lstrip(".") for e in self.allowed_extensions} - set(ATTACHMENT_TYPES))
        if unknown:
            raise ValueError(
                f"feedback.attachments.allowed_extensions names types AgentLab cannot check: {', '.join(unknown)} "
                f"(it can check: {', '.join(ATTACHMENT_TYPES)})"
            )
        self.allowed_extensions = sorted({e.lower().lstrip(".") for e in self.allowed_extensions})
        if self.max_file_mb > self.max_total_mb:
            raise ValueError("feedback.attachments.max_file_mb cannot be more than max_total_mb")
        return self


class RedactionRule(Model):
    """A pattern of the owner's own that diagnostic text is masked by."""

    name: str = Field(
        pattern=r"^[A-Za-z0-9_\-]{1,40}$", description="What the masked value is called in a report: custom:<name>"
    )
    pattern: str = Field(min_length=1, max_length=500, description="A regular expression; every match is masked")

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str) -> str:
        import re

        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"not a valid regular expression: {exc}") from exc
        return value


class RedactionConfig(Model):
    """How attached and collected text is masked before it is shown, kept or shared (the DiagnosticRedactor)."""

    rules: list[RedactionRule] = Field(
        default_factory=list, max_length=50, description="Patterns of your own to mask, beyond the built-in ones"
    )
    redact_ip_addresses: bool = Field(
        default=False,
        description="Mask IPv4 addresses. They help to diagnose a network problem, so they are kept unless you turn this on",
    )


class DiagnosticsConfig(Model):
    """What a person may attach to an issue report from AgentLab itself, and the log AgentLab keeps for it."""

    capture_logs: bool = Field(
        default=True,
        description="Keep a bounded, redacted copy of what the server logs (warnings, errors and the requests that failed or "
        "were slow), correlated with runs and requests, so it can be attached to an issue report",
    )
    log_level: Literal["WARNING", "INFO"] = Field(
        default="WARNING",
        description="The least important thing that is kept. INFO also raises the level of AgentLab's own logger, so it "
        "shows on the server's standard error too",
    )
    log_retention_hours: int = Field(default=48, ge=1, le=24 * 30, description="How long captured log lines are kept")
    log_max_rows: int = Field(default=50_000, ge=1_000, le=5_000_000, description="The most captured log lines kept")
    max_window_minutes: int = Field(
        default=360, ge=1, le=24 * 60, description="The longest custom time window a collection may ask for"
    )
    max_item_kb: int = Field(
        default=512, ge=16, le=16_384, description="The most one collected item may hold; more is cut"
    )
    max_browser_entries: int = Field(
        default=200,
        ge=1,
        le=2_000,
        description="The most console and network entries a browser may report in one collection",
    )
    draft_hours: int = Field(
        default=24, ge=1, le=24 * 14, description="How long a collection that was not attached to a report is kept"
    )
    max_collections_per_hour: int = Field(
        default=30, ge=1, le=1_000, description="Collections one person may make in an hour"
    )


class HandoffConfig(Model):
    """The engineering handoff: a sanitized package that an authorised engineer can give to Claude Code."""

    max_package_mb: int = Field(
        default=50,
        ge=1,
        le=1024,
        description="The largest package that may be built; attachments that do not fit are refused",
    )
    allow_internal_notes: bool = Field(
        default=True,
        description="Whether a maintainer may choose to put internal notes in a package. They are never put in by default",
    )


class FeedbackConfig(Model):
    """Feedback & Support: feature requests and issue reports, who sees what, and the lists a form offers."""

    feature_categories: list[str] = Field(
        default_factory=lambda: [
            "User Interface / UX",
            "AI Agent Testing",
            "Test Case Generation",
            "Evaluation Metrics",
            "Security Testing",
            "RAG Evaluation",
            "Browser Automation",
            "Provider Integration",
            "Local LLM Support",
            "Reporting and Analytics",
            "Performance",
            "Workflow Automation",
            "API",
            "CLI",
            "Deployment",
            "Developer Experience",
            "Other",
        ],
        description="The categories a feature request can have",
    )
    issue_categories: list[str] = Field(
        default_factory=lambda: [
            "Application Bug",
            "UI / Display Problem",
            "Test Execution Failure",
            "Incorrect Evaluation Result",
            "Test Case Generation Problem",
            "Browser Automation Problem",
            "Provider / LLM Integration Problem",
            "API Error",
            "Repository Ingestion Problem",
            "Document Processing Problem",
            "Incorrect Score or Report",
            "Performance / Slow Response",
            "Crash or Application Unavailable",
            "Installation / Configuration",
            "Authentication / Permissions",
            "Data Persistence Problem",
            "Unexpected Cost or Resource Usage",
            "Suspected Security Issue",
            "Other",
        ],
        description="The categories an issue report can have",
    )
    feature_statuses: list[str] = Field(
        default_factory=lambda: [
            "Submitted",
            "Needs Triage",
            "Under Review",
            "Planned",
            "In Progress",
            "Completed",
            "Declined",
            "Duplicate",
        ],
        description="The statuses a feature request moves through; the first is the one it starts with",
    )
    issue_statuses: list[str] = Field(
        default_factory=lambda: [
            "Submitted",
            "Needs Triage",
            "Investigating",
            "Awaiting User",
            "Reproducing",
            "In Progress",
            "Fix Available",
            "Resolved",
            "Closed",
            "Duplicate",
            "Cannot Reproduce",
        ],
        description="The statuses an issue report moves through; the first is the one it starts with",
    )
    resolved_statuses: list[str] = Field(
        default_factory=lambda: ["Completed", "Fix Available", "Resolved"],
        description="Statuses that set the resolution time of a submission",
    )
    closed_statuses: list[str] = Field(
        default_factory=lambda: ["Completed", "Declined", "Duplicate", "Closed", "Cannot Reproduce"],
        description="Statuses that set the closing time of a submission",
    )
    security_category: str = Field(
        default="Suspected Security Issue",
        description="An issue with this category is security-sensitive: only its reporter and authorised maintainers see it",
    )
    service_token_sees_security: bool = Field(
        default=False,
        description="Whether the shared API token (a credential many people may hold) may see security-sensitive "
        "submissions made by other people. Maintainers and admins, and the local machine, always may",
    )
    max_submissions_per_day: int = Field(
        default=20, ge=1, le=10_000, description="Submissions one person with the role reporter may make in 24 hours"
    )
    max_comments_per_day: int = Field(
        default=100, ge=1, le=10_000, description="Comments one person with the role reporter may write in 24 hours"
    )
    similar_limit: int = Field(
        default=5, ge=1, le=20, description="How many possibly similar submissions are suggested"
    )
    attachments: AttachmentsConfig = Field(default_factory=AttachmentsConfig)
    redaction: RedactionConfig = Field(default_factory=RedactionConfig)
    diagnostics: DiagnosticsConfig = Field(default_factory=DiagnosticsConfig)
    handoff: HandoffConfig = Field(default_factory=HandoffConfig)
    notify_reporter_on: list[Literal["comment", "information_request", "status_change"]] = Field(
        default_factory=_all_notifications,
        description="What makes a submission unread for its reporter: a maintainer's public comment, a request for "
        "information or a reproduction attempt, or a status change. Remove one to stop that notification",
    )

    @model_validator(mode="after")
    def _the_lists_agree(self) -> FeedbackConfig:
        for name in ("feature_categories", "issue_categories", "feature_statuses", "issue_statuses"):
            values = getattr(self, name)
            if not values or len(set(values)) != len(values) or any(not v.strip() or len(v) > 80 for v in values):
                raise ValueError(
                    f"feedback.{name} must be a list of different, non-empty names of at most 80 characters"
                )
        known = set(self.feature_statuses) | set(self.issue_statuses)
        for name in ("resolved_statuses", "closed_statuses"):
            unknown = sorted(set(getattr(self, name)) - known)
            if unknown:
                raise ValueError(f"feedback.{name} names statuses no workflow has: {', '.join(unknown)}")
        if self.security_category not in self.issue_categories:
            raise ValueError("feedback.security_category must be one of feedback.issue_categories")
        return self


#: A reply read from a web page includes the page's own rendering and, for an assistant, a model's time to write it. A
#: latency budget written for an API (a few seconds) would score every web chat as slow, so an agent reached only through
#: a browser gets at least this much unless the owner sets one (evaluation.latency_budget_ms).
WEB_LATENCY_BUDGET_MS = 30_000.0


class AgentLabConfig(Model):
    providers: list[ProviderConfig] = Field(
        default_factory=lambda: [ProviderConfig(name="mock", type="mock", model="mock-judge")]
    )
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    reporting: ReportingConfig = Field(default_factory=ReportingConfig)
    queue: QueueConfig = Field(default_factory=QueueConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    accounts: AccountsConfig = Field(default_factory=AccountsConfig)
    feedback: FeedbackConfig = Field(default_factory=FeedbackConfig)
    planning: PlanningConfig = Field(default_factory=PlanningConfig)
    max_parallel: int = 4
    skill_dirs: list[str] = Field(default_factory=list)
    plugins: list[str] = Field(default_factory=list, description="Python modules imported at start-up")

    def provider(self, name: str) -> ProviderConfig:
        for p in self.providers:
            if p.name == name:
                return p
        raise UserError(f"provider '{name}' is not configured (known: {[p.name for p in self.providers]})")

    @classmethod
    def load(cls, path: str | Path | None = None) -> AgentLabConfig:
        """The configuration from ``path`` (else ``$AGENTLAB_CONFIG``, else ``./agentlab.yaml``, else the defaults).

        ``AGENTLAB_DATABASE_URL`` replaces ``storage.database_url``, so that a database password never has to be written
        into a file (``queue.redis_url`` has ``AGENTLAB_REDIS_URL`` for the same reason)."""
        config = cls._read(path)
        url = os.environ.get("AGENTLAB_DATABASE_URL")
        if url:
            config = config.model_copy(update={"storage": config.storage.model_copy(update={"database_url": url})})
        return config

    @classmethod
    def _read(cls, path: str | Path | None) -> AgentLabConfig:
        candidate = path or os.environ.get("AGENTLAB_CONFIG")
        if candidate is None and Path("agentlab.yaml").exists():
            candidate = "agentlab.yaml"
        if candidate is None:
            return cls()
        p = Path(candidate)
        if not p.exists():
            raise UserError(f"config file not found: {p}")
        try:
            data = safeyaml.load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise UserError(f"invalid YAML in {p}: {exc}") from exc
        if isinstance(data.get("providers"), list):
            data["providers"] = [{"name": x, "type": x} if isinstance(x, str) else x for x in data["providers"]]
        try:
            return cls.model_validate(data)
        except Exception as exc:
            raise UserError(f"invalid configuration in {p}: {exc}") from exc

    def dump_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False)
