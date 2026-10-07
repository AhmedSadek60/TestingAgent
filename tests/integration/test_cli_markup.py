"""Text that comes from outside reaches the terminal as text.

The CLI colours its output with rich markup, and a finding's title, a test's name, a reviewer's note or a skill's title can
hold anything, including ``[/]`` (a closing tag with nothing to close, which rich refuses) and ``[bold]`` (which would restyle
what follows). A target that can crash or repaint the evaluator's console controls a little of the evaluator. Every command
that prints such text is run here with hostile text in it, and the text must come out exactly as it went in.

The first test is the regression that found this: a run in which a test was stopped by a limit crashed with ``MarkupError`` as
it printed its summary, because ``stopped_due_to_step_limit`` had no style and the empty style left a ``[/]`` behind.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agentlab.cli.main import app
from agentlab.cli.markup import esc, short, styled
from agentlab.cli.render import STATUS_STYLE
from agentlab.core.enums import RunStatus, TestStatus

runner = CliRunner()

CONFIG = """\
storage:
  database_url: sqlite:///lab.db
  artifacts_dir: artifacts
  secrets_file: secrets.enc
security:
  sandbox: {provider: disabled}
reporting:
  formats: [json]
skill_dirs: [skills]
"""

HOSTILE = "[/][bold]boom[/bold][link=https://evil.example]x[/link][red]\\"
RUN_ARGS = (
    "--mock",
    "success",
    "--skills",
    "agent-fingerprinting",
    "--suite",
    "functional",
    "--no-judge",
    "--report",
    "none",
)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "agentlab.yaml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "skills").mkdir()
    monkeypatch.chdir(tmp_path)
    for var in ("AGENTLAB_CONFIG", "AGENTLAB_MASTER_KEY"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def run(*args: str):  # noqa: ANN201
    """The command as a person runs it, on a terminal wide enough that nothing is wrapped in the middle of the text."""
    return runner.invoke(app, list(args), env={"COLUMNS": "400"})


def clean(result) -> str:  # noqa: ANN001
    assert result.exception is None or isinstance(result.exception, SystemExit), repr(result.exception)
    out = result.output
    assert "MarkupError" not in out and "Traceback" not in out, out
    return out


def write_tests(project: Path, tests: list[dict]) -> Path:
    path = project / "mytests.yaml"
    path.write_text(yaml.safe_dump({"tests": tests}), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------------------------------- the regression
def test_every_status_a_test_can_end_in_has_a_style() -> None:
    needs_style = {s.value for s in TestStatus} - {"draft", "ready", "running"}
    assert needs_style <= set(STATUS_STYLE), sorted(needs_style - set(STATUS_STYLE))
    assert {s.value for s in RunStatus if s.value.startswith("stopped")} <= set(STATUS_STYLE) | {"stopped_due_to_cost"}


def test_a_run_with_a_test_stopped_by_a_limit_prints_its_summary(project: Path) -> None:
    tests = write_tests(
        project,
        [
            {
                "name": "Three turns, a budget of one step",
                "turns": [{"input": "a"}, {"input": "b"}, {"input": "c"}],
                "max_steps": 1,
            }
        ],
    )
    result = run("test", *RUN_ARGS, "--tests", str(tests))
    out = clean(result)
    assert re.search(r"^stopped_due_to_step_limit 1$", out, re.M), out
    assert "were stopped by a configured limit and are not scored" in out
    assert result.exit_code == 4, "nothing was scored, so there is no verdict"


# ---------------------------------------------------------------------------------------------- hostile text, end to end
def test_hostile_text_is_shown_as_text_by_every_command(project: Path) -> None:
    tests = write_tests(
        project,
        [
            {
                "name": HOSTILE,
                "input": "Hello",
                "must_contain": [HOSTILE],
                "severity_on_failure": "high",
                "objective": HOSTILE,
            }
        ],
    )
    result = run("test", *RUN_ARGS, "--name", HOSTILE, "--description", HOSTILE, "--tests", str(tests), "--show-tests")
    out = clean(result)
    assert result.exit_code == 1, out  # the finding is high, so the run exits with 1
    assert "[/][bold]boom[/bold]" in out, "the finding and the target name are shown as they were written"

    listed = json.loads(run("runs", "list", "--json").stdout)
    run_id = str(listed[0]["id"])
    results = json.loads(run("runs", "show", run_id, "--json").stdout)["results"]
    test_id = next(r["test_id"] for r in results if r["test_id"].startswith("USER-"))

    for args in (
        ("runs", "list"),
        ("runs", "show", run_id),
        ("runs", "show", run_id, "--tests"),
        ("runs", "show", run_id, "--finding", test_id),
        ("runs", "plan", run_id),
        ("runs", "plan", run_id, "--detail"),
    ):
        text = clean(run(*args))
        assert text.strip(), args
    assert "[/][bold]boom[/bold]" in clean(run("runs", "show", run_id, "--finding", test_id))
    assert "[/][bold]boom[/bold]" in clean(run("runs", "show", run_id, "--tests"))
    assert "[/][bold]boom[/bold]" in clean(run("runs", "list"))

    reviewed = run(
        "review", "result", run_id, test_id, "--decision", "comment", "--reviewer", HOSTILE, "--comment", HOSTILE
    )
    assert "[/][bold]boom[/bold]" in clean(reviewed) and reviewed.exit_code == 0
    assert "[/][bold]boom[/bold]" in clean(run("review", "list", run_id))

    second = run("test", *RUN_ARGS, "--name", HOSTILE, "--tests", str(tests))
    clean(second)
    both = [str(r["id"]) for r in json.loads(run("runs", "list", "--json").stdout)]
    assert len(both) == 2
    assert clean(run("compare", both[1], both[0])).strip()
    assert clean(run("report", "--run", run_id, "--format", "json")).strip()


def test_a_skill_with_hostile_text_in_its_manifest_is_listed_and_shown(project: Path) -> None:
    created = run("skills", "new", "hostile-skill")
    assert created.exit_code == 0, clean(created)
    manifest = project / "skills" / "hostile-skill" / "skill.yaml"
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    data["title"], data["description"] = HOSTILE, HOSTILE
    data["limitations"] = [HOSTILE]
    manifest.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert "[/][bold]boom[/bold]" in clean(run("skills", "list"))
    shown = clean(run("skills", "show", "hostile-skill"))
    assert shown.count("[/][bold]boom[/bold]") >= 3, shown


def test_an_error_that_quotes_text_prints_the_text(project: Path) -> None:
    result = run("runs", "show", HOSTILE)
    out = clean(result)
    assert result.exit_code != 0 and "boom" in out


# ----------------------------------------------------------------------------------------------------- the helpers
def test_esc_styled_and_short_keep_the_text_and_never_leave_a_dangling_tag() -> None:
    from rich.console import Console

    console = Console(width=400, record=True, force_terminal=False)
    for text in (HOSTILE, "[/]", "[", "]", "\\", "[/\\", "ordinary text", "[bold]" * 30):
        for markup in (
            esc(text),
            styled(text, "red"),
            styled(text, None),
            styled(text, ""),
            short(text, 12),
            short(text),
        ):
            console.print(markup)
    out = console.export_text()
    assert "[/][bold]boom[/bold]" in out
    assert styled("x", None) == "x" == styled("x", "")
    assert short("a\nb", 10) == "a b"
    assert esc("[red]") == "\\[red]"


def test_the_profile_and_the_plan_of_a_hostile_target_are_shown_as_text() -> None:
    """What discovery reads from a target (its tools, their descriptions, its frameworks, evidence of its type) and what the
    planner writes about it (notes, warnings, why a skill was chosen) can quote the target."""
    from rich.console import Console

    from agentlab.cli.render import render_plan, render_profile
    from agentlab.core.enums import AgentType, Support
    from agentlab.core.models import AgentProfile
    from agentlab.core.models.profile import CapabilityEntry, Evidence, ToolInfo, TypeScore
    from agentlab.design.models import BudgetEstimate, CoverageEntry, PlanWarning, TestPlan
    from agentlab.skills.model import SkillMatch

    profile = AgentProfile(
        target_name=HOSTILE,
        summary=HOSTILE,
        types=[TypeScore(type=AgentType.CHATBOT, confidence=0.7, evidence=[Evidence(source=HOSTILE, detail=HOSTILE)])],
        interfaces=[HOSTILE],
        tools=[ToolInfo(name=HOSTILE, description=HOSTILE, side_effects=HOSTILE)],
        models=[HOSTILE],
        frameworks=[HOSTILE],
        limitations=[HOSTILE],
        attack_surfaces=[HOSTILE],
        capability_matrix=[CapabilityEntry(capability=HOSTILE, detected=True, testable=Support.SUPPORTED)],
    )
    plan = TestPlan(
        target=HOSTILE,
        summary=HOSTILE,
        skills=[SkillMatch(skill=HOSTILE, version=HOSTILE, selected=True, reasons=[HOSTILE])],
        coverage=[CoverageEntry(key=HOSTILE, name=HOSTILE, status="not_covered", note=HOSTILE)],
        budget=BudgetEstimate(cost_note=HOSTILE),
        warnings=[PlanWarning(level="warning", code="x", message=HOSTILE)],
        plan_hash=HOSTILE,
    )
    console = Console(width=400, record=True, force_terminal=False)
    render_profile(console, profile, warnings=[HOSTILE])
    render_plan(console, plan)
    out = console.export_text()
    assert out.count("[/][bold]boom[/bold]") >= 12, out
