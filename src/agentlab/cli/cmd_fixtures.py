"""``agentlab fixtures``: the disposable example agents with planted defects (spec section 23).

They exist to answer one question honestly: *does AgentLab find what is wrong?* ``verify`` runs the full proof;
``serve`` lets anyone point ``agentlab test`` at one of them; ``target`` prints the ``target.yaml`` that goes with it.
Fixtures bind to loopback only and hold no real data, credentials or capabilities.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from rich import box
from rich.table import Table

from agentlab.cli.common import EXIT_FINDINGS, EXIT_INPUT, console, emit_json, err, fail
from agentlab.cli.markup import esc
from agentlab.fixtures import REGISTRY, fixture_class
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
        table.add_row(
            esc(row["kind"]), esc(row["title"]), esc(", ".join(row["types"]) or "-"), str(len(row["defects"]))
        )
    console.print(table)
    if defects:
        for row in rows:
            console.print(f"\n[bold]{esc(row['kind'])}[/bold]")
            for name, what in row["defects"].items():
                console.print(f"  [cyan]{esc(name)}[/cyan]  {esc(what)}")


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
    if agent.transport == "command":
        raise fail(
            f"'{kind}' is a command-line agent, not a service: write it to a folder with "
            f"`agentlab fixtures target {kind} --dir DIR --variant {variant}` and test that target",
            EXIT_INPUT,
        )
    if host not in {"127.0.0.1", "localhost", "::1"}:
        err.print(
            f"[yellow]warning:[/yellow] {esc(host)} is not loopback; a fixture is deliberately flawed and must not be exposed"
        )
    console.print(
        f"serving [bold]{esc(agent.title)}[/bold] ({esc(variant)}) at http://{esc(host)}:{port}  (Ctrl-C stops it)"
    )
    serve_forever(agent.asgi_app(token=token), host=host, port=port, lifespan="on" if agent.lifespan else "off")


@fixtures_app.command("target")
def fixtures_target(
    kind: Annotated[str, typer.Argument(help="Fixture kind.")],
    url: Annotated[str, typer.Option("--url", help="Where the fixture is served.")] = "http://127.0.0.1:8765",
    credential: Annotated[
        str | None, typer.Option("--credential", help="Credential name to send as the bearer token.")
    ] = None,
    variant: Annotated[str, typer.Option("--variant", "-V", help=VARIANT_HELP)] = "correct",
    directory: Annotated[
        Path | None,
        typer.Option("--dir", help="Command-line fixtures only: the folder to write the agent's code to."),
    ] = None,
) -> None:
    """Print the target.yaml an owner would write for a fixture.

    A command-line fixture (the coding agent) is a repository, not a service: ``--dir`` writes it to a folder and the
    printed target points at that folder."""
    try:
        agent = fixture_class(kind).build(variant)
    except ValueError as exc:
        raise fail(str(exc)) from exc
    if agent.transport == "command":
        if directory is None:
            raise fail(f"'{kind}' is a command-line agent: pass --dir to say where to write it", EXIT_INPUT)
        agent.write_to(directory)
        sys.stdout.write(yaml.safe_dump(agent.target(str(directory.resolve())), sort_keys=False))
        return
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
    require_all: Annotated[
        bool,
        typer.Option(
            "--require-all",
            help="Fail when a kind had to be skipped (Docker or a browser is missing) instead of warning.",
        ),
    ] = False,
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
    for kind in chosen:
        absent = [name for name in defect or [] if name not in fixture_class(kind).DEFECTS]
        if absent:
            raise fail(
                f"fixture '{kind}' has no defect {', '.join(absent)}; its defects are {', '.join(fixture_class(kind).DEFECTS)}"
                " (name the kind too when you name a defect)",
                EXIT_INPUT,
            )
    err.print(f"[dim]verifying {esc(', '.join(chosen))} with {max(1, workers)} worker(s)...[/dim]")
    reports = verify(chosen, workers=workers, defects=defect or None, all_defects=not skip_all)
    skipped = [r for r in reports if r.skipped]
    if as_json:
        emit_json(
            {
                "ok": all(r.ok for r in reports) and not (require_all and skipped),
                "skipped": [r.kind for r in skipped],
                "fixtures": [r.to_dict() for r in reports],
            }
        )
    else:
        for report in reports:
            if report.skipped:
                console.print(f"[yellow]{esc(report.kind)}: SKIPPED[/yellow] {esc(report.skipped)}")
                continue
            table = Table(box=box.SIMPLE_HEAD, title=esc(f"{report.kind}: {'OK' if report.ok else 'NOT OK'}"))
            table.add_column("Check")
            table.add_column("Result")
            table.add_column("Detail", overflow="fold")
            for check in report.checks:
                table.add_row(
                    esc(check.name), "[green]ok[/green]" if check.ok else "[red]FAILED[/red]", esc(check.detail)
                )
            console.print(table)
    if skipped and not as_json:
        err.print(
            f"[yellow]warning:[/yellow] {len(skipped)} kind(s) were not verified on this machine ({esc(', '.join(r.kind for r in skipped))}); "
            "that is not a pass"
        )
    if not all(r.ok for r in reports) or (require_all and skipped):
        raise typer.Exit(EXIT_FINDINGS)
