"""The files that deploy AgentLab on a server (docker/Dockerfile, docker/compose.yaml, docker/compose.vps.yaml, docker/Caddyfile,
docker/seccomp-chromium.json, docker/agentlab.vps.yaml) are checked against each other and against the code they configure.

Nothing here starts a container. docs/deployment-vps.md says what was and was not verified on the server itself."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from agentlab.core.config import AgentLabConfig
from tests.support.api import running_api

ROOT = Path(__file__).resolve().parents[2]
DOCKER = ROOT / "docker"
DOCKERFILE = DOCKER / "Dockerfile"
COMPOSE = DOCKER / "compose.yaml"
COMPOSE_VPS = DOCKER / "compose.vps.yaml"
CADDYFILE = DOCKER / "Caddyfile"
SECCOMP = DOCKER / "seccomp-chromium.json"
VPS_YAML = DOCKER / "agentlab.vps.yaml"
TOKEN = "dp-" + "Hq4Zk8Vn2Bt6Yu1Mc9Xe"  # a made-up API token


class _ComposeLoader(yaml.SafeLoader):
    """Compose's own tags (!reset, !override) are not YAML types a safe loader knows."""


_ComposeLoader.add_constructor("!reset", lambda loader, node: [])
_ComposeLoader.add_constructor("!override", lambda loader, node: loader.construct_sequence(node, deep=True))


def _compose(path: Path) -> dict[str, Any]:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_ComposeLoader)  # noqa: S506 - a safe loader subclass


# ======================================================================================================== docker/Dockerfile
def test_the_dockerfile_installs_git_because_a_repository_given_by_url_is_cloned_with_it() -> None:
    runtime = DOCKERFILE.read_text(encoding="utf-8").split("AS runtime", 1)[1]
    assert re.search(r"apt-get install\b[^\n]*(\\\n[^\n]*)*\bgit\b", runtime), "git is missing from the runtime image"


def test_the_image_starts_agentlab_as_an_unprivileged_user_and_builds_chromium_only_when_asked() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert 'ENTRYPOINT ["agentlab"]' in dockerfile and 'CMD ["serve"]' in dockerfile
    runtime = dockerfile.split("AS runtime", 1)[1]
    assert re.search(r"^USER agentlab$", runtime, re.M), "the process must not run as root"
    assert dockerfile.count("ARG WITH_BROWSER=0") >= 2, "Chromium is opt-in, in both the build and the runtime stage"
    assert "playwright install --with-deps chromium" in runtime and 'if [ "$WITH_BROWSER" = "1" ]' in runtime


# ============================================================================================ the health check, without a token
async def test_the_healthcheck_is_answered_without_a_token_and_nothing_else_is(tmp_path: Path) -> None:
    """A container health check and a load balancer ask /health with no credentials and wait for a 2xx."""
    async with running_api(tmp_path, token=TOKEN, allowed_hosts=None) as api:
        probe = {"Host": "agentlab.internal", "Authorization": ""}
        health = await api.client.get("/health", headers=probe)
        assert health.status_code == 200
        assert health.json()["status"] == "ok" and health.json()["auth_required"] is True
        assert (await api.client.get("/settings", headers=probe)).status_code == 401


# ================================================================================================ docker/compose.vps.yaml
def test_only_caddy_is_published_to_the_world_and_every_other_port_is_on_the_loopback() -> None:
    services = _compose(COMPOSE_VPS)["services"]
    assert services["api"]["ports"] == [], "the API is reached through Caddy only: the base file's host port is reset"
    assert services["caddy"]["ports"] == ["443:443"]
    for name in ("postgres", "ollama"):
        assert all(str(p).startswith("127.0.0.1:") for p in services[name]["ports"]), (
            f"{name} must stay on the loopback"
        )
    assert "ports" not in services["worker"] and "ports" not in services.get("redis", {})


def test_the_worker_has_the_chromium_profile_and_keeps_the_rest_of_its_hardening() -> None:
    base = _compose(COMPOSE)["services"]
    vps = _compose(COMPOSE_VPS)["services"]["worker"]
    assert "seccomp=./seccomp-chromium.json" in vps["security_opt"]
    anchored = base["worker"]
    assert anchored.get("read_only") is True and anchored.get("cap_drop") == ["ALL"], "the base hardening is inherited"
    assert "no-new-privileges:true" in anchored["security_opt"]
    assert vps["environment"]["HOME"] == "/tmp", "the root file system is read-only; Chromium writes under HOME"
    assert vps["shm_size"] == "1gb"


def test_both_services_that_share_the_image_build_it_with_chromium() -> None:
    services = _compose(COMPOSE_VPS)["services"]
    assert services["api"]["build"]["args"]["WITH_BROWSER"] == "1"
    assert services["worker"]["build"]["args"]["WITH_BROWSER"] == "1", "the last build of the shared tag would win"


# ================================================================================================ docker/seccomp-chromium.json
def test_the_seccomp_profile_is_the_default_deny_profile_plus_the_namespace_calls_chromium_needs() -> None:
    profile = json.loads(SECCOMP.read_text(encoding="utf-8"))
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO", "everything not listed is still refused"
    rules = profile["syscalls"]
    unconditional = {
        name
        for rule in rules
        if rule["action"] == "SCMP_ACT_ALLOW" and not rule.get("args") and not rule.get("includes")
        for name in rule["names"]
    }
    assert {"clone", "clone3", "unshare", "setns"} <= unconditional
    assert len(rules) > 30, "this is Docker's full default profile, not a short allow-list"
    # the calls that reach the host stay refused unless a capability is held
    assert not {"mount", "reboot", "kexec_load", "init_module", "pivot_root"} & unconditional


# ======================================================================================================== docker/Caddyfile
def test_caddy_serves_the_certificate_it_made_for_the_address_even_when_the_client_sends_no_name() -> None:
    text = CADDYFILE.read_text(encoding="utf-8")
    assert "default_sni {$AGENTLAB_PUBLIC_HOST}" in text, "a client that connects to an IP sends no server name"
    assert "tls internal" in text and "reverse_proxy api:8080" in text and "flush_interval -1" in text


# ============================================================================== docker/agentlab.vps.yaml, and no secret anywhere
def test_the_server_configuration_loads_and_judges_with_the_local_model() -> None:
    config = AgentLabConfig.load(VPS_YAML)
    names = {p.name: p for p in config.providers}
    assert names["local"].type == "ollama" and names["local"].base_url == "http://ollama:11434"
    assert [j.provider for j in config.evaluation.judges] == ["local"]
    assert config.server.token_ref == "env:AGENTLAB_API_TOKEN" and config.queue.backend == "redis"
    assert config.storage.database_url.startswith("sqlite:////data/"), "overridden by AGENTLAB_DATABASE_URL on a server"


def test_no_deployment_file_holds_a_secret() -> None:
    pattern = re.compile(r"(?i)(password|token|api[_-]?key|secret)\s*[:=]\s*['\"]?[A-Za-z0-9]{16,}")
    for path in (COMPOSE, COMPOSE_VPS, CADDYFILE, VPS_YAML, DOCKERFILE):
        text = path.read_text(encoding="utf-8")
        assert not pattern.search(text), f"{path.name} looks like it holds a secret"
    assert "${AGENTLAB_API_TOKEN:?" in COMPOSE.read_text(encoding="utf-8"), "the token comes from the environment"
