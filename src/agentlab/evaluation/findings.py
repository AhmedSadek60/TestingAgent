"""Findings generation (spec sections 30 and 66).

A finding says *what* was observed, *where* (test + evidence), what was expected, why it matters,
how severe it is, how confident we are, how to reproduce it and what to do about it. It never says
"the agent is bad". Statements are separated into observed **facts**, **inferences** (e.g. root
cause hypotheses), **judgments** (LLM-judge or reviewer opinions) and **recommendations**.
"""

from __future__ import annotations

from collections import defaultdict

from agentlab.core.enums import RootCause, Severity, TestStatus
from agentlab.core.models import AssertionResult, AttemptResult, Finding, JudgeResult, TestCase, TestResult
from agentlab.evaluation.rootcause import RootCauseAnalysis, classify_root_cause
from agentlab.evaluation.severity import SECURITY_TYPES, SeverityDecision, decide_severity

IMPACT: dict[str, str] = {
    "no_canary_leak": "A protected value was disclosed in the user-visible channel; a real secret in the same position "
    "would be exposed to whoever can talk to the agent.",
    "no_secret_leak": "A credential-like secret was exposed in the agent's output and could be reused by an attacker.",
    "no_destructive_without_confirmation": "Destructive or externally visible actions can be triggered without human "
    "approval, so unauthorised side effects are possible.",
    "no_external_recipient": "Messages or data can be sent to unauthorised external recipients.",
    "no_injection_followed": "Untrusted content can steer the agent (goal hijacking), enabling data exfiltration and "
    "unauthorised tool use.",
    "no_injection_indicators_in_output": "Instructions embedded in untrusted data are being reproduced or acted upon.",
    "tool_allowlist": "The agent can use tools outside the set that is authorised for this task.",
    "tool_not_called": "A tool that must not be used in this scenario was invoked.",
    "grounded": "Users may receive confidently stated claims that are not supported by the knowledge base.",
    "abstains": "The agent answers questions it has no evidence for instead of declining, which invites hallucination.",
    "citations_valid": "Sources cited to the user do not exist in the retrieved material, so answers cannot be verified.",
    "cites_source": "Answers are not attributed to their sources, which prevents verification.",
    "retrieved_source": "The retrieval step does not surface the document needed to answer correctly.",
    "tool_called": "The task is not completed (or is completed with the wrong capability) because the expected tool "
    "was not used.",
    "trajectory:tool_selection": "Tasks are handled with the wrong tool, or with unnecessary tools, which can cause wrong "
    "results and unintended side effects.",
    "trajectory:tool_arguments": "Tools receive incorrect inputs, producing wrong results or unintended side effects.",
    "trajectory:unnecessary_actions": "Redundant tool calls waste time and cost and can repeat side effects.",
    "trajectory:error_recovery": "A tool failure is neither handled nor reported to the user.",
    "loop_free": "Runaway repetition can exhaust time and budget (denial of wallet).",
    "no_duplicate_tool_calls": "Identical repeated calls waste budget and may repeat side effects.",
    "steps_max": "The agent used more steps than allowed, which raises cost and latency and may signal looping.",
    "cost_max": "The scenario exceeded its cost budget.",
    "tokens_max": "The scenario exceeded its token budget.",
    "latency_max": "Slow responses degrade the user experience and may time out upstream callers.",
    "json_schema": "API consumers receive responses that violate the documented contract.",
    "status_code": "The endpoint returns a status code that clients will mis-handle.",
    "handoff_path": "Requests are routed to the wrong specialist or are not delegated as designed.",
    "no_handoff_cycle": "Agents delegate to each other in a cycle, which wastes budget and can hang the workflow.",
    "max_handoffs": "Excessive delegation increases cost and latency.",
    "state_equals": "The final UI/workspace state does not match what the user asked for.",
    "state_contains": "The final UI/workspace state does not contain the expected outcome.",
    "asks_confirmation": "A consequential action is carried out without asking the user first, so a mistaken or "
    "manipulated request takes effect immediately.",
    "tool_args_safe": "A crafted input reaches a tool unchanged, so anyone who can talk to the agent can read files, "
    "run commands or query data they were never meant to reach.",
    "tool_args_valid": "Tools receive malformed or out-of-contract arguments, which gives wrong results or errors and "
    "can trigger unintended side effects.",
    "tool_schema_valid": "A tool definition is invalid, so the model cannot call it reliably.",
    "tool_description_clean": "A tool description contains instructions or hidden text that can steer the model.",
    "tool_sequence": "Steps happen in the wrong order, so results can be wrong or side effects can occur before the "
    "checks that should precede them.",
    "tool_call_count": "Tools are called more or fewer times than the task needs, which wastes cost and time or "
    "leaves the task unfinished.",
    "no_tools_called": "Tools were used where none were needed, which risks unnecessary side effects.",
    "refuses": "A request that should be declined was fulfilled, so the agent can be used to produce content or "
    "take actions its operator prohibits.",
    "rejects_input": "Invalid or hostile input is accepted instead of rejected, so downstream components receive data "
    "they cannot handle safely.",
    "not_empty": "The agent returned no usable answer.",
    "no_error": "The agent failed with an error instead of handling the request.",
    "max_length": "Replies exceed the allowed length, which raises cost and breaks clients that assume a bound.",
    "contains": "The answer lacks something the scenario requires, so users receive an incomplete or wrong result.",
    "equals": "The answer differs from the required value, so users receive a wrong result.",
    "regex": "The answer does not have the required form, so users or downstream code cannot rely on it.",
    "jsonpath": "A required field of the structured answer is missing or wrong, so consumers cannot rely on it.",
    "numeric": "A number in the answer is outside the accepted range or tolerance.",
    "context_contains": "The context the agent worked from lacks information the answer depends on.",
    "plan_contains": "The plan omits a step the task requires, so the task can be left incomplete or unsafe.",
    "file_contains": "A produced file does not contain what the task requires.",
    "file_exists": "A file the task should have produced does not exist.",
    "not_contains": "The reply contains something the scenario forbids, as shown in the observed value.",
    "not_regex": "The reply matches a pattern the scenario forbids, as shown in the observed value.",
    "diff_no_secrets": "The change adds a credential-like value to the repository, where it can be read by anyone with "
    "access to the code.",
    "diff_not_contains": "The change contains content the task forbids.",
    "diff_not_touches": "The change modifies files it should not touch, which widens its blast radius.",
    "diff_only_touches": "The change goes beyond the files the task allows.",
    "diff_size_max": "The change is larger than the task justifies, which makes it hard to review and risky to merge.",
    "tests_pass": "The change breaks the project's own tests.",
    "load_stats": "Under concurrent load the error rate or latency exceeds the allowed limits.",
    "all_of": "At least one required condition of the scenario was not met.",
    "any_of": "None of the acceptable outcomes of the scenario was observed.",
}

RECOMMEND: dict[str, str] = {
    "no_canary_leak": "Keep secrets out of the model context entirely (hold them server-side behind tools); never put them in "
    "the system prompt; add an output filter for secret patterns and re-run this test.",
    "no_secret_leak": "Remove the secret from prompts/context, rotate it, and add output redaction as defence in depth.",
    "no_destructive_without_confirmation": "Enforce human confirmation in code (not only in the prompt) before destructive or "
    "external tools run; return a confirmation request and execute only on explicit approval.",
    "no_external_recipient": "Restrict recipients to an allow-list/domain and require confirmation for anything external.",
    "no_injection_followed": "Treat retrieved and tool content as data: delimit it, strip instruction-like text, give the "
    "agent least-privilege tools, and add a policy check before side-effecting calls.",
    "no_injection_indicators_in_output": "Add an output check for injected-instruction echoes and isolate untrusted content.",
    "tool_allowlist": "Bind each task to an explicit tool allow-list and reject calls outside it at the executor layer.",
    "tool_not_called": "Gate the tool behind an explicit user intent or permission check.",
    "grounded": "Require answers to be derived from retrieved passages and to abstain when no relevant context is found; "
    "add a groundedness guard before returning an answer.",
    "abstains": "Teach the agent to say it does not know when retrieval returns nothing relevant; add an abstention test to CI.",
    "citations_valid": "Only emit citations that map to retrieved document identifiers and verify them before responding.",
    "retrieved_source": "Inspect chunking, embeddings and the retrieval query; check the document was ingested and is not filtered out.",
    "tool_called": "Improve the tool description and selection criteria, add examples, or route this intent deterministically.",
    "trajectory:tool_selection": "Tighten tool descriptions so intents map unambiguously to one tool; add few-shot examples; "
    "consider deterministic routing for high-risk tools.",
    "trajectory:tool_arguments": "Validate tool arguments against a JSON schema before execution and return actionable errors to the model.",
    "trajectory:unnecessary_actions": "Add duplicate-call detection and a per-request tool budget.",
    "trajectory:error_recovery": "Handle tool errors explicitly: retry with changes or tell the user what failed.",
    "loop_free": "Add max-iteration limits and repeated-call detection; surface an error to the model instead of retrying identically.",
    "no_duplicate_tool_calls": "Cache tool results within a turn and detect identical repeated calls.",
    "steps_max": "Add a hard step budget and stop conditions.",
    "cost_max": "Add per-request cost budgets and cheaper fallbacks.",
    "latency_max": "Profile the slowest step; stream partial results or parallelise independent calls.",
    "json_schema": "Align the response with the published schema and add contract tests.",
    "handoff_path": "Review routing instructions and hand-off conditions between agents.",
    "no_handoff_cycle": "Add delegation depth limits and visited-agent tracking.",
    "state_equals": "Compare the recorded action timeline with the expected one to locate the step that diverged.",
    "asks_confirmation": "Enforce a confirmation step in code before consequential tools run; do not rely on the prompt "
    "alone.",
    "tool_args_safe": "Validate and constrain tool arguments in the executor (allow-lists, parameterised queries, path "
    "and host restrictions); never pass model-chosen text to a shell or file API unchecked.",
    "tool_args_valid": "Validate arguments against the tool's schema before execution and return actionable errors.",
    "tool_schema_valid": "Fix the tool definition so it is valid JSON Schema with typed, described parameters.",
    "tool_description_clean": "Remove instructions from tool descriptions and review third-party tool metadata.",
    "tool_sequence": "Encode the required order in the workflow (or a state machine) instead of leaving it to the model.",
    "tool_call_count": "Review the tool descriptions and add a per-request tool budget.",
    "no_tools_called": "Describe when a tool is appropriate and add a test that a plain question needs none.",
    "refuses": "Add or strengthen a refusal policy for this class of request and test it in CI.",
    "rejects_input": "Validate input at the boundary and answer invalid input with a clear error.",
    "not_empty": "Return an explicit message when no answer can be given instead of an empty reply.",
    "no_error": "Handle the failing path and return a user-readable message; fix the underlying error.",
    "max_length": "Cap the reply length in the prompt and in code.",
    "contains": "Compare the observed answer with the expected one and fix the instruction or data that led to the gap.",
    "equals": "Compare the observed answer with the expected one and fix the instruction or data that led to the gap.",
    "regex": "Constrain the output format (structured output or a validator) and re-run the test.",
    "jsonpath": "Constrain the output to the documented schema (structured output or a validator).",
    "numeric": "Check the calculation or the data it is based on; compute numbers in code, not in the model.",
    "not_contains": "Find where the forbidden content comes from (prompt, data or tool output) and filter or remove it.",
    "not_regex": "Find where the forbidden content comes from (prompt, data or tool output) and filter or remove it.",
    "diff_no_secrets": "Remove the value from the change, rotate it if it was real, and add a secret scanner to the "
    "agent's workflow.",
    "diff_not_touches": "Restrict the agent's write scope to the paths the task needs.",
    "diff_only_touches": "Restrict the agent's write scope to the paths the task needs.",
    "tests_pass": "Run the project's tests inside the agent's loop and block the change when they fail.",
    "status_code": "Return the documented status code for this case and add a contract test.",
    "tokens_max": "Trim the prompt and context, or raise the budget deliberately.",
    "max_handoffs": "Add a limit on delegation depth and review when hand-offs are needed.",
    "state_contains": "Compare the recorded action timeline with the expected one to locate the step that diverged.",
    "cites_source": "Require a source for each claim and verify the citations before answering.",
    "all_of": "Look at the failing sub-checks in the observed facts; each names what was missing.",
    "any_of": "None of the acceptable outcomes happened; compare the observed output with the expected behaviour.",
    "context_contains": "Check that the retrieval or context-building step supplies the information the answer needs.",
    "plan_contains": "Make the planning prompt or workflow require the missing step, and test it.",
    "file_contains": "Check the step that writes this file and the instruction it follows.",
    "file_exists": "Check the step that should create this file and why it did not run.",
    "diff_not_contains": "Review why the change contains the forbidden content and constrain the agent's edit scope.",
    "diff_size_max": "Ask for smaller, focused changes and stop the agent from rewriting unrelated code.",
    "load_stats": "Find the bottleneck under concurrency (rate limits, locks, cold starts) and re-run the load test.",
}

BY_CAUSE: dict[RootCause, str] = {
    RootCause.PROMPT: "Review the system prompt and instructions for the behaviour under test; add explicit rules and examples.",
    RootCause.MODEL_LIMITATION: "Try a stronger model or add deterministic guardrails around this behaviour.",
    RootCause.RETRIEVAL: "Inspect the retrieval pipeline (chunking, embeddings, filters, top-k).",
    RootCause.TOOL_IMPLEMENTATION: "Check the tool's error handling and its contract with the agent.",
    RootCause.TIMEOUT: "Investigate the slow dependency or raise the timeout if the work is legitimately long.",
    RootCause.INFRASTRUCTURE: "Resolve the environment problem and re-run; this result does not reflect agent quality.",
    RootCause.EVALUATOR_UNCERTAINTY: "Have a human review this test; consider a stronger judge or a deterministic assertion.",
}


def _failed(attempts: list[AttemptResult]) -> list[AssertionResult]:
    seen: set[tuple[str, str]] = set()
    out: list[AssertionResult] = []
    for a in attempts:
        for x in a.assertions:
            key = (x.type, x.message)
            if not x.passed and not x.evaluator_error and key not in seen:
                seen.add(key)
                out.append(x)
    return out


def _failed_judges(attempts: list[AttemptResult]) -> list[JudgeResult]:
    return [j for a in attempts for j in a.judge if not j.passed]


def assess(
    test: TestCase, result: TestResult, *, production: bool = False
) -> tuple[SeverityDecision, RootCauseAnalysis]:
    """Compute root cause and severity for a non-passing result (shared by executor and findings)."""
    rca = classify_root_cause(test, result.attempts, result.status, result.error_kind)
    failed = _failed(result.attempts)
    judge_only = not failed and bool(_failed_judges(result.attempts))
    side_effects = any(a.trajectory.get("side_effects") for a in result.attempts)
    sev = decide_severity(
        test,
        failed,
        stats=result.reliability,
        confidence=result.confidence,
        judge_only=judge_only,
        production=production,
        has_side_effects=side_effects,
    )
    if (
        rca.cause in {RootCause.INFRASTRUCTURE, RootCause.EXTERNAL_DEPENDENCY, RootCause.TIMEOUT}
        and sev.severity.rank > 2
    ):
        sev.severity = Severity.MEDIUM
        sev.adjustments.append(f"capped at MEDIUM: likely cause is {rca.cause.value}, not agent behaviour")
    return sev, rca


def _title(test: TestCase, failed: list[AssertionResult], judges: list[JudgeResult], status: TestStatus) -> str:
    if status == TestStatus.TIMEOUT:
        return f"Target did not respond within {test.timeout:g}s ({test.name})"
    if failed:
        msg = failed[0].message.strip().rstrip(".")
        msg = msg[0].upper() + msg[1:] if msg else msg
        return f"{msg[:150]} [{test.id}]"
    if judges:
        return f"{test.name}: judged below threshold on '{judges[0].metric}' [{test.id}]"
    return f"{test.name} did not meet expectations [{test.id}]"


def _reproduction(test: TestCase, run_id: str) -> str:
    steps = []
    for i, t in enumerate(test.all_turns(), 1):
        who = f" (session '{t.session}')" if t.session != "default" else ""
        steps.append(f"{i}. Send{who}: {t.input[:300]!r}")
    if not steps and test.browser_steps:
        steps = [
            f"{i}. Browser step: {s.action} {s.target or s.value or ''}".rstrip()
            for i, s in enumerate(test.browser_steps, 1)
        ]
    steps.append(
        "Re-run only this test with the same inputs and checks: "
        f"agentlab test <the same target flags> --baseline {run_id} --only {test.id}"
    )
    if test.context.get("fixtures"):
        steps.append("Fixtures required: " + ", ".join(map(str, test.context["fixtures"])))
    return "\n".join(steps)


def build_finding(
    run_id: str, test: TestCase, result: TestResult, target_name: str, *, production: bool = False
) -> Finding | None:
    if result.status not in {TestStatus.FAILED, TestStatus.TIMEOUT}:
        return None
    failed = _failed(result.attempts)
    judges = _failed_judges(result.attempts)
    sev, rca = assess(test, result, production=production)
    primary = failed[0].type if failed else (f"judge:{judges[0].metric}" if judges else "unknown")
    facts: list[str] = []
    for f in failed[:8]:
        facts.append(f"[{f.type}] {f.message}")
    for a in result.attempts[:1]:
        if a.trajectory.get("tool_calls"):
            facts.append(
                "Observed tool calls: "
                + ", ".join(
                    f"{c['name']}({', '.join(f'{k}={v!r}' for k, v in list(c.get('arguments', {}).items())[:3])})"
                    for c in a.trajectory["tool_calls"][:6]
                )
            )
    if result.reliability and result.reliability.repetitions > 1:
        r = result.reliability
        facts.append(
            f"Passed {r.passes}/{r.repetitions} repetitions (pass rate {r.pass_rate:.0%}"
            f"{', flaky' if r.flaky else ', deterministic failure' if r.deterministic_failure else ''})."
        )
    if result.error_kind:
        facts.append(f"Error kind: {result.error_kind.value}")
    inferences = [rca.statement()]
    if sev.signals:
        inferences += [f"Severity signal: {s}" for s in sev.signals]
    judgments = []
    for j in judges[:3]:
        top = j.votes[0].reasoning if j.votes else (j.error or "")
        judgments.append(
            f"Judge ({', '.join(v.judge for v in j.votes) or 'n/a'}) scored '{j.metric}' {j.score:.2f} "
            f"(confidence {j.confidence:.2f}, agreement {j.agreement:.2f}): {top[:240]}"
        )
    observed = "; ".join(f.message for f in failed[:3]) or (
        "; ".join(f"{j.metric} score {j.score:.2f} below threshold" for j in judges[:3])
        or f"test ended with status {result.status.value}"
    )
    expected = test.expected_behavior or test.objective
    impact = (
        IMPACT.get(primary, IMPACT.get(primary.split(":")[0], ""))
        or "The agent's behaviour deviates from the specified behaviour for this scenario."
    )
    rec = (
        RECOMMEND.get(primary)
        or RECOMMEND.get(primary.split(":")[0])
        or BY_CAUSE.get(
            rca.cause,
            "Compare the observed behaviour with the expected behaviour and adjust the agent's instructions or logic.",
        )
    )
    if rca.cause in BY_CAUSE and BY_CAUSE[rca.cause] not in rec:
        rec = f"{rec} Also: {BY_CAUSE[rca.cause]}"
    is_sec = (
        bool(sev.signals)
        or test.category.lower() in {"security", "safety"}
        or any(f.type in SECURITY_TYPES for f in failed)
    )
    return Finding(
        run_id=run_id,
        test_id=test.id,
        title=_title(test, failed, judges, result.status),
        category=test.category,
        severity=sev.severity,
        confidence=result.confidence,
        expected=expected,
        observed=observed,
        impact=impact,
        evidence=[*result.evidence, *[f"trace:{t}" for t in result.trace_ids]],
        reproduction=_reproduction(test, run_id),
        recommendation=rec,
        root_cause=rca.cause,
        root_cause_confidence=rca.confidence,
        is_security=is_sec,
        facts=facts,
        inferences=inferences,
        judgments=judgments,
        severity_breakdown=sev.to_dict(),
        fact_kind="observed" if failed else "judgment",
    )


def generate_findings(
    run_id: str, tests: list[TestCase], results: list[TestResult], target_name: str, *, production: bool = False
) -> list[Finding]:
    by_id = {t.id: t for t in tests}
    out: list[Finding] = []
    for r in results:
        t = by_id.get(r.test_id)
        if t is None:
            continue
        f = build_finding(run_id, t, r, target_name, production=production)
        if f:
            out.append(f)
    out.sort(key=lambda f: (-f.severity.rank, -f.confidence, f.test_id))
    return out


def cross_test_findings(run_id: str, findings: list[Finding], min_group: int = 3) -> list[Finding]:
    """Systemic patterns across tests (analysis phase 12): the same root cause and defect class repeated."""
    groups: dict[tuple[str, str], list[Finding]] = defaultdict(list)
    for f in findings:
        primary = f.facts[0].split("]")[0].lstrip("[") if f.facts else "unknown"
        groups[(f.root_cause.value, primary)].append(f)
    out: list[Finding] = []
    used: set[str] = set()
    for (cause, primary), group in groups.items():
        if len(group) < min_group:
            continue
        sev = max((g.severity for g in group), key=lambda s: s.rank)
        tid = "CROSS-" + primary.replace(":", "-").upper()
        if tid in used:  # the same failed check with a different likely cause is a separate pattern
            tid += "-" + cause.split("_")[0].upper()
        used.add(tid)
        out.append(
            Finding(
                run_id=run_id,
                test_id=tid,
                title=f"Systemic pattern: {len(group)} tests failed on '{primary}' (likely {cause.replace('_', ' ')})",
                category="cross-test-analysis",
                severity=sev,
                confidence=round(min(g.confidence for g in group), 3),
                expected="Behaviour consistent with the specification across scenarios.",
                observed=f"{len(group)} different scenarios failed the same check: "
                + ", ".join(g.test_id for g in group[:10]),
                impact="A repeated failure across scenarios suggests a design-level defect rather than an isolated case.",
                evidence=sorted({e for g in group for e in g.evidence})[:20],
                reproduction="Run the listed tests: " + ", ".join(g.test_id for g in group[:10]),
                recommendation=group[0].recommendation,
                root_cause=group[0].root_cause,
                root_cause_confidence=min(0.8, group[0].root_cause_confidence + 0.1),
                is_security=any(g.is_security for g in group),
                facts=[f"{g.test_id}: {g.observed[:160]}" for g in group[:10]],
                inferences=[
                    f"The same defect class appears in {len(group)} independent tests, so it is likely systemic."
                ],
                severity_breakdown={"derived_from": [g.test_id for g in group], "severity": sev.value},
            )
        )
    return out
