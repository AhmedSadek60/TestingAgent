"""Optional model-suggested tests (``evaluation.llm_test_generation``; spec section 7).

A model may propose extra *safe* functional scenarios for behaviour the deterministic skills do not cover. The rules
that keep this from becoming a way into the evaluator:

* it is off by default and runs only with a configured evaluator provider - never the target itself;
* everything it is told about the target (description, tools, documents) is wrapped as untrusted data;
* its answer is schema-validated and then filtered: plain conversational input, bounded sizes, no URLs or
  instruction-like text, SAFE risk only, severity at most medium, checks limited to must-contain / must-not-contain
  plus one judged criterion;
* the tests are labelled *model-suggested (unverified)* with the provider and model, and sit in the plan like any
  other test, so a human sees and can deselect them before anything runs.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from agentlab.core.enums import RiskClass, Severity
from agentlab.core.models import AssertionSpec, JudgeCriterion, TestCase
from agentlab.design.models import TestPlan
from agentlab.security.untrusted import EVALUATOR_POLICY, injection_indicators, wrap_untrusted
from agentlab.skills.context import Draft, IdAllocator, SkillContext

log = logging.getLogger(__name__)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tests": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "objective": {"type": "string"},
                    "input": {"type": "string"},
                    "expected_behavior": {"type": "string"},
                    "must_contain": {"type": "array", "items": {"type": "string"}},
                    "must_not_contain": {"type": "array", "items": {"type": "string"}},
                    "severity": {"type": "string", "enum": ["low", "medium"]},
                },
                "required": ["name", "objective", "input", "expected_behavior"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["tests"],
    "additionalProperties": False,
}
URL = re.compile(r"https?://|www\.", re.I)
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f​-‏‪-‮⁠-⁤﻿]")
MAX_INPUT = 500


@dataclass
class Suggestions:
    drafts: list[Draft] = field(default_factory=list)
    label: str | None = None  # "provider:model"
    rejected: list[str] = field(default_factory=list)  # why a suggestion was dropped (shown in the plan)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", text.upper()).strip("-")[:24].strip("-") or "IDEA"


def _clean(text: object, limit: int) -> str | None:
    s = str(text).strip()
    if not s or len(s) > limit or CONTROL.search(s) or URL.search(s) or injection_indicators(s):
        return None
    return s


def _build(raw: dict[str, Any], ids: IdAllocator, label: str, ctx: SkillContext) -> tuple[Draft | None, str | None]:
    name = _clean(raw.get("name"), 100)
    objective = _clean(raw.get("objective"), 240)
    text = _clean(raw.get("input"), MAX_INPUT)
    expected = _clean(raw.get("expected_behavior"), 300)
    if not (name and objective and text and expected):
        return (
            None,
            f"'{str(raw.get('name'))[:40]}': a field was empty, too long or contained a link or instruction-like text",
        )
    asserts = [AssertionSpec(type="not_empty"), AssertionSpec(type="no_error")]
    for key, kind in (("must_contain", "contains"), ("must_not_contain", "not_contains")):
        for item in list(raw.get(key) or [])[:4]:
            clean = _clean(item, 80)
            if clean:
                asserts.append(AssertionSpec(type=kind, params={"text": clean, "case_sensitive": False}))
    test = TestCase(
        id=ids.alloc("LLM", _slug(name)),
        name=name,
        category="functional",
        objective=objective,
        rationale=f"Model-suggested (unverified) by {label}: {objective}",
        skill="llm-suggested",
        skill_version="1",
        risk_level=RiskClass.SAFE,
        severity_on_failure=Severity.LOW if raw.get("severity") == "low" else Severity.MEDIUM,
        input=text,
        expected_behavior=expected,
        assertions=asserts,
        judge=[JudgeCriterion(metric="task_completion", rubric=expected, threshold=0.6)],
        evaluation_metrics=["task_completion"],
        tags=["llm-suggested", "unverified"],
        score_category="functional_quality",
    )
    return Draft(
        test=test,
        reasons=[
            f"Suggested by {label} as a scenario the built-in skills do not cover. Unverified: review before relying on it."
        ],
        evidence=[f"target: {ctx.profile.target_name}"],
        taxonomy=["A"],
    ), None


async def suggest_tests(
    providers: Any,
    ctx: SkillContext,
    plan: TestPlan,
    *,
    provider: str | None = None,
    model: str | None = None,
    max_tests: int = 6,
) -> Suggestions:
    """Ask the evaluator model for extra safe scenarios. Never raises: failures become ``rejected`` notes."""
    from agentlab.providers import CompletionRequest, Message

    out = Suggestions()
    if providers is None or not providers.names():
        out.rejected.append("no evaluator provider is configured")
        return out
    judges = ctx.config.evaluation.judges
    name = provider or (judges[0].provider if judges else providers.names()[0])
    model = model or (judges[0].model if judges and judges[0].provider == name else None)
    gaps = [f"{e.key} {e.name}" for e in plan.coverage if e.status in {"not_covered", "partial"}]
    profile = ctx.profile
    facts = "\n".join(f"- {f.subject}: {f.statement}" for f in ctx.facts[:8]) if ctx.documents else ""
    context = (
        f"Target name: {profile.target_name}\n"
        f"Detected types: {', '.join(t.type.value for t in profile.types[:5]) or 'unknown'}\n"
        f"Tools: {', '.join(t.name for t in ctx.tools[:12]) or 'none'}\n"
        f"Areas with no or partial coverage: {', '.join(gaps) or 'none'}\n"
        f"Owner requirements: {'; '.join(ctx.user_requirements[:5]) or 'none'}"
    )
    user = (
        wrap_untrusted("repository", f"{profile.summary}\n{context}\n{facts}", max_chars=4000)
        + f"\nSuggest up to {max_tests} additional, harmless functional test scenarios (plain user messages) for behaviour "
        "the existing tests miss. Each needs a measurable expectation. Do not suggest attacks, secrets, links or code."
    )
    try:
        resp = await providers.get(name).complete(
            CompletionRequest(
                messages=[Message(role="system", content=EVALUATOR_POLICY), Message(role="user", content=user)],
                model=model,
                json_schema=SCHEMA,
                schema_name="test_suggestions",
                max_tokens=1200,
                temperature=0.2,
            )
        )
    except Exception as exc:
        out.rejected.append(f"the provider call failed ({type(exc).__name__})")
        return out
    data = resp.parsed
    if not isinstance(data, dict) or not isinstance(data.get("tests"), list):
        out.rejected.append("the answer did not match the requested schema")
        return out
    out.label = f"{name}:{resp.model}"
    ids = IdAllocator()
    ids.used |= {p.id for p in plan.tests}
    for raw in data["tests"][:max_tests]:
        if not isinstance(raw, dict):
            out.rejected.append("a suggestion was not an object")
            continue
        draft, why = _build(raw, ids, out.label, ctx)
        if draft:
            out.drafts.append(draft)
        elif why:
            out.rejected.append(why)
    return out
