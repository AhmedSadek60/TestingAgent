"""The ``agentlab`` command (spec section 42)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Annotated

import typer

from agentlab import __version__
from agentlab.cli import cmd_doctor, cmd_init, cmd_test
from agentlab.cli.cmd_credentials import credentials_app
from agentlab.cli.cmd_providers import models_app, providers_app
from agentlab.cli.cmd_runs import runs_app
from agentlab.cli.cmd_skills import skills_app
from agentlab.cli.common import AgentLabGroup, CliState
from agentlab.security.redactor import RedactingLogFilter

app = typer.Typer(
    name="agentlab",
    cls=AgentLabGroup,
    help="AgentLab: test, evaluate and security-test AI agents. Point it at an agent, get an evidenced report.",
    epilog="Exit codes: 0 ok · 1 findings at --fail-on · 2 invalid input · 3 run incomplete · 4 nothing could be tested.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
    pretty_exceptions_show_locals=False,  # a traceback must never print local variables: they can hold secrets
)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"agentlab {__version__}")
        raise typer.Exit()


def configure_logging(verbose: bool) -> None:
    """Warnings and errors to stderr; ``--verbose`` adds AgentLab's own debug log. Records are redacted, and HTTP client
    libraries stay quiet because their debug output can include request details."""
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(RedactingLogFilter())
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.WARNING)
    logging.getLogger("agentlab").setLevel(logging.DEBUG if verbose else logging.WARNING)
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@app.callback()
def root(
    ctx: typer.Context,
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", envvar="AGENTLAB_CONFIG", help="Configuration file (default: ./agentlab.yaml)."),
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show AgentLab's debug log on stderr.")] = False,
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show the version and exit.")
    ] = False,
) -> None:
    ctx.obj = CliState(config_path=config, verbose=verbose)
    configure_logging(verbose)


app.command("init")(cmd_init.init)
app.command("discover")(cmd_test.discover)
app.command("test")(cmd_test.test)
app.command("doctor")(cmd_doctor.doctor)
app.add_typer(runs_app, name="runs")
app.add_typer(skills_app, name="skills")
app.add_typer(providers_app, name="providers")
app.add_typer(models_app, name="models")
app.add_typer(credentials_app, name="credentials")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
