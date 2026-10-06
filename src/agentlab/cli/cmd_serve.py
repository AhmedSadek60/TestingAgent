"""``agentlab serve`` (the REST API and the web interface) and ``agentlab worker`` (a process that only runs jobs).

``serve`` listens on this machine unless told otherwise, and refuses to listen anywhere else without an API token, because
whoever can call the API can make AgentLab send requests, start containers and drive a browser.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from typing import Annotated

import typer

from agentlab import __version__
from agentlab.api.security import is_loopback, resolve_token
from agentlab.cli.common import EXIT_NOT_TESTED, console, err, fail, load_config, make_services, run_async, state
from agentlab.core.config import AgentLabConfig
from agentlab.core.errors import InfrastructureError
from agentlab.jobs.queue import RedisQueue, create_queue
from agentlab.jobs.worker import Worker


async def _ping(config: AgentLabConfig) -> None:
    """Fail early, and in plain words, when the Redis queue cannot be reached."""
    queue = create_queue(config.queue, redis_url=os.environ.get("AGENTLAB_REDIS_URL"))
    try:
        if isinstance(queue, RedisQueue):
            await queue.ping()
    finally:
        await queue.close()


def serve(
    ctx: typer.Context,
    host: Annotated[str | None, typer.Option("--host", help="Address to listen on (default: server.host).")] = None,
    port: Annotated[int | None, typer.Option("--port", min=1, max=65535, help="Port (default: server.port).")] = None,
    no_ui: Annotated[bool, typer.Option("--no-ui", help="Serve the API only, not the web interface.")] = False,
    worker: Annotated[
        bool | None,
        typer.Option(
            "--worker/--no-worker",
            help="Run jobs in this process. Default: yes with the in-process queue, no with Redis (`agentlab worker`).",
        ),
    ] = None,
    access_log: Annotated[bool, typer.Option("--access-log", help="Log every request.")] = False,
) -> None:
    """Start the REST API (reference at /docs) and the web interface."""
    import uvicorn

    from agentlab.api.app import create_app, default_ui_dir

    st = state(ctx)
    overrides: dict[str, object] = {"server.host": host, "server.port": port}
    if no_ui:
        overrides["server.serve_ui"] = False
    services = make_services(st, overrides=overrides)
    try:
        scfg = services.config.server
        try:
            token = resolve_token(scfg.token_ref, services.credentials)
        except Exception as exc:
            raise fail(str(exc)) from exc
        if token is None and not is_loopback(scfg.host):
            raise fail(
                f"refusing to listen on {scfg.host}: other machines could reach it and no API token is configured. "
                "Set server.token_ref (for example env:AGENTLAB_API_TOKEN, 16 or more characters, from "
                "`openssl rand -hex 24`), or listen on 127.0.0.1"
            )
        backend = services.config.queue.backend
        if backend == "redis":
            try:
                run_async(_ping(services.config))
            except InfrastructureError as exc:
                raise fail(str(exc), EXIT_NOT_TESTED) from exc
        runs_jobs = (backend == "inline") if worker is None else worker
        serve_ui = scfg.serve_ui
        app = create_app(services, token=token, start_worker=runs_jobs, serve_ui=serve_ui)
        ui_dir = default_ui_dir() if serve_ui else None
        base = f"http://{'127.0.0.1' if scfg.host in {'0.0.0.0', '::'} else scfg.host}:{scfg.port}"  # noqa: S104 - compared, not bound

        console.print(f"[bold]AgentLab {__version__}[/bold] listening on [cyan]{scfg.host}:{scfg.port}[/cyan]")
        console.print(f"  API reference   {base}/docs")
        if not serve_ui:
            console.print("  web interface   off")
        elif ui_dir is None:
            console.print("  web interface   not built here (cd web && npm ci && npm run build)")
        else:
            console.print(f"  web interface   {base}/")
        console.print(
            "  authentication  "
            + (
                "API token required (Authorization: Bearer <token>)"
                if token
                else "none; this machine only (the Host and Origin of requests are checked)"
            )
        )
        console.print(
            f"  queue           {backend}, "
            + ("jobs run in this process" if runs_jobs else "jobs are worked by `agentlab worker` processes")
        )
        # log_config=None: uvicorn's own loggers (including the traceback of an unhandled error) go through the handler
        # `agentlab` configured, which masks secrets, instead of through handlers of uvicorn's own
        config = uvicorn.Config(
            app,
            host=scfg.host,
            port=scfg.port,
            log_level="warning",
            log_config=None,
            access_log=access_log,
            server_header=False,
            timeout_graceful_shutdown=10,
            lifespan="on",
        )
        if access_log:
            logging.getLogger("uvicorn.access").setLevel(logging.INFO)
        # uvicorn stops gracefully on SIGINT/SIGTERM and then sends the signal again to the process, restoring the handler
        # that was there before, so that the process "dies of" it (exit status 143, or an interrupted-by-Ctrl+C error).
        # An orderly stop is a success: ignoring the signals around the call makes that last step do nothing
        before = {sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            uvicorn.Server(config).run()
        finally:
            for sig, handler in before.items():
                signal.signal(sig, handler)
    finally:
        run_async(services.aclose())


def work(
    ctx: typer.Context,
    concurrency: Annotated[
        int | None,
        typer.Option(
            "--concurrency", min=1, max=64, help="Runs at the same time (default: queue.max_concurrent_runs)."
        ),
    ] = None,
    drain_seconds: Annotated[
        float,
        typer.Option(
            "--drain-seconds",
            min=0,
            help="On stopping, how long running jobs may finish on their own before they are asked to stop at a safe point.",
        ),
    ] = 30.0,
) -> None:
    """Run jobs from the Redis queue (queue.backend: redis). Start as many as you need; queued runs survive an API restart."""
    st = state(ctx)
    cfg, _ = load_config(st)
    if cfg.queue.backend != "redis":
        raise fail(
            "a separate worker needs a shared queue: set queue.backend: redis (and queue.redis_url or AGENTLAB_REDIS_URL). "
            "With the in-process queue, `agentlab serve` already runs the jobs"
        )
    services = make_services(st)

    async def main() -> None:
        queue = create_queue(services.config.queue, redis_url=os.environ.get("AGENTLAB_REDIS_URL"))
        runner = Worker(services, queue, concurrency=concurrency)
        try:
            if isinstance(queue, RedisQueue):
                try:
                    await queue.ping()
                except InfrastructureError as exc:
                    raise fail(str(exc), EXIT_NOT_TESTED) from exc
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                with contextlib.suppress(NotImplementedError):  # not every platform can do this
                    loop.add_signal_handler(sig, stop.set)
            runner.start()
            console.print(
                f"[bold]AgentLab worker {__version__}[/bold] working the {queue.name} queue, "
                f"{runner.concurrency} run(s) at a time. Ctrl+C stops it once the runs in progress have ended."
            )
            await stop.wait()
            err.print("stopping: no new runs are taken; the ones in progress may finish...")
            await runner.stop(drain_seconds=drain_seconds)
        finally:
            await queue.close()
            await services.aclose()

    logging.getLogger("agentlab").setLevel(logging.INFO)
    run_async(main())
