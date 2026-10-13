"""``agentlab feedback purge``: the retention policy, applied by hand."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentlab.accounts import AccountService
from agentlab.accounts.models import Principal
from agentlab.cli.main import app
from agentlab.core.config import AccountsConfig, FeedbackConfig
from agentlab.feedback import SubmissionIn, SubmissionPatch, SubmissionService, actor_for
from agentlab.feedback.attachments import AttachmentService
from agentlab.storage.artifacts import LocalArtifactStore
from agentlab.storage.db import Database
from tests.integration.test_cli import CONFIG

runner = CliRunner()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "agentlab.yaml").write_text(
        CONFIG + "feedback:\n  retention: {attachments_days: 1}\n", encoding="utf-8"
    )
    (tmp_path / "skills").mkdir()
    monkeypatch.chdir(tmp_path)
    for var in ("AGENTLAB_CONFIG", "AGENTLAB_MASTER_KEY"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def closed_submission_with_a_file(project: Path) -> None:
    """A closed submission, closed long ago, with one file, made through the same database and store the command will open."""
    from datetime import UTC, datetime

    db = Database(f"sqlite:///{project}/lab.db")
    db.migrate()
    config = FeedbackConfig()
    svc = SubmissionService(db, config)
    accounts = AccountService(db, AccountsConfig())
    user, _ = accounts.create_user("test", "alice", role="reporter", password="correct-horse-battery-7")  # type: ignore[arg-type]
    alice = actor_for(
        Principal(kind="user", role="reporter", user_id=user.id, username="alice", display_name="alice"), config
    )
    maria = actor_for(Principal(kind="service", role="maintainer"), config)
    made = svc.create(
        alice,
        SubmissionIn(
            type="issue",
            title="A run hangs",
            description="It hangs.",
            category="Application Bug",
            issue={"impact_summary": "I wait"},  # type: ignore[arg-type]
        ),
    )
    AttachmentService(svc, LocalArtifactStore(project / "artifacts"), config).add(
        alice, made.id, filename="run.log", declared_type="text/plain", data=b"INFO started\n"
    )
    svc.patch(maria, made.id, SubmissionPatch(version=1, status="Closed"))
    from sqlalchemy import text

    with db.session() as s:
        s.execute(text("update submissions set closed_at = :t"), {"t": datetime(2026, 1, 1, tzinfo=UTC)})


def test_without_a_period_it_says_nothing_is_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "agentlab.yaml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "skills").mkdir()
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["feedback", "purge"])
    assert result.exit_code == 0, result.output
    assert "no retention period is set" in " ".join(result.output.split())


def test_a_dry_run_reports_and_changes_nothing_and_the_real_run_removes(project: Path) -> None:
    closed_submission_with_a_file(project)
    dry = runner.invoke(app, ["feedback", "purge", "--dry-run", "--json"])
    assert dry.exit_code == 0, dry.output
    report = json.loads(dry.stdout)
    assert report["dry_run"] is True and report["attachments"] == 1 and report["files_deleted"] >= 1
    assert any((project / "artifacts").rglob("*")), "nothing was removed by a dry run"

    real = runner.invoke(app, ["feedback", "purge", "--json"])
    assert real.exit_code == 0, real.output
    done = json.loads(real.stdout)
    assert done["dry_run"] is False and done["attachments"] == 1
    again = json.loads(runner.invoke(app, ["feedback", "purge", "--json"]).stdout)
    assert again["attachments"] == 0 and again["files_deleted"] == 0


def test_the_table_names_what_was_removed(project: Path) -> None:
    closed_submission_with_a_file(project)
    result = runner.invoke(app, ["feedback", "purge", "--dry-run"])
    text = " ".join(result.output.split())
    assert result.exit_code == 0 and "Would be removed" in text and "Files removed from kept submissions" in text
