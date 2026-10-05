"""Inputs and helpers handed to skills when they generate tests.

``SkillContext`` is a read-only view of everything the TestDesignerAgent knows: the discovery profile,
repository analysis, documents, available interfaces and environment. ``SkillRun`` is the per-skill
workbench a generator uses to create tests; it fills defaults, assigns stable ids and records *why*
every test exists so the plan can explain itself before anything runs.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from agentlab.adapters.base import AdapterCapabilities
from agentlab.core.config import AgentLabConfig
from agentlab.core.enums import AgentType, RiskClass, Severity
from agentlab.core.models import (
    AgentProfile,
    AssertionSpec,
    ExpectedToolCall,
    JudgeCriterion,
    TargetSpec,
    TestCase,
    TestResult,
    ToolInfo,
    Turn,
)
from agentlab.documents.analyzer import extract_facts, extract_requirements, find_conflicts
from agentlab.documents.models import AnalyzedDocument, Fact, Requirement
from agentlab.repository.models import RepositoryAnalysis
from agentlab.skills.model import SkillManifest

INTENSITIES = ("quick", "standard", "thorough")

DESTRUCTIVE_NAME = re.compile(
    r"delete|remove|drop|destroy|erase|wipe|purge|truncate|kill|terminate|cancel|transfer|"
    r"refund|pay|charge|withdraw|deploy|publish|merge|push|exec|shell|run_command|rm_",
    re.I,
)
OUTBOUND_NAME = re.compile(
    r"send|email|mail|sms|message|post|tweet|notify|upload|webhook|publish|share|slack|http_|fetch|"
    r"request",
    re.I,
)
READ_NAME = re.compile(r"read|get|list|search|find|lookup|look_up|query|fetch|retrieve|view|show|describe", re.I)
FILE_NAME = re.compile(r"file|path|fs_|directory|folder|read_text|open_", re.I)
URL_NAME = re.compile(r"url|fetch|http|browse|download|web_|scrape|crawl", re.I)
SQL_NAME = re.compile(r"sql|query_db|database|db_", re.I)
SHELL_NAME = re.compile(r"shell|exec|command|bash|run_code|python|eval|subprocess", re.I)


@dataclass
class SkillContext:
    profile: AgentProfile
    target: TargetSpec
    config: AgentLabConfig
    repo: RepositoryAnalysis | None = None
    documents: list[AnalyzedDocument] = field(default_factory=list)
    interfaces: list[str] = field(default_factory=list)
    adapter_capabilities: dict[str, AdapterCapabilities] = field(default_factory=dict)
    judge_available: bool = False
    docker_available: bool = False
    browser_available: bool = False
    credential_names: list[str] = field(default_factory=list)
    intensity: str = "standard"
    fixtures_dir: Path | None = None  # writable directory for generated fixture files (attachments)
    doc_paths: dict[str, Path] = field(default_factory=dict)  # document name -> file the user supplied
    previous: list[TestResult] = field(default_factory=list)
    seed: int = 0
    user_requirements: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ derived knowledge
    @cached_property
    def tools(self) -> list[ToolInfo]:
        return list(self.profile.tools)

    @cached_property
    def facts(self) -> list[Fact]:
        out: list[Fact] = []
        for d in self.documents:
            out += extract_facts(d, limit=12)
        return out

    @cached_property
    def requirements(self) -> list[Requirement]:
        out: list[Requirement] = []
        for d in self.documents:
            out += extract_requirements(d, limit=12)
        return out

    @cached_property
    def conflicts(self) -> list[tuple[Fact, Fact]]:
        return find_conflicts(self.documents)

    @cached_property
    def documents_with_injection(self) -> list[AnalyzedDocument]:
        return [d for d in self.documents if d.injection_indicators or d.hidden_content]

    def has_type(self, t: AgentType | str, threshold: float | None = None) -> bool:
        thr = threshold if threshold is not None else 0.5
        try:
            return self.profile.has_type(AgentType(t), thr)
        except ValueError:
            return False

    def type_confidence(self, t: str) -> float:
        try:
            return self.profile.type_confidence(AgentType(t))
        except ValueError:
            return 0.0

    def capability(self, name: str) -> bool:
        return any(c.capability == name and c.detected for c in self.profile.capability_matrix)

    def testable(self, name: str) -> str:
        for c in self.profile.capability_matrix:
            if c.capability == name:
                return c.testable.value
        return "unknown"

    def tools_matching(self, pattern: re.Pattern[str] | str) -> list[ToolInfo]:
        rx = re.compile(pattern, re.I) if isinstance(pattern, str) else pattern
        return [t for t in self.tools if rx.search(t.name) or rx.search(t.description or "")]

    @cached_property
    def destructive_tools(self) -> list[ToolInfo]:
        return [t for t in self.tools if t.side_effects == "destructive" or DESTRUCTIVE_NAME.search(t.name)]

    @cached_property
    def outbound_tools(self) -> list[ToolInfo]:
        return [t for t in self.tools if t.side_effects == "external" or OUTBOUND_NAME.search(t.name)]

    @cached_property
    def side_effect_tools(self) -> list[ToolInfo]:
        return [
            t
            for t in self.tools
            if t.side_effects in {"write", "external", "destructive"}
            or DESTRUCTIVE_NAME.search(t.name)
            or OUTBOUND_NAME.search(t.name)
        ]

    @cached_property
    def read_tools(self) -> list[ToolInfo]:
        return [t for t in self.tools if t.side_effects in {"read", "none"} and t not in self.side_effect_tools]

    def tool_evidence(self, tool: ToolInfo) -> str:
        src = f" (declared in {tool.source})" if tool.source and tool.source != "unknown" else ""
        return f"tool '{tool.name}' [{tool.side_effects}]{src}"

    def adapter_supports(self, feature: str, interface: str | None = None) -> bool:
        caps = self.adapter_capabilities
        if interface:
            return bool(getattr(caps.get(interface), feature, False))
        return any(bool(getattr(c, feature, False)) for c in caps.values())

    @property
    def has_conversation_interface(self) -> bool:
        return bool(set(self.interfaces) & {"mock", "llm", "api", "command", "mcp", "web"})

    @property
    def authenticated(self) -> bool:
        return bool(self.credential_names)

    def pick(self, quick: int, standard: int, thorough: int | None = None) -> int:
        """How many items of a family to generate at the current intensity."""
        return {
            "quick": quick,
            "standard": standard,
            "thorough": thorough if thorough is not None else standard * 2,
        }.get(self.intensity, standard)

    @property
    def latency_budget_ms(self) -> float:
        return self.config.evaluation.latency_budget_ms

    @cached_property
    def observed_citations(self) -> bool:
        probe = self.profile.raw_signals.get("probe") or {}
        return any(o.get("citations") for o in probe.get("observations", []))

    @cached_property
    def observed_contexts(self) -> bool:
        probe = self.profile.raw_signals.get("probe") or {}
        return bool(probe.get("contexts_seen"))

    @property
    def reports_contexts(self) -> bool:
        return self.adapter_supports("reports_contexts") or self.observed_contexts

    @property
    def reports_tool_calls(self) -> bool:
        return self.adapter_supports("reports_tool_calls") or bool(
            (self.profile.raw_signals.get("probe") or {}).get("tools_seen")
        )

    def new_fixture(self, name: str, data: bytes) -> str | None:
        """Write a generated fixture (e.g. a corrupt PDF) where the conversation engine may attach it from."""
        if self.fixtures_dir is None:
            return None
        self.fixtures_dir.mkdir(parents=True, exist_ok=True)
        (self.fixtures_dir / name).write_bytes(data)
        return name

    def attach_document(self, name: str) -> str | None:
        """Make a user-supplied document attachable: copy it into the fixtures directory, return its file name."""
        src = self.doc_paths.get(name)
        if src is None or self.fixtures_dir is None or not src.is_file():
            return None
        self.fixtures_dir.mkdir(parents=True, exist_ok=True)
        target = self.fixtures_dir / src.name
        if not target.exists():
            target.write_bytes(src.read_bytes())
        return src.name

    def risky_fraction(self) -> float:
        if not self.tools:
            return 0.0
        return len(self.side_effect_tools) / len(self.tools)


class IdAllocator:
    def __init__(self) -> None:
        self._counts: dict[tuple[str, str], int] = {}
        self.used: set[str] = set()

    def alloc(self, prefix: str, topic: str) -> str:
        key = (prefix, topic)
        while True:
            self._counts[key] = self._counts.get(key, 0) + 1
            tid = f"{prefix}-{topic}-{self._counts[key]:03d}"
            if tid not in self.used:
                self.used.add(tid)
                return tid


@dataclass
class Draft:
    """A test plus the reasoning that produced it (the plan shows this before execution)."""

    test: TestCase
    reasons: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    taxonomy: list[str] = field(default_factory=list)
    requires: dict[str, Any] = field(default_factory=dict)  # capabilities the interface must provide


def _slug_topic(topic: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", topic.upper()).strip("-")[:24].strip("-") or "GENERAL"


class SkillRun:
    """Workbench for one skill's generation pass."""

    def __init__(self, manifest: SkillManifest, ctx: SkillContext, ids: IdAllocator, wave: int = 1) -> None:
        self.manifest = manifest
        self.ctx = ctx
        self.ids = ids
        self.wave = wave
        self.drafts: list[Draft] = []
        self.notes: list[str] = []

    # ------------------------------------------------------------------ small helpers
    def n(self, quick: int, standard: int, thorough: int | None = None) -> int:
        return self.ctx.pick(quick, standard, thorough)

    def note(self, text: str, area: str | None = None) -> None:
        """Record why something was *not* generated. ``area`` ("N11", "E", ...) lets the plan show the reason next to
        the coverage entry it explains; untagged notes are general remarks about the skill."""
        line = f"[{area}] {text}" if area else text
        if line not in self.notes:
            self.notes.append(line)

    # ------------------------------------------------------------------ creation
    def add(
        self,
        topic: str,
        name: str,
        objective: str,
        *,
        input: str | None = None,
        turns: Sequence[Turn | dict[str, Any]] | None = None,
        assertions: Sequence[AssertionSpec | dict[str, Any]] | None = None,
        judge: Sequence[JudgeCriterion | dict[str, Any]] | None = None,
        expected_behavior: str = "",
        expected_output: str | None = None,
        expected_tool_calls: Sequence[ExpectedToolCall | dict[str, Any]] | None = None,
        forbidden: list[str] | None = None,
        severity: Severity = Severity.MEDIUM,
        risk: RiskClass | None = None,
        why: list[str] | None = None,
        evidence: list[str] | None = None,
        metrics: list[str] | None = None,
        tags: list[str] | None = None,
        subcategory: str = "general",
        interfaces: list[str] | None = None,
        credentials: list[str] | None = None,
        context: dict[str, Any] | None = None,
        repetitions: int | None = None,
        timeout: float | None = None,
        max_steps: int | None = None,
        max_cost: float | None = None,
        max_tokens: int | None = None,
        preconditions: list[str] | None = None,
        applicable: list[str] | None = None,
        isolation_key: str | None = None,
        score_category: str | None = None,
        category: str | None = None,
        cleanup: str = "none",
        evidence_requirements: list[str] | None = None,
        requires: dict[str, Any] | None = None,
        taxonomy: list[str] | None = None,
        browser_steps: list[dict[str, Any]] | None = None,
    ) -> Draft:
        cfg = self.ctx.config.limits
        m = self.manifest
        tid = self.ids.alloc(m.prefix, _slug_topic(topic))
        why = list(why or [])
        ev = list(evidence or [])
        rationale = " ".join(why)
        if ev:
            rationale += (" " if rationale else "") + "Evidence: " + "; ".join(ev) + "."
        a_specs = [a if isinstance(a, AssertionSpec) else AssertionSpec(**a) for a in (assertions or [])]
        t = TestCase(
            id=tid,
            name=name,
            category=category or m.category,
            subcategory=subcategory,
            objective=objective,
            rationale=rationale,
            skill=m.name,
            skill_version=m.version,
            risk_level=risk or m.risk_class,
            severity_on_failure=severity,
            preconditions=list(preconditions or []),
            required_credentials=list(credentials or []),
            required_interfaces=list(interfaces or []),
            input=input,
            turns=[x if isinstance(x, Turn) else Turn(**x) for x in (turns or [])],
            context=dict(context or {}),
            expected_behavior=expected_behavior,
            expected_output=expected_output,
            expected_tool_calls=[
                x if isinstance(x, ExpectedToolCall) else ExpectedToolCall(**x) for x in (expected_tool_calls or [])
            ],
            forbidden_behavior=list(forbidden or []),
            assertions=a_specs,
            judge=[j if isinstance(j, JudgeCriterion) else JudgeCriterion(**j) for j in (judge or [])],
            evaluation_metrics=list(metrics or m.metrics),
            browser_steps=[],  # filled below (typed)
            timeout=timeout if timeout is not None else 60.0,
            max_steps=max_steps if max_steps is not None else 20,
            max_cost=max_cost if max_cost is not None else min(0.5, cfg.max_test_cost_usd),
            max_tokens=max_tokens if max_tokens is not None else 20_000,
            repetitions=repetitions,
            cleanup_strategy=cleanup,
            evidence_requirements=list(evidence_requirements or m.evidence_requirements),
            applicable_agent_types=[AgentType(a) for a in (applicable or []) if a in AgentType._value2member_map_],
            isolation_key=isolation_key,
            tags=list(dict.fromkeys([m.name, *(tags or [])])),
            score_category=score_category or m.score_category,
        )
        if browser_steps:
            from agentlab.core.models import BrowserStep

            t.browser_steps = [BrowserStep(**s) for s in browser_steps]
        judge_only = (
            t.judge and not t.assertions and not any(x.assertions for x in t.turns) and not t.expected_tool_calls
        )
        if judge_only and "judge" not in t.preconditions:
            t.preconditions.append("judge")  # every criterion needs a judge: BLOCKED (never FAILED) without one
        needs = dict(requires or {})
        caps = list(t.context.get("requires_capabilities") or [])
        if caps:
            needs.setdefault("capabilities", caps)
        d = Draft(test=t, reasons=why, evidence=ev, taxonomy=list(taxonomy or m.taxonomy), requires=needs)
        self.drafts.append(d)
        return d


# ---------------------------------------------------------------------------------- assertion shorthands
def A(
    type_: str,
    *,
    severity: Severity | None = None,
    description: str | None = None,
    required: bool = True,
    weight: float = 1.0,
    metric: str | None = None,
    turn: int | None = None,
    **params: Any,
) -> AssertionSpec:
    return AssertionSpec(
        type=type_,
        params=params,
        severity=severity,
        description=description,
        required=required,
        weight=weight,
        metric=metric,
        turn=turn,
    )


def STATUS(codes: list[int], *, optional: bool = False, description: str | None = None) -> AssertionSpec:
    """HTTP status must be one of ``codes``; with ``optional`` it is ignored on interfaces that have no status."""
    return AssertionSpec(type="status_code", params={"in": codes, "optional": optional}, description=description)


def J(metric: str, rubric: str, *, weight: float = 1.0, threshold: float = 0.6) -> JudgeCriterion:
    return JudgeCriterion(metric=metric, rubric=rubric, weight=weight, threshold=threshold)


def turn(
    text: str,
    *,
    session: str = "default",
    assertions: list[AssertionSpec] | None = None,
    attachments: list[str] | None = None,
) -> Turn:
    return Turn(input=text, session=session, assertions=assertions or [], attachments=attachments or [])
