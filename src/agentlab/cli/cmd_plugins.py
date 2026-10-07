"""``agentlab plugins``: which plug-ins are installed, by kind, and where each one is defined.

It imports the modules the configuration lists under ``plugins:`` and loads the installed entry points, exactly as a run
does, so what it shows is what a run would use. Nothing is sent anywhere and nothing is written.
"""

from __future__ import annotations

from typing import Annotated, Any

import typer
from rich import box
from rich.table import Table

from agentlab.cli.common import console, emit_json, err, load_config, state
from agentlab.cli.markup import esc
from agentlab.core.errors import UserError
from agentlab.registries import all_registries, is_builtin, origin
from agentlab.services import load_plugin_modules

plugins_app = typer.Typer(help="Plug-ins: what is installed.", no_args_is_help=True)


def _rows(kind: str | None) -> dict[str, list[dict[str, Any]]]:
    registries = all_registries()
    if kind is not None and kind not in registries:
        raise UserError(f"unknown kind '{kind}' (use {', '.join(registries)})")
    out: dict[str, list[dict[str, Any]]] = {}
    for name, registry in registries.items():
        if kind is not None and name != kind:
            continue
        out[name] = [
            {"name": item_name, "builtin": is_builtin(item), "module": origin(item)}
            for item_name, item in registry.items()
        ]
    return out


@plugins_app.command("list")
def plugins_list(
    ctx: typer.Context,
    kind: Annotated[
        str | None, typer.Argument(help="Only this kind (providers, adapters, engines, sandbox, ...). Default: all.")
    ] = None,
    show_all: Annotated[
        bool, typer.Option("--all", "-a", help="List every name, built-in ones too (assertions and parsers are many).")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON: every name with where it is defined.")] = False,
) -> None:
    """List plug-ins by kind: how many ship with AgentLab and which ones came from a package or module you installed.

    A plug-in that could not be loaded is not listed; the reason is printed as a warning.
    """
    cfg, _ = load_config(state(ctx))
    for warning in load_plugin_modules(cfg.plugins):
        err.print(f"warning: {warning}", markup=False)
    rows = _rows(kind)
    if as_json:
        emit_json(rows)
        return
    table = Table("Kind", "Built in", "Plug-ins" if not show_all else "Names", box=box.SIMPLE_HEAD)
    for name, items in rows.items():
        builtin = [r for r in items if r["builtin"]]
        added = [r for r in items if not r["builtin"]]
        if show_all:
            names = ", ".join(
                esc(r["name"]) if r["builtin"] else f"[bold]{esc(r['name'])}[/bold] ({esc(r['module'])})" for r in items
            )
        else:
            names = ", ".join(f"{esc(r['name'])} ({esc(r['module'])})" for r in added) or "[dim]none[/dim]"
        table.add_row(esc(name), str(len(builtin)), names)
    console.print(table)
    console.print(
        "[dim]Add one with an entry point in the group agentlab.<kind> or a module under `plugins:` in agentlab.yaml "
        "(docs/plugins.md). Test skills are listed by `agentlab skills list`.[/dim]"
    )
