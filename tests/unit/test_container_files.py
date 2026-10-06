"""The container files keep the promises ``docs/installation.md#docker`` makes, without needing Docker to check them.

Building the image and starting the stack is checked by hand and written down in ``docs/development.md``; this keeps the
properties that matter for safety from being edited away unnoticed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agentlab.core.config import AgentLabConfig

ROOT = Path(__file__).resolve().parents[2]
DOCKER = ROOT / "docker"


def compose() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((DOCKER / "compose.yaml").read_text(encoding="utf-8"))
    return data


def container_config() -> AgentLabConfig:
    return AgentLabConfig.model_validate(yaml.safe_load((DOCKER / "agentlab.yaml").read_text(encoding="utf-8")))


def instructions(name: str) -> list[str]:
    """The Dockerfile's instructions with comments and blank lines removed, continuation lines joined."""
    text = (DOCKER / name).read_text(encoding="utf-8").replace("\\\n", " ")
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


# ================================================================================================== the image
def test_the_image_runs_as_an_unprivileged_user_and_checks_its_own_health() -> None:
    lines = instructions("Dockerfile")
    users = [line for line in lines if line.startswith("USER ")]
    assert users and users[-1] != "USER root" and users[-1] != "USER 0", "the last USER must not be root"
    assert any(line.startswith("HEALTHCHECK") and "/health" in line for line in lines)
    assert lines[-1] == 'CMD ["serve"]' and 'ENTRYPOINT ["agentlab"]' in lines


def test_the_image_carries_no_secret_and_no_container_runtime() -> None:
    text = "\n".join(instructions("Dockerfile")).lower()  # what the build runs, not the examples in its comments
    for word in ("docker.sock", "apt-get install docker", "--privileged", "secrets.key", "api_token"):
        assert word not in text, word


def test_what_the_build_does_not_need_never_enters_it() -> None:
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    for path in (".git", "tests", "secrets.key", "*.enc", "**/.env", "web/node_modules", "src/agentlab/api/static"):
        assert path in ignored, f"{path} should be in .dockerignore"


# ============================================================================================ its configuration
def test_the_container_configuration_loads_and_asks_for_a_token_because_it_listens_beyond_loopback() -> None:
    config = container_config()
    assert config.server.host == "0.0.0.0"
    assert config.server.token_ref == "env:AGENTLAB_API_TOKEN", (
        "a token is mandatory off loopback; it comes from the environment"
    )
    assert config.queue.backend == "redis"


def test_everything_the_container_writes_goes_to_the_data_volume_and_nothing_next_to_the_configuration() -> None:
    storage = container_config().storage
    paths = [
        storage.artifacts_dir,
        storage.secrets_file,
        storage.reports_dir,
        storage.work_dir,
        storage.uploads_dir,
        storage.skill_drafts_dir,
        storage.database_url.removeprefix("sqlite:///"),
    ]
    assert all(path.startswith("/data/") for path in paths), paths


def test_no_password_or_token_is_written_into_the_configuration_or_the_compose_file() -> None:
    for name in ("agentlab.yaml", "compose.yaml"):
        text = (DOCKER / name).read_text(encoding="utf-8")
        assert "api_key:" not in text.lower() and "password: " not in text.lower().replace("postgres_password:", "")
    assert "${AGENTLAB_DB_PASSWORD:?" in (DOCKER / "compose.yaml").read_text(encoding="utf-8")
    assert "${AGENTLAB_API_TOKEN:?" in (DOCKER / "compose.yaml").read_text(encoding="utf-8")


# =================================================================================================== the stack
def test_the_stack_has_an_api_a_worker_a_queue_and_a_database() -> None:
    assert set(compose()["services"]) == {"api", "worker", "redis", "postgres"}


def test_the_api_is_published_on_this_machine_only_and_nothing_else_is_published() -> None:
    services = compose()["services"]
    assert services["api"]["ports"] == ["127.0.0.1:8080:8080"]
    assert all("ports" not in services[name] for name in ("worker", "redis", "postgres"))


def test_the_application_containers_are_locked_down() -> None:
    services = compose()["services"]
    for name in ("api", "worker"):
        service = services[name]
        assert service["read_only"] is True, name
        assert service["cap_drop"] == ["ALL"], name
        assert "no-new-privileges:true" in service["security_opt"], name
        assert not service.get("privileged"), name
        assert all("docker.sock" not in str(volume) for volume in service["volumes"]), name
        assert all(str(volume).startswith("data:") for volume in service["volumes"]), "no folder of the host is mounted"


def test_the_api_and_the_worker_share_the_data_and_wait_for_their_services() -> None:
    services = compose()["services"]
    for name in ("api", "worker"):
        assert services[name]["depends_on"]["postgres"]["condition"] == "service_healthy"
        assert services[name]["depends_on"]["redis"]["condition"] == "service_healthy"
    assert services["api"]["command"] == ["serve", "--no-worker"], "the API does not run jobs itself"
    assert services["worker"]["command"] == ["worker"]


def test_a_stopping_worker_has_the_time_it_needs_to_finish_its_runs() -> None:
    grace = compose()["services"]["worker"]["stop_grace_period"]
    assert grace.endswith("s") and int(grace.removesuffix("s")) > 30, "longer than `agentlab worker --drain-seconds`"
