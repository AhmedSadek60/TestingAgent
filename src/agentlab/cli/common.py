"""Shared CLI plumbing: configuration, services, output and the exit-code contract.

Exit codes (documented in docs/installation.md and used by CI):

* 0 - the run completed and no finding reaches ``--fail-on``
* 1 - findings at or above ``--fail-on`` (default: high)
* 2 - invalid input or configuration (nothing was run against the target)
* 3 - the run did not complete (cancelled, stopped by a budget, or failed)
* 4 - the target could not be tested (no test ran): no verdict

When several apply the order is 4, 1, 3: "nothing was tested" beats everything, a finding at or above the threshold
is reported even when the run was cut short, and 3 means "no finding reached the threshold *but the run is partial*".
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from typer.core import TyperGroup

from agentlab.core.config import AgentLabConfig
from agentlab.core.errors import AgentLabError, PolicyBlocked, UserError
from agentlab.services import Services

EXIT_OK, EXIT_FINDINGS, EXIT_INPUT, EXIT_INCOMPLETE, EXIT_NOT_TESTED = 0, 1, 2, 3, 4

console = Console()
err = Console(stderr=True)


@dataclass
class CliState:
    config_path: Path | None = None
    verbose: bool = False
    json: bool = False


def state(ctx: typer.Context) -> CliState:
    obj = ctx.find_root().obj
    if not isinstance(obj, CliState):
        obj = CliState()
        ctx.find_root().obj = obj
    return obj


def load_config(st: CliState) -> tuple[AgentLabConfig, Path]:
    """The configuration and the directory relative paths in it refer to."""
    cfg = AgentLabConfig.load(st.config_path)
    base = st.config_path.resolve().parent if st.config_path else Path.cwd()
    return cfg, base


def make_services(st: CliState, *, overrides: dict[str, Any] | None = None, migrate: bool = True) -> Services:
    cfg, base = load_config(st)
    if overrides:
        cfg = _override(cfg, overrides)
    return Services.create(cfg, base_dir=base, migrate=migrate)


def _override(cfg: AgentLabConfig, o: dict[str, Any]) -> AgentLabConfig:
    data = cfg.model_dump(mode="json")
    for dotted, value in o.items():
        if value is None:
            continue
        cur = data
        *parents, last = dotted.split(".")
        for part in parents:
            cur = cur[part]
        cur[last] = value
    return AgentLabConfig.model_validate(data)


def run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def emit_json(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, indent=2, default=str) + "\n")


def fail(message: str, code: int = EXIT_INPUT) -> typer.Exit:
    err.print(f"[bold red]error:[/bold red] {message}")
    return typer.Exit(code)


class AgentLabGroup(TyperGroup):
    """The root command group. AgentLab's typed errors become a one-line message and the documented exit code, never a
    stack trace; a bug (any other exception) still raises so it can be reported."""

    def invoke(self, ctx: Any) -> Any:
        try:
            return super().invoke(ctx)
        except (UserError, PolicyBlocked) as exc:
            raise fail(str(exc), EXIT_INPUT) from exc
        except AgentLabError as exc:
            raise fail(f"{exc.kind.value}: {exc}", EXIT_INCOMPLETE) from exc
