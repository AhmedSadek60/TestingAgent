"""``agentlab plugins list``: what is installed, by kind, and where each plug-in is defined."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentlab.cli.main import app
from agentlab.registries import all_registries

runner = CliRunner()

PLUGIN = """\
from agentlab.evaluation.assertions import ASSERTIONS
from agentlab.reporting.renderers import REPORT_RENDERERS, ReportRenderer


def always_true(params, ctx):
    raise NotImplementedError


class Nothing(ReportRenderer):
    file_name = "report.nothing.txt"
    media_type = "text/plain"

    def render(self, report, context):
        return b""


ASSERTIONS.register("plugin_always_true", always_true, replace=True)
REPORT_RENDERERS.register("nothing", Nothing, replace=True)
"""


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    (tmp_path / "agentlab.yaml").write_text("plugins: [listing_plugin]\n", encoding="utf-8")
    (tmp_path / "listing_plugin.py").write_text(PLUGIN, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "listing_plugin", raising=False)
    for var in ("AGENTLAB_CONFIG", "AGENTLAB_MASTER_KEY"):
        monkeypatch.delenv(var, raising=False)
    yield tmp_path
    registries = all_registries()
    registries["assertions"].unregister("plugin_always_true")
    registries["report_renderers"].unregister("nothing")


def flat(text: str) -> str:
    return " ".join(text.split())


def test_the_listing_counts_what_ships_and_names_what_a_module_added(project: Path) -> None:
    res = runner.invoke(app, ["plugins", "list", "--json"])
    assert res.exit_code == 0, res.output + res.stderr
    listing = json.loads(res.stdout)
    assert list(listing) == list(all_registries()), "every kind, in a fixed order"
    added = {kind: [r for r in rows if not r["builtin"]] for kind, rows in listing.items()}
    assert {k: [r["name"] for r in v] for k, v in added.items() if v} == {
        "assertions": ["plugin_always_true"],
        "report_renderers": ["nothing"],
    }
    assert added["assertions"][0]["module"] == "listing_plugin", "it says where the plug-in is defined"
    assert {r["name"] for r in listing["report_renderers"] if r["builtin"]} == {"json", "md", "html", "pdf"}

    table = runner.invoke(app, ["plugins", "list"])
    text = flat(table.stdout)
    assert table.exit_code == 0 and "plugin_always_true (listing_plugin)" in text and "nothing (listing_plugin)" in text
    everything = flat(runner.invoke(app, ["plugins", "list", "report_renderers", "--all"]).stdout)
    assert "json, md, html, pdf" not in everything and "html" in everything and "nothing (listing_plugin)" in everything


def test_one_kind_can_be_asked_for_and_an_unknown_one_is_refused(project: Path) -> None:
    only = json.loads(runner.invoke(app, ["plugins", "list", "sandbox", "--json"]).stdout)
    assert list(only) == ["sandbox"] and {r["name"] for r in only["sandbox"]} == {"docker", "disabled"}
    bad = runner.invoke(app, ["plugins", "list", "widgets"])
    assert bad.exit_code == 2 and "unknown kind 'widgets' (use providers, adapters" in flat(bad.stdout + bad.stderr)


def test_a_module_that_cannot_be_imported_is_a_warning_and_the_rest_is_still_listed(project: Path) -> None:
    (project / "agentlab.yaml").write_text("plugins: [listing_plugin, not_a_module_anywhere]\n", encoding="utf-8")
    res = runner.invoke(app, ["plugins", "list", "--json"])
    assert res.exit_code == 0, res.output + res.stderr
    assert "plug-in module 'not_a_module_anywhere' could not be imported" in flat(res.stderr)
    assert [r["name"] for r in json.loads(res.stdout)["report_renderers"] if not r["builtin"]] == ["nothing"]
