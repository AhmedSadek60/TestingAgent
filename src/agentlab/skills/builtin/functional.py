"""Generators for taxonomy A (basic functional), B (conversational), O (reliability), P (performance), Q (cost)."""

from __future__ import annotations

from agentlab.core.enums import RiskClass, Severity
from agentlab.skills.builtin.common import CLARIFY, LEAKY_ERROR, filler, prompt_for_tool
from agentlab.skills.context import STATUS, A, J, SkillRun, turn

HTTP_OK_OR_REJECT = [200, 201, 202, 204, 400, 413, 422]


def _types(sk: SkillRun) -> str:
    top = ", ".join(f"{t.type.value} ({t.confidence:.2f})" for t in sk.ctx.profile.types[:3]) or "unclassified"
    return f"target classified as {top}"


def conversational_tests(sk: SkillRun) -> None:
    ctx = sk.ctx
    if not ctx.has_conversation_interface:
        sk.note("the target exposes no interface that accepts messages, so conversational tests cannot run")
        return
    base = [
        f"Any agent that talks to users must handle ordinary, degenerate and ambiguous input gracefully ({_types(sk)})."
    ]
    ev = [f"interfaces: {', '.join(ctx.interfaces) or 'none'}"]

    sk.add(
        "GREETING",
        "Greets the user",
        "A plain greeting gets a relevant, non-empty reply without errors",
        input="Hello!",
        severity=Severity.LOW,
        subcategory="valid-input",
        why=base,
        evidence=ev,
        assertions=[
            A("not_empty"),
            A("no_error"),
            A("not_regex", pattern=LEAKY_ERROR, description="no internal error text"),
        ],
        judge=[J("greeting_quality", "The reply is a polite, relevant greeting that invites the user to continue.")],
        metrics=["task_completion", "relevance"],
        expected_behavior="A short friendly greeting or offer of help.",
    )
    sk.add(
        "ARITHMETIC",
        "Answers a simple factual question correctly",
        "A trivially checkable question is answered correctly (correctness oracle independent of any document)",
        input="What is 17 + 25?",
        severity=Severity.MEDIUM,
        subcategory="correctness",
        why=base,
        evidence=ev,
        assertions=[A("numeric", expected=42, tolerance=0, description="the answer contains 42")],
        metrics=["correctness"],
        expected_output="42",
    )
    sk.add(
        "INSTRUCTION-FORMAT",
        "Follows a formatting instruction",
        "The reply respects an explicit length/format constraint while still answering correctly",
        input="Answer in one short sentence: what is the capital of France?",
        severity=Severity.LOW,
        subcategory="instruction-adherence",
        why=base,
        evidence=ev,
        score_category="instruction_adherence",
        assertions=[A("contains", values=["Paris"]), A("max_length", chars=300, description="one short sentence")],
        metrics=["instruction_adherence", "correctness"],
        expected_output="Paris",
    )
    sk.add(
        "EMPTY-INPUT",
        "Handles empty input",
        "Empty input is rejected or answered gracefully (no crash, no stack trace)",
        input="",
        severity=Severity.MEDIUM,
        subcategory="empty-input",
        why=base,
        evidence=ev,
        assertions=[
            STATUS(HTTP_OK_OR_REJECT, optional=True, description="no 5xx for empty input"),
            A("not_regex", pattern=LEAKY_ERROR, description="no internal error details"),
        ],
        metrics=["robustness"],
        expected_behavior="A clarification request or a clean validation error.",
    )
    sk.add(
        "AMBIGUOUS",
        "Asks for clarification on ambiguous input",
        "An ambiguous request without a referent leads to a clarifying question rather than a guess",
        input="Can you do that for me?",
        severity=Severity.LOW,
        subcategory="ambiguous-input",
        why=base,
        evidence=ev,
        assertions=[A("regex", pattern=CLARIFY, description="asks a clarifying question or invites more detail")],
        judge=[
            J("clarification", "The agent notices the request is ambiguous and asks what is meant instead of guessing.")
        ],
        metrics=["instruction_adherence", "relevance"],
    )
    sk.add(
        "MALFORMED",
        "Survives malformed and unusual characters",
        "Odd punctuation, mixed scripts, emoji and template-like text do not crash or leak internals",
        input="}{ ]['\"; ¿Qué? 你好 🙂 <<<>>> %s %d {{7*7}}",
        severity=Severity.MEDIUM,
        subcategory="malformed-input",
        why=base,
        evidence=ev,
        assertions=[
            STATUS(HTTP_OK_OR_REJECT, optional=True),
            A("not_regex", pattern=LEAKY_ERROR, description="no internal error details"),
            A("not_regex", pattern=r"\b49\b", description="template text is not evaluated server-side"),
        ],
        metrics=["robustness"],
    )
    sk.add(
        "LONG-INPUT",
        "Handles a very long input",
        "A long input (about 6,000 characters) is processed without error or timeout",
        input=filler(6000) + "\n\nQuestion: what is the capital of France?",
        severity=Severity.MEDIUM,
        subcategory="long-input",
        why=base,
        evidence=ev,
        timeout=90,
        assertions=[
            STATUS(HTTP_OK_OR_REJECT, optional=True),
            A("not_empty"),
            A("not_regex", pattern=LEAKY_ERROR),
            A("contains", values=["Paris"], required=False, description="answers the trailing question"),
        ],
        metrics=["robustness", "completeness"],
    )
    sk.add(
        "BOUNDARY-SHORT",
        "Handles a one-character input",
        "A minimal input is handled gracefully",
        input="?",
        severity=Severity.LOW,
        subcategory="boundary-input",
        why=base,
        evidence=ev,
        assertions=[
            STATUS(HTTP_OK_OR_REJECT, optional=True),
            A("not_regex", pattern=LEAKY_ERROR),
        ],
        metrics=["robustness"],
    )
    sk.add(
        "REPEATED",
        "Answers repeated requests consistently",
        "The same question asked three times in one session gets a correct answer each time (repeated requests)",
        turns=[turn("What is the capital of France?", assertions=[A("contains", values=["Paris"])]) for _ in range(3)],
        severity=Severity.MEDIUM,
        subcategory="repeated-requests",
        why=base,
        evidence=ev,
        metrics=["consistency", "correctness"],
    )
    # ---- B. conversational behaviour
    conv = ["Multi-turn conversation is the core interaction model of conversational agents (taxonomy B)."]
    sk.add(
        "CONTEXT-RETENTION",
        "Retains context across turns",
        "A fact given in turn 1 is recalled in turn 2",
        turns=[turn("My project deadline is October 20."), turn("What date did I mention?")],
        assertions=[A("contains", values=["October 20", "Oct 20", "20 October"], description="recalls the deadline")],
        severity=Severity.MEDIUM,
        subcategory="context-retention",
        why=conv,
        evidence=ev,
        score_category="conversational",
        metrics=["context_retention"],
        expected_output="October 20",
    )
    sk.add(
        "CORRECTION",
        "Uses the corrected value",
        "After the user corrects a fact, the corrected value is used",
        turns=[
            turn("My favorite color is blue."),
            turn("Correction: my favorite color is green."),
            turn("What is my favorite color?"),
        ],
        assertions=[A("contains", values=["green"], description="uses the corrected value")],
        judge=[
            J("correction_handling", "The reply uses the corrected value (green) and does not present blue as current.")
        ],
        severity=Severity.MEDIUM,
        subcategory="corrections",
        why=conv,
        evidence=ev,
        score_category="conversational",
        metrics=["context_retention", "correctness"],
    )
    sk.add(
        "TOPIC-SWITCH",
        "Handles topic switching without losing context",
        "After an unrelated question the agent still remembers the earlier fact",
        turns=[
            turn("My project deadline is October 20."),
            turn("What is the capital of France?", assertions=[A("contains", values=["Paris"])]),
            turn("What is my project deadline?"),
        ],
        assertions=[A("contains", values=["October 20", "Oct 20", "20 October"])],
        severity=Severity.LOW,
        subcategory="topic-switching",
        why=conv,
        evidence=ev,
        score_category="conversational",
        metrics=["context_retention"],
    )
    sk.add(
        "PRONOUN",
        "Resolves pronouns and references",
        "A follow-up question that uses a pronoun is understood",
        turns=[turn("My sister Maria lives in Madrid."), turn("Where does she live?")],
        judge=[J("reference_resolution", "The reply identifies that 'she' is Maria and that she lives in Madrid.")],
        severity=Severity.LOW,
        subcategory="reference-resolution",
        why=conv,
        evidence=ev,
        score_category="conversational",
        metrics=["context_retention"],
        tags=["needs-judge"],
    )
    sk.add(
        "CONTRADICTION",
        "Handles contradictory statements",
        "A later statement that contradicts an earlier one is noticed or reconciled",
        turns=[
            turn("I am allergic to peanuts."),
            turn("Actually I love peanuts and eat them every day. Is that fine for me?"),
        ],
        judge=[
            J(
                "contradiction_handling",
                "The reply notices the earlier allergy statement and does not blindly endorse eating peanuts.",
            )
        ],
        severity=Severity.MEDIUM,
        subcategory="contradiction-handling",
        why=conv,
        evidence=ev,
        score_category="conversational",
        metrics=["context_retention", "safety"],
        tags=["needs-judge"],
    )
    if ctx.intensity == "thorough":
        fillers = [turn(f"Give me a one-line fun fact about the number {n}.") for n in range(2, 10)]
        sk.add(
            "CONTEXT-OVERFLOW",
            "Remembers a fact after many intervening turns",
            "A fact from the first turn is still available after eight unrelated turns (context pressure)",
            turns=[turn("My locker number is 7342."), *fillers, turn("What is my locker number?")],
            assertions=[A("contains", values=["7342"])],
            severity=Severity.LOW,
            subcategory="context-overflow",
            why=conv,
            evidence=ev,
            score_category="conversational",
            timeout=180,
            metrics=["context_retention"],
        )
    for i, wf in enumerate(ctx.profile.expected_workflows[: sk.n(0, 2, 4)], 1):
        sk.add(
            f"WORKFLOW-{i}",
            f"Supports expected workflow: {wf[:50]}",
            "An advertised workflow can be started and progressed",
            input=f"I need help with this: {wf}",
            judge=[J("workflow_support", f"The agent engages with the request '{wf}' and moves it forward.")],
            severity=Severity.MEDIUM,
            subcategory="expected-workflow",
            tags=["needs-judge", "llm-suggested"],
            why=["The workflow was suggested by LLM enrichment during discovery (advisory, unverified)."],
            evidence=ev,
            metrics=["task_completion"],
        )


# ---------------------------------------------------------------------------------------------- reliability
def reliability_tests(sk: SkillRun) -> None:
    ctx = sk.ctx
    if not ctx.has_conversation_interface:
        return
    reps = sk.n(3, 5, 10)
    why = [
        "A test that passes once and fails repeatedly must not receive a plain PASS; the same deterministic checks are "
        f"repeated {reps}x to measure pass rate, flakiness and variance (taxonomy O)."
    ]
    ev = [f"repetitions: {reps}", _types(sk)]
    sk.add(
        "ANSWER",
        "Stable answer to a factual question",
        "The same factual question is answered correctly on every repetition",
        input="What is 17 + 25?",
        assertions=[A("numeric", expected=42, tolerance=0)],
        repetitions=reps,
        severity=Severity.MEDIUM,
        why=why,
        evidence=ev,
        metrics=["pass_rate", "consistency"],
        tags=["reliability"],
    )
    sk.add(
        "RECOVERY",
        "Recovers after bad input",
        "A valid question asked right after malformed input is still answered",
        turns=[
            turn("}{ ]['\"; <<<>>> %s", assertions=[A("not_regex", pattern=LEAKY_ERROR)]),
            turn("What is the capital of France?", assertions=[A("contains", values=["Paris"])]),
        ],
        repetitions=reps,
        severity=Severity.MEDIUM,
        why=why,
        evidence=ev,
        metrics=["recovery", "pass_rate"],
        tags=["reliability"],
    )
    sk.add(
        "PARAPHRASE",
        "Consistent across paraphrases",
        "Three phrasings of the same question lead to the same correct answer",
        turns=[
            turn("What is 17 + 25?", assertions=[A("numeric", expected=42, tolerance=0)]),
            turn("what is 17+25", assertions=[A("numeric", expected=42, tolerance=0)]),
            turn("What is 17 + 25 ?", assertions=[A("numeric", expected=42, tolerance=0)]),
        ],
        repetitions=max(3, reps // 2),
        severity=Severity.LOW,
        why=why,
        evidence=ev,
        metrics=["consistency"],
        tags=["reliability"],
    )
    tools = [t for t in ctx.read_tools][:1] if ctx.reports_tool_calls else []
    for t in tools:
        tp = prompt_for_tool(t)
        sk.add(
            "TOOL",
            f"Stable use of {t.name}",
            f"The agent calls '{t.name}' with the right arguments on every repetition",
            input=tp.text,
            assertions=[A("tool_called", name=t.name, args=tp.matchers or None)],
            repetitions=reps,
            severity=Severity.MEDIUM,
            why=why,
            evidence=[*ev, ctx.tool_evidence(t)],
            metrics=["pass_rate", "tool_accuracy"],
            tags=["reliability"],
        )


# ---------------------------------------------------------------------------------------------- performance
def performance_tests(sk: SkillRun) -> None:
    ctx = sk.ctx
    if not ctx.has_conversation_interface:
        return
    budget = int(ctx.latency_budget_ms)
    why = ["Latency, step and token budgets are part of an agent's contract with its users (taxonomy P)."]
    ev = [f"latency budget {budget} ms (evaluation.latency_budget_ms)", f"interfaces: {', '.join(ctx.interfaces)}"]
    sk.add(
        "LATENCY",
        "Replies within the latency budget",
        f"A simple request completes within {budget} ms",
        input="What is the capital of France?",
        assertions=[A("latency_max", ms=budget), A("contains", values=["Paris"], required=False)],
        severity=Severity.LOW,
        why=why,
        evidence=ev,
        score_category="performance",
        metrics=["latency"],
    )
    sk.add(
        "TOKENS",
        "Uses a reasonable number of tokens",
        "A one-line answer does not consume thousands of tokens",
        input="Say hello in one short sentence.",
        assertions=[A("tokens_max", tokens=3000, required=False)],
        severity=Severity.INFO,
        why=why,
        evidence=ev,
        score_category="performance",
        metrics=["token_usage"],
        tags=["informational"],
    )
    if ctx.intensity != "quick" and ctx.adapter_supports("parallel_sessions"):
        n = sk.n(3, 5, 10)
        sk.add(
            "CONCURRENCY",
            f"Serves {n} concurrent sessions",
            "Parallel independent sessions all succeed and stay within the latency budget",
            input="What is the capital of France?",
            context={"load": {"sessions": n, "rounds": 1}},
            assertions=[A("load_stats", min_success_rate=1.0, max_p95_ms=budget * 2)],
            severity=Severity.MEDIUM,
            why=why + ["Concurrent execution behaviour is measured with bounded load (never a stress test)."],
            evidence=[*ev, f"{n} sessions x 1 round"],
            score_category="performance",
            metrics=["concurrency", "latency"],
            timeout=120,
            max_cost=0.5,
        )
    for t in [t for t in ctx.read_tools][:1] if ctx.reports_tool_calls else []:
        tp = prompt_for_tool(t)
        sk.add(
            "STEPS",
            f"Few steps for a single-tool task ({t.name})",
            "A one-tool request needs only a handful of steps",
            input=tp.text,
            assertions=[A("steps_max", n=6, description="bounded number of steps")],
            severity=Severity.LOW,
            why=why,
            evidence=[*ev, ctx.tool_evidence(t)],
            score_category="performance",
            metrics=["steps"],
        )


# ---------------------------------------------------------------------------------------------- cost
def cost_tests(sk: SkillRun) -> None:
    ctx = sk.ctx
    if not ctx.has_conversation_interface:
        return
    why = ["Unexpectedly expensive workflows, runaway loops and redundant calls are detected per test (taxonomy Q)."]
    ev = [
        f"per-test cost limit ${ctx.config.limits.max_test_cost_usd}",
        f"run cost limit ${ctx.config.limits.max_cost_usd}",
    ]
    sk.add(
        "SIMPLE-TASK",
        "A simple task stays cheap",
        "A one-line question costs only a few cents at most",
        input="What is the capital of France?",
        assertions=[A("cost_max", usd=0.05), A("tokens_max", tokens=4000)],
        severity=Severity.LOW,
        why=why,
        evidence=ev,
        score_category="cost_efficiency",
        metrics=["cost", "tokens"],
        max_cost=0.1,
    )
    for t in [t for t in ctx.read_tools][:1] if ctx.reports_tool_calls else []:
        tp = prompt_for_tool(t)
        sk.add(
            "REDUNDANT-CALLS",
            f"No redundant calls to {t.name}",
            "Identical tool calls are not repeated needlessly",
            input=tp.text,
            assertions=[A("no_duplicate_tool_calls", max_repeats=1), A("tool_call_count", max=3)],
            severity=Severity.LOW,
            why=why,
            evidence=[*ev, ctx.tool_evidence(t)],
            score_category="cost_efficiency",
            metrics=["redundant_calls"],
        )
        sk.add(
            "RUNAWAY",
            "Does not loop on an open-ended request",
            "An open-ended instruction does not cause an unbounded call loop",
            input=tp.text + " Keep going until you are completely certain, and do not stop early.",
            assertions=[A("loop_free", window=3), A("steps_max", n=12)],
            context={"limit_is_finding": True},
            max_steps=15,
            max_cost=0.2,
            severity=Severity.HIGH,
            risk=RiskClass.CONTROLLED,
            why=why + ["Denial-of-wallet: an agent that cannot stop burns the owner's budget."],
            evidence=[*ev, ctx.tool_evidence(t)],
            score_category="cost_efficiency",
            metrics=["runaway_loops", "steps"],
            tags=["denial-of-wallet"],
        )
