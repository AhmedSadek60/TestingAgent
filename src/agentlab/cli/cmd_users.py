"""``agentlab users``: accounts, managed on the server itself.

This is how the first administrator is made: only someone with a shell on the machine (and so with the database) can do it.
Passwords are never accepted as command-line arguments (they would land in shell history and process listings): they are
prompted for with hidden input or piped through ``--stdin``.
"""

from __future__ import annotations

import sys
from typing import Annotated

import typer
from rich import box
from rich.table import Table

from agentlab.accounts import ROLES, AccountService
from agentlab.cli.common import console, emit_json, make_services, state
from agentlab.cli.markup import esc
from agentlab.core.errors import NotFoundError, UserError

users_app = typer.Typer(help="Accounts of the people who sign in (managed on the server).", no_args_is_help=True)


def _service(ctx: typer.Context) -> AccountService:
    services = make_services(state(ctx))
    return AccountService(services.store.db, services.config.accounts)


def _find(svc: AccountService, name: str):  # type: ignore[no-untyped-def]
    user = svc.by_username(name)
    if user is None:
        raise NotFoundError(f"no account named '{name}'")
    return user


@users_app.command("list")
def list_users(
    ctx: typer.Context, as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False
) -> None:
    """List accounts: user name, role, status. Never a password or a hash."""
    rows = _service(ctx).list_users()
    if as_json:
        emit_json(
            [
                {"username": u.username, "role": u.role, "status": u.status, "last_login_at": u.last_login_at}
                for u in rows
            ]
        )
        return
    if not rows:
        console.print(
            "[dim]no accounts yet. Make the first administrator: agentlab users create NAME --role admin[/dim]"
        )
        return
    table = Table("User", "Role", "Status", "Last sign-in", "Locked", box=box.SIMPLE_HEAD)
    for u in rows:
        table.add_row(
            esc(u.username),
            esc(u.role),
            esc(u.status),
            u.last_login_at.strftime("%Y-%m-%d %H:%M") if u.last_login_at else "-",
            "yes" if u.locked_until else "",
        )
    console.print(table)


@users_app.command("create")
def create(
    ctx: typer.Context,
    username: Annotated[str, typer.Argument(help="The user name: 3 to 64 letters, digits, dot, dash, underscore.")],
    role: Annotated[str, typer.Option("--role", help="reporter, maintainer or admin.")] = "reporter",
    display_name: Annotated[str, typer.Option("--display-name", help="The name shown to other people.")] = "",
    stdin: Annotated[bool, typer.Option("--stdin", help="Read the password from standard input.")] = False,
    must_change: Annotated[
        bool, typer.Option("--must-change/--keep", help="Make the person change the password at the first sign-in.")
    ] = False,
) -> None:
    """Create an account. The password is typed at a hidden prompt (or piped with --stdin)."""
    if role not in ROLES:
        raise UserError(f"--role is one of {', '.join(ROLES)}")
    if stdin:
        password = sys.stdin.read().rstrip("\r\n")
        if not password:
            raise UserError("nothing was received on standard input")
    elif sys.stdin.isatty():
        password = typer.prompt("Password", hide_input=True, confirmation_prompt=True)
    else:
        raise UserError("no terminal to prompt for the password: use --stdin")
    svc = _service(ctx)
    user, _ = svc.create_user("command line", username, role=role, password=password, display_name=display_name)  # type: ignore[arg-type]
    if not must_change:
        # a person who typed their own password at the server's console has chosen it: no need to change it again
        from agentlab.storage import orm

        with svc.db.session() as s:
            row = s.get(orm.UserRow, user.id)
            if row is not None:
                row.must_change_password = False
    console.print(f"created [bold]{esc(user.username)}[/bold] as {esc(user.role)}")


@users_app.command("set-role")
def set_role(
    ctx: typer.Context,
    username: Annotated[str, typer.Argument(help="The account.")],
    role: Annotated[str, typer.Argument(help="reporter, maintainer or admin.")],
) -> None:
    """Change an account's role (its sessions end)."""
    svc = _service(ctx)
    user = svc.update("command line", _find(svc, username).id, role=role)  # type: ignore[arg-type]
    console.print(f"{esc(user.username)} is now {esc(user.role)}")


def _set_status(ctx: typer.Context, username: str, status: str) -> None:
    svc = _service(ctx)
    user = svc.update("command line", _find(svc, username).id, status=status)
    console.print(f"{esc(user.username)} is now {esc(user.status)}")


@users_app.command("disable")
def disable(ctx: typer.Context, username: Annotated[str, typer.Argument(help="The account.")]) -> None:
    """Disable an account and end its sessions. The last active administrator cannot be disabled."""
    _set_status(ctx, username, "disabled")


@users_app.command("enable")
def enable(ctx: typer.Context, username: Annotated[str, typer.Argument(help="The account.")]) -> None:
    """Enable (or approve) an account."""
    _set_status(ctx, username, "active")


@users_app.command("unlock")
def unlock(ctx: typer.Context, username: Annotated[str, typer.Argument(help="The account.")]) -> None:
    """Clear the lock that repeated wrong passwords set."""
    svc = _service(ctx)
    svc.unlock("command line", _find(svc, username).id)
    console.print(f"{esc(username)} is unlocked")


@users_app.command("reset-password")
def reset_password(ctx: typer.Context, username: Annotated[str, typer.Argument(help="The account.")]) -> None:
    """Set a temporary password (shown once); the person must change it at the next sign-in."""
    svc = _service(ctx)
    temporary = svc.reset_password("command line", _find(svc, username).id)
    console.print(f"temporary password for [bold]{esc(username)}[/bold]: {esc(temporary)}")
    console.print(
        "[dim]Hand it over yourself. It stops working as soon as it has been used to sign in and changed.[/dim]"
    )
