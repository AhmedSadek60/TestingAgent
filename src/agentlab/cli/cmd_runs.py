"""``agentlab runs``: look at stored runs. Nothing here recomputes a result; it shows what was concluded at the time."""

from __future__ import annotations

from typing import Annotated

import typer
from rich import box
from rich.table import Table

from agentlab.cli.common import console, emit_json, make_services, state
from agentlab.cli.render import SEV_STYLE, STATUS_STYLE, render_outcome, render_plan, short
from agentlab.core.errors import UserError
from agentlab.orchestrator.load import load_outcome
from agentlab.services import Services

runs_app = typer.Typer(help="Stored runs: list them, show a result, a plan or a finding.", no_args_is_help=True)

RunId = Annotated[str, typer.Argument(help="Run id (an unambiguous prefix is enough).")]


def resolve_run_id(services: Services, ident: str) -> str:
    """An exact id, or the one stored run whose id starts with ``ident``."""
    try:
        return str(services.store.get_run(ident)["id"])
    except UserError:
        pass
    hits = [r["id"] for r in services.store.list_runs(limit=1000) if str(r["id"]).startswith(ident)]
    if len(hits) == 1:
        return str(hits[0])
    if not hits:
        raise UserError(f"run '{ident}' not found (see `agentlab runs list`)")
    raise UserError(f"'{ident}' matches {len(hits)} runs; give more of the id")


@runs_app.command("list")
def runs_list(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option("--limit", "-n", help="How many runs to show.")] = 20,
    project: Annotated[str | None, typer.Option("--project", help="Only this project.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """List recent runs, newest first."""
    services = make_services(state(ctx))
    try:
        project_id = services.store.get_project(project)["id"] if project else None
        rows = services.store.list_runs(project_id=project_id, limit=limit)
    finally:
        _close(services)
    if as_json:
        emit_json(
            [
                {k: r.get(k) for k in ("id", "mode", "status", "started_at", "finished_at", "totals")}
                | {"target": r["manifest"].get("target", {}).get("name")}
                for r in rows
            ]
        )
        return
    if not rows:
        console.print("[dim]no runs yet: try `agentlab test --mock success --intensity quick`[/dim]")
        return
    t = Table(
        "Run", "Target", "Suite", "Status", "Tests", "Findings", "Score", box=box.SIMPLE_HEAD, title="Recent runs"
    )
    qualified = False
    for r in rows:
        tot = r.get("totals") or {}
        score = tot.get("overall")
        status = r["status"]
        grade = str(tot.get("grade") or "")
        qualified = qualified or "(" in grade
        t.add_row(
            str(r["id"])[:8],
            str(r["manifest"].get("target", {}).get("name") or ""),
            str(r["mode"]),
            f"[{STATUS_STYLE.get(status, '')}]{status}[/]" if status in STATUS_STYLE else status,
            str(tot.get("tests", tot.get("runnable", ""))),
            str(tot.get("findings", "")),
            "" if score is None else f"{score:.0f} {grade.split()[0] if grade else ''}{'*' if '(' in grade else ''}",
        )
    console.print(t)
    if qualified:
        console.print(
            "[dim]* the grade carries a qualification (security cap, limited scope...): `agentlab runs show`[/dim]"
        )
    console.print("[dim]Use the first characters of an id: `agentlab runs show 68ef0ba`.[/dim]")


@runs_app.command("show")
def runs_show(
    ctx: typer.Context,
    run: RunId,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
    tests: Annotated[bool, typer.Option("--tests", help="List every test with its status.")] = False,
    finding: Annotated[str | None, typer.Option("--finding", help="Show one finding in full (its id).")] = None,
) -> None:
    """Show a finished run: scorecard, security, reliability, findings and what was not tested."""
    services = make_services(state(ctx))
    try:
        run_id = resolve_run_id(services, run)
        outcome = load_outcome(services, run_id)
    finally:
        _close(services)
    if as_json:
        emit_json(outcome.to_dict())
        return
    if finding:
        hit = next((f for f in outcome.findings if f.id == finding or f.test_id == finding), None)
        if hit is None:
            raise UserError(f"no finding '{finding}' in run {run_id}")
        _show_finding(hit)
        return
    render_outcome(console, outcome)
    if tests:
        t = Table("Test", "Category", "Status", "Score", "Why / reason", box=box.SIMPLE_HEAD, title="Tests")
        t.columns[0].no_wrap = True
        for r in outcome.results:
            style = STATUS_STYLE.get(r.status.value, "")
            t.add_row(
                r.test_id,
                r.category,
                f"[{style}]{r.status.value}[/]",
                f"{r.score:.2f}",
                short(r.blocked_reason or r.test_name, 70),
            )
        console.print(t)


@runs_app.command("plan")
def runs_plan(
    ctx: typer.Context,
    run: RunId,
    detail: Annotated[bool, typer.Option("--detail", help="Show every test.")] = False,
) -> None:
    """Show the test plan(s) a run used: which skills, which tests and why, what was predicted to be blocked."""
    services = make_services(state(ctx))
    try:
        outcome = load_outcome(services, resolve_run_id(services, run))
    finally:
        _close(services)
    if not outcome.plans:
        raise UserError("this run has no stored plan")
    for plan in outcome.plans:
        render_plan(console, plan, detail=detail)


def _show_finding(f: object) -> None:
    from agentlab.core.models import Finding

    assert isinstance(f, Finding)
    sev = f.severity.value
    console.print(f"[{SEV_STYLE[sev]}]{sev.upper()}[/] [bold]{f.title}[/bold]")
    console.print(
        f"test {f.test_id} · category {f.category} · confidence {f.confidence:.2f} · "
        f"likely cause {f.root_cause.value.replace('_', ' ')} ({f.root_cause_confidence:.2f}) · {f.fact_kind}"
    )
    for label, text in (
        ("Expected", f.expected),
        ("Observed", f.observed),
        ("Impact", f.impact),
        ("Reproduction", f.reproduction),
        ("Recommendation", f.recommendation),
    ):
        console.print(f"[bold]{label}[/bold] {text}")
    for label, items in (("Facts", f.facts), ("Inferences", f.inferences), ("Judgments", f.judgments)):
        if items:
            console.print(f"[bold]{label}[/bold]")
            for i in items:
                console.print(f"  - {short(i, 200)}")
    if f.evidence:
        console.print("[bold]Evidence[/bold] " + ", ".join(f.evidence[:10]))


def _close(services: Services) -> None:
    import asyncio

    asyncio.run(services.aclose())
