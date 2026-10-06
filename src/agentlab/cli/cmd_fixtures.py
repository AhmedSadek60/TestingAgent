"""``agentlab fixtures``: the disposable example agents with planted defects (spec section 23).

They exist to answer one question honestly: *does AgentLab find what is wrong?* ``verify`` runs the full proof;
``serve`` lets anyone point ``agentlab test`` at one of them; ``target`` prints the ``target.yaml`` that goes with it.
Fixtures bind to loopback only and hold no real data, credentials or capabilities.
"""

from __future__ import annotations

import sys
from typing import Annotated, Any

import typer
import yaml
from rich import box
from rich.table import Table

from agentlab.cli.common import EXIT_FINDINGS, EXIT_INPUT, console, emit_json, err, fail
from agentlab.fixtures import REGISTRY, fixture_class, make_app
from agentlab.fixtures.selftest import verify
from agentlab.fixtures.server import serve_forever

fixtures_app = typer.Typer(
    help="Disposable example agents with planted defects: serve one, print its target file, or prove AgentLab finds the defects.",
    no_args_is_help=True,
)

VARIANT_HELP = "'correct' (no defects), 'flawed' (all defects) or a comma-separated list of defect names."


@fixtures_app.command("list")
def fixtures_list(
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
    defects: Annotated[bool, typer.Option("--defects", help="List every planted defect.")] = False,
) -> None:
    """List the fixture kinds with what they are and how many defects each can carry."""
    rows: list[dict[str, Any]] = [
        {
            "kind": kind,
            "title": cls.title,
            "summary": cls.summary,
            "types": list(cls.declared_types),
            "defects": dict(cls.DEFECTS),
        }
        for kind, cls in sorted(REGISTRY.items())
    ]
    if as_json:
        emit_json({"fixtures": rows})
        return
    table = Table(box=box.SIMPLE_HEAD, title="Fixture agents")
    for column in ("Kind", "Agent", "Declared types", "Defects"):
        table.add_column(column)
    for row in rows:
        table.add_row(str(row["kind"]), str(row["title"]), ", ".join(row["types"]) or "-", str(len(row["defects"])))
    console.print(table)
    if defects:
        for row in rows:
            console.print(f"\n[bold]{row['kind']}[/bold]")
            for name, what in row["defects"].items():
                console.print(f"  [cyan]{name}[/cyan]  {what}")


@fixtures_app.command("serve")
def fixtures_serve(
    kind: Annotated[str, typer.Argument(help="Fixture kind (see `agentlab fixtures list`).")],
    variant: Annotated[str, typer.Option("--variant", "-V", help=VARIANT_HELP)] = "correct",
    port: Annotated[int, typer.Option("--port", "-p", help="Port to listen on.")] = 8765,
    host: Annotated[
        str, typer.Option("--host", help="Interface. Anything but loopback exposes a deliberately flawed agent.")
    ] = "127.0.0.1",
    token: Annotated[
        str | None, typer.Option("--token", help="Require 'Authorization: Bearer TOKEN' on /chat.")
    ] = None,
) -> None:
    """Run a fixture agent as an HTTP service (for example to test it with `agentlab test --url`)."""
    try:
        agent = fixture_class(kind).build(variant)
    except ValueError as exc:
        raise fail(str(exc)) from exc
    if host not in {"127.0.0.1", "localhost", "::1"}:
        err.print(
            f"[yellow]warning:[/yellow] {host} is not loopback; a fixture is deliberately flawed and must not be exposed"
        )
    console.print(f"serving [bold]{agent.title}[/bold] ({variant}) at http://{host}:{port}  (Ctrl-C stops it)")
    serve_forever(make_app(agent, token=token), host=host, port=port)


@fixtures_app.command("target")
def fixtures_target(
    kind: Annotated[str, typer.Argument(help="Fixture kind.")],
    url: Annotated[str, typer.Option("--url", help="Where the fixture is served.")] = "http://127.0.0.1:8765",
    credential: Annotated[
        str | None, typer.Option("--credential", help="Credential name to send as the bearer token.")
    ] = None,
) -> None:
    """Print the target.yaml an owner would write for a fixture."""
    try:
        agent = fixture_class(kind).build("correct")
    except ValueError as exc:
        raise fail(str(exc)) from exc
    sys.stdout.write(yaml.safe_dump(agent.target(url, credential=credential), sort_keys=False))


@fixtures_app.command("verify")
def fixtures_verify(
    kinds: Annotated[list[str] | None, typer.Argument(help="Fixture kinds (default: all).")] = None,
    workers: Annotated[int, typer.Option("--workers", "-w", help="Runs in parallel.")] = 4,
    defect: Annotated[list[str] | None, typer.Option("--defect", "-d", help="Only these defects (repeatable).")] = None,
    skip_all: Annotated[
        bool, typer.Option("--skip-all-defects", help="Skip the run with every defect planted.")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Prove that AgentLab finds the planted defects, and only those.

    For every kind: the correct build must pass the whole suite cleanly, each defect planted alone must make one of
    its expected tests fail with a finding of at least the expected severity, and all defects together must lower the
    score. Exit code 1 when any expectation is not met. The runs need no LLM and no network beyond loopback.
    """
    chosen = kinds or sorted(REGISTRY)
    unknown = [k for k in chosen if k not in REGISTRY]
    if unknown:
        raise fail(f"unknown fixture(s) {', '.join(unknown)}; available: {', '.join(sorted(REGISTRY))}", EXIT_INPUT)
    err.print(f"[dim]verifying {', '.join(chosen)} with {max(1, workers)} worker(s)...[/dim]")
    reports = verify(chosen, workers=workers, defects=defect or None, all_defects=not skip_all)
    if as_json:
        emit_json({"ok": all(r.ok for r in reports), "fixtures": [r.to_dict() for r in reports]})
    else:
        for report in reports:
            table = Table(box=box.SIMPLE_HEAD, title=f"{report.kind}: {'OK' if report.ok else 'NOT OK'}")
            table.add_column("Check")
            table.add_column("Result")
            table.add_column("Detail", overflow="fold")
            for check in report.checks:
                table.add_row(check.name, "[green]ok[/green]" if check.ok else "[red]FAILED[/red]", check.detail)
            console.print(table)
    if not all(r.ok for r in reports):
        raise typer.Exit(EXIT_FINDINGS)
