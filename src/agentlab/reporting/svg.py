"""Inline SVG charts for the HTML report.

No JavaScript, no external library, no image files: every chart is a self-contained ``<svg>`` whose colours come from
CSS classes (so light/dark themes and printing just work) and whose text is escaped. The report stays one file that
opens offline.
"""

from __future__ import annotations

import html
import math
from collections.abc import Sequence
from typing import Any

from markupsafe import Markup

STATUS_CLASS = {
    "passed": "s-passed",
    "failed": "s-failed",
    "timeout": "s-failed",
    "blocked": "s-blocked",
    "error": "s-error",
    "skipped": "s-skipped",
}
SEVERITY_CLASS = {
    "critical": "v-critical",
    "high": "v-high",
    "medium": "v-medium",
    "low": "v-low",
    "info": "v-info",
}


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _short(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def score_class(score: float | None) -> str:
    if score is None:
        return "s-skipped"
    return "q-good" if score >= 90 else "q-ok" if score >= 75 else "q-warn" if score >= 60 else "q-bad"


def _svg(width: int, height: int, label: str, body: str, extra: str = "", display_width: str = "100%") -> Markup:
    return Markup(  # noqa: S704 - every dynamic part of ``body`` is escaped by the callers in this module
        f'<svg class="chart" viewBox="0 0 {width} {height}" width="{display_width}" role="img" aria-label="{esc(label)}" '
        f'preserveAspectRatio="xMinYMin meet"{extra}><title>{esc(label)}</title>{body}</svg>'
    )


def bar_chart(
    items: Sequence[tuple[str, float | None, str]],
    *,
    maximum: float = 100.0,
    label: str = "Bar chart",
    width: int = 720,
    unit: str = "",
    label_width: int = 190,
    value_fmt: str = "{:.0f}",
) -> Markup:
    """Horizontal bars: ``(label, value, css class)``; a ``None`` value is drawn as 'n/a' without a bar."""
    row = 28
    height = max(row * len(items) + 8, 40)
    bar_x = label_width + 8
    bar_w = width - bar_x - 70
    parts: list[str] = []
    for i, (name, value, cls) in enumerate(items):
        y = 4 + i * row
        parts.append(
            f'<text x="{label_width}" y="{y + 17}" text-anchor="end" class="ct">{esc(_short(name, 30))}</text>'
        )
        parts.append(f'<rect x="{bar_x}" y="{y + 4}" width="{bar_w}" height="16" rx="3" class="track"/>')
        if value is None:
            parts.append(f'<text x="{bar_x + 6}" y="{y + 17}" class="ct muted">n/a</text>')
            continue
        w = max(0.0, min(1.0, value / maximum)) * bar_w if maximum else 0.0
        parts.append(f'<rect x="{bar_x}" y="{y + 4}" width="{w:.1f}" height="16" rx="3" class="{esc(cls)}"/>')
        parts.append(
            f'<text x="{bar_x + bar_w + 8}" y="{y + 17}" class="ct strong">{esc(value_fmt.format(value))}{esc(unit)}</text>'
        )
    return _svg(width, height, label, "".join(parts))


def stacked_bars(
    rows: Sequence[tuple[str, dict[str, int]]],
    order: Sequence[str],
    *,
    label: str = "Results by category",
    width: int = 720,
    label_width: int = 190,
) -> Markup:
    """One horizontal stacked bar per row, segments in ``order`` (statuses)."""
    row = 28
    height = max(row * len(rows) + 30, 50)
    bar_x = label_width + 8
    bar_w = width - bar_x - 20
    biggest = max((sum(c.values()) for _n, c in rows), default=1) or 1
    parts: list[str] = []
    for i, (name, counts) in enumerate(rows):
        y = 4 + i * row
        parts.append(
            f'<text x="{label_width}" y="{y + 17}" text-anchor="end" class="ct">{esc(_short(name, 30))}</text>'
        )
        x = float(bar_x)
        for status in order:
            n = counts.get(status, 0)
            if not n:
                continue
            w = n / biggest * bar_w
            parts.append(
                f'<rect x="{x:.1f}" y="{y + 4}" width="{w:.1f}" height="16" class="{STATUS_CLASS.get(status, "s-skipped")}">'
                f"<title>{esc(name)}: {n} {esc(status)}</title></rect>"
            )
            if w > 18:
                parts.append(f'<text x="{x + w / 2:.1f}" y="{y + 16}" text-anchor="middle" class="ct inv">{n}</text>')
            x += w
    lx = bar_x
    ly = height - 14
    for status in order:
        parts.append(
            f'<rect x="{lx}" y="{ly}" width="10" height="10" rx="2" class="{STATUS_CLASS.get(status, "s-skipped")}"/>'
        )
        parts.append(f'<text x="{lx + 14}" y="{ly + 9}" class="ct small">{esc(status)}</text>')
        lx += 22 + 7 * len(status)
    return _svg(width, height, label, "".join(parts))


def donut(
    segments: Sequence[tuple[str, float, str]],
    *,
    center: str = "",
    sub: str = "",
    label: str = "Distribution",
    size: int = 190,
) -> Markup:
    total = sum(v for _n, v, _c in segments) or 1.0
    cx = cy = size / 2
    r = size / 2 - 18
    circ = 2 * math.pi * r
    parts = [f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" class="track-stroke" stroke-width="22"/>']
    offset = 0.0
    for name, value, cls in segments:
        if value <= 0:
            continue
        length = value / total * circ
        parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke-width="22" class="{esc(cls)}-stroke" '
            f'stroke-dasharray="{length:.2f} {circ - length:.2f}" stroke-dashoffset="{-offset:.2f}" '
            f'transform="rotate(-90 {cx} {cy})"><title>{esc(name)}: {value:g}</title></circle>'
        )
        offset += length
    parts.append(f'<text x="{cx}" y="{cy + 2}" text-anchor="middle" class="ct big">{esc(center)}</text>')
    if sub:
        parts.append(f'<text x="{cx}" y="{cy + 20}" text-anchor="middle" class="ct small muted">{esc(sub)}</text>')
    return _svg(size, size, label, "".join(parts), display_width=str(size))


def column_chart(
    items: Sequence[tuple[str, float, str]],
    *,
    label: str = "Column chart",
    width: int = 720,
    height: int = 220,
    unit: str = "",
    value_fmt: str = "{:g}",
    rule: tuple[float, str] | None = None,
) -> Markup:
    """Vertical columns ``(label, value, css class)``; ``rule`` draws a labelled horizontal line (e.g. a budget)."""
    left, bottom, top = 44, 46, 14
    plot_w, plot_h = width - left - 12, height - bottom - top
    biggest = max([v for _n, v, _c in items] + ([rule[0]] if rule else []) + [1e-9])
    top_tick = _nice(biggest, integer=all(float(v).is_integer() for _n, v, _c in items))
    n = max(len(items), 1)
    slot = plot_w / n
    bw = min(46.0, slot * 0.7)
    parts: list[str] = []
    for k in range(5):
        v = top_tick * k / 4
        y = top + plot_h - (v / top_tick * plot_h)
        parts.append(f'<line x1="{left}" x2="{width - 8}" y1="{y:.1f}" y2="{y:.1f}" class="gridline"/>')
        parts.append(f'<text x="{left - 6}" y="{y + 4:.1f}" text-anchor="end" class="ct small muted">{v:g}</text>')
    for i, (name, value, cls) in enumerate(items):
        h = value / top_tick * plot_h if top_tick else 0
        x = left + i * slot + (slot - bw) / 2
        y = top + plot_h - h
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{max(h, 0):.1f}" rx="2" class="{esc(cls)}">'
            f"<title>{esc(name)}: {esc(value_fmt.format(value))}{esc(unit)}</title></rect>"
        )
        if value:
            parts.append(
                f'<text x="{x + bw / 2:.1f}" y="{y - 4:.1f}" text-anchor="middle" class="ct small">{esc(value_fmt.format(value))}</text>'
            )
        parts.append(
            f'<text x="{x + bw / 2:.1f}" y="{height - bottom + 14}" text-anchor="middle" class="ct small">{esc(_short(name, 14))}</text>'
        )
    if rule:
        y = top + plot_h - (rule[0] / top_tick * plot_h)
        parts.append(f'<line x1="{left}" x2="{width - 8}" y1="{y:.1f}" y2="{y:.1f}" class="rule"/>')
        parts.append(
            f'<text x="{width - 10}" y="{y - 4:.1f}" text-anchor="end" class="ct small rule-t">{esc(rule[1])}</text>'
        )
    return _svg(width, height, label, "".join(parts))


def _nice(v: float, *, integer: bool = False) -> float:
    """Top of a value axis with four equal, round steps (0, 1/4, 1/2, 3/4, top). Counts never get fractional ticks."""
    if v <= 0:
        return 4.0 if integer else 1.0
    exp = math.floor(math.log10(v / 4)) if v > 0 else 0
    for mult in (1, 1.5, 2, 2.5, 5, 10):
        step = mult * 10**exp
        if integer and step < 1:
            continue
        if integer and step != int(step):
            continue
        if step * 4 >= v - 1e-9:
            return step * 4
    return 10 ** (exp + 1) * 4


def line_chart(
    points: Sequence[tuple[str, float | None]],
    *,
    label: str = "Trend",
    width: int = 720,
    height: int = 200,
    maximum: float = 100.0,
    highlight_last: bool = True,
) -> Markup:
    left, bottom, top = 40, 38, 12
    plot_w, plot_h = width - left - 20, height - bottom - top
    n = len(points)
    parts: list[str] = []
    for k in range(5):
        v = maximum * k / 4
        y = top + plot_h - v / maximum * plot_h
        parts.append(f'<line x1="{left}" x2="{width - 10}" y1="{y:.1f}" y2="{y:.1f}" class="gridline"/>')
        parts.append(f'<text x="{left - 6}" y="{y + 4:.1f}" text-anchor="end" class="ct small muted">{v:g}</text>')
    coords: list[tuple[float, float, str, float] | None] = []
    for i, (name, value) in enumerate(points):
        x = left + (plot_w * i / (n - 1) if n > 1 else plot_w / 2)
        if value is None:
            coords.append(None)
        else:
            coords.append((x, top + plot_h - max(0.0, min(maximum, value)) / maximum * plot_h, name, value))
        parts.append(
            f'<text x="{x:.1f}" y="{height - bottom + 14}" text-anchor="middle" class="ct small">{esc(_short(name, 10))}</text>'
        )
    segment: list[str] = []
    for c in [*coords, None]:
        if c is None:
            if len(segment) > 1:
                parts.append(f'<polyline points="{" ".join(segment)}" fill="none" class="line" stroke-width="2.5"/>')
            segment = []
        else:
            segment.append(f"{c[0]:.1f},{c[1]:.1f}")
    for i, c in enumerate(coords):
        if c is None:
            continue
        cls = "dot dot-last" if highlight_last and i == n - 1 else "dot"
        parts.append(
            f'<circle cx="{c[0]:.1f}" cy="{c[1]:.1f}" r="5" class="{cls}"><title>{esc(c[2])}: {c[3]:g}</title></circle>'
        )
    return _svg(width, height, label, "".join(parts))


_KIND_COLUMN = {
    "actor": 0,
    "interface": 1,
    "orchestrator": 2,
    "agent": 2,
    "llm": 3,
    "retrieval": 3,
    "vectordb": 4,
    "tool": 3,
    "mcp": 3,
    "memory": 3,
    "browser": 3,
    "database": 4,
}


def architecture(
    nodes: Sequence[dict[str, str]], edges: Sequence[dict[str, str]], *, label: str = "Architecture"
) -> Markup:
    """Layered left-to-right diagram: actors, interfaces, the agent, its tools/models/data, and the stores behind them."""
    if not nodes:
        return Markup("")  # noqa: S704
    columns: dict[int, list[dict[str, str]]] = {}
    for n in nodes:
        columns.setdefault(_KIND_COLUMN.get(n["kind"], 3), []).append(n)
    used = sorted(columns)
    col_w, node_w, node_h, gap = 190, 150, 34, 14
    width = max(len(used) * col_w + 20, 360)
    tallest = max(len(v) for v in columns.values())
    height = max(tallest * (node_h + gap) + 24, 80)
    pos: dict[str, tuple[float, float]] = {}
    parts: list[str] = [
        '<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
        '<path d="M0 0L10 5L0 10z" class="arrow"/></marker></defs>'
    ]
    for ci, col in enumerate(used):
        items = columns[col]
        offset = (height - len(items) * (node_h + gap) + gap) / 2
        for ri, n in enumerate(items):
            x = 10 + ci * col_w
            y = offset + ri * (node_h + gap)
            pos[n["id"]] = (x, y)
    for e in edges:
        a, b = pos.get(e["source"]), pos.get(e["target"])
        if a is None or b is None:
            continue
        x1, y1 = a[0] + node_w, a[1] + node_h / 2
        x2, y2 = b[0], b[1] + node_h / 2
        if x2 <= x1:  # same column or back edge: connect from the bottom
            x1, y1, x2, y2 = a[0] + node_w / 2, a[1] + node_h, b[0] + node_w / 2, b[1]
        mid = (x1 + x2) / 2
        parts.append(
            f'<path d="M{x1:.1f} {y1:.1f} C{mid:.1f} {y1:.1f} {mid:.1f} {y2:.1f} {x2:.1f} {y2:.1f}" class="edge" marker-end="url(#arr)"/>'
        )
    for n in nodes:
        px, py = pos[n["id"]]
        parts.append(
            f'<g class="node k-{esc(n["kind"])}"><rect x="{px:.1f}" y="{py:.1f}" width="{node_w}" height="{node_h}" rx="6"/>'
            f'<text x="{px + node_w / 2:.1f}" y="{py + 15:.1f}" text-anchor="middle" class="ct small strong">{esc(_short(n["label"], 22))}</text>'
            f'<text x="{px + node_w / 2:.1f}" y="{py + 28:.1f}" text-anchor="middle" class="ct tiny muted">{esc(n["kind"])}</text></g>'
        )
    return _svg(width, height, label, "".join(parts))


def heat_matrix(
    rows: Sequence[str],
    cols: Sequence[str],
    cells: dict[tuple[str, str], tuple[int, str]],
    *,
    label: str = "Test matrix",
    cell: int = 46,
    label_width: int = 190,
) -> Markup:
    """Rows x columns grid; each cell is ``(count, css class)``. Used for category x status."""
    head = 46
    width = label_width + len(cols) * cell + 10
    height = head + len(rows) * (cell // 2 + 4) + 8
    parts: list[str] = []
    for j, c in enumerate(cols):
        x = label_width + j * cell + cell / 2
        parts.append(
            f'<text x="{x:.1f}" y="{head - 10}" text-anchor="middle" class="ct small">{esc(_short(c, 9))}</text>'
        )
    for i, r in enumerate(rows):
        y = head + i * (cell // 2 + 4)
        parts.append(
            f'<text x="{label_width - 8}" y="{y + 16}" text-anchor="end" class="ct">{esc(_short(r, 30))}</text>'
        )
        for j, c in enumerate(cols):
            n, cls = cells.get((r, c), (0, "s-none"))
            x = label_width + j * cell + 3
            hook = f' data-row="{esc(r)}" data-col="{esc(c)}"' if n else ""
            parts.append(
                f'<rect x="{x}" y="{y}" width="{cell - 6}" height="{cell // 2 - 2 + 4}" rx="4" '
                f'class="{"cell " if n else ""}{esc(cls)}"{hook}>'
                f"<title>{esc(r)} / {esc(c)}: {n}</title></rect>"
            )
            if n:
                parts.append(
                    f'<text x="{x + (cell - 6) / 2:.1f}" y="{y + 18}" text-anchor="middle" class="ct inv strong">{n}</text>'
                )
    return _svg(width, height, label, "".join(parts))
