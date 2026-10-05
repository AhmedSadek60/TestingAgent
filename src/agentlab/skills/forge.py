"""SkillForge: draft skills for capabilities no installed skill covers (spec section 6).

When discovery finds an agent type or capability that no built-in skill tests, AgentLab does not pretend it is
covered. The forge writes a *generated draft* skill (trust ``generated``): a manifest with a baseline smoke
template, a SKILL.md whose sections cite the evidence from discovery and say what a human still has to add, and
- optionally - model-suggested methodology notes that are schema-validated, labelled as unverified and quoted,
never executed. Drafts are never selected until a human promotes them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agentlab.core.errors import AgentLabError, UserError
from agentlab.core.models import AgentProfile
from agentlab.security.untrusted import EVALUATOR_POLICY, wrap_untrusted
from agentlab.skills.model import NAME, REQUIRED_DOC_SECTIONS
from agentlab.skills.registry import SkillRegistry

GENERIC_TYPES = {"hybrid", "repository", "chatbot", "conversational"}
SUGGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "methodology": {"type": "string"},
        "test_ideas": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["methodology", "test_ideas", "risks"],
    "additionalProperties": False,
}
FORGED_MARKER = "GENERATED-DRAFT"


@dataclass
class UncoveredCapability:
    capability: str
    confidence: float
    evidence: list[str] = field(default_factory=list)

    @property
    def skill_name(self) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", self.capability.lower()).strip("-")
        return base if base.endswith("-testing") else f"{base}-testing"


def uncovered_capabilities(
    profile: AgentProfile, registry: SkillRegistry, threshold: float = 0.5
) -> list[UncoveredCapability]:
    """Detected agent types (and detected capabilities) that no usable test skill lists in its applicability."""
    covered_types: set[str] = set()
    covered_caps: set[str] = set()
    for s in registry.all(include_drafts=False):
        if s.manifest.kind == "tests" and s.usable:
            covered_types.update(s.manifest.applicability.types)
            covered_caps.update(s.manifest.applicability.capabilities)
            if s.manifest.applicability.always:
                covered_types.update({"chatbot", "conversational"})
    out: list[UncoveredCapability] = []
    for t in profile.types:
        if t.confidence >= threshold and t.type.value not in covered_types and t.type.value not in GENERIC_TYPES:
            out.append(
                UncoveredCapability(t.type.value, t.confidence, [f"{e.source}: {e.detail}" for e in t.evidence[:4]])
            )
    seen = {u.capability for u in out}
    for c in profile.capability_matrix:
        if (
            c.detected
            and c.capability not in covered_caps
            and c.capability not in covered_types
            and c.capability not in seen
        ):
            if c.capability in {"conversation", "tool_calling", "rag", "memory"}:
                continue
            out.append(UncoveredCapability(c.capability, 0.5, [c.reason]))
    return out


def _baseline_template(cap: UncoveredCapability) -> dict[str, Any]:
    return {
        "id": "SMOKE",
        "test": {
            "name": f"Basic response while acting as a {cap.capability} agent",
            "objective": f"The target answers a plain request without errors; a baseline before {cap.capability}-specific checks exist",
            "input": f"Hello, please briefly explain what you can do with {cap.capability.replace('_', ' ')}.",
            "assertions": [
                {"type": "not_empty"},
                {"type": "no_error"},
                {
                    "type": "not_regex",
                    "params": {"pattern": "(Traceback \\(most recent call last\\)|Exception in thread)"},
                },
            ],
            "severity_on_failure": "low",
            "evaluation_metrics": ["task_completion", "robustness"],
        },
        "reasons": [
            f"Discovery detected '{cap.capability}' but no installed skill covers it; this is only a baseline."
        ],
    }


def _doc(cap: UncoveredCapability, suggestion: dict[str, Any] | None, provider_label: str | None) -> str:
    ev = "\n".join(f"- {e}" for e in cap.evidence) or "- (no evidence recorded)"
    todo = "TODO (human review): "
    parts = {
        "Purpose": f"{todo}state what a failure of a {cap.capability} agent costs its users.",
        "Applicability": f"Detected with confidence {cap.confidence:.2f}. Evidence from discovery:\n\n{ev}",
        "Prerequisites": f"{todo}list the interfaces, credentials and tools this skill needs.",
        "Methodology": f"{todo}describe how {cap.capability} behaviour should be tested; start from docs/references.md.",
        "Test generation": "Only a smoke template exists. "
        + todo
        + "add templates for the behaviours specific to this capability.",
        "Execution": f"{todo}describe isolation, risk class and cleanup.",
        "Evaluation rules": f"{todo}prefer deterministic oracles; add judge criteria only when none exist.",
        "Severity guidance": f"{todo}explain which failures are critical, high, medium or low.",
        "Evidence requirements": f"{todo}name the artefacts a reviewer needs to reproduce a finding.",
    }
    body = "\n".join(f"## {s}\n\n{parts[s]}\n" for s in REQUIRED_DOC_SECTIONS)
    extra = ""
    if suggestion:
        ideas = "\n".join(f"> - {i}" for i in suggestion.get("test_ideas", []))
        risks = "\n".join(f"> - {i}" for i in suggestion.get("risks", []))
        extra = (
            f"\n## Model-suggested notes (unverified, quoted)\n\nSuggested by `{provider_label}`. Treat as untrusted text.\n\n"
            f"> {suggestion.get('methodology', '')}\n>\n> Test ideas:\n{ideas}\n>\n> Risks:\n{risks}\n"
        )
    return (
        f"# {cap.skill_name.replace('-', ' ').title()}\n\n"
        f"**{FORGED_MARKER}** — created by SkillForge because no installed skill covers `{cap.capability}`. "
        "It is untrusted until a human completes and promotes it.\n\n" + body + extra
    )


async def suggest_methodology(
    providers: Any, cap: UncoveredCapability, profile: AgentProfile, judge: tuple[str, str | None] | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    """Optional LLM suggestions for a draft. Output is schema-validated, labelled and never executed."""
    from agentlab.providers import CompletionRequest, Message

    if providers is None:
        return None, None
    name = judge[0] if judge else (providers.names()[0] if providers.names() else None)
    if name is None:
        return None, None
    user = (
        f"Capability: {cap.capability}\nTarget: {profile.target_name}\n"
        + wrap_untrusted("repository", "\n".join(cap.evidence), max_chars=2000)
        + "\nSuggest, as JSON, how to test this capability: a short methodology, concrete test ideas with measurable oracles and the main risks."
    )
    try:
        resp = await providers.get(name).complete(
            CompletionRequest(
                messages=[Message(role="system", content=EVALUATOR_POLICY), Message(role="user", content=user)],
                model=judge[1] if judge else None,
                json_schema=SUGGEST_SCHEMA,
                schema_name="skill_suggestion",
                max_tokens=700,
                temperature=0.0,
            )
        )
    except (AgentLabError, Exception):
        return None, None
    d = resp.parsed
    if not isinstance(d, dict) or set(d) != {"methodology", "test_ideas", "risks"}:
        return None, None
    clean = {
        "methodology": str(d["methodology"])[:800],
        "test_ideas": [str(x)[:200] for x in d["test_ideas"]][:8],
        "risks": [str(x)[:200] for x in d["risks"]][:6],
    }
    return clean, f"{name}:{resp.model}"


def forge_skill(
    cap: UncoveredCapability,
    dest_root: str | Path,
    *,
    suggestion: dict[str, Any] | None = None,
    provider_label: str | None = None,
) -> Path:
    """Write a draft skill directory for ``cap`` and return it. Never overwrites an existing skill."""
    name = cap.skill_name
    if not NAME.match(name):
        raise UserError(f"cannot derive a valid skill name from '{cap.capability}'")
    dest = Path(dest_root) / name
    if dest.exists():
        raise UserError(f"{dest} already exists")
    dest.mkdir(parents=True)
    manifest = {
        "name": name,
        "version": "0.1.0",
        "title": f"{cap.capability.replace('_', ' ').title()} testing (draft)",
        "description": f"Generated draft for the detected capability '{cap.capability}'. Baseline only; needs human review.",
        "kind": "tests",
        "status": "draft",
        "category": cap.capability,
        "id_prefix": re.sub(r"[^A-Z]", "", cap.capability.upper())[:5] or "GEN",
        "applicability": {"types": [cap.capability], "min_confidence": 0.5},
        "templates": [_baseline_template(cap)],
        "limitations": [
            "Generated draft: covers a smoke check only. Behaviours specific to this capability are NOT tested."
        ],
        "provenance": {"origin": "agentlab-skillforge", "license": "Apache-2.0", "sources": []},
    }
    (dest / "skill.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")
    (dest / "SKILL.md").write_text(_doc(cap, suggestion, provider_label), encoding="utf-8")
    return dest


__all__ = ["FORGED_MARKER", "UncoveredCapability", "forge_skill", "suggest_methodology", "uncovered_capabilities"]
