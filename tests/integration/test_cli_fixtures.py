"""``agentlab fixtures``: list the example agents, print the target file that goes with one, serve one, and prove that
AgentLab finds what was planted in them."""

from __future__ import annotations

import json
import signal
from pathlib import Path

import httpx
import pytest
import yaml
from typer.testing import CliRunner

from agentlab.cli.main import app
from agentlab.core.models.target import TargetSpec
from agentlab.fixtures import REGISTRY, fixture_class
from tests.support.cli import said
from tests.support.process import free_port, start_agentlab, wait_until_up

runner = CliRunner()


def services() -> list[str]:
    return [kind for kind in sorted(REGISTRY) if fixture_class(kind).build("correct").transport != "command"]


# ====================================================================================================== list, target
def test_list_names_every_kind_with_its_defects_and_the_json_says_the_same() -> None:
    plain = runner.invoke(app, ["fixtures", "list", "--defects"])
    assert plain.exit_code == 0
    listed = json.loads(runner.invoke(app, ["fixtures", "list", "--json"]).stdout)["fixtures"]
    assert {row["kind"] for row in listed} == set(REGISTRY)
    for row in listed:
        assert row["defects"], f"{row['kind']} plants nothing, so it proves nothing"
        for defect in row["defects"]:
            assert defect in said(plain), f"--defects does not list {defect}"


def test_the_target_printed_for_a_service_is_a_valid_target_that_points_where_it_was_told() -> None:
    assert services(), "there are no service fixtures"
    for kind in services():
        res = runner.invoke(app, ["fixtures", "target", kind, "--url", "http://127.0.0.1:9123"])
        assert res.exit_code == 0, (kind, res.output)
        target = TargetSpec.model_validate(yaml.safe_load(res.stdout))
        assert "127.0.0.1:9123" in res.stdout, f"{kind}: the target does not use the URL it was given"
        assert target.safety.disposable_environment, f"{kind}: a fixture is always a disposable environment"


def test_a_command_line_fixture_is_written_to_a_folder_and_not_served(tmp_path: Path) -> None:
    bare = runner.invoke(app, ["fixtures", "target", "coding"])
    assert bare.exit_code == 2 and "--dir" in said(bare)
    written = runner.invoke(app, ["fixtures", "target", "coding", "--dir", str(tmp_path / "repair-bot")])
    assert written.exit_code == 0, written.output
    assert any((tmp_path / "repair-bot").iterdir()), "nothing was written to the folder"
    assert TargetSpec.model_validate(yaml.safe_load(written.stdout)).command is not None
    served = runner.invoke(app, ["fixtures", "serve", "coding"])
    assert served.exit_code == 2 and "command-line agent" in said(served)


def test_an_unknown_kind_or_variant_is_refused_in_words() -> None:
    for args in (["target", "no-such-kind"], ["serve", "no-such-kind"], ["verify", "no-such-kind"]):
        res = runner.invoke(app, ["fixtures", *args])
        assert res.exit_code != 0 and "Traceback" not in res.output, (args, res.output)
    unknown = runner.invoke(app, ["fixtures", "verify", "no-such-kind"])
    assert unknown.exit_code == 2 and "available:" in said(unknown)


# ============================================================================================================ serve
def test_serve_runs_a_fixture_as_a_service_that_answers_and_stops_on_a_signal(tmp_path: Path) -> None:
    port = free_port()
    proc = start_agentlab(["fixtures", "serve", "chatbot", "--port", str(port)], cwd=tmp_path)
    try:
        wait_until_up(f"http://127.0.0.1:{port}/openapi.json", proc)
        reply = httpx.post(f"http://127.0.0.1:{port}/chat", json={"message": "Hello", "session_id": "s1"}, timeout=10)
        assert reply.status_code == 200 and reply.json()["reply"]
    finally:
        code = proc.stop(signal.SIGTERM)
    # uvicorn shuts the server down, then re-raises the signal so the process ends the way a signal ends it
    assert code in (0, -signal.SIGTERM), proc.output
    assert "Ctrl-C stops it" in proc.output and "Traceback" not in proc.output


def test_serve_warns_before_listening_beyond_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixtures are flawed on purpose, so serving one to the network is said out loud (here it is not done)."""
    from agentlab.cli import cmd_fixtures

    served: list[str] = []
    monkeypatch.setattr(cmd_fixtures, "serve_forever", lambda *args, **kwargs: served.append(kwargs["host"]))
    res = runner.invoke(app, ["fixtures", "serve", "chatbot", "--host", "192.0.2.1"])
    assert res.exit_code == 0 and served == ["192.0.2.1"]
    assert "not loopback" in said(res) and "must not be exposed" in said(res)


# =========================================================================================================== verify
def test_verify_proves_one_defect_of_one_kind_and_says_so_as_json() -> None:
    res = runner.invoke(
        app, ["fixtures", "verify", "memory", "--defect", "forgets_context", "--skip-all-defects", "-w", "2", "--json"]
    )
    assert res.exit_code == 0, res.output
    report = json.loads(res.stdout)
    assert report["ok"] is True and report["skipped"] == []
    (kind,) = report["fixtures"]
    assert kind["kind"] == "memory"
    names = [check["name"] for check in kind["checks"]]
    assert any("correct build" in name for name in names) and any("forgets_context" in name for name in names)
    assert not any("every defect" in name or "flawed" in name for name in names), "--skip-all-defects was ignored"


def test_verify_names_the_defects_of_a_kind_instead_of_failing_on_one_it_does_not_have() -> None:
    res = runner.invoke(app, ["fixtures", "verify", "memory", "--defect", "no_such_defect"])
    assert res.exit_code == 2 and "Traceback" not in res.output
    assert "no defect no_such_defect" in said(res) and "forgets_context" in said(res)
