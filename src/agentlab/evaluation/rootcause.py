"""Rule-based root-cause hypotheses with calibrated confidence (spec section 33).

The classifier looks only at observable evidence (which checks failed, error kinds, whether
contexts were retrieved, which tools ran). It never claims certainty: a hypothesis derived from
a *direct* signal (a timeout, an HTTP 5xx) is allowed higher confidence than one inferred from
the shape of a failure (e.g. "no context retrieved, so likely a retrieval problem"). Every
hypothesis lists the evidence it rests on and the alternatives that remain plausible.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentlab.core.enums import ErrorKind, RootCause, TestStatus
from agentlab.core.models import AssertionResult, AttemptResult, JudgeResult, TestCase

_TOOL_SELECTION = {
    "tool_called",
    "tool_sequence",
    "trajectory:tool_selection",
    "trajectory:tool_ordering",
    "tool_call_count",
    "no_tools_called",
    "tool_not_called",
}
_TOOL_ARGUMENTS = {"trajectory:tool_arguments"}
_AUTHZ = {"no_destructive_without_confirmation", "asks_confirmation", "tool_allowlist", "no_external_recipient"}
_RETRIEVAL = {"retrieved_source", "context_contains"}
_GROUNDING = {"grounded", "abstains", "citations_valid", "cites_source"}
_SECURITY = {"no_canary_leak", "no_secret_leak", "no_injection_followed", "no_injection_indicators_in_output"}
_ORCH = {
    "handoff_path",
    "max_handoffs",
    "no_handoff_cycle",
    "loop_free",
    "steps_max",
    "plan_contains",
    "no_duplicate_tool_calls",
    "trajectory:unnecessary_actions",
    "trajectory:efficiency",
}
_API = {"status_code", "json_schema", "jsonpath", "no_error"}
_BROWSER = {"state_equals", "state_contains"}
_MEMORY_HINTS = ("memory", "session", "isolation", "context retention", "recall")


@dataclass
class RootCauseAnalysis:
    cause: RootCause
    confidence: float
    rationale: str
    evidence: list[str] = field(default_factory=list)
    alternatives: list[tuple[RootCause, float]] = field(default_factory=list)

    def statement(self) -> str:
        hedge = (
            "most likely" if self.confidence >= 0.7 else "possibly" if self.confidence >= 0.4 else "unclear, perhaps"
        )
        return (
            f"Root cause ({hedge} {self.cause.value.replace('_', ' ')}, confidence {self.confidence:.2f}): "
            f"{self.rationale}"
        )


def classify_root_cause(
    test: TestCase, attempts: list[AttemptResult], status: TestStatus, error_kind: ErrorKind | None = None
) -> RootCauseAnalysis:
    if status.is_stopped:
        return RootCauseAnalysis(
            RootCause.TIMEOUT if status == TestStatus.STOPPED_DUE_TO_TIMEOUT else RootCause.ORCHESTRATION,
            0.9,
            f"execution was stopped by a configured limit ({status.value}); this is not a verdict on the agent",
        )
    if status == TestStatus.TIMEOUT or error_kind == ErrorKind.TIMEOUT:
        return RootCauseAnalysis(
            RootCause.TIMEOUT,
            0.8,
            "the target did not answer within the test timeout",
            alternatives=[(RootCause.INFRASTRUCTURE, 0.2)],
        )
    if error_kind in {ErrorKind.INFRASTRUCTURE_ERROR, ErrorKind.SANDBOX_ERROR, ErrorKind.BROWSER_ERROR}:
        return RootCauseAnalysis(RootCause.INFRASTRUCTURE, 0.75, f"{error_kind.value} raised while executing the test")
    if error_kind == ErrorKind.TARGET_ERROR:
        return RootCauseAnalysis(
            RootCause.API,
            0.6,
            "the target returned an error response or could not be reached",
            alternatives=[(RootCause.INFRASTRUCTURE, 0.3), (RootCause.EXTERNAL_DEPENDENCY, 0.2)],
        )
    if error_kind in {ErrorKind.RATE_LIMIT, ErrorKind.PROVIDER_ERROR}:
        return RootCauseAnalysis(
            RootCause.EXTERNAL_DEPENDENCY, 0.6, f"{error_kind.value}: an upstream dependency failed"
        )

    failed: list[AssertionResult] = []
    judges: list[JudgeResult] = []
    for a in attempts:
        failed += [x for x in a.assertions if not x.passed]
        judges += [j for j in a.judge if not j.passed]
    types = {f.type for f in failed}
    ev = [f"{f.type}: {f.message}" for f in failed[:4]]
    responses_with_contexts = any(a.trajectory.get("contexts_retrieved") for a in attempts)
    tool_errors = any(a.trajectory.get("tool_errors") for a in attempts)

    def hit(*groups: set[str]) -> bool:
        return bool(types & set().union(*groups))

    if types & _SECURITY:
        if "no_injection_followed" in types or "no_injection_indicators_in_output" in types:
            return RootCauseAnalysis(
                RootCause.SECURITY_VULNERABILITY,
                0.85,
                "embedded instructions in untrusted data influenced the agent",
                ev,
                [(RootCause.PROMPT, 0.4)],
            )
        return RootCauseAnalysis(
            RootCause.SECURITY_VULNERABILITY,
            0.9,
            "a protected value left its authorised channel",
            ev,
            [(RootCause.PROMPT, 0.3)],
        )
    if hit(_AUTHZ):
        return RootCauseAnalysis(
            RootCause.AUTHORIZATION,
            0.8,
            "the agent acted (or would act) without the required confirmation/authorisation",
            ev,
            [(RootCause.TOOL_IMPLEMENTATION, 0.3), (RootCause.PROMPT, 0.3)],
        )
    if tool_errors and (hit(_TOOL_ARGUMENTS) or "no_error" in types):
        return RootCauseAnalysis(
            RootCause.TOOL_IMPLEMENTATION,
            0.55,
            "a tool returned an error; arguments or the tool itself may be at fault",
            ev,
            [(RootCause.TOOL_SELECTION, 0.4)],
        )
    if hit(_TOOL_ARGUMENTS):
        return RootCauseAnalysis(
            RootCause.TOOL_SELECTION,
            0.65,
            "the right tool was chosen but with incorrect arguments",
            ev,
            [(RootCause.PROMPT, 0.3)],
        )
    if hit(_TOOL_SELECTION):
        return RootCauseAnalysis(
            RootCause.TOOL_SELECTION,
            0.7,
            "the agent selected the wrong tool, or no tool",
            ev,
            [(RootCause.PROMPT, 0.35), (RootCause.MODEL_LIMITATION, 0.25)],
        )
    if hit(_RETRIEVAL):
        return RootCauseAnalysis(
            RootCause.RETRIEVAL, 0.7, "expected source/context was not retrieved", ev, [(RootCause.DATA, 0.3)]
        )
    if hit(_GROUNDING):
        if not responses_with_contexts:
            return RootCauseAnalysis(
                RootCause.RETRIEVAL,
                0.5,
                "unsupported claims were made while no supporting context was retrieved",
                ev,
                [(RootCause.PROMPT, 0.4), (RootCause.MODEL_LIMITATION, 0.3)],
            )
        return RootCauseAnalysis(
            RootCause.PROMPT,
            0.45,
            "context was retrieved but the answer was not faithful to it",
            ev,
            [(RootCause.MODEL_LIMITATION, 0.4)],
        )
    if hit(_ORCH):
        return RootCauseAnalysis(
            RootCause.ORCHESTRATION,
            0.6,
            "control flow (delegation, planning, loops or step usage) deviated from expectations",
            ev,
        )
    if hit(_BROWSER) or test.category.lower() in {"browser", "ui"}:
        ui = any("selector" in str(f.evidence) or "visible" in f.message for f in failed)
        return RootCauseAnalysis(
            RootCause.UI if ui else RootCause.BROWSER_INTERACTION,
            0.5,
            "the observed page state or action sequence differed from the expected one",
            ev,
        )
    if hit(_API):
        return RootCauseAnalysis(RootCause.API, 0.55, "the response contract (status/schema/fields) was violated", ev)
    cat = f"{test.category} {test.subcategory}".lower()
    if any(h in cat for h in _MEMORY_HINTS) or "memory" in test.tags:
        return RootCauseAnalysis(
            RootCause.MEMORY, 0.55, "conversation state was lost, leaked or mixed between sessions", ev
        )
    if judges and not failed:
        low_conf = all(j.confidence < 0.5 or j.error for j in judges)
        if low_conf:
            return RootCauseAnalysis(
                RootCause.EVALUATOR_UNCERTAINTY,
                0.6,
                "only the LLM judge flagged this and it was uncertain or errored",
                [f"judge:{j.metric} score {j.score:.2f} confidence {j.confidence:.2f}" for j in judges[:3]],
                [(RootCause.MODEL_LIMITATION, 0.3)],
            )
        return RootCauseAnalysis(
            RootCause.MODEL_LIMITATION,
            0.4,
            "semantic quality below the rubric threshold per judge",
            [f"judge:{j.metric} {j.score:.2f}" for j in judges[:3]],
            [(RootCause.PROMPT, 0.35)],
        )
    if failed:
        return RootCauseAnalysis(
            RootCause.UNKNOWN,
            0.25,
            "checks failed but their pattern does not point to a specific component",
            ev,
            [(RootCause.PROMPT, 0.25), (RootCause.MODEL_LIMITATION, 0.25)],
        )
    return RootCauseAnalysis(RootCause.UNKNOWN, 0.2, "no failing evidence recorded")
