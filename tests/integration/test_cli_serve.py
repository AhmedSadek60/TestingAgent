"""``agentlab serve`` and ``agentlab worker``: what they refuse, and what they do as real processes."""

from __future__ import annotations

import signal
import uuid
from pathlib import Path
from typing import Any

import httpx
from typer.testing import CliRunner

from agentlab.cli.main import app
from agentlab.core.config import QueueConfig, ServerConfig
from tests.support.api import SMALL_RUN, api_config, mock_target
from tests.support.process import Proc, free_port, poll, start_agentlab, wait_until_up, write_config
from tests.support.redis import needs_redis, redis_test_url

runner = CliRunner()
TERMINAL = {
    "completed",
    "failed",
    "cancelled",
    "stopped_due_to_cost",
    "stopped_due_to_timeout",
    "stopped_due_to_step_limit",
}
TOKEN = "serve-" + "token-" + "q" * 16


def invoke(root: Path, *args: str, env: dict[str, str] | None = None) -> Any:
    return runner.invoke(app, ["--config", str(root / "agentlab.yaml"), *args], env=env or {})


def said(result: Any) -> str:
    """What a command printed, with the line breaks the terminal width added taken out again."""
    return " ".join(result.output.split())


# ================================================================================================== what it refuses
def test_serve_refuses_to_listen_beyond_this_machine_without_a_token(tmp_path: Path) -> None:
    write_config(tmp_path, api_config(tmp_path))
    res = invoke(tmp_path, "serve", "--host", "0.0.0.0", "--port", str(free_port()))  # noqa: S104 - refused, never bound
    assert res.exit_code == 2
    assert "refusing to listen on 0.0.0.0" in said(res) and "server.token_ref" in said(res)


def test_serve_refuses_a_token_that_is_too_short_or_missing(tmp_path: Path) -> None:
    write_config(tmp_path, api_config(tmp_path, server=ServerConfig(token_ref="env:AGENTLAB_API_TOKEN")))
    weak = "x7Qm-" + "9kLp"
    short = invoke(tmp_path, "serve", "--port", str(free_port()), env={"AGENTLAB_API_TOKEN": weak})
    assert short.exit_code == 2 and "at least 16 characters" in said(short)
    assert weak not in said(short), "a refused token is not echoed back"
    missing = invoke(tmp_path, "serve", "--port", str(free_port()))
    assert missing.exit_code == 2 and "AGENTLAB_API_TOKEN" in said(missing)


def test_a_worker_needs_a_shared_queue(tmp_path: Path) -> None:
    write_config(tmp_path, api_config(tmp_path))
    res = invoke(tmp_path, "worker")
    assert res.exit_code == 2
    assert "queue.backend: redis" in said(res) and "agentlab serve" in said(res)


def test_serve_and_worker_report_an_unreachable_redis_in_plain_words(tmp_path: Path) -> None:
    password = "hunter2-" + "not-a-real-password"
    write_config(tmp_path, api_config(tmp_path, queue=QueueConfig(backend="redis")))
    env = {"AGENTLAB_REDIS_URL": f"redis://queue-user:{password}@127.0.0.1:1/0"}
    for command in (["serve", "--port", str(free_port())], ["worker"]):
        res = invoke(tmp_path, *command, env=env)
        assert res.exit_code == 4, (command, said(res))
        assert "cannot reach the Redis queue" in said(res)
        assert password not in said(res) and "127.0.0.1:1" not in said(res)


def test_serve_and_worker_describe_their_options(tmp_path: Path) -> None:
    write_config(tmp_path, api_config(tmp_path))
    serve = said(invoke(tmp_path, "serve", "--help"))
    for option in ("--host", "--port", "--no-ui", "--worker", "--access-log"):
        assert option in serve, option
    worker = said(invoke(tmp_path, "worker", "--help"))
    assert "--concurrency" in worker and "--drain-seconds" in worker


# ====================================================================================== a real server, in-process queue
def test_serve_runs_an_evaluation_from_request_to_report_and_stops_cleanly(tmp_path: Path) -> None:
    port = free_port()
    write_config(tmp_path, api_config(tmp_path))
    server = start_agentlab(["--config", "agentlab.yaml", "serve", "--port", str(port)], cwd=tmp_path, name="serve")
    base = f"http://127.0.0.1:{port}"
    try:
        wait_until_up(f"{base}/health", server)
        with httpx.Client(base_url=base, timeout=60) as c:
            health = c.get("/health").json()
            assert health["status"] == "ok" and health["queue"] == "inline"
            assert (
                c.get("/docs").status_code == 200 and c.get("/openapi.json").json()["info"]["title"] == "AgentLab API"
            )
            run_id = c.post("/test-runs", json={"target": mock_target(), "options": SMALL_RUN}).json()["run_id"]
            run = poll(
                lambda: (r := c.get(f"/test-runs/{run_id}").json())["status"] in TERMINAL and r, what="the run ending"
            )
            assert run["status"] == "completed", run
            assert c.get(f"/test-runs/{run_id}/results").json()
            report = c.get(f"/test-runs/{run_id}/reports").json()[0]
            md = next(f for f in report["formats"] if f["format"] == "md")
            assert c.get(md["url"]).status_code == 200
    finally:
        code = server.stop(signal.SIGTERM)
    assert code == 0, server.output
    out = server.output
    assert "listening on 127.0.0.1" in out and "API reference" in out and "this machine only" in out
    assert "Traceback" not in out, out


def test_a_server_with_a_token_from_the_environment_demands_it(tmp_path: Path) -> None:
    port = free_port()
    write_config(tmp_path, api_config(tmp_path, server=ServerConfig(token_ref="env:AGENTLAB_API_TOKEN")))
    server = start_agentlab(
        ["--config", "agentlab.yaml", "serve", "--port", str(port)],
        cwd=tmp_path,
        env={"AGENTLAB_API_TOKEN": TOKEN},
        name="serve",
    )
    base = f"http://127.0.0.1:{port}"
    try:
        wait_until_up(f"{base}/health", server)
        assert httpx.get(f"{base}/projects").status_code == 401
        assert httpx.get(f"{base}/projects", headers={"Authorization": "Bearer " + "x" * 24}).status_code == 401
        assert httpx.get(f"{base}/projects", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    finally:
        assert server.stop() == 0
    assert "API token required" in server.output
    assert TOKEN not in server.output, "the token is never printed or logged"


def test_ctrl_c_stops_the_server_in_order_and_the_access_log_is_there_when_asked_for(tmp_path: Path) -> None:
    port = free_port()
    write_config(tmp_path, api_config(tmp_path))
    server = start_agentlab(
        ["--config", "agentlab.yaml", "serve", "--port", str(port), "--access-log", "--no-ui"],
        cwd=tmp_path,
        name="serve",
    )
    base = f"http://127.0.0.1:{port}"
    try:
        wait_until_up(f"{base}/health", server)
        assert httpx.get(f"{base}/projects").status_code == 200
        assert httpx.get(f"{base}/no-such-page").status_code == 404
    finally:
        code = server.stop(signal.SIGINT)
    assert code == 0, server.output
    out = server.output
    assert 'GET /projects HTTP/1.1" 200' in out and 'GET /no-such-page HTTP/1.1" 404' in out
    assert "web interface   off" in out
    assert "Aborted" not in out and "Traceback" not in out and "KeyboardInterrupt" not in out


def test_a_server_that_cannot_start_says_why_and_exits_with_an_error(tmp_path: Path) -> None:
    port = free_port()
    write_config(tmp_path, api_config(tmp_path))
    first = start_agentlab(["--config", "agentlab.yaml", "serve", "--port", str(port)], cwd=tmp_path, name="first")
    try:
        wait_until_up(f"http://127.0.0.1:{port}/health", first)
        second = start_agentlab(
            ["--config", "agentlab.yaml", "serve", "--port", str(port)], cwd=tmp_path, name="second"
        )
        assert second.wait(30) != 0, "the port is taken"
        assert "address already in use" in second.output.lower() or "errno" in second.output.lower()
    finally:
        first.stop()


# ================================================================ real processes sharing a real Redis queue
def redis_deployment(tmp_path: Path) -> tuple[Proc, str, dict[str, str], str]:
    """An API process (which does not run jobs) over a Redis queue; returns it with its URL, environment and key prefix."""
    port = free_port()
    prefix = f"agentlab-test-{uuid.uuid4().hex[:10]}"
    config = api_config(
        tmp_path,
        queue=QueueConfig(backend="redis", key_prefix=prefix, worker_timeout_seconds=5, max_concurrent_runs=1),
    )
    write_config(tmp_path, config)
    env = {"AGENTLAB_REDIS_URL": redis_test_url()}
    server = start_agentlab(
        ["--config", "agentlab.yaml", "serve", "--port", str(port)], cwd=tmp_path, env=env, name="api"
    )
    wait_until_up(f"http://127.0.0.1:{port}/health", server)
    return server, f"http://127.0.0.1:{port}", env, prefix


def drop_keys(prefix: str) -> None:
    import redis

    client = redis.Redis.from_url(redis_test_url())
    keys = list(client.scan_iter(f"{prefix}:*"))
    if keys:
        client.delete(*keys)


SLOW = {
    "target": mock_target("demo", "success", "slow"),
    "options": {"intensity": "quick", "second_wave": False},
    "overrides": {"max_parallel": 1},
}


@needs_redis
def test_the_api_and_worker_processes_share_a_redis_queue(tmp_path: Path) -> None:
    api, base, env, prefix = redis_deployment(tmp_path)
    worker: Proc | None = None
    try:
        with httpx.Client(base_url=base, timeout=60) as c:
            assert c.get("/health").json()["queue"] == "redis"
            first = c.post("/test-runs", json={"target": mock_target(), "options": SMALL_RUN}).json()["run_id"]
            assert c.get(f"/test-runs/{first}").json()["status"] == "pending"
            assert c.get("/health").json()["queued"] == 1, "the job waits in Redis: the API does not run it"

            worker = start_agentlab(
                ["--config", "agentlab.yaml", "worker", "--drain-seconds", "0"], cwd=tmp_path, env=env, name="worker"
            )
            done = poll(lambda: (r := c.get(f"/test-runs/{first}").json())["status"] in TERMINAL and r, what="run 1")
            assert done["status"] == "completed" and c.get(f"/test-runs/{first}/results").json()
            assert c.get(f"/test-runs/{first}/reports").json(), "the report was built by the other process"
            assert c.get("/health").json()["queued"] == 0

            # cancelling reaches a run that another process is working on
            second = c.post("/test-runs", json=SLOW).json()["run_id"]
            poll(
                lambda: sum(e["type"] == "TestCompleted" for e in c.get(f"/test-runs/{second}/events").json()) >= 3,
                what="three tests of run 2",
            )
            assert c.post(f"/test-runs/{second}/cancel", json={"reason": "from another process"}).status_code == 202
            stopped = poll(
                lambda: (r := c.get(f"/test-runs/{second}").json())["status"] in TERMINAL and r, what="run 2"
            )
            assert stopped["status"] == "cancelled" and stopped["totals"]["cancel_reason"] == "from another process"
            assert c.get(f"/test-runs/{second}/reports").json(), "what ran is still reported"

            # stopping the worker while it is idle is clean
            assert worker.stop(signal.SIGTERM) == 0, worker.output
            assert "stopping" in worker.output and "Traceback" not in worker.output
            worker = None

            # a worker that is killed in the middle of a run leaves the run for the API to close
            third = c.post("/test-runs", json=SLOW).json()["run_id"]
            doomed = start_agentlab(
                ["--config", "agentlab.yaml", "worker", "--drain-seconds", "0"], cwd=tmp_path, env=env, name="doomed"
            )
            poll(
                lambda: sum(e["type"] == "TestCompleted" for e in c.get(f"/test-runs/{third}/events").json()) >= 2,
                what="the doomed worker getting going",
            )
            doomed.kill()
            lost = poll(
                lambda: (r := c.get(f"/test-runs/{third}").json())["status"] in TERMINAL and r,
                timeout=45,
                what="the API closing the abandoned run",
            )
            assert lost["status"] == "failed" and "worker stopped" in lost["error"]["message"]
    finally:
        if worker is not None:
            worker.stop()
        api.stop()
        drop_keys(prefix)
    assert "Traceback" not in api.output, api.output


@needs_redis
def test_a_stopped_worker_winds_its_run_down_and_reports_it(tmp_path: Path) -> None:
    api, base, env, prefix = redis_deployment(tmp_path)
    worker = start_agentlab(
        ["--config", "agentlab.yaml", "worker", "--drain-seconds", "0"], cwd=tmp_path, env=env, name="worker"
    )
    try:
        with httpx.Client(base_url=base, timeout=60) as c:
            run_id = c.post("/test-runs", json=SLOW).json()["run_id"]
            poll(
                lambda: sum(e["type"] == "TestCompleted" for e in c.get(f"/test-runs/{run_id}/events").json()) >= 3,
                what="three tests",
            )
            assert worker.stop(signal.SIGTERM, timeout=60) == 0, worker.output
            run = c.get(f"/test-runs/{run_id}").json()
            assert run["status"] == "cancelled" and "worker is stopping" in run["totals"]["cancel_reason"]
            assert c.get(f"/test-runs/{run_id}/reports").json(), "stopping a worker does not lose what ran"
    finally:
        worker.stop()
        api.stop()
        drop_keys(prefix)
