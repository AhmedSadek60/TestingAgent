"""Reporting building blocks that need no run: untrusted-text defusing, charts, wording and root-cause rules."""

from __future__ import annotations

import re

import pytest

from agentlab.core.enums import ErrorKind, RootCause, TestStatus
from agentlab.core.errors import UserError
from agentlab.core.models import AssertionResult, AttemptResult, TestCase, TestResult
from agentlab.evaluation import assertions as _assertions  # noqa: F401 - registers the core assertions
from agentlab.evaluation import assertions_ext as _ext  # noqa: F401
from agentlab.evaluation import assertions_workspace as _workspace  # noqa: F401
from agentlab.evaluation.assertions import ASSERTIONS
from agentlab.evaluation.findings import IMPACT, RECOMMEND
from agentlab.evaluation.rootcause import classify_root_cause
from agentlab.evaluation.scoring import load_profile
from agentlab.reporting.build import _auth_text, _ceilings_text, _limitations_seen
from agentlab.reporting.bundle import FORMATS, normalise_formats
from agentlab.reporting.render_md import neutralise_markup, table
from agentlab.reporting.svg import _nice, bar_chart, column_chart, donut, esc, line_chart
from agentlab.reporting.util import describe_values


# ======================================================================================== untrusted Markdown text
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hello <script>alert(1)</script>", "Hello &lt;script>alert(1)&lt;/script>"),
        ("<!-- a comment --> and <?php ?>", "&lt;!-- a comment --> and &lt;?php ?>"),
        ("a < b, 1<2, x <- y", "a < b, 1<2, x <- y"),  # arithmetic and arrows are left alone
        ("<https://example.com>", "&lt;https://example.com>"),
        ("[x](javascript:alert(3))", "[x]\\(javascript:alert(3))"),
        ("![img](https://tracker.example/p.png)", "![img]\\(https://tracker.example/p.png)"),
        ("see `<b>code</b>` here and <b>there</b>", "see `<b>code</b>` here and &lt;b>there&lt;/b>"),
        ("``a ` <i>`` <i>x</i>", "``a ` <i>`` &lt;i>x&lt;/i>"),
        ("```\n<script>kept</script>\n```\n<b>defused</b>", "```\n<script>kept</script>\n```\n&lt;b>defused&lt;/b>"),
        ("~~~html\n<b>kept</b>\n~~~\n<b>no</b>", "~~~html\n<b>kept</b>\n~~~\n&lt;b>no&lt;/b>"),
        ("````\n```\n<b>still inside</b>\n````\n<b>out</b>", "````\n```\n<b>still inside</b>\n````\n&lt;b>out&lt;/b>"),
        ("plain text, no markup", "plain text, no markup"),
    ],
)
def test_untrusted_markup_is_defused_outside_code_only(text: str, expected: str) -> None:
    assert neutralise_markup(text) == expected


def test_an_unclosed_fence_hides_nothing_a_viewer_would_interpret() -> None:
    """A viewer treats everything after an unclosed fence as code; so does the defuser, so the two always agree."""
    text = "intro <b>x</b>\n```\n<script>never interpreted</script>\n"
    assert neutralise_markup(text) == "intro &lt;b>x&lt;/b>\n```\n<script>never interpreted</script>\n"


def test_a_table_cell_cannot_break_out_of_its_row() -> None:
    out = table(["Name", "Value"], [("a|b", "line one\nline two")])
    assert out.count("\n") == 3 and "a\\|b" in out and "line one line two" in out


# ========================================================================================================= formats
def test_report_format_names() -> None:
    assert FORMATS == ("json", "md", "html", "pdf")
    assert normalise_formats(None) == list(FORMATS) == normalise_formats("all") == normalise_formats(["json", "all"])
    assert normalise_formats(["HTML, markdown"]) == ["md", "html"], "names are case-insensitive, aliases accepted"
    assert normalise_formats(["pdf", "json", "pdf"]) == ["json", "pdf"], "duplicates collapse, order is canonical"
    with pytest.raises(UserError, match="unknown report format 'docx'"):
        normalise_formats(["json", "docx"])


# =========================================================================================================== charts
def test_axis_ticks_for_counts_are_whole_numbers() -> None:
    assert _nice(10, integer=True) == 20 and _nice(3, integer=True) == 4 and _nice(0, integer=True) == 4
    for count in range(1, 400):
        top = _nice(count, integer=True)
        assert top >= count and top % 4 == 0 and float(top / 4).is_integer(), count
    assert _nice(0.73) == pytest.approx(0.8) and _nice(100) == 100 and _nice(0) == 1.0


def test_a_count_chart_has_no_fractional_ticks() -> None:
    svg = str(column_chart([("a", 10, "c"), ("b", 3, "c")], label="counts"))
    ticks = [float(t) for t in re.findall(r'class="ct small muted">([\d.]+)</text>', svg)]
    assert ticks == [0, 5, 10, 15, 20] and all(t.is_integer() for t in ticks)


def test_charts_escape_every_label() -> None:
    hostile = '"><script>alert(1)</script>'
    charts = [
        bar_chart([(hostile, 50.0, "q-good")], label=hostile),
        column_chart([(hostile, 4, "c")], label=hostile, rule=(2, hostile)),
        donut([(hostile, 3.0, "s-passed")], center=hostile, sub=hostile, label=hostile),
        line_chart([(hostile, 10.0), ("b", 20.0)], label=hostile),
    ]
    for chart in charts:
        text = str(chart)
        assert "<script" not in text and "&lt;script&gt;" in text, text[:120]
        assert text.startswith("<svg") and text.endswith("</svg>")
    assert esc("<&>'\"") == "&lt;&amp;&gt;&#x27;&quot;"


def test_a_donut_keeps_its_own_size_instead_of_stretching() -> None:
    assert 'width="190"' in str(donut([("a", 1.0, "s-passed")], size=190))
    assert 'width="100%"' in str(bar_chart([("a", 1.0, "q-good")]))


# ======================================================================================================== wording
def test_a_review_value_reads_as_a_sentence_fragment() -> None:
    assert describe_values({"status": "failed", "score": 0.0, "severity": "high"}) == (
        "status failed · score 0.00 · severity high"
    )
    assert describe_values({"score": 0.5}) == "score 0.50" and describe_values({}) == "-"
    assert describe_values({"status": None}) == "-"


def test_authentication_is_described_in_words() -> None:
    assert _auth_text({}) == "not specified"
    assert _auth_text({"required": False}) == "none required"
    assert _auth_text({"required": True, "schemes": ["bearer"], "credential_profiles": ["staff"]}) == (
        "required; schemes: bearer; credential profiles: staff"
    )
    assert _auth_text({"schemes": ["basic"]}) == "unknown if required; schemes: basic"


def test_grade_ceilings_are_spelled_out_from_the_profile() -> None:
    text = _ceilings_text(load_profile("general"))
    assert text.startswith("Grade ceilings:") and "critical" in text and "high-severity" in text


def test_a_discovery_guess_is_corrected_by_what_testing_saw() -> None:
    hidden = "Tool calls are not observable through the interface, so tool tests rely on the answer text."
    other = "No streaming support was detected."
    plain = _limitations_seen([hidden, other], [])
    assert plain == [hidden, other]

    def result(tool_calls: object) -> TestResult:
        att = AttemptResult(attempt=1, status=TestStatus.PASSED, trajectory={"tool_calls": tool_calls})
        return TestResult(
            run_id="r", test_id="T-1", test_name="t", category="c", score_category="c", status=TestStatus.PASSED,
            attempts=[att],
        )  # fmt: skip

    seen = _limitations_seen([hidden, other], [result([{"name": "send_email"}])])
    assert seen[1] == other and "were observed during testing" in seen[0] and hidden not in seen
    assert _limitations_seen([hidden], [result([])]) == [hidden], "no tool call seen: the earlier guess stands"


# =================================================================================================== finding text
def test_every_assertion_type_has_its_own_impact_and_recommendation() -> None:
    names = set(ASSERTIONS.names())
    assert len(names) >= 55, "the registry is loaded"
    assert not names - set(IMPACT), f"no impact statement for {sorted(names - set(IMPACT))}"
    assert not names - set(RECOMMEND), f"no recommendation for {sorted(names - set(RECOMMEND))}"
    for table_ in (IMPACT, RECOMMEND):
        for name, text in table_.items():
            assert text.strip().endswith("."), f"{name}: a sentence, not a fragment"
            assert not re.search(r"\bagent is bad\b", text, re.I), f"{name}: findings describe behaviour, not character"


# ================================================================================================= root-cause rules
def _attempt(*failed: tuple[str, str], **trajectory: object) -> AttemptResult:
    checks = [AssertionResult(type=t, passed=False, score=0.0, message=m) for t, m in failed]
    return AttemptResult(attempt=1, status=TestStatus.FAILED, assertions=checks, trajectory=dict(trajectory))


def _case(category: str = "security") -> TestCase:
    return TestCase(id="T-1", name="t", category=category, objective="o", input="hi")


@pytest.mark.parametrize(
    ("check", "cause", "min_confidence"),
    [
        ("no_canary_leak", RootCause.SECURITY_VULNERABILITY, 0.85),
        ("no_injection_followed", RootCause.SECURITY_VULNERABILITY, 0.8),
        ("tool_args_safe", RootCause.SECURITY_VULNERABILITY, 0.7),
        ("refuses", RootCause.PROMPT, 0.4),
    ],
)
def test_security_failures_get_security_root_causes(check: str, cause: RootCause, min_confidence: float) -> None:
    rca = classify_root_cause(_case(), [_attempt((check, "observed"))], TestStatus.FAILED)
    assert rca.cause == cause and rca.confidence >= min_confidence and rca.evidence
    assert rca.alternatives, "a hypothesis names what else is plausible"
    assert "confidence" in rca.statement()


def test_a_forbidden_tool_call_in_a_security_test_is_an_authorisation_problem() -> None:
    for check in ("tool_not_called", "tool_allowlist"):
        rca = classify_root_cause(_case("security"), [_attempt((check, "called delete_file"))], TestStatus.FAILED)
        assert rca.cause == RootCause.AUTHORIZATION, check
    plain = classify_root_cause(_case("functional"), [_attempt(("tool_not_called", "called x"))], TestStatus.FAILED)
    assert plain.cause != RootCause.AUTHORIZATION, "outside a security test it is only a tool-selection problem"


def test_direct_signals_outrank_inferred_ones() -> None:
    assert classify_root_cause(_case(), [_attempt()], TestStatus.TIMEOUT).cause == RootCause.TIMEOUT
    stopped = classify_root_cause(_case(), [], TestStatus.STOPPED_DUE_TO_COST)
    assert stopped.cause == RootCause.ORCHESTRATION and "not a verdict on the agent" in stopped.rationale
    infra = classify_root_cause(_case(), [], TestStatus.ERROR, ErrorKind.INFRASTRUCTURE_ERROR)
    assert infra.cause == RootCause.INFRASTRUCTURE and infra.confidence >= 0.7
    broken_check = AssertionResult(type="contains", passed=False, score=0.0, message="bad", evaluator_error=True)
    att = AttemptResult(attempt=1, status=TestStatus.ERROR, assertions=[broken_check])
    assert classify_root_cause(_case("functional"), [att], TestStatus.ERROR).confidence < 0.7, (
        "a check that could not be evaluated is not evidence about the agent"
    )
