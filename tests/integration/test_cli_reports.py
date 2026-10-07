"""``agentlab report``, ``compare`` and ``review`` as a user (or CI) drives them: arguments in, files and exit codes out."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentlab.cli.main import app

runner = CliRunner()

CONFIG = """\
storage:
  database_url: sqlite:///lab.db
  artifacts_dir: artifacts
  secrets_file: secrets.enc
  reports_dir: reports
security:
  sandbox: {provider: disabled}   # Docker is never assumed in tests
reporting:
  formats: [json]
skill_dirs: [skills]
"""


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "agentlab.yaml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "skills").mkdir()
    monkeypatch.chdir(tmp_path)
    for var in ("AGENTLAB_CONFIG", "AGENTLAB_MASTER_KEY"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def flat(text: str) -> str:
    """Terminal output with its line wrapping undone."""
    return " ".join(text.split())


def run(*args: str):  # noqa: ANN201 - CliRunner's Result
    return runner.invoke(app, list(args))


def test_run(behavior: str, *extra: str) -> str:
    res = run(
        "test", "--mock", behavior, "--intensity", "quick", "--no-second-wave", "--fail-on", "none", "--json", *extra
    )
    assert res.exit_code == 0, res.output + res.stderr
    return str(json.loads(res.stdout)["summary"]["run_id"])


test_run.__test__ = False  # type: ignore[attr-defined]  # a helper that pytest should not collect


@pytest.fixture
def runs(project: Path) -> tuple[str, str]:
    """A clean run and a flawed one of the same target, in that order."""
    return test_run("success", "--report", "none"), test_run("unsafe_behavior", "--report", "none")


# ================================================================================================ agentlab test
def test_the_test_command_writes_the_configured_reports(project: Path) -> None:
    res = run("test", "--mock", "success", "--intensity", "quick", "--no-second-wave", "--fail-on", "none", "-q")
    assert res.exit_code == 0, res.output
    bundles = list((project / "reports").glob("*/v1"))
    assert len(bundles) == 1 and sorted(p.name for p in bundles[0].iterdir()) == [
        "checksums.json",
        "report.json",
        "run-manifest.json",
    ], "the configured formats (here only json) and nothing else"
    assert str(bundles[0] / "report.json") in res.stdout.replace("\n", "")


def test_report_formats_can_be_chosen_or_switched_off_per_run(project: Path) -> None:
    both = run(
        "test", "--mock", "success", "--intensity", "quick", "--no-second-wave", "--fail-on", "none",
        "--report", "json,md", "--report", "html", "-q",
    )  # fmt: skip
    assert both.exit_code == 0, both.output
    only = next((project / "reports").glob("*/v1"))
    assert {p.name for p in only.iterdir()} >= {"report.json", "report.md", "report.html"}
    assert not (only / "report.pdf").exists()

    none = test_run("success", "--report", "none")
    assert not (project / "reports" / none).exists(), "--report none writes no report"
    assert run("report", "--run", none[:8], "--format", "json").exit_code == 0, "but one can still be made later"

    for bad in (["--report", "docx"], ["--report", "none,json"]):
        res = run("test", "--mock", "success", "--intensity", "quick", *bad)
        assert res.exit_code == 2 and "Traceback" not in res.output, bad


# ================================================================================================ agentlab report
def test_report_writes_all_formats_with_checksums_and_can_verify_them(runs: tuple[str, str], project: Path) -> None:
    _, bad = runs
    res = run("report", "--run", bad[:8], "--format", "all", "--json")
    assert res.exit_code == 0, res.output + res.stderr
    out = json.loads(res.stdout)
    assert out["run_id"] == bad and out["report_version"] == 1 and out["formats"] == ["html", "json", "md", "pdf"]
    folder = Path(out["directory"])
    assert folder == project / "reports" / bad / "v1"
    assert {p.name for p in folder.iterdir()} == {
        "report.json", "report.md", "report.html", "report.pdf", "run-manifest.json", "checksums.json",
    }  # fmt: skip
    assert (folder / "report.pdf").read_bytes()[:5] == b"%PDF-"
    assert json.loads((folder / "report.json").read_text(encoding="utf-8"))["run"]["run_id"] == bad

    ok = run("report", "--verify", str(folder))
    assert ok.exit_code == 0 and "matches its checksums" in flat(ok.stdout)
    (folder / "report.md").write_text("edited after the fact\n", encoding="utf-8")
    broken = run("report", "--verify", str(folder), "--json")
    assert broken.exit_code == 1
    body = json.loads(broken.stdout)
    assert body["intact"] is False and any("report.md" in p and "checksum" in p for p in body["problems"])


def test_each_report_is_a_new_version_and_the_latest_run_is_the_default(runs: tuple[str, str], project: Path) -> None:
    first = run("report", "--format", "json", "--json")
    second = run("report", "--format", "json", "--json")
    assert first.exit_code == second.exit_code == 0
    a, b = json.loads(first.stdout), json.loads(second.stdout)
    assert a["run_id"] == b["run_id"] == runs[1], "no --run: the latest finished run"
    assert (a["report_version"], b["report_version"]) == (1, 2) and a["directory"] != b["directory"]
    assert run("report", "--format", "json", "--run", runs[0][:8], "--output", a["directory"]).exit_code == 2, (
        "an existing bundle is never overwritten"
    )


def test_report_rejects_bad_input_with_exit_code_2(project: Path, runs: tuple[str, str]) -> None:
    for args in (["--run", "zzzzzzzz"], ["--format", "docx"], ["--baseline", "zzzzzzzz"], ["--verify", "nowhere"]):
        res = run("report", *args)
        assert res.exit_code in (1, 2) and "Traceback" not in res.output, args
    assert run("report", "--run", "zzzzzzzz").exit_code == 2
    assert run("report", "--format", "docx").exit_code == 2


def test_report_with_no_run_yet_says_what_to_do(project: Path) -> None:
    res = run("report")
    assert res.exit_code == 2 and "agentlab test" in res.stderr


def test_report_with_a_baseline_includes_the_regression_section(runs: tuple[str, str], project: Path) -> None:
    clean, bad = runs
    res = run("report", "--run", bad[:8], "--baseline", clean[:8], "--format", "md", "--json")
    assert res.exit_code == 0, res.output + res.stderr
    markdown = Path(json.loads(res.stdout)["files"]["md"]).read_text(encoding="utf-8")
    assert "## 22. Regression comparison" in markdown and "New failures" in markdown


# ================================================================================================ agentlab compare
def test_compare_exit_codes_tell_ci_what_happened(runs: tuple[str, str], project: Path) -> None:
    clean, bad = runs
    worse = run("compare", clean[:8], bad[:8], "--fail-on-regression")
    assert worse.exit_code == 1, "run B regressed: the exit code says so"
    assert "New failures" in worse.stdout and "Compatibility" in worse.stdout
    better = run("compare", bad[:8], clean[:8], "--fail-on-regression")
    assert better.exit_code == 0 and "Resolved failures" in better.stdout
    assert run("compare", clean[:8], bad[:8]).exit_code == 0, "without --fail-on-regression it only reports"
    same = run("compare", clean[:8], clean[:8], "--fail-on-regression")
    assert same.exit_code == 3, "a run compared with itself is inconclusive, never a pass"
    assert run("compare", clean[:8], "zzzzzzzz").exit_code == 2


def test_compare_can_write_markdown_html_and_json(runs: tuple[str, str], project: Path) -> None:
    clean, bad = runs
    for fmt, marker in (
        ("md", "# Regression comparison"),
        ("html", "<!doctype html"),
        ("json", '"agentlab.comparison"'),
    ):
        target = project / f"cmp.{fmt}"
        res = run("compare", clean[:8], bad[:8], "--format", fmt, "--output", str(target))
        assert res.exit_code == 0, res.output + res.stderr
        assert marker.lower() in target.read_text(encoding="utf-8").lower()
    printed = run("compare", clean[:8], bad[:8], "--json")
    assert json.loads(printed.stdout)["verdict"] == "regressed"
    assert run("compare", clean[:8], bad[:8], "--format", "pdf").exit_code == 2


# ================================================================================================= agentlab review
def test_review_records_beside_the_original_and_shows_in_the_next_report(runs: tuple[str, str], project: Path) -> None:
    _, bad = runs
    shown = json.loads(run("runs", "show", bad[:8], "--json").stdout)
    test_id = next(r["test_id"] for r in shown["results"] if r["status"] == "failed")

    ok = run(
        "review", "result", bad[:8], test_id, "--decision", "false_positive", "--reviewer", "Dana",
        "--reason", "the check was too strict",
    )  # fmt: skip
    assert ok.exit_code == 0 and "original evaluation is unchanged" in flat(ok.stdout)
    again = json.loads(run("runs", "show", bad[:8], "--json").stdout)
    assert {r["test_id"]: r["status"] for r in again["results"]}[test_id] == "failed"

    listing = run("review", "list", bad[:8], "--json")
    rows = json.loads(listing.stdout)
    assert len(rows) == 1 and rows[0]["reviewer"] == "Dana" and rows[0]["original"]["status"] == "failed"
    assert test_id in run("review", "list", bad[:8]).stdout

    report = run("report", "--run", bad[:8], "--format", "json", "--json")
    data = json.loads(Path(json.loads(report.stdout)["files"]["json"]).read_text(encoding="utf-8"))
    assert data["reviewed"] is True and data["reviews"][0]["reason"] == "the check was too strict"

    finding = data["findings"][0]
    done = run(
        "review", "finding", bad[:8], finding["test_id"], "--decision", "change_severity", "--severity", "low",
        "--reviewer", "Dana", "--reason", "needs a login first",
    )  # fmt: skip
    assert done.exit_code == 0, done.output + done.stderr


def test_review_refuses_what_it_cannot_do_with_exit_code_2(runs: tuple[str, str], project: Path) -> None:
    _, bad = runs
    shown = json.loads(run("runs", "show", bad[:8], "--json").stdout)
    test_id = next(r["test_id"] for r in shown["results"] if r["status"] == "failed")
    cases = [
        ["result", bad[:8], test_id, "--decision", "false_positive", "--reviewer", "Dana"],  # no reason
        ["result", bad[:8], test_id, "--decision", "comment", "--reviewer", ""],  # nobody
        ["result", bad[:8], "NOPE-001", "--decision", "comment", "--reviewer", "Dana", "--comment", "x"],
        ["result", bad[:8], test_id, "--decision", "agree", "--reviewer", "Dana"],
        ["finding", bad[:8], test_id, "--decision", "override_score", "--reviewer", "Dana", "--reason", "x"],
        ["result", "zzzzzzzz", test_id, "--decision", "comment", "--reviewer", "Dana", "--comment", "x"],
    ]
    for args in cases:
        res = run("review", *args)
        assert res.exit_code == 2 and "Traceback" not in res.output, args
    assert json.loads(run("review", "list", bad[:8], "--json").stdout) == [], "a refused review leaves nothing behind"
    assert "no reviews" in run("review", "list", bad[:8]).stdout


def test_the_new_commands_are_listed_in_the_help(project: Path) -> None:
    out = run("--help").stdout
    for cmd in ("report", "compare", "review"):
        assert cmd in out


# ================================================================================================ plug-in formats
CSV_PLUGIN = """\
from agentlab.reporting.renderers import REPORT_RENDERERS, ReportRenderer


class CsvSummary(ReportRenderer):
    file_name = "report.summary.csv"
    media_type = "text/csv"

    def render(self, report, context):
        rows = ["test_id,status", *(f"{r.test_id},{r.status}" for r in report.results)]
        return ("\\n".join(rows) + "\\n").encode()


REPORT_RENDERERS.register("csv-summary", CsvSummary, replace=True)
"""


@pytest.fixture
def csv_plugin(project: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project whose configuration lists a plug-in module (``plugins:``) that adds a report format."""
    from agentlab.reporting.renderers import REPORT_RENDERERS

    (project / "csv_summary_plugin.py").write_text(CSV_PLUGIN, encoding="utf-8")
    with (project / "agentlab.yaml").open("a", encoding="utf-8") as fh:
        fh.write("plugins: [csv_summary_plugin]\n")
    monkeypatch.syspath_prepend(str(project))
    monkeypatch.delitem(sys.modules, "csv_summary_plugin", raising=False)
    yield project
    REPORT_RENDERERS.unregister("csv-summary")


def test_a_format_a_plug_in_module_adds_can_be_asked_for_by_name(csv_plugin: Path) -> None:
    res = run("test", "--mock", "success", "--intensity", "quick", "--no-second-wave", "--fail-on", "none", "-q",
              "--report", "json,csv-summary")  # fmt: skip
    assert res.exit_code == 0, res.output + res.stderr
    bundle = next((csv_plugin / "reports").glob("*/v1"))
    assert sorted(p.name for p in bundle.iterdir()) == [
        "checksums.json", "report.json", "report.summary.csv", "run-manifest.json",
    ]  # fmt: skip
    rows = (bundle / "report.summary.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0] == "test_id,status" and len(rows) > 5

    later = run("report", "--run", bundle.parent.name[:8], "--format", "csv-summary", "--json")
    assert later.exit_code == 0, later.output + later.stderr
    assert json.loads(later.stdout)["formats"] == ["csv-summary"]


def test_a_format_nobody_provides_is_refused_with_the_names_that_exist(project: Path) -> None:
    res = run("test", "--mock", "success", "--intensity", "quick", "--report", "csv-summary")
    assert res.exit_code == 2 and "Traceback" not in res.output
    assert "unknown report format 'csv-summary' (use json, md, html, pdf or all)" in flat(res.output + res.stderr)
