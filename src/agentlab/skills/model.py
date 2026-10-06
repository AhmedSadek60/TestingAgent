"""Skill manifest (spec section 6).

A skill is a reusable, independently versioned unit of testing knowledge: when it applies, what it
needs, how it tests, how results are judged, how severe a failure is and which evidence is kept.
On disk a skill is a directory with ``skill.yaml`` (machine readable) and ``SKILL.md`` (the
methodology in prose). Tests are produced either by declarative templates in the manifest (safe for
third-party skills) or by a *trusted* Python generator (built-in skills and installed plug-ins only).
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, field_validator

from agentlab.core.enums import RiskClass, Severity, normalize_score_category
from agentlab.core.models.base import Model

SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$")
NAME = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")

TRUST_LEVELS = ("builtin", "plugin", "local", "imported", "generated")
# "imported" and "generated" skills are *drafts*: they are never selected until a human promotes them.
UNTRUSTED = {"imported", "generated"}


class Applicability(Model):
    """When a skill is relevant. A skill applies when *any* positive rule matches and no negative rule does."""

    always: bool = False
    types: list[str] = Field(default_factory=list, description="AgentType values detected with enough confidence")
    capabilities: list[str] = Field(default_factory=list, description="capability-matrix entries detected")
    interfaces: list[str] = Field(default_factory=list, description="adapter kinds the target exposes")
    tool_patterns: list[str] = Field(default_factory=list, description="regexes matched against tool names")
    needs_documents: bool = False
    needs_repository: bool = False
    needs_tools: bool = False
    min_confidence: float = 0.4
    exclude_types: list[str] = Field(default_factory=list)
    expression: str | None = Field(
        default=None, description="Optional sandboxed boolean expression over profile/target/ctx"
    )


class Prerequisites(Model):
    """What must be available for the skill's tests to *run* (missing items make tests BLOCKED, not FAILED)."""

    interfaces_any: list[str] = Field(default_factory=list)
    credentials: bool = False
    docker: bool = False
    browser: bool = False
    judge: bool = False
    multimodal: bool = False
    notes: list[str] = Field(default_factory=list)


class SeverityGuidance(Model):
    """How serious a failure in this area is, with the reasoning a reviewer should expect."""

    default: Severity = Severity.MEDIUM
    guidance: list[str] = Field(default_factory=list)


class TestTemplate(Model):
    """Declarative test template: strings are rendered with ``[[ ... ]]`` placeholders (sandboxed Jinja).

    ``foreach`` names a collection from the skill context (``tools``, ``facts``, ``documents``,
    ``requirements``) and renders one test per element, bound to ``item``.
    """

    __test__ = False

    id: str = Field(description="Topic part of the test id, e.g. GROUNDED -> RAG-GROUNDED-001")
    when: str | None = Field(default=None, description="Sandboxed boolean expression; omitted = always")
    foreach: str | None = None
    limit: int = 6
    test: dict[str, Any] = Field(description="TestCase fields (name, objective, input/turns, assertions, ...)")
    reasons: list[str] = Field(default_factory=list, description="Why this test exists (rendered)")


class Provenance(Model):
    origin: str = "agentlab"
    license: str = "Apache-2.0"
    sources: list[str] = Field(default_factory=list, description="references the methodology was adapted from")
    author: str | None = None
    homepage: str | None = None


class SkillManifest(Model):
    name: str
    version: str
    title: str
    description: str
    kind: Literal["tests", "analysis", "meta"] = "tests"
    status: Literal["stable", "experimental", "draft", "deprecated"] = "stable"
    taxonomy: list[str] = Field(default_factory=list, description="spec taxonomy letters (A-Q) this skill covers")
    category: str = "functional"
    score_category: str = "functional_quality"
    id_prefix: str | None = None
    risk_class: RiskClass = RiskClass.SAFE
    applicability: Applicability = Field(default_factory=Applicability)
    prerequisites: Prerequisites = Field(default_factory=Prerequisites)
    methodology: str = ""
    test_generation: str = ""
    execution: str = ""
    evaluation_rules: list[str] = Field(default_factory=list)
    severity: SeverityGuidance = Field(default_factory=SeverityGuidance)
    evidence_requirements: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    generator: str | None = Field(
        default=None, description="'module:function' of a trusted generator (built-in or plug-in skills only)"
    )
    templates: list[TestTemplate] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)
    trust: str = "local"

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME.match(v):
            raise ValueError("skill names are lowercase words joined by '-'")
        return v

    @field_validator("version")
    @classmethod
    def _version(cls, v: str) -> str:
        if not SEMVER.match(v):
            raise ValueError("version must be semantic (MAJOR.MINOR.PATCH)")
        return v

    @field_validator("score_category")
    @classmethod
    def _score_category(cls, v: str) -> str:
        return normalize_score_category(v)

    @field_validator("trust")
    @classmethod
    def _trust(cls, v: str) -> str:
        if v not in TRUST_LEVELS:
            raise ValueError(f"trust must be one of {TRUST_LEVELS}")
        return v

    @property
    def prefix(self) -> str:
        if self.id_prefix:
            return self.id_prefix.upper()
        return "".join(w[0] for w in self.name.split("-")[:3]).upper() if "-" in self.name else self.name[:4].upper()


class Skill(Model):
    """A loaded skill: manifest + SKILL.md + where it came from."""

    manifest: SkillManifest
    doc: str = ""
    source: str = Field(default="", description="directory or entry point the skill was loaded from")
    content_hash: str = ""
    problems: list[str] = Field(default_factory=list, description="validation findings that make the skill unusable")

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def version(self) -> str:
        return self.manifest.version

    @property
    def usable(self) -> bool:
        return not self.problems and self.manifest.status != "deprecated" and self.manifest.trust not in UNTRUSTED

    @property
    def draft(self) -> bool:
        return self.manifest.trust in UNTRUSTED or self.manifest.status == "draft"


class SkillMatch(Model):
    """Why a skill was (not) selected for a target — shown to the user before execution."""

    skill: str
    version: str
    selected: bool
    score: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    skipped_reason: str | None = None
    kind: str = "tests"
    taxonomy: list[str] = Field(default_factory=list)
    tests: int = 0
    predicted_blocked: int = 0
    trust: str = "builtin"


REQUIRED_DOC_SECTIONS = (
    "Purpose",
    "Applicability",
    "Prerequisites",
    "Methodology",
    "Test generation",
    "Execution",
    "Evaluation rules",
    "Severity guidance",
    "Evidence requirements",
)
