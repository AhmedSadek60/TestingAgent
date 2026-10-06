"""Live progress for the CLI: the same structured events the web UI streams, printed as they happen."""

from __future__ import annotations

import sys
from typing import Any

from rich.console import Console

from agentlab.cli.markup import esc, styled
from agentlab.core.enums import EventType
from agentlab.tracing import Event

SEV_STYLE = {"critical": "bold red", "high": "red", "medium": "yellow", "low": "cyan", "info": "dim"}


class ProgressPrinter:
    """Subscribes to an EventBus. Quiet mode prints only problems; a non-TTY gets plain lines (CI logs)."""

    def __init__(self, console: Console, run_id: str | None = None, *, quiet: bool = False, show_tests: bool = False):
        self.console, self.run_id, self.quiet, self.show_tests = console, run_id, quiet, show_tests
        self.total_tests = 0
        self.done = 0
        self._last_pct = -1

    def __call__(self, ev: Event) -> None:
        if self.run_id and ev.run_id != self.run_id:
            return
        p: dict[str, Any] = ev.payload
        t = ev.type
        if t == EventType.PHASE_COMPLETED and not self.quiet:
            mark = {"completed": "[green]✓[/green]", "skipped": "[yellow]-[/yellow]", "failed": "[red]✗[/red]"}[
                p.get("status", "completed")
            ]
            note = f" [dim]{esc(p['note'])}[/dim]" if p.get("note") else ""
            self.console.print(
                f"[dim]\\[{p.get('index', 0):>2}/{p.get('total', 17)}][/dim] {mark} {esc(p['phase'])} "
                f"[dim]{p.get('duration_s', 0):.1f}s[/dim]{note}"
            )
        elif t == EventType.SKILL_SELECTED and self.show_tests:
            self.console.print(
                f"      [dim]skill[/dim] {esc(p['skill'])} [dim]{esc('; '.join(p.get('reasons', [])[:1]))}[/dim]"
            )
        elif t == EventType.TEST_PLAN_GENERATED and not self.quiet:
            self.total_tests += int(p.get("runnable", 0)) + int(p.get("blocked", 0))
            self.console.print(
                f"      plan wave {p.get('wave', 1)}: {p.get('tests')} tests "
                f"({p.get('runnable')} runnable, {p.get('blocked')} predicted BLOCKED)"
            )
        elif t == EventType.TEST_COMPLETED:
            self.done += 1
            if self.show_tests:
                self.console.print(f"      {esc(ev.test_id)} {esc(p.get('status'))}")
            elif not self.quiet and self.total_tests:
                pct = int(100 * self.done / max(1, self.total_tests))
                if pct // 10 != self._last_pct // 10 and pct < 100:
                    self._last_pct = pct
                    self.console.print(f"      [dim]tests {self.done}/{self.total_tests} ({pct}%)[/dim]")
        elif t == EventType.FINDING_CREATED and not self.quiet:
            sev = str(p.get("severity", "info"))
            tag = "security " if p.get("security") else ""
            head = styled(f"finding {sev.upper()}", SEV_STYLE.get(sev))
            self.console.print(f"      {head} {tag}{esc(str(p.get('title', ''))[:110])}")
        elif t == EventType.LIMIT_REACHED:
            self.console.print(
                f"      [yellow]limit reached[/yellow] {esc(p.get('status'))}: "
                f"{esc(p.get('reason') or p.get('message') or '')}"
            )
        elif t == EventType.ERROR and self.show_tests:
            self.console.print(f"      [red]error[/red] {esc(p.get('message'))}")


def is_tty() -> bool:
    return sys.stdout.isatty()
