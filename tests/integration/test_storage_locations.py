"""Everything AgentLab keeps is written where ``storage`` says, and nowhere next to the configuration file.

The container image keeps its configuration in ``/etc/agentlab``, which is read-only there. Relative paths are relative to
the folder of the configuration file, so before ``work_dir`` and ``uploads_dir`` could be set, the API refused to start
(it wanted ``/etc/agentlab/.agentlab/uploads``) and a run would have failed the same way.
"""

from __future__ import annotations

from pathlib import Path

import httpx
from typer.testing import CliRunner

from agentlab.api.app import create_app
from agentlab.cli.main import app as cli
from agentlab.core.config import AgentLabConfig, StorageConfig
from agentlab.core.models import TargetSpec
from agentlab.orchestrator import RunOptions, TestOrchestratorAgent
from agentlab.services import Services
from tests.support.api import SMALL_RUN, mock_target
from tests.support.lab import make_config


def everything_under(data: Path) -> AgentLabConfig:
    return make_config(
        data,
        storage=StorageConfig(
            database_url=f"sqlite:///{data}/agentlab.db",
            artifacts_dir=str(data / "artifacts"),
            secrets_file=str(data / "secrets.enc"),
            reports_dir=str(data / "reports"),
            work_dir=str(data / "work"),
            uploads_dir=str(data / "uploads"),
            skill_drafts_dir=str(data / "skills" / "drafts"),
        ),
        formats=["json", "md"],
    )


def folders(tmp_path: Path) -> tuple[Path, Path]:
    """A configuration folder that is left empty (read-only in the image) and the folder the data goes to."""
    etc, data = tmp_path / "etc", tmp_path / "data"
    etc.mkdir()
    return etc, data


def test_the_folders_default_to_a_hidden_folder_next_to_the_configuration() -> None:
    storage = StorageConfig()
    assert (storage.work_dir, storage.uploads_dir, storage.skill_drafts_dir) == (
        ".agentlab/work",
        ".agentlab/uploads",
        ".agentlab/skills/drafts",
    )


def test_a_relative_setting_is_relative_to_the_configuration_and_an_absolute_one_is_used_as_it_is(
    tmp_path: Path,
) -> None:
    etc, data = folders(tmp_path)
    relative = Services.create(
        make_config(
            tmp_path, storage=StorageConfig(work_dir="w", uploads_dir="u", database_url=f"sqlite:///{data}/a.db")
        ),
        base_dir=etc,
        migrate=False,
    )
    assert relative.workdir("r1") == etc / "w" / "r1"
    assert relative.uploads_dir == etc / "u"
    absolute = Services.create(everything_under(data), base_dir=etc, migrate=False)
    assert absolute.workdir("r1") == data / "work" / "r1"
    assert absolute.uploads_dir == data / "uploads"


async def test_a_run_leaves_the_configuration_folder_as_it_was(tmp_path: Path) -> None:
    etc, data = folders(tmp_path)
    services = Services.create(everything_under(data), base_dir=etc)
    try:
        options = RunOptions(**SMALL_RUN, keep_workspace=True)  # the work folder is removed after a run unless kept
        outcome = await TestOrchestratorAgent(services).run(TargetSpec(**mock_target()), options)
    finally:
        await services.aclose()
    assert outcome.status.value == "completed", outcome.error
    assert list(etc.iterdir()) == [], "something was written next to the configuration"
    assert (data / "work" / outcome.run_id).is_dir()
    assert (data / "agentlab.db").is_file() and any((data / "reports").rglob("*.json"))


async def test_the_api_keeps_what_is_uploaded_where_storage_says(tmp_path: Path) -> None:
    etc, data = folders(tmp_path)
    services = Services.create(everything_under(data), base_dir=etc)
    app = create_app(services, start_worker=False, allowed_hosts=("testserver",), serve_ui=False)
    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                sent = await client.post("/documents", files={"file": ("policy.md", b"# Policy\n\nTwenty days.\n")})
            assert sent.status_code in (200, 201), sent.text
    finally:
        services.store.db.dispose()
    assert any((data / "uploads").rglob("policy.md")), "the upload is not under storage.uploads_dir"
    assert list(etc.iterdir()) == []


def test_imported_skills_wait_in_the_configured_drafts_folder(tmp_path: Path) -> None:
    etc, data = folders(tmp_path)
    config = etc / "agentlab.yaml"
    config.write_text(
        f"providers: [mock]\nstorage:\n  database_url: sqlite:///{data}/agentlab.db\n  skill_drafts_dir: {data}/drafts\n",
        encoding="utf-8",
    )
    source = tmp_path / "SKILL.md"
    source.write_text("# Greeter\n\n## When to use\n\nWhen an agent greets people.\n", encoding="utf-8")
    result = CliRunner().invoke(cli, ["--config", str(config), "skills", "import", str(source), "--name", "greeter"])
    assert result.exit_code == 0, result.output
    assert any((data / "drafts").rglob("*")), "the draft is not in storage.skill_drafts_dir"
    assert sorted(p.name for p in etc.iterdir()) == ["agentlab.yaml"], "a draft was written next to the configuration"
