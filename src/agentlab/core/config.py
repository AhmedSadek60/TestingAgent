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
from pydantic import Field, field_validator

from agentlab.core.errors import UserError
from agentlab.core.models.base import Model


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
    provider: Literal["docker", "disabled"] = "docker"
    image: str = "mirror.gcr.io/library/python:3.12-slim"
    cpus: float = 1.0
    memory_mb: int = 1024
    pids_limit: int = 256
    disk_mb: int = 512
    timeout_seconds: float = 300.0
    user: str = "65534:65534"


class SecurityConfig(Model):
    sandbox_required: bool = True
    allow_production_targets: bool = False
    block_metadata_endpoints: bool = True
    allow_private_networks: bool = True
    custom_secret_patterns: list[str] = Field(default_factory=list)
    canary_prefix: str = "AGENTLAB_CANARY"
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)


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
    artifacts_dir: str = ".agentlab/artifacts"
    secrets_file: str = ".agentlab/secrets.enc"
    reports_dir: str = ".agentlab/reports"
    work_dir: str = ".agentlab/work"
    uploads_dir: str = ".agentlab/uploads"
    skill_drafts_dir: str = ".agentlab/skills/drafts"


ReportFormat = Literal["json", "md", "html", "pdf"]
_ALL_FORMATS: tuple[ReportFormat, ...] = ("json", "md", "html", "pdf")


class ReportingConfig(Model):
    """What ``agentlab test`` writes at the end of a run. ``agentlab report`` can render any format later."""

    formats: list[ReportFormat] = Field(
        default_factory=lambda: list(_ALL_FORMATS),
        description="Report formats written after every run (the folder is storage.reports_dir)",
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
    allowed_paths: list[str] = Field(
        default_factory=list,
        description="Folders on the server a request may point a target at (a repository path, a document). "
        "Uploads are always allowed. Empty means API clients cannot name server paths at all",
    )
    serve_ui: bool = Field(default=True, description="Serve the web interface at / when it has been built")


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
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
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
