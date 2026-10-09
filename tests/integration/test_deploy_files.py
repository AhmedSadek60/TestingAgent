"""The files that deploy AgentLab on Railway (railway.json, docker/Dockerfile, docker/railway-start.sh and
docker/agentlab.railway.yaml) are checked against each other and against the code they configure.

Nothing here talks to Railway. docs/deployment-railway.md says what was and was not verified on the platform itself."""

from __future__ import annotations

import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from agentlab.core.config import AgentLabConfig
from tests.support.api import running_api

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "docker" / "Dockerfile"
SCRIPT = ROOT / "docker" / "railway-start.sh"
RAILWAY_YAML = ROOT / "docker" / "agentlab.railway.yaml"
RAILWAY_JSON = ROOT / "railway.json"
TOKEN = "rw-" + "Hq4Zk8Vn2Bt6Yu1Mc9Xe"  # a made-up API token

IS_ROOT = hasattr(os, "getuid") and os.getuid() == 0
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="the start script is a POSIX shell script")


# ================================================================================================== railway.json
def test_railway_json_builds_this_dockerfile_and_starts_the_script_the_dockerfile_installs() -> None:
    config = json.loads(RAILWAY_JSON.read_text(encoding="utf-8"))
    assert config["build"] == {"builder": "DOCKERFILE", "dockerfilePath": "docker/Dockerfile"}
    assert (ROOT / config["build"]["dockerfilePath"]).is_file()
    installed = re.search(
        r"^COPY --chmod=0?755 docker/railway-start\.sh (\S+)$", DOCKERFILE.read_text(encoding="utf-8"), re.M
    )
    assert installed, "the Dockerfile has to install docker/railway-start.sh as an executable"
    assert config["deploy"]["startCommand"] == installed.group(1)


def test_railway_json_asks_for_one_instance_and_only_keys_railway_defines() -> None:
    deploy = json.loads(RAILWAY_JSON.read_text(encoding="utf-8"))["deploy"]
    assert deploy["numReplicas"] == 1, "jobs run in the API process and the volume belongs to one instance"
    assert deploy["healthcheckPath"] == "/health"
    assert deploy["restartPolicyType"] in {"ON_FAILURE", "ALWAYS", "NEVER"}
    # the keys of Railway's published schema (https://railway.com/railway.schema.json) that this file uses
    assert set(deploy) <= {
        "startCommand",
        "healthcheckPath",
        "healthcheckTimeout",
        "restartPolicyType",
        "restartPolicyMaxRetries",
        "numReplicas",
        "drainingSeconds",
    }
    # a run that is still going when Railway stops the container gets this long to be wound down and reported
    assert deploy["drainingSeconds"] >= 20


def test_the_dockerfile_installs_git_because_a_repository_given_by_url_is_cloned_with_it() -> None:
    runtime = DOCKERFILE.read_text(encoding="utf-8").split("AS runtime", 1)[1]
    assert re.search(r"apt-get install\b[^\n]*(\\\n[^\n]*)*\bgit\b", runtime), "git is missing from the runtime image"


def test_the_dockerfile_has_no_volume_instruction() -> None:
    """Railway's builder stops with 'The VOLUME keyword is banned in Dockerfiles'. Compose names its volume itself."""
    assert not re.search(r"^\s*VOLUME\b", DOCKERFILE.read_text(encoding="utf-8"), re.M | re.I)


def test_the_dockerfile_installs_the_railway_configuration_the_script_passes_to_agentlab() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY docker/agentlab.railway.yaml /etc/agentlab/railway.yaml" in dockerfile
    assert 'CONFIG="/etc/agentlab/railway.yaml"' in SCRIPT.read_text(encoding="utf-8")
    assert not any("railway" in line.lower() for line in dockerfile.splitlines() if line.startswith("ENTRYPOINT"))


# ================================================================================ docker/agentlab.railway.yaml
def test_the_railway_configuration_is_one_service_with_everything_under_the_volume() -> None:
    config = AgentLabConfig.load(RAILWAY_YAML)
    assert config.queue.backend == "inline", "a volume cannot be shared with a separate worker service"
    assert config.server.host == "0.0.0.0"
    assert config.server.token_ref == "env:AGENTLAB_API_TOKEN"
    assert config.security.allow_private_networks is False
    storage = config.storage
    for path in (
        storage.artifacts_dir,
        storage.secrets_file,
        storage.reports_dir,
        storage.work_dir,
        storage.uploads_dir,
        storage.skill_drafts_dir,
    ):
        assert path.startswith("/data/"), f"{path} is outside the volume, where nothing can be written"
    assert storage.database_url.startswith("sqlite:////data/")


def test_the_railway_configuration_holds_no_secret() -> None:
    config = AgentLabConfig.load(RAILWAY_YAML)
    for provider in config.providers:
        assert provider.api_key_ref is None or provider.api_key_ref.startswith(("env:", "secret:"))
    text = RAILWAY_YAML.read_text(encoding="utf-8")
    assert not re.search(r"(?i)(password|token|api[_-]?key)\s*:\s*['\"]?[A-Za-z0-9]{16,}", text)


async def test_railways_healthcheck_is_answered_without_a_token_and_nothing_else_is(tmp_path: Path) -> None:
    """Railway asks /health with the Host `healthcheck.railway.app` and no credentials, and waits for a 2xx."""
    async with running_api(tmp_path, token=TOKEN, allowed_hosts=None) as api:
        probe = {"Host": "healthcheck.railway.app", "Authorization": ""}
        health = await api.client.get("/health", headers=probe)
        assert health.status_code == 200
        assert health.json()["status"] == "ok" and health.json()["auth_required"] is True
        assert (await api.client.get("/settings", headers=probe)).status_code == 401


# ======================================================================================= docker/railway-start.sh
@pytest.fixture
def work() -> Iterator[Path]:
    """A directory anybody may enter. pytest's own temporary folders are private to the user running the tests, and a
    script that drops privileges has to be able to reach the files it runs."""
    folder = Path(tempfile.mkdtemp(prefix="agentlab-railway-"))
    folder.chmod(0o755)
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def fake_agentlab(work: Path) -> Path:
    """A stand-in for the `agentlab` command that says how it was started."""
    bin_dir = work / "bin"
    bin_dir.mkdir(exist_ok=True)
    bin_dir.chmod(0o755)
    fake = bin_dir / "agentlab"
    fake.write_text(
        '#!/bin/sh\necho "args: $*"\necho "uid: $(id -u)"\necho "home: $HOME"\ngrep -E "^NoNewPrivs" /proc/self/status\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return bin_dir


def start(work: Path, *args: str, env: dict[str, str] | None = None, as_user: str | None = None):
    """Run docker/railway-start.sh with the fake command in front of the PATH, optionally started as another user (the
    way a container is started without RAILWAY_RUN_UID=0)."""
    environment = {
        "PATH": f"{fake_agentlab(work)}:/usr/sbin:/usr/bin:/sbin:/bin",
        "AGENTLAB_DATA_DIR": str(work / "data"),
        **({"AGENTLAB_RUN_USER": "nobody"} if IS_ROOT else {}),
        **(env or {}),
    }
    # A copy in `work`, which anybody may enter: the checkout may be in a folder that only its owner can read.
    script = work / "railway-start.sh"
    shutil.copyfile(SCRIPT, script)
    script.chmod(0o755)
    command = ["sh", str(script), *args]
    if as_user:
        account = pwd.getpwnam(as_user)
        command = ["setpriv", f"--reuid={account.pw_uid}", f"--regid={account.pw_gid}", "--clear-groups", *command]
    return subprocess.run(command, capture_output=True, text=True, env=environment, check=False, timeout=60)


def facts(stdout: str) -> dict[str, str]:
    return dict(line.split(": ", 1) if ": " in line else line.split(":\t", 1) for line in stdout.splitlines())


@posix_only
def test_the_script_is_valid_shell_and_executable() -> None:
    assert subprocess.run(["sh", "-n", str(SCRIPT)], capture_output=True, check=False).returncode == 0
    assert os.access(SCRIPT, os.X_OK)


@posix_only
@pytest.mark.parametrize("args", [(), ("serve",)])
def test_the_script_serves_on_the_port_railway_injects(work: Path, args: tuple[str, ...]) -> None:
    (work / "data").mkdir()
    done = start(work, *args, env={"PORT": "4321"})
    assert done.returncode == 0, done.stderr
    assert facts(done.stdout)["args"] == "--config /etc/agentlab/railway.yaml serve --host 0.0.0.0 --port 4321"


@posix_only
def test_the_script_serves_on_8080_without_a_port_and_passes_other_commands_on(work: Path) -> None:
    (work / "data").mkdir()
    assert facts(start(work).stdout)["args"].endswith("--port 8080")
    assert facts(start(work, "doctor", "--json", env={"PORT": "4321"}).stdout)["args"] == (
        "--config /etc/agentlab/railway.yaml doctor --json"
    )


@posix_only
@pytest.mark.skipif(not IS_ROOT, reason="needs a root user, as in a container started with RAILWAY_RUN_UID=0")
@pytest.mark.skipif(shutil.which("setpriv") is None, reason="needs setpriv (util-linux)")
def test_started_as_root_it_gives_the_volume_to_the_unprivileged_user_and_runs_as_that_user(work: Path) -> None:
    try:
        nobody = pwd.getpwnam("nobody")
    except KeyError:
        pytest.skip("no 'nobody' user to stand in for the image's own")
    volume = work / "data"
    (volume / "reports" / "r1").mkdir(parents=True)
    (volume / "reports" / "r1" / "report.json").write_text("{}", encoding="utf-8")
    (volume / "link").symlink_to("/etc/passwd")  # chown must not follow a link out of the volume
    outside_owner = Path("/etc/passwd").stat().st_uid

    done = start(work)

    assert done.returncode == 0, done.stderr
    seen = facts(done.stdout)
    assert seen["uid"] == str(nobody.pw_uid), "AgentLab must not run as root"
    assert seen["home"] == nobody.pw_dir
    assert seen["NoNewPrivs"] == "1", "a process that cannot gain privileges, so root cannot be reached again"
    for path in (volume, volume / "reports", volume / "reports" / "r1", volume / "reports" / "r1" / "report.json"):
        assert path.stat().st_uid == nobody.pw_uid, f"{path} was left to root"
    assert (volume / "link").lstat().st_uid == nobody.pw_uid
    assert Path("/etc/passwd").stat().st_uid == outside_owner


@posix_only
@pytest.mark.skipif(not IS_ROOT, reason="needs a root user to start the script as somebody else")
@pytest.mark.skipif(shutil.which("setpriv") is None, reason="needs setpriv (util-linux)")
def test_started_as_an_unprivileged_user_it_says_what_to_do_when_the_volume_is_not_writable(work: Path) -> None:
    volume = work / "data"
    volume.mkdir()  # owned by root, as a Railway volume is
    done = start(work, as_user="nobody")
    assert done.returncode == 1
    assert "RAILWAY_RUN_UID=0" in done.stderr and "docs/deployment-railway.md" in done.stderr
    assert done.stdout == "", "AgentLab must not start on a volume it cannot write to"


@posix_only
@pytest.mark.skipif(not IS_ROOT, reason="needs a root user to start the script as somebody else")
@pytest.mark.skipif(shutil.which("setpriv") is None, reason="needs setpriv (util-linux)")
def test_started_as_an_unprivileged_user_it_runs_when_the_volume_is_writable(work: Path) -> None:
    volume = work / "data"
    volume.mkdir()
    shutil.chown(volume, user="nobody")
    done = start(work, as_user="nobody")
    assert done.returncode == 0, done.stderr
    assert facts(done.stdout)["uid"] == str(pwd.getpwnam("nobody").pw_uid)
