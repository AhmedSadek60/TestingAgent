"""``agentlab doctor``: check what this machine can and cannot do, so a missing capability is known before a run.

The checks themselves live in :mod:`agentlab.diagnostics` (the API reports the same ones). Remote providers are *not*
contacted unless ``--live`` is given; their keys are only checked for being set.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.table import Table

from agentlab.cli.common import console, emit_json, make_services, run_async, state
from agentlab.cli.markup import esc
from agentlab.diagnostics import Check, Level, run_checks

MARK: dict[Level, str] = {
    "ok": "[green]ok[/green]",
    "info": "[dim]info[/dim]",
    "warn": "[yellow]warn[/yellow]",
    "fail": "[red]FAIL[/red]",
}


def doctor(
    ctx: typer.Context,
    live: Annotated[
        bool, typer.Option("--live", help="Also contact remote providers (a model-listing request with their keys).")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Check the environment: Docker, browser, providers, storage, skills. Exit code 1 when something is broken."""
    st = state(ctx)
    services = make_services(st, migrate=False)
    config_path = st.config_path or (Path("agentlab.yaml") if Path("agentlab.yaml").exists() else None)

    async def go() -> list[Check]:
        try:
            return await run_checks(services, config_path, live=live)
        finally:
            await services.aclose()

    checks = run_async(go())
    failed = any(c.level == "fail" for c in checks)
    if as_json:
        emit_json({"ok": not failed, "checks": [c.__dict__ for c in checks]})
    else:
        t = Table("", "Check", "Result", box=box.SIMPLE_HEAD)
        for c in checks:
            t.add_row(MARK[c.level], esc(c.name), esc(c.detail) + (f"\n[dim]-> {esc(c.fix)}[/dim]" if c.fix else ""))
        console.print(t)
        warns = sum(c.level == "warn" for c in checks)
        console.print(
            "[red]problems found[/red]"
            if failed
            else "[green]ready[/green]"
            + (
                f", {warns} warning(s): the features named above will be reported as BLOCKED, not failed"
                if warns
                else ""
            )
        )
    if failed:
        raise typer.Exit(1)
