"""``agentlab credentials``: test credentials for the targets you are allowed to test.

Secrets are never accepted as command-line arguments (they would land in shell history and process listings). They are
prompted for with hidden input, piped through ``--stdin``, read from a file (browser state) or referenced as an
environment variable (``--from-env``), in which case no value is stored at all.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, cast

import typer
from rich import box
from rich.table import Table

from agentlab.cli.common import console, emit_json, make_services, run_async, state
from agentlab.core.errors import UserError
from agentlab.security.credentials import CredentialKind, CredentialProfile

credentials_app = typer.Typer(help="Stored test credentials (encrypted, scoped to hosts).", no_args_is_help=True)

KINDS = ("bearer", "api_key", "basic", "headers", "cookies", "oauth_token", "browser_state", "env")
FIXED_FIELDS = {"bearer": ["token"], "oauth_token": ["token"], "api_key": ["key"], "basic": ["username", "password"]}
CLEAR_FIELDS = {"username"}  # not secret: shown while typing


@credentials_app.command("list")
def credentials_list(
    ctx: typer.Context,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """List credential profiles. Only names, kinds and scopes are shown; values never are."""
    services = make_services(state(ctx), migrate=False)
    rows = services.credentials.list_profiles()
    run_async(services.aclose())
    if as_json:
        emit_json(rows)
        return
    if not rows:
        console.print(
            "[dim]no credentials stored. Add one with `agentlab credentials add NAME --kind bearer --scope host`[/dim]"
        )
        return
    t = Table("Name", "Kind", "Scope (hosts)", "Fields", "Expires", "Version", box=box.SIMPLE_HEAD)
    for r in rows:
        t.add_row(
            r["name"],
            r["kind"],
            ", ".join(r.get("scopes") or []) or "[yellow]any host[/yellow]",
            ", ".join(r.get("fields") or []) or "stored encrypted",
            str(r.get("expires_at") or "-"),
            str(r.get("secret_version")),
        )
    console.print(t)


@credentials_app.command("add")
def credentials_add(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Name to refer to it by, e.g. test-user.")],
    kind: Annotated[str, typer.Option("--kind", "-k", help=f"One of: {', '.join(KINDS)}.")] = "bearer",
    scope: Annotated[
        list[str] | None,
        typer.Option("--scope", help="Host (or URL prefix) the credential may be sent to (repeatable)."),
    ] = None,
    unscoped: Annotated[
        bool, typer.Option("--unscoped", help="Allow any host the target definition names (not recommended).")
    ] = False,
    field: Annotated[
        list[str] | None, typer.Option("--field", help="Field/header/cookie name to prompt for (headers, cookies).")
    ] = None,
    from_env: Annotated[
        list[str] | None,
        typer.Option(
            "--from-env", help="FIELD=ENV_VAR: read the value from the environment at use time; nothing stored."
        ),
    ] = None,
    state_file: Annotated[
        Path | None, typer.Option("--state-file", help="Playwright storage-state JSON (browser_state).")
    ] = None,
    header_name: Annotated[
        str | None, typer.Option("--header-name", help="Header for api_key (default X-API-Key).")
    ] = None,
    description: Annotated[str, typer.Option("--description", help="What this credential is for.")] = "",
    expires: Annotated[str | None, typer.Option("--expires", help="Expiry date, ISO format (e.g. 2026-12-31).")] = None,
    use_stdin: Annotated[
        bool, typer.Option("--stdin", help="Read the single secret value from standard input.")
    ] = False,
) -> None:
    """Store a test credential, encrypted. The value is prompted for (hidden), piped with --stdin, or referenced
    with --from-env."""
    if kind not in KINDS:
        raise UserError(f"--kind must be one of {', '.join(KINDS)}")
    if not (scope or unscoped) and not from_env:
        raise UserError(
            "give --scope HOST (the hosts this credential may be sent to), or --unscoped to allow any host the target names"
        )
    references: dict[str, str] = {}
    for item in from_env or []:
        key, sep, var = item.partition("=")
        if not sep or not key or not var:
            raise UserError("--from-env expects FIELD=ENV_VAR")
        references[key] = f"env:{var}"
    secrets = _collect_secrets(kind, field or [], references, state_file, use_stdin)
    expires_at = _parse_expiry(expires)
    profile = CredentialProfile(
        name=name,
        kind=cast(CredentialKind, kind),
        description=description,
        scopes=list(scope or []),
        header_name=header_name,
        expires_at=expires_at,
        references=references,
    )
    services = make_services(state(ctx), migrate=False)
    try:
        services.credentials.add(profile, secrets or None)
    finally:
        run_async(services.aclose())
    where = "references environment variables only" if references and not secrets else "stored encrypted"
    console.print(f"credential [bold]{name}[/bold] ({kind}) {where}; scope: {', '.join(scope or []) or 'any host'}")
    console.print(f"use it with `agentlab test --credentials {name} ...`")


def _collect_secrets(
    kind: str, fields: list[str], references: dict[str, str], state_file: Path | None, use_stdin: bool
) -> dict[str, str]:
    if kind == "env":
        if not references:
            raise UserError("kind 'env' needs --from-env FIELD=ENV_VAR")
        return {}
    if kind == "browser_state":
        if references:
            raise UserError("browser_state cannot be a reference; give --state-file")
        if state_file is None or not state_file.is_file():
            raise UserError("browser_state needs --state-file PATH (a Playwright storage state JSON)")
        return {"state": state_file.read_text(encoding="utf-8")}
    wanted = FIXED_FIELDS.get(kind) or list(fields)
    if kind in {"headers", "cookies"} and not (fields or references):
        raise UserError(f"kind '{kind}' needs at least one --field NAME (or --from-env NAME=ENV_VAR)")
    missing = [f for f in wanted if f not in references]
    if not missing:
        return {}
    if use_stdin:
        if len(missing) != 1:
            raise UserError("--stdin works for credentials with a single secret field")
        value = sys.stdin.read().rstrip("\r\n")
        if not value:
            raise UserError("nothing was received on standard input")
        return {missing[0]: value}
    if not sys.stdin.isatty():
        raise UserError("no terminal to prompt for the secret; use --stdin, --from-env or --state-file")
    out: dict[str, str] = {}
    for f in missing:
        value = typer.prompt(f, hide_input=f not in CLEAR_FIELDS)
        if not value:
            raise UserError(f"{f} must not be empty")
        out[f] = value
    return out


def _parse_expiry(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise UserError("--expires must be an ISO date such as 2026-12-31") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@credentials_app.command("remove")
def credentials_remove(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Credential name.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
) -> None:
    """Delete a stored credential."""
    services = make_services(state(ctx), migrate=False)
    try:
        if not services.credentials.has(name):
            raise UserError(f"credential '{name}' is not stored")
        if not yes and not typer.confirm(f"Delete credential '{name}'?", default=False):
            console.print("kept")
            return
        services.credentials.remove(name)
    finally:
        run_async(services.aclose())
    console.print(f"removed {name}")
