"""Loading the configuration: what is accepted, what is refused, and that the refusal says why."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentlab.core.config import AgentLabConfig
from agentlab.core.errors import UserError


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "agentlab.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_no_file_means_the_defaults_with_the_offline_provider_only() -> None:
    config = AgentLabConfig()
    assert [p.name for p in config.providers] == ["mock"]
    assert config.security.sandbox_required is True
    assert config.server.host == "127.0.0.1"
    assert config.queue.backend == "inline"


def test_a_file_overrides_only_what_it_names(tmp_path: Path) -> None:
    config = AgentLabConfig.load(write(tmp_path, "limits:\n  max_cost_usd: 2.5\nserver:\n  port: 9000\n"))
    assert config.limits.max_cost_usd == 2.5
    assert config.server.port == 9000
    assert config.limits.max_steps == 100, "what the file does not say keeps its default"


def test_a_provider_may_be_named_by_type_alone(tmp_path: Path) -> None:
    config = AgentLabConfig.load(write(tmp_path, "providers: [mock, ollama]\n"))
    assert [(p.name, p.type) for p in config.providers] == [("mock", "mock"), ("ollama", "ollama")]


def test_a_missing_file_and_broken_yaml_are_said_plainly(tmp_path: Path) -> None:
    with pytest.raises(UserError, match="config file not found"):
        AgentLabConfig.load(tmp_path / "nope.yaml")
    with pytest.raises(UserError, match="invalid YAML"):
        AgentLabConfig.load(write(tmp_path, "limits: [unclosed\n"))


def test_a_wrong_value_names_the_file_and_the_key(tmp_path: Path) -> None:
    with pytest.raises(UserError, match=r"(?s)invalid configuration in .*agentlab\.yaml.*server\.port"):
        AgentLabConfig.load(write(tmp_path, "server:\n  port: 99999\n"))


def test_the_environment_names_the_file_when_the_flag_does_not(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTLAB_CONFIG", str(write(tmp_path, "limits:\n  max_steps: 7\n")))
    assert AgentLabConfig.load().limits.max_steps == 7


def test_only_chromium_can_be_asked_for_because_nothing_else_was_verified(tmp_path: Path) -> None:
    assert AgentLabConfig.load(write(tmp_path, "browser:\n  browsers: [chromium]\n")).browser.browsers == ["chromium"]
    for other in ("firefox", "webkit"):
        with pytest.raises(UserError, match=rf"{other} is not supported in this build.*Chromium only"):
            AgentLabConfig.load(write(tmp_path, f"browser:\n  browsers: [chromium, {other}]\n"))


def test_a_provider_key_is_a_reference_and_a_dump_shows_the_reference_only(tmp_path: Path) -> None:
    text = "providers:\n  - name: gem\n    type: gemini\n    api_key_ref: env:GEMINI_API_KEY\n    model: some-model\n"
    config = AgentLabConfig.load(write(tmp_path, text))
    dumped = config.dump_yaml()
    assert "env:GEMINI_API_KEY" in dumped
    assert (
        AgentLabConfig.model_validate_json(config.model_dump_json()).provider("gem").api_key_ref == "env:GEMINI_API_KEY"
    )


def test_an_unknown_provider_lists_the_known_ones() -> None:
    with pytest.raises(UserError, match=r"provider 'ghost' is not configured \(known: \['mock'\]\)"):
        AgentLabConfig().provider("ghost")


def test_the_database_url_can_come_from_the_environment_so_its_password_stays_out_of_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "postgresql+psycopg://lab:" + "from-the-environment" + "@db.internal:5432/lab"
    path = write(tmp_path, "storage:\n  database_url: sqlite:///.agentlab/other.db\n  artifacts_dir: kept-from-file\n")
    monkeypatch.setenv("AGENTLAB_DATABASE_URL", url)
    from_file = AgentLabConfig.load(path)
    assert from_file.storage.database_url == url
    assert from_file.storage.artifacts_dir == "kept-from-file", "only the URL is replaced"
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENTLAB_CONFIG", raising=False)
    path.unlink()
    assert AgentLabConfig.load().storage.database_url == url, "also with no file at all"
    monkeypatch.delenv("AGENTLAB_DATABASE_URL")
    assert AgentLabConfig.load().storage.database_url == "sqlite:///.agentlab/agentlab.db"
