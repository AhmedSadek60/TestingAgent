"""``agentlab users``: the way the first administrator is made, on the server, without a password in a file or a command line."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentlab.cli.main import app
from tests.integration.test_cli import CONFIG

runner = CliRunner()
PASSWORD = "correct-horse-battery-7"  # made up


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "agentlab.yaml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "skills").mkdir()
    monkeypatch.chdir(tmp_path)
    for var in ("AGENTLAB_CONFIG", "AGENTLAB_MASTER_KEY"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def run(*args: str, input: str | None = None):  # noqa: A002
    return runner.invoke(app, list(args), input=input)


def test_the_first_administrator_is_made_with_a_password_that_is_not_on_the_command_line(project: Path) -> None:
    empty = run("users", "list")
    assert empty.exit_code == 0 and "no accounts yet" in " ".join(empty.output.split())
    made = run("users", "create", "alice", "--role", "admin", "--stdin", input=PASSWORD + "\n")
    assert made.exit_code == 0, made.output
    assert PASSWORD not in made.output
    listed = run("users", "list", "--json")
    rows = json.loads(listed.stdout)
    assert rows == [{"username": "alice", "role": "admin", "status": "active", "last_login_at": None}]
    assert PASSWORD not in listed.output
    assert PASSWORD.encode() not in (project / "lab.db").read_bytes(), "only a hash is stored"


def test_a_weak_password_a_taken_name_and_a_missing_terminal_are_refused(project: Path) -> None:
    weak = run("users", "create", "bob", "--stdin", input="short\n")
    assert weak.exit_code == 2 and "not accepted" in " ".join(weak.output.split() + weak.stderr.split())
    assert run("users", "create", "bob", "--stdin", input=PASSWORD + "\n").exit_code == 0
    taken = run("users", "create", "BOB", "--stdin", input=PASSWORD + "\n")
    assert taken.exit_code != 0
    no_tty = run("users", "create", "carol")  # no --stdin and no terminal
    assert no_tty.exit_code == 2 and "--stdin" in " ".join(no_tty.stderr.split() + no_tty.output.split())
    bad_role = run("users", "create", "dave", "--role", "root", "--stdin", input=PASSWORD + "\n")
    assert bad_role.exit_code == 2


def test_roles_status_locks_and_resets_are_managed_on_the_server(project: Path) -> None:
    run("users", "create", "root", "--role", "admin", "--stdin", input=PASSWORD + "\n")
    run("users", "create", "erin", "--stdin", input=PASSWORD + "\n")
    assert run("users", "set-role", "erin", "maintainer").exit_code == 0
    assert run("users", "disable", "erin").exit_code == 0
    status = {r["username"]: r for r in json.loads(run("users", "list", "--json").stdout)}
    assert status["erin"]["role"] == "maintainer" and status["erin"]["status"] == "disabled"
    assert run("users", "enable", "erin").exit_code == 0
    assert run("users", "unlock", "erin").exit_code == 0
    reset = run("users", "reset-password", "erin")
    assert reset.exit_code == 0 and "temporary password for" in " ".join(reset.output.split())
    last = run("users", "disable", "root")
    assert last.exit_code != 0 and "last active administrator" in " ".join(last.output.split() + last.stderr.split())
    missing = run("users", "disable", "nobody-by-that-name")
    assert missing.exit_code == 2
