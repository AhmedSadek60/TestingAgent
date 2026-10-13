"""``agentlab feedback``: what happens to Feedback & Support data over time, run on the server itself."""

from __future__ import annotations

from typing import Annotated

import typer
from rich import box
from rich.table import Table

from agentlab.cli.common import console, emit_json, make_services, state
from agentlab.feedback import SubmissionService
from agentlab.feedback.retention import RetentionService

feedback_app = typer.Typer(help="Feedback & Support: apply the retention policy.", no_args_is_help=True)


@feedback_app.command("purge")
def purge(
    ctx: typer.Context,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Say what would be removed and change nothing.")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Apply `feedback.retention` now: closed submissions whose period has passed lose what the policy says.

    Nothing is removed unless the owner set a period, and a submission that is not closed is never touched."""
    services = make_services(state(ctx))
    config = services.config.feedback
    service = RetentionService(SubmissionService(services.store.db, config), services.artifacts, config)
    report = service.run(dry_run=dry_run)
    if as_json:
        emit_json(report.model_dump(mode="json"))
        return
    if not config.retention.active:
        console.print(
            "[dim]no retention period is set (feedback.retention), so nothing is ever removed by the policy.[/dim]"
        )
    table = Table(box=box.SIMPLE, title="Would be removed" if dry_run else "Removed")
    table.add_column("What")
    table.add_column("Count", justify="right")
    for name, value in (
        ("Closed submissions deleted", report.submissions),
        ("Files removed from kept submissions", report.attachments),
        ("Diagnostic items removed", report.diagnostic_items),
        ("Handoff packages removed", report.handoffs),
        ("Internal notes deleted", report.internal_notes),
        ("Stored files deleted", report.files_deleted),
    ):
        table.add_row(name, str(value))
    console.print(table)
