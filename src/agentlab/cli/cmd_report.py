"""``agentlab report``, ``agentlab compare`` and ``agentlab review``.

Nothing here runs a test or contacts a target: a report, a comparison and a review are all built from what the stored
run recorded, so they can be produced (and reproduced) at any time after the run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich import box
from rich.markup import escape
from rich.table import Table

from agentlab.cli.cmd_runs import resolve_run_id
from agentlab.cli.common import (
    EXIT_FINDINGS,
    EXIT_INCOMPLETE,
    EXIT_OK,
    console,
    emit_json,
    err,
    make_services,
    state,
)
from agentlab.cli.markup import esc, short, styled
from agentlab.cli.render import STATUS_STYLE
from agentlab.core.enums import ReviewDecision
from agentlab.core.errors import UserError
from agentlab.reporting.bundle import FORMATS, generate_report, normalise_formats, verify_bundle
from agentlab.reporting.compare import KIND_TITLES, Comparison, compare_runs, comparison_markdown, format_metric
from agentlab.reporting.render_html import render_comparison_html
from agentlab.reporting.review import review_finding, review_result, subject_labels
from agentlab.services import Services

review_app = typer.Typer(
    help="Human review: confirm, reject or re-rate a result or a finding. The original evaluation is never changed.",
    no_args_is_help=True,
)

DECISIONS = ", ".join(d.value for d in ReviewDecision)
VERDICT_STYLE = {
    "regressed": "bold red",
    "mixed": "bold yellow",
    "improved": "bold green",
    "unchanged": "green",
    "inconclusive": "bold yellow",
}
COMPAT_STYLE = {"comparable": "green", "comparable_with_caveats": "yellow", "not_comparable": "bold red"}


def _close(services: Services) -> None:
    import asyncio

    asyncio.run(services.aclose())


# ============================================================================================================ report
def report(
    ctx: typer.Context,
    run: Annotated[
        str | None, typer.Option("--run", "-r", help="Run id (a prefix is enough). Default: the latest run.")
    ] = None,
    fmt: Annotated[
        list[str] | None,
        typer.Option(
            "--format", "-f", help=f"{', '.join(FORMATS)} or all; repeat or separate with commas. Default: all."
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Folder for the report bundle (default: the configured reports folder)."),
    ] = None,
    baseline: Annotated[
        str | None,
        typer.Option("--baseline", help="Run to compare with (default: the baseline the run was started with)."),
    ] = None,
    include_sensitive: Annotated[
        bool,
        typer.Option(
            "--include-sensitive",
            help="Embed restricted evidence (screenshots taken while signed in). Off by default.",
        ),
    ] = False,
    verify: Annotated[
        Path | None,
        typer.Option("--verify", help="Check an existing report folder against its checksums.json and exit."),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print what was written as JSON.")] = False,
) -> None:
    """Write the report of a finished run: HTML, Markdown, JSON and PDF, with checksums.

    Each call writes a new version next to the earlier ones; reports are never overwritten.
    """
    if verify is not None:
        problems = verify_bundle(verify)
        if as_json:
            emit_json({"directory": str(verify), "intact": not problems, "problems": problems})
        elif problems:
            for p in problems:
                console.print(f"[red]x[/red] {escape(p)}", soft_wrap=True, highlight=False)
        else:
            console.print(
                f"[green]ok[/green] {escape(str(verify))} matches its checksums", soft_wrap=True, highlight=False
            )
        raise typer.Exit(EXIT_OK if not problems else EXIT_FINDINGS)

    formats = normalise_formats(fmt)
    services = make_services(state(ctx))
    try:
        if run:
            run_id = resolve_run_id(services, run)
        else:
            runs = [r for r in services.store.list_runs(limit=50) if r.get("status") not in {"pending", "running"}]
            if not runs:
                raise UserError("there is no finished run yet: run `agentlab test` first, or give --run")
            run_id = str(runs[0]["id"])
            err.print(f"[dim]no --run given: using the latest run {esc(run_id[:8])}[/dim]")  # stderr keeps --json clean
        baseline_id = resolve_run_id(services, baseline) if baseline else None
        bundle = generate_report(
            services,
            run_id,
            formats=formats,
            output=output,
            baseline=baseline_id,
            include_sensitive=include_sensitive,
        )
    finally:
        _close(services)
    if as_json:
        emit_json(bundle.summary())
        return
    console.print(
        f"[bold]Report[/bold] run {esc(run_id[:8])} · version {bundle.report_version} · bundle {esc(bundle.bundle_id[:12])}"
    )
    # paths are printed unwrapped so they can be copied from a narrow terminal or a CI log
    for name, path in bundle.paths.items():
        console.print(f"  {esc(f'{name:<5}')} {esc(path)}", soft_wrap=True, highlight=False)
    if bundle.directory:
        console.print(
            f"  [dim]checksums: {escape(str(bundle.directory / 'checksums.json'))}[/dim]",
            soft_wrap=True,
            highlight=False,
        )
        console.print(
            f"  [dim]verify:    agentlab report --verify {escape(str(bundle.directory))}[/dim]",
            soft_wrap=True,
            highlight=False,
        )
    for w in bundle.warnings:
        console.print(f"  [yellow]![/yellow] {esc(w)}")


# ========================================================================================================= compare
def compare(
    ctx: typer.Context,
    run_a: Annotated[str, typer.Argument(help="Run A: the baseline (an id prefix is enough).")],
    run_b: Annotated[str, typer.Argument(help="Run B: the run to judge against it.")],
    fmt: Annotated[
        str | None,
        typer.Option("--format", "-f", help="Write the comparison as md, html or json instead of a summary."),
    ] = None,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="File to write (default: standard output).")
    ] = None,
    fail_on_regression: Annotated[
        bool,
        typer.Option(
            "--fail-on-regression",
            help="Exit 1 when run B regressed (or mixed), 3 when the runs cannot be compared, else 0.",
        ),
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Same as --format json, to standard output.")] = False,
) -> None:
    """Compare two runs: new failures, resolved failures, score, latency, cost and reliability changes.

    Runs made with a different plan, scoring profile, skills or judges are compared only with the differences
    flagged at the top; a test that could not run in one of them is a change of coverage, never a regression.
    """
    if as_json:
        fmt = "json"
    if fmt is not None and fmt not in {"md", "html", "json"}:
        raise UserError("--format must be md, html or json")
    services = make_services(state(ctx))
    try:
        a, b = resolve_run_id(services, run_a), resolve_run_id(services, run_b)
        comparison = compare_runs(services, a, b)
    finally:
        _close(services)
    if fmt is None:
        render_comparison(comparison)
    else:
        text = {
            "md": lambda: comparison_markdown(comparison, title=True),
            "html": lambda: render_comparison_html(comparison),
            "json": lambda: json.dumps(comparison.to_json_dict(), indent=2, default=str) + "\n",
        }[fmt]()
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text, encoding="utf-8")
            console.print(f"written to {escape(str(output))}", soft_wrap=True, highlight=False)
        else:
            typer.echo(text, nl=False)
    if fail_on_regression:
        if comparison.verdict == "inconclusive":
            raise typer.Exit(EXIT_INCOMPLETE)
        if comparison.verdict in {"regressed", "mixed"}:
            raise typer.Exit(EXIT_FINDINGS)


def _status(value: str | None) -> str:
    if not value:
        return "-"
    return styled(value, STATUS_STYLE.get(value))


def render_comparison(c: Comparison) -> None:
    comp = c.compatibility
    console.print(f"[bold]Regression comparison[/bold]  A {esc(c.run_a.run_id[:8])}  ->  B {esc(c.run_b.run_id[:8])}")
    console.print(styled(c.summary, VERDICT_STYLE[c.verdict]))
    console.print(
        f"{styled(f'Compatibility: {comp.verdict}'.replace('_', ' '), COMPAT_STYLE[comp.verdict])} - {esc(comp.summary)}"
    )
    if comp.differences:
        t = Table("Impact", "What differs", "A", "B", box=box.SIMPLE_HEAD, title="Differences between the runs")
        for diff in comp.differences:
            t.add_row(
                esc(diff.impact.replace("_", " ")),
                esc(diff.field),
                esc(str(diff.base)[:40]),
                esc(str(diff.current)[:40]),
            )
        console.print(t)
    for note in comp.notes:
        console.print(f"  [yellow]![/yellow] {esc(note)}")
    sc: dict[str, Any] = c.score
    delta = "" if sc.get("overall_delta") is None else f" ({sc['overall_delta']:+.1f})"
    console.print(
        f"Score {esc(format_metric(sc.get('overall_a'), ''))} -> {esc(format_metric(sc.get('overall_b'), ''))}{delta}"
        f" · grade {esc(sc.get('grade_a') or '-')} -> {esc(sc.get('grade_b') or '-')}"
    )
    if sc.get("note"):
        console.print(f"  [yellow]![/yellow] {esc(sc['note'])}")
    for kind, title in KIND_TITLES.items():
        items = [d for d in c.tests if d.kind == kind]
        if not items:
            continue
        console.print(f"\n[bold]{esc(title)}: {len(items)}[/bold]")
        for d in items[:12]:
            console.print(
                f"  {esc(d.test_id)}  {_status(d.status_a)} -> {_status(d.status_b)}  {short(d.note or d.name, 100)}"
            )
        if len(items) > 12:
            console.print(f"  [dim]... and {len(items) - 12} more (use --format md)[/dim]")
    sec = c.security
    if sec.get("new_attacks_succeeded"):
        console.print(
            f"\n[bold red]New attacks that succeeded:[/bold red] {esc(', '.join(sec['new_attacks_succeeded'][:10]))}"
        )
    if sec.get("attacks_no_longer_succeeding"):
        console.print(
            f"[green]Attacks that no longer succeed:[/green] {esc(', '.join(sec['attacks_no_longer_succeeding'][:10]))}"
        )
    if not c.tests:
        console.print("\n[green]No test changed outcome.[/green]")


# ========================================================================================================== review
ReviewRun = Annotated[str, typer.Argument(help="Run id (an id prefix is enough).")]
Reviewer = Annotated[str, typer.Option("--reviewer", help="Who is reviewing (recorded with the review).")]
Decision = Annotated[str, typer.Option("--decision", "-d", help=f"One of: {DECISIONS}.")]
Reason = Annotated[str, typer.Option("--reason", help="Why (required for most decisions).")]
Comment = Annotated[str, typer.Option("--comment", help="An optional note for the next reader.")]


def _print_review(row: dict[str, Any]) -> None:
    console.print(
        f"[green]recorded[/green] {esc(row['decision'])} on {esc(row['subject_type'])} by {esc(row['reviewer'])} "
        f"(review {esc(str(row['id'])[:8])}); the original evaluation is unchanged."
    )
    console.print("[dim]Run `agentlab report` to write a report that shows it next to the original.[/dim]")


@review_app.command("result")
def review_result_cmd(
    ctx: typer.Context,
    run: ReviewRun,
    test: Annotated[str, typer.Argument(help="Test id (or result id).")],
    decision: Decision,
    reviewer: Reviewer,
    reason: Reason = "",
    comment: Comment = "",
    score: Annotated[float | None, typer.Option("--score", help="New score 0..1 (override_score).")] = None,
    severity: Annotated[str | None, typer.Option("--severity", help="New severity (change_severity).")] = None,
) -> None:
    """Review a test result: false positive, false negative, override its score or severity, or comment."""
    services = make_services(state(ctx))
    try:
        row = review_result(
            services,
            resolve_run_id(services, run),
            test,
            decision=decision,
            reviewer=reviewer,
            reason=reason,
            comment=comment,
            score=score,
            severity=severity,
        )
    finally:
        _close(services)
    _print_review(row)


@review_app.command("finding")
def review_finding_cmd(
    ctx: typer.Context,
    run: ReviewRun,
    finding: Annotated[str, typer.Argument(help="Finding id (or the id of the test that produced it).")],
    decision: Decision,
    reviewer: Reviewer,
    reason: Reason = "",
    comment: Comment = "",
    severity: Annotated[str | None, typer.Option("--severity", help="New severity (change_severity).")] = None,
) -> None:
    """Review a finding: approve (confirm) it, mark it a false positive, change its severity or comment."""
    services = make_services(state(ctx))
    try:
        row = review_finding(
            services,
            resolve_run_id(services, run),
            finding,
            decision=decision,
            reviewer=reviewer,
            reason=reason,
            comment=comment,
            severity=severity,
        )
    finally:
        _close(services)
    _print_review(row)


@review_app.command("list")
def review_list(
    ctx: typer.Context,
    run: ReviewRun,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """List the reviews of a run, oldest first, with the values before and after."""
    services = make_services(state(ctx))
    try:
        run_id = resolve_run_id(services, run)
        rows = services.store.list_reviews(run_id)
        labels = subject_labels(services, run_id, rows)
    finally:
        _close(services)
    if as_json:
        emit_json(rows)
        return
    if not rows:
        console.print("[dim]no reviews for this run[/dim]")
        return
    t = Table("When", "Reviewer", "About", "Decision", "Change", "Reason", box=box.SIMPLE_HEAD)
    for i in (0, 2, 3):  # when, about and decision are never split across lines (a test id must stay one word)
        t.columns[i].no_wrap = True
    for r in sorted(rows, key=lambda x: str(x.get("created_at"))):
        original, reviewed = r.get("original") or {}, r.get("reviewed") or {}
        change = "; ".join(f"{k} {_value(original.get(k))} -> {_value(v)}" for k, v in reviewed.items()) or "-"
        about = labels.get(str(r["subject_id"]), str(r["subject_id"])[:8])
        t.add_row(
            esc(str(r.get("created_at"))[:16]),
            esc(r["reviewer"]),
            esc(f"{r['subject_type']} {about}"),
            esc(r["decision"]),
            esc(change),
            esc(r.get("reason") or r.get("comment") or ""),
        )
    console.print(t)


def _value(v: Any) -> str:
    return "-" if v is None else f"{v:.2f}" if isinstance(v, float) else str(v)
