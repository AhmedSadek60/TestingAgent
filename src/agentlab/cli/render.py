"""Rich rendering for the CLI. Every statement that qualifies a result (blocked, not tested, capped) is shown, not hidden."""

from __future__ import annotations

from collections import Counter
from typing import Any

from rich import box
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agentlab.core.enums import TestStatus
from agentlab.core.models import AgentProfile
from agentlab.design.models import TestPlan
from agentlab.design.render import plan_markdown
from agentlab.orchestrator.options import RunOutcome

SEV_STYLE = {"critical": "bold red", "high": "red", "medium": "yellow", "low": "cyan", "info": "dim"}
STATUS_STYLE = {
    "passed": "green",
    "failed": "red",
    "blocked": "yellow",
    "error": "magenta",
    "timeout": "red",
    "skipped": "dim",
}
VERDICT_STYLE = {
    "vulnerable": "bold red",
    "resistant": "green",
    "partially_tested": "yellow",
    "not_tested": "yellow",
    "not_covered": "dim",
    "not_applicable": "dim",
}


def short(text: object, n: int = 100) -> str:
    s = str(text).replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def render_profile(c: Console, profile: AgentProfile, warnings: list[str] | None = None) -> None:
    c.print(Panel(Text(profile.summary or profile.target_name), title=f"Target: {profile.target_name}", expand=False))
    if profile.types:
        t = Table("Type", "Confidence", "Evidence", box=box.SIMPLE_HEAD, title="Detected agent types")
        for ts in profile.types[:8]:
            ev = "; ".join(f"{e.source}: {e.detail}" for e in ts.evidence[:2])
            t.add_row(ts.type.value, f"{ts.confidence:.2f}", short(ev, 90))
        c.print(t)
    c.print(
        f"[bold]Evaluation mode[/bold] {', '.join(m.value for m in profile.modes) or 'unknown'}   "
        f"[bold]Interfaces[/bold] {', '.join(profile.interfaces) or 'none'}   "
        f"[bold]Models[/bold] {', '.join(profile.models[:4]) or 'unknown'}   "
        f"[bold]Frameworks[/bold] {', '.join(profile.frameworks[:6]) or 'unknown'}"
    )
    if profile.tools:
        t = Table("Tool", "Side effects", "Description", box=box.SIMPLE_HEAD, title=f"{len(profile.tools)} tool(s)")
        for tool in profile.tools[:20]:
            t.add_row(tool.name, tool.side_effects, short(tool.description, 70))
        c.print(t)
    detected = [e for e in profile.capability_matrix if e.detected]
    absent = [e for e in profile.capability_matrix if not e.detected]
    if profile.capability_matrix:
        c.print(
            "[bold]Capabilities detected[/bold] "
            + (", ".join(e.capability for e in detected) or "none")
            + "   [dim]not detected: "
            + (", ".join(e.capability for e in absent) or "none")
            + "[/dim]"
        )
    if profile.attack_surfaces:
        c.print("[bold]Attack surfaces[/bold] " + "; ".join(profile.attack_surfaces[:8]))
    if profile.limitations:
        c.print("[bold]Known limitations[/bold]")
        for line in profile.limitations[:6]:
            c.print(f"  - {short(line, 140)}")
    for w in (warnings or [])[:10]:
        c.print(f"[yellow]warning[/yellow] {short(w, 160)}")


def render_plan(c: Console, plan: TestPlan, *, detail: bool = False) -> None:
    if detail:
        c.print(Markdown(plan_markdown(plan, detail=True)))
        return
    counts = plan.counts()
    c.print(
        Panel(Text(plan.summary), title=f"Test plan {plan.id} · suite {plan.suite} · {plan.intensity}", expand=False)
    )
    c.print(
        f"[bold]{counts['tests']}[/bold] tests: {counts['runnable']} runnable, "
        f"[yellow]{counts['blocked']} predicted BLOCKED[/yellow] (not failed: reported as not tested)   "
        f"plan hash [dim]{plan.plan_hash}[/dim]"
    )
    for w in plan.warnings:
        style = {"blocker": "bold red", "warning": "yellow", "info": "dim"}[w.level]
        c.print(f"  [{style}]{w.level}[/] {short(w.message, 150)}")
    t = Table("Skill", "Tests", "Blocked", "Why", box=box.SIMPLE_HEAD, title="Skills used")
    for m in plan.skills:
        if m.selected:
            t.add_row(
                f"{m.skill} {m.version}", str(m.tests), str(m.predicted_blocked), short("; ".join(m.reasons[:1]), 90)
            )
    c.print(t)
    gaps = [e for e in plan.coverage if e.status in {"not_covered", "partial"}]
    if gaps:
        c.print("[bold]Coverage gaps[/bold]")
        for e in gaps:
            c.print(f"  {e.key} {e.name}: {e.status.replace('_', ' ')} - {short(e.note, 120)}")
    b = plan.budget
    cost = f"${b.est_cost_usd:.4f}" if b.est_cost_usd is not None else "unknown"
    c.print(
        f"[bold]Estimate[/bold] ~{b.target_calls} target calls, ~{b.est_tokens} tokens, ~{b.est_wall_seconds:.0f}s, "
        f"cost {cost} [dim]({b.cost_note})[/dim]"
    )


def _scorecard_table(o: RunOutcome) -> Table | None:
    sc = o.scorecard
    if sc is None:
        return None
    t = Table("Category", "Score", "Confidence", "Tests", "Note", box=box.SIMPLE_HEAD, title="Scorecard")
    for cat in sc.categories:
        score = "n/a" if cat.score is None else f"{cat.score:.0f}"
        t.add_row(cat.category, score, f"{cat.confidence:.2f}", f"{cat.passed}/{cat.tests}", short(cat.note or "", 60))
    return t


def render_outcome(c: Console, o: RunOutcome, *, top_findings: int = 10) -> None:
    style = {"completed": "green", "cancelled": "yellow", "failed": "red"}.get(o.status.value, "yellow")
    c.print(
        Panel(
            f"run [bold]{o.run_id}[/bold] · target [bold]{o.target}[/bold] · status [{style}]{o.status.value}[/] · "
            f"{o.limits.get('elapsed_s', 0)}s · {o.limits.get('tokens', 0)} tokens · ${o.limits.get('cost_usd', 0):.4f} · "
            f"{len(o.plans)} wave(s)",
            expand=False,
        )
    )
    if o.error:
        c.print(f"[red]error[/red] {o.error}")
    counts = Counter({k: v for k, v in o.counts.items()})
    c.print(
        "  ".join(f"[{STATUS_STYLE.get(k, '')}]{k}[/] {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
        or "[yellow]no tests ran[/yellow]"
    )
    if not o.tested:
        c.print(
            Panel(
                "[bold yellow]Nothing could be tested, so there is no verdict.[/bold yellow] "
                "A BLOCKED or unavailable test is not a pass. See the warnings below.",
                expand=False,
            )
        )
    table = _scorecard_table(o)
    if table is not None and o.scorecard and o.scorecard.categories:
        c.print(table)
        sc = o.scorecard
        overall = "n/a" if sc.overall is None else f"{sc.overall:.1f}"
        c.print(
            f"[bold]Overall[/bold] {overall}  [bold]Grade[/bold] {sc.grade or 'n/a'}  [dim]profile {sc.profile}[/dim]"
        )
        if sc.security_cap_applied:
            c.print(f"[red]Security cap:[/red] {sc.cap_reason}")
        for q in sc.qualifiers:
            c.print(Panel(Text(q), border_style="yellow", expand=False, title="Read this with the score"))
        for n in sc.notes:
            c.print(f"  [dim]{short(n, 160)}[/dim]")
    if o.security is not None:
        sec = o.security
        posture_style = {
            "vulnerabilities_observed": "bold red",
            "no_vulnerabilities_observed": "green",
            "partially_tested": "yellow",
            "not_tested": "yellow",
        }[sec.posture]
        c.print(f"[bold]Security[/bold] [{posture_style}]{sec.posture.replace('_', ' ')}[/]: {sec.summary}")
        shown = [x for x in sec.categories if x.verdict not in {"not_applicable"}]
        if shown:
            t = Table("", "Category", "Verdict", "Tests", "Note", box=box.SIMPLE_HEAD)
            for x in shown:
                t.add_row(
                    x.code,
                    x.name,
                    f"[{VERDICT_STYLE[x.verdict]}]{x.verdict.replace('_', ' ')}[/]",
                    f"{x.passed}/{x.tests}",
                    short(x.note, 70),
                )
            c.print(t)
        for a in sec.attacks_succeeded[:8]:
            c.print(
                f"  [red]attack succeeded[/red] {a.test_id} ({', '.join(a.codes) or short(a.name, 48)}) "
                f"[{SEV_STYLE.get(a.severity or 'info', '')}]{(a.severity or '?').upper()}[/]"
                + (f" via {'/'.join(a.channels)}" if a.channels else "")
            )
        for cv in sec.caveats:
            c.print(f"  [dim]caveat: {short(cv, 170)}[/dim]")
    if o.reliability is not None:
        r = o.reliability
        c.print(f"[bold]Reliability[/bold] {r.verdict.replace('_', ' ')}" + (f" - {r.notes[0]}" if r.notes else ""))
    if o.findings:
        t = Table(
            "Severity", "Test", "Finding", "Confidence", "Likely cause", box=box.SIMPLE_HEAD, title="Top findings"
        )
        for f in o.findings[:top_findings]:
            t.add_row(
                f"[{SEV_STYLE[f.severity.value]}]{f.severity.value.upper()}[/]",
                f.test_id,
                short(f.title, 70),
                f"{f.confidence:.2f}",
                f.root_cause.value.replace("_", " "),
            )
        c.print(t)
        if len(o.findings) > top_findings:
            c.print(
                f"  [dim]... and {len(o.findings) - top_findings} more: `agentlab runs show {o.run_id[:8]}` lists them "
                "all, `--finding TEST_ID` shows one in full[/dim]"
            )
    blocked = Counter(
        short(r.blocked_reason or "prerequisite missing", 110) for r in o.results if r.status == TestStatus.BLOCKED
    )
    if blocked:
        c.print("[bold]BLOCKED tests[/bold] (not run, not failed):")
        for reason, how_many in blocked.most_common(5):
            c.print(f"  {how_many} x {reason}")
    if o.warnings:
        c.print("[bold]Warnings[/bold]")
        for w in o.warnings[:8]:
            c.print(f"  [yellow]-[/yellow] {short(w, 170)}")
    if o.report is not None and getattr(o.report, "paths", None):
        c.print("[bold]Reports[/bold] " + ", ".join(str(p) for p in o.report.paths.values()))
    plan_hash = o.manifest.get("plan", {}).get("hash", "")
    judges = o.manifest.get("judges", {})
    c.print(
        f"[dim]manifest: plan {plan_hash} · {len(o.manifest.get('skills', []))} skills · scoring "
        f"{o.manifest.get('scoring_profile', {}).get('name')} · judge "
        f"{'on' if judges.get('enabled') else 'off'} · agentlab {o.manifest.get('agentlab_version')}[/dim]"
    )


def summary_group(parts: list[Any]) -> Group:
    return Group(*parts)
