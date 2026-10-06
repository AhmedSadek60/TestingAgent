"""Closed vocabularies used across AgentLab.

Every enum is a ``StrEnum`` so values serialise to stable, human-readable strings in
traces, reports and the database.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal


class AgentType(StrEnum):
    CHATBOT = "chatbot"
    CONVERSATIONAL = "conversational"
    RAG = "rag"
    TOOL_CALLING = "tool_calling"
    FUNCTION_CALLING = "function_calling"
    WORKFLOW = "workflow"
    REACT = "react"
    PLANNING = "planning"
    AUTONOMOUS = "autonomous"
    BROWSER = "browser"
    CODING = "coding"
    REPOSITORY = "repository"
    MULTI_AGENT = "multi_agent"
    SUPERVISOR = "supervisor"
    SUB_AGENTS = "sub_agents"
    MCP = "mcp"
    API = "api"
    MEMORY = "memory"
    LONG_RUNNING = "long_running"
    EVENT_DRIVEN = "event_driven"
    MULTIMODAL = "multimodal"
    VOICE = "voice"
    DOCUMENT = "document"
    RESEARCH = "research"
    DATA_ANALYSIS = "data_analysis"
    COMPUTER_USE = "computer_use"
    HYBRID = "hybrid"


class EvaluationMode(StrEnum):
    BLACK_BOX = "black_box"
    WHITE_BOX = "white_box"
    HYBRID = "hybrid"


class SuiteKind(StrEnum):
    DISCOVERY = "discovery"
    FUNCTIONAL = "functional"
    SECURITY = "security"
    BROWSER = "browser"
    RELIABILITY = "reliability"
    FULL = "full"
    REGRESSION = "regression"


# The same two vocabularies as types. A request that names anything else is refused where it enters, and the web client's
# generated types list exactly these, so a screen cannot offer a choice the server does not know.
Suite = Literal["discovery", "functional", "security", "browser", "reliability", "full", "regression"]
Intensity = Literal["quick", "standard", "thorough"]


class TestStatus(StrEnum):
    __test__ = False  # stop pytest from collecting this as a test class

    DRAFT = "draft"
    READY = "ready"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    ERROR = "error"
    TIMEOUT = "timeout"
    STOPPED_DUE_TO_COST = "stopped_due_to_cost"
    STOPPED_DUE_TO_TIMEOUT = "stopped_due_to_timeout"
    STOPPED_DUE_TO_STEP_LIMIT = "stopped_due_to_step_limit"

    @property
    def is_terminal(self) -> bool:
        return self not in {TestStatus.DRAFT, TestStatus.READY, TestStatus.RUNNING}

    @property
    def is_stopped(self) -> bool:
        return self.value.startswith("stopped_due_to")


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STOPPED_DUE_TO_COST = "stopped_due_to_cost"
    STOPPED_DUE_TO_TIMEOUT = "stopped_due_to_timeout"
    STOPPED_DUE_TO_STEP_LIMIT = "stopped_due_to_step_limit"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        return {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}[self.value]


class RiskClass(StrEnum):
    """Authorisation gate class (spec section 35)."""

    SAFE = "safe"
    CONTROLLED = "controlled"
    HIGH_IMPACT = "high_impact"


class ErrorKind(StrEnum):
    USER_ERROR = "USER_ERROR"
    TARGET_ERROR = "TARGET_ERROR"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    EVALUATOR_ERROR = "EVALUATOR_ERROR"
    INFRASTRUCTURE_ERROR = "INFRASTRUCTURE_ERROR"
    TIMEOUT = "TIMEOUT"
    CREDENTIAL_ERROR = "CREDENTIAL_ERROR"
    SANDBOX_ERROR = "SANDBOX_ERROR"
    BROWSER_ERROR = "BROWSER_ERROR"
    PARSER_ERROR = "PARSER_ERROR"
    RATE_LIMIT = "RATE_LIMIT"
    COST_LIMIT = "COST_LIMIT"
    POLICY_BLOCK = "POLICY_BLOCK"


class RootCause(StrEnum):
    PROMPT = "prompt_problem"
    MODEL_LIMITATION = "model_limitation"
    TOOL_SELECTION = "tool_selection_problem"
    TOOL_IMPLEMENTATION = "tool_implementation_problem"
    RETRIEVAL = "retrieval_problem"
    DATA = "data_problem"
    MEMORY = "memory_problem"
    AUTHORIZATION = "authorization_problem"
    BROWSER_INTERACTION = "browser_interaction_problem"
    UI = "ui_problem"
    API = "api_problem"
    ORCHESTRATION = "orchestration_problem"
    EVALUATOR_UNCERTAINTY = "evaluator_uncertainty"
    INFRASTRUCTURE = "infrastructure_problem"
    TIMEOUT = "timeout"
    EXTERNAL_DEPENDENCY = "external_dependency"
    SECURITY_VULNERABILITY = "security_vulnerability"
    UNKNOWN = "unknown"


class RedactionStatus(StrEnum):
    CLEAN = "clean"
    REDACTED = "redacted"
    NOT_SCANNED = "not_scanned"


class ScoreCategory(StrEnum):
    FUNCTIONAL = "functional_quality"
    INSTRUCTION_ADHERENCE = "instruction_adherence"
    CONVERSATIONAL = "conversational"
    RAG = "rag_quality"
    TOOL_USE = "tool_use"
    MEMORY = "memory"
    PLANNING = "planning"
    BROWSER = "browser_execution"
    CODING = "coding"
    MULTI_AGENT = "multi_agent"
    MCP = "mcp"
    DOCUMENT = "document"
    RELIABILITY = "reliability"
    PERFORMANCE = "performance"
    COST = "cost_efficiency"
    SECURITY = "security"
    SAFETY = "safety"


SCORE_CATEGORY_ALIASES: dict[str, str] = {
    "functional": "functional_quality",
    "conversation": "conversational",
    "rag": "rag_quality",
    "tool_calling": "tool_use",
    "tools": "tool_use",
    "browser": "browser_execution",
    "cost": "cost_efficiency",
    "multimodal": "document",
    "autonomy": "planning",
}


def normalize_score_category(value: str) -> str:
    """The canonical :class:`ScoreCategory` value for a name (an alias is accepted); anything else is an error, because
    a test scored under a category no profile weighs would silently drop out of the overall score."""
    key = value.strip().lower().replace("-", "_").replace(" ", "_")
    key = SCORE_CATEGORY_ALIASES.get(key, key)
    known = {c.value for c in ScoreCategory}
    if key not in known:
        raise ValueError(f"unknown score category '{value}'; use one of {sorted(known)}")
    return key


class EventType(StrEnum):
    """Provider-neutral internal event schema (spec sections 12 and 48)."""

    RUN_STARTED = "RunStarted"
    PHASE_STARTED = "PhaseStarted"
    PHASE_COMPLETED = "PhaseCompleted"
    DISCOVERY_STARTED = "DiscoveryStarted"
    DISCOVERY_COMPLETED = "DiscoveryCompleted"
    SKILL_SELECTED = "SkillSelected"
    TEST_PLAN_GENERATED = "TestPlanGenerated"
    TEST_STARTED = "TestStarted"
    AGENT_REQUEST = "AgentRequest"
    AGENT_RESPONSE = "AgentResponse"
    LLM_CALLED = "LLMCalled"
    TOOL_CALLED = "ToolCalled"
    TOOL_RETURNED = "ToolReturned"
    RETRIEVAL = "Retrieval"
    HANDOFF = "Handoff"
    PLAN_STEP = "PlanStep"
    BROWSER_ACTION = "BrowserAction"
    SANDBOX_COMMAND = "SandboxCommand"
    ASSERTION_EVALUATED = "AssertionEvaluated"
    JUDGE_EVALUATED = "JudgeEvaluated"
    FINDING_CREATED = "FindingCreated"
    TEST_COMPLETED = "TestCompleted"
    LIMIT_REACHED = "LimitReached"
    SECURITY_ALERT = "SecurityAlert"
    ERROR = "Error"
    RUN_COMPLETED = "RunCompleted"
    RUN_FAILED = "RunFailed"
    RUN_CANCELLED = "RunCancelled"


class Phase(StrEnum):
    """The seventeen orchestrator phases (spec section 9)."""

    INPUT_VALIDATION = "input_validation"
    TARGET_INGESTION = "target_ingestion"
    TARGET_FINGERPRINTING = "target_fingerprinting"
    ENVIRONMENT_PREPARATION = "environment_preparation"
    SKILL_SELECTION = "skill_selection"
    TEST_PLAN_GENERATION = "test_plan_generation"
    RISK_CLASSIFICATION = "risk_classification"
    TEST_EXECUTION = "test_execution"
    TRACE_COLLECTION = "trace_collection"
    DETERMINISTIC_EVALUATION = "deterministic_evaluation"
    LLM_JUDGE_EVALUATION = "llm_judge_evaluation"
    CROSS_TEST_ANALYSIS = "cross_test_analysis"
    SECURITY_ANALYSIS = "security_analysis"
    RELIABILITY_ANALYSIS = "reliability_analysis"
    SCORING = "scoring"
    REPORT_GENERATION = "report_generation"
    ARTIFACT_PACKAGING = "artifact_packaging"


class ReviewDecision(StrEnum):
    APPROVE = "approve"
    FALSE_POSITIVE = "false_positive"
    FALSE_NEGATIVE = "false_negative"
    OVERRIDE_SCORE = "override_score"
    CHANGE_SEVERITY = "change_severity"
    COMMENT = "comment"


class Support(StrEnum):
    """Honest capability status used for adapters, providers and features."""

    SUPPORTED = "supported"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
