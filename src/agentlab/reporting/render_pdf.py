"""PDF rendering of a :class:`ReportData` (reportlab).

The PDF is the print-friendly form of the same data as the Markdown and HTML reports: the same 27 sections in the same
order, the finding format of spec section 30, charts drawn as vector graphics, a table of contents with page numbers
and a PDF outline. It needs no network and no browser.

Text is written with a Unicode TrueType font when one is installed (DejaVu, Liberation or Arial are looked for; the
folder can be given with ``AGENTLAB_PDF_FONT_DIR``). Without one the built-in Latin-1 fonts are used and characters
they cannot show are replaced by close ASCII forms, so a missing font degrades the look, never the content.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import re
import xml.sax.saxutils as sx
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    Image,
    KeepTogether,
    LongTable,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

from agentlab.reporting import model as m
from agentlab.reporting.compare import KIND_TITLES, Comparison, format_metric
from agentlab.reporting.util import SEVERITY_ORDER, describe_values, money, ms, pct, plural

log = logging.getLogger(__name__)
BlobLoader = Callable[[str], bytes | None]

PAGE = A4
MARGIN = 16 * mm
WIDTH = PAGE[0] - 2 * MARGIN
MAX_IMAGE_BYTES = 300_000
MAX_IMAGES = 12
MAX_CODE_LINES = 45
CODE_COLUMNS = 104

INK = colors.HexColor("#1a2030")
MUTED = colors.HexColor("#5d6678")
LINE = colors.HexColor("#dde1e8")
PANEL = colors.HexColor("#f3f5f8")
ACCENT = colors.HexColor("#3a5bd9")
GOOD = colors.HexColor("#1f9d55")
BAD = colors.HexColor("#d64545")
WARN = colors.HexColor("#d9a406")
INFO = colors.HexColor("#6b7893")
SEVERITY_COLORS = {
    "critical": colors.HexColor("#9b1c31"),
    "high": colors.HexColor("#d64545"),
    "medium": colors.HexColor("#e08a00"),
    "low": colors.HexColor("#3a7bd5"),
    "info": INFO,
}
STATUS_COLORS = {
    "passed": GOOD,
    "failed": BAD,
    "timeout": BAD,
    "error": colors.HexColor("#8a4fd1"),
    "blocked": INFO,
    "skipped": colors.HexColor("#aab2c0"),
}

# What the built-in Latin-1 fonts (and any glyph missing from a TrueType font) are shown as.
_ASCII = {
    "→": "->",
    "←": "<-",
    "↔": "<->",
    "≥": ">=",
    "≤": "<=",
    "≠": "!=",
    "×": "x",
    "·": "-",
    "…": "...",
    "–": "-",
    "—": "-",
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "✓": "ok",
    "✔": "ok",
    "✅": "ok",
    "⚠": "(!)",
    "⛔": "(x)",
    "○": "o",
    "•": "*",
    "Δ": "delta",
    "±": "+/-",
    "\u00a0": " ",
    "\u200b": "",
}
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# ===================================================================================================== fonts
@dataclass(frozen=True)
class Fonts:
    regular: str
    bold: str
    italic: str
    mono: str
    unicode: bool
    glyphs: frozenset[int] | None = None  # code points the regular font can draw (TrueType only)


_FONT_SETS = (
    ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf", "DejaVuSansMono.ttf"),
    (
        "LiberationSans-Regular.ttf",
        "LiberationSans-Bold.ttf",
        "LiberationSans-Italic.ttf",
        "LiberationMono-Regular.ttf",
    ),
    ("arial.ttf", "arialbd.ttf", "ariali.ttf", "cour.ttf"),
)
_FONT_DIRS = (
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/dejavu",
    "/usr/share/fonts/TTF",
    "/usr/share/fonts/truetype/liberation",
    "/usr/share/fonts/liberation",
    "/usr/local/share/fonts",
    "/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
    "C:/Windows/Fonts",
)
_FONTS: Fonts | None = None


def _find_fonts() -> Fonts:
    dirs = [Path(os.environ["AGENTLAB_PDF_FONT_DIR"])] if os.environ.get("AGENTLAB_PDF_FONT_DIR") else []
    dirs += [Path(d) for d in _FONT_DIRS]
    for names in _FONT_SETS:
        for d in dirs:
            paths = [d / n for n in names]
            if not all(p.is_file() for p in paths):
                continue
            try:
                for alias, path in zip(("AL-Regular", "AL-Bold", "AL-Italic", "AL-Mono"), paths, strict=True):
                    if alias not in pdfmetrics.getRegisteredFontNames():
                        pdfmetrics.registerFont(TTFont(alias, str(path)))
                face = pdfmetrics.getFont("AL-Regular").face
                glyphs = frozenset(getattr(face, "charToGlyph", {}) or ())
                return Fonts("AL-Regular", "AL-Bold", "AL-Italic", "AL-Mono", True, glyphs or None)
            except Exception as exc:  # a damaged or unsupported font file: try the next candidate
                log.debug("font set %s in %s could not be used: %s", names[0], d, exc)
                continue
    return Fonts("Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Courier", False)


def fonts() -> Fonts:
    global _FONTS
    if _FONTS is None:
        _FONTS = _find_fonts()
    return _FONTS


def safe_text(value: object, f: Fonts) -> str:
    """Text as the chosen font can draw it (control characters removed, unknown glyphs replaced)."""
    text = _CONTROL.sub("", "" if value is None else str(value))
    out: list[str] = []
    for ch in text:
        if ch in "\n\t" or ord(ch) < 128 or (f.unicode and f.glyphs is not None and ord(ch) in f.glyphs):
            out.append(ch)
        elif ch in _ASCII:
            out.append(_ASCII[ch])
        elif not f.unicode and ord(ch) < 256:
            out.append(ch)
        else:
            out.append("?")
    return "".join(out)


class Mk(str):
    """Text that is already ReportLab paragraph markup (everything else is escaped)."""


# ===================================================================================================== styles
def make_styles(f: Fonts) -> dict[str, ParagraphStyle]:
    base = {"fontName": f.regular, "textColor": INK}
    return {
        "body": ParagraphStyle("body", fontSize=9, leading=12.4, spaceAfter=4, **base),
        "small": ParagraphStyle("small", fontSize=7.8, leading=10.2, spaceAfter=3, **{**base, "textColor": MUTED}),
        "bullet": ParagraphStyle(
            "bullet", fontSize=9, leading=12.2, leftIndent=11, bulletIndent=2, spaceAfter=2, **base
        ),
        "h1": ParagraphStyle(
            "h1", fontName=f.bold, fontSize=15, leading=19, spaceBefore=14, spaceAfter=7, textColor=INK, keepWithNext=1
        ),
        "h2": ParagraphStyle(
            "h2",
            fontName=f.bold,
            fontSize=11.5,
            leading=15,
            spaceBefore=10,
            spaceAfter=4,
            textColor=INK,
            keepWithNext=1,
        ),
        "h3": ParagraphStyle(
            "h3", fontName=f.bold, fontSize=9.6, leading=13, spaceBefore=7, spaceAfter=3, textColor=INK, keepWithNext=1
        ),
        "title": ParagraphStyle("title", fontName=f.bold, fontSize=22, leading=27, spaceAfter=4, textColor=INK),
        "subtitle": ParagraphStyle("subtitle", fontName=f.regular, fontSize=10, leading=14, textColor=MUTED),
        "cell": ParagraphStyle("cell", fontSize=7.5, leading=9.4, **base),
        "cellh": ParagraphStyle("cellh", fontName=f.bold, fontSize=7.2, leading=9, textColor=MUTED),
        "code": ParagraphStyle(
            "code",
            fontName=f.mono,
            fontSize=6.9,
            leading=8.4,
            textColor=INK,
            backColor=PANEL,
            borderPadding=3,
            borderColor=LINE,
            borderWidth=0.5,
            spaceBefore=3,
            spaceAfter=7,
        ),
        "callout": ParagraphStyle(
            "callout",
            leftIndent=6,
            rightIndent=6,
            spaceBefore=7,
            fontSize=8.8,
            leading=12,
            backColor=PANEL,
            borderPadding=5,
            borderColor=ACCENT,
            spaceAfter=7,
            **base,
        ),
        "warnbox": ParagraphStyle(
            "warnbox",
            leftIndent=6,
            rightIndent=6,
            spaceBefore=7,
            fontSize=8.8,
            leading=12,
            backColor=colors.HexColor("#fbf3dc"),
            borderPadding=5,
            borderColor=WARN,
            spaceAfter=7,
            **base,
        ),
        "badbox": ParagraphStyle(
            "badbox",
            leftIndent=6,
            rightIndent=6,
            spaceBefore=7,
            fontSize=8.8,
            leading=12,
            backColor=colors.HexColor("#fbe9e9"),
            borderPadding=5,
            borderColor=BAD,
            spaceAfter=7,
            **base,
        ),
        "toc1": ParagraphStyle("toc1", fontName=f.regular, fontSize=9, leading=12.6, leftIndent=0, textColor=INK),
    }


# ===================================================================================================== helpers
class Ctx:
    """The font, the styles and the helpers that turn report values into flowables."""

    def __init__(self, f: Fonts, load: BlobLoader | None) -> None:
        self.f = f
        self.st = make_styles(f)
        self.load = load
        self.images = 0

    # -- text
    def t(self, value: object) -> str:
        """Escaped text, or the markup itself when it is an :class:`Mk`."""
        if isinstance(value, Mk):
            return str(value)
        return sx.escape(safe_text(value, self.f)).replace("\n", "<br/>")

    def p(self, value: object, style: str = "body") -> Paragraph:
        return Paragraph(self.t(value), self.st[style])

    def h(self, level: int, text: str) -> Paragraph:
        return Paragraph(self.t(text), self.st[f"h{level}"])

    def bullets(self, items: Iterable[object], style: str = "bullet") -> list[Flowable]:
        return [Paragraph(self.t(x), self.st[style], bulletText="•" if self.f.unicode else "*") for x in items if x]

    def badge(self, text: str, color: colors.Color) -> Mk:
        return Mk(
            f'<font color="{color.hexval().replace("0x", "#")}"><b>{sx.escape(safe_text(text, self.f))}</b></font>'
        )

    def status(self, value: str) -> Mk:
        return self.badge(value, STATUS_COLORS.get(value, INFO))

    def severity(self, value: str | None) -> Mk | str:
        return self.badge(value.upper(), SEVERITY_COLORS.get(value, INFO)) if value else ""

    def callout(self, text: str, kind: str = "callout") -> Paragraph:
        return Paragraph(self.t(text), self.st[kind])

    def code(self, text: str, limit: int = MAX_CODE_LINES) -> Preformatted:
        """A block of fixed-width text, wrapped at the page width (Preformatted does not wrap) and bounded."""
        lines: list[str] = []
        for raw in safe_text(text, self.f).splitlines() or [""]:
            raw = raw.replace("\t", "    ")
            lines.extend(raw[i : i + CODE_COLUMNS] for i in range(0, max(len(raw), 1), CODE_COLUMNS))
        if len(lines) > limit:
            lines = [*lines[:limit], f"... ({len(lines) - limit} more lines; the full text is in report.json)"]
        return Preformatted("\n".join(lines), self.st["code"])

    # -- tables
    def table(
        self,
        headers: Sequence[str],
        rows: Iterable[Sequence[object]],
        widths: Sequence[float] | None = None,
        *,
        empty: str = "Nothing to show.",
    ) -> Flowable:
        body = [list(r) for r in rows]
        if not body:
            return self.p(empty, "small")
        n = len(headers)
        if widths is None:
            widths = [1.0] * n
        total = float(sum(widths))
        col_w = [WIDTH * w / total for w in widths]
        data: list[list[Paragraph]] = [[Paragraph(self.t(h).upper(), self.st["cellh"]) for h in headers]]
        for row in body:
            data.append([Paragraph(self.t(c), self.st["cell"]) for c in row])
        tbl = LongTable(data, colWidths=col_w, repeatRows=1)
        style = [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, 0), 0.8, MUTED),
            ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINE),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 2.2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2),
        ]
        style += [("BACKGROUND", (0, i), (-1, i), PANEL) for i in range(2, len(data), 2)]
        tbl.setStyle(TableStyle(style))
        return tbl

    def kv(self, pairs: Iterable[tuple[str, object]], widths: tuple[float, float] = (1.0, 3.6)) -> Flowable:
        """A two-column label / value list."""
        rows = [(Mk(f"<b>{sx.escape(safe_text(k, self.f))}</b>"), v) for k, v in pairs]
        return self._kv_table(rows, widths)

    def _kv_table(self, rows: list[tuple[Mk, object]], widths: tuple[float, float]) -> Flowable:
        total = sum(widths)
        data = [[Paragraph(self.t(k), self.st["cell"]), Paragraph(self.t(v), self.st["cell"])] for k, v in rows]
        tbl = LongTable(data, colWidths=[WIDTH * w / total for w in widths])
        tbl.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                    ("TOPPADDING", (0, 0), (-1, -1), 2.2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2),
                ]
            )
        )
        return tbl

    # -- charts
    def bars(
        self, items: Sequence[tuple[str, float | None, colors.Color]], maximum: float = 100.0, label_w: float = 115
    ) -> Drawing:
        row_h = 13
        d = Drawing(WIDTH, row_h * len(items) + 4)
        track_w = WIDTH * 0.62 - label_w
        for i, (name, value, color) in enumerate(items):
            y = row_h * (len(items) - 1 - i) + 3
            d.add(
                String(
                    label_w - 5,
                    y + 2.5,
                    safe_text(name, self.f)[:30],
                    fontName=self.f.regular,
                    fontSize=7.6,
                    textAnchor="end",
                    fillColor=INK,
                )
            )
            d.add(Rect(label_w, y, track_w, 8, fillColor=PANEL, strokeColor=LINE, strokeWidth=0.4))
            if value is None:
                d.add(String(label_w + 4, y + 2, "n/a", fontName=self.f.regular, fontSize=6.8, fillColor=MUTED))
                continue
            d.add(
                Rect(
                    label_w, y, max(track_w * min(value, maximum) / maximum, 0.8), 8, fillColor=color, strokeColor=None
                )
            )
            d.add(
                String(
                    label_w + track_w + 5,
                    y + 2,
                    f"{value:.0f}" if maximum == 100 else f"{value:g}",
                    fontName=self.f.bold,
                    fontSize=7.6,
                    fillColor=INK,
                )
            )
        return d

    def stacked(self, parts: Sequence[tuple[str, float, colors.Color]]) -> Drawing:
        d = Drawing(WIDTH, 30)
        total = sum(v for _n, v, _c in parts) or 1.0
        x = 0.0
        for _name, value, color in parts:
            if value <= 0:
                continue
            w = WIDTH * value / total
            d.add(Rect(x, 14, w, 12, fillColor=color, strokeColor=colors.white, strokeWidth=0.6))
            x += w
        lx = 0.0
        for name, value, color in parts:
            if value <= 0:
                continue
            d.add(Rect(lx, 2, 7, 7, fillColor=color, strokeColor=None))
            label = f"{name} {value:g}"
            d.add(String(lx + 10, 3, label, fontName=self.f.regular, fontSize=7.4, fillColor=MUTED))
            lx += 22 + 4.2 * len(label)
        return d

    # -- images
    def image(self, data: bytes, width: float = WIDTH * 0.46) -> Flowable | None:
        try:
            from reportlab.lib.utils import ImageReader

            w, h = ImageReader(io.BytesIO(data)).getSize()
            if not w or not h:
                return None
            return Image(io.BytesIO(data), width=width, height=width * h / w)
        except Exception:
            return None


class Heading(Paragraph):
    """A heading the table of contents and the PDF outline pick up."""

    def __init__(self, text: str, style: ParagraphStyle, level: int) -> None:
        super().__init__(text, style)
        self.level = level


class ReportDoc(BaseDocTemplate):
    def __init__(self, target: Any, *, title: str, footer: str, f: Fonts, generated: datetime, **kw: Any) -> None:
        super().__init__(
            target,
            pagesize=PAGE,
            leftMargin=MARGIN,
            rightMargin=MARGIN,
            topMargin=MARGIN + 6 * mm,
            bottomMargin=MARGIN + 4 * mm,
            title=title,
            author="AgentLab",
            subject="AI agent test, evaluation and security report",
            creator="AgentLab",
            invariant=1,
            **kw,
        )
        self._footer, self._font, self._title, self._generated = footer, f, title, generated
        frame = Frame(
            self.leftMargin,
            self.bottomMargin,
            self.width,
            self.height,
            id="body",
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
        )
        self.addPageTemplates([PageTemplate(id="page", frames=[frame], onPage=self._decorate)])
        self._seq = 0

    def _decorate(self, canv: Any, doc: Any) -> None:
        canv.saveState()
        canv.setFont(self._font.regular, 7.4)
        canv.setFillColor(MUTED)
        canv.drawString(MARGIN, PAGE[1] - MARGIN + 2 * mm, safe_text(self._title, self._font)[:110])
        canv.setStrokeColor(LINE)
        canv.line(MARGIN, PAGE[1] - MARGIN, PAGE[0] - MARGIN, PAGE[1] - MARGIN)
        canv.line(MARGIN, MARGIN, PAGE[0] - MARGIN, MARGIN)
        canv.drawString(MARGIN, MARGIN - 4 * mm, safe_text(self._footer, self._font)[:130])
        canv.drawRightString(PAGE[0] - MARGIN, MARGIN - 4 * mm, f"Page {canv.getPageNumber()}")
        canv.restoreState()

    def beforeDocument(self) -> None:
        self._seq = 0  # bookmark keys must be the same on every pass or the table of contents never settles
        # ReportLab's "invariant" mode makes the bytes reproducible but stamps the file with the year 2000; show the
        # time the report was generated instead (the same on every render of the same report).
        stamp = f"D:{self._generated:%Y%m%d%H%M%S}Z"
        with contextlib.suppress(AttributeError):
            self.canv._doc.info._dateFormatter = lambda *_a: stamp

    def afterFlowable(self, flowable: Flowable) -> None:
        if isinstance(flowable, Heading):
            self._seq += 1
            key = f"sec{self._seq}"
            text = flowable.getPlainText()
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, level=flowable.level, closed=flowable.level > 0)
            if flowable.level == 0:
                self.notify("TOCEntry", (0, text, self.page, key))


# ===================================================================================================== sections
def _sec(c: Ctx, number: str, title: str) -> Heading:
    return Heading(c.t(f"{number}  {title}"), c.st["h1"], 0)


def _sub(c: Ctx, text: str) -> Heading:
    return Heading(c.t(text), c.st["h2"], 1)


def _cover(c: Ctx, r: m.ReportData) -> list[Flowable]:
    ex, sc, run, v = r.executive, r.scorecard, r.run, r.versioning
    out: list[Flowable] = [Spacer(1, 18), c.p(r.title, "title")]
    out.append(
        c.p(
            f"Run {run.run_id} ({run.status}) · suite {run.suite} / {run.intensity} · generated {r.generated_at:%Y-%m-%d %H:%M} UTC "
            f"· report version {r.report_version} · AgentLab {v.agentlab_version}"
            + (f" ({v.agentlab_commit})" if v.agentlab_commit else ""),
            "subtitle",
        )
    )
    out.append(Spacer(1, 10))
    grade_color = (
        SEVERITY_COLORS["high"] if (sc.grade or "F")[:1] in "DF" else GOOD if (sc.grade or "")[:1] in "AB" else WARN
    )
    score = "n/a" if sc.overall is None else f"{sc.overall:.0f}"
    big = ParagraphStyle("big", parent=c.st["body"], fontName=c.f.bold, fontSize=26, leading=30, alignment=1)
    cap = ParagraphStyle("cap", parent=c.st["small"], alignment=1)
    grade_letter = sx.escape(safe_text((sc.grade or "n/a")[:1], c.f))
    box = Table(
        [
            [
                Paragraph(c.t(score), big),
                Paragraph(grade_letter, ParagraphStyle("g", parent=big, textColor=grade_color)),
                c.p(ex.headline, "h3"),
            ],
            [Paragraph("overall score / 100", cap), Paragraph("grade", cap), ""],
        ],
        colWidths=[WIDTH * 0.2, WIDTH * 0.14, WIDTH * 0.66],
    )
    box.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                ("BACKGROUND", (0, 0), (-1, -1), PANEL),
                ("LINEAFTER", (0, 0), (0, -1), 0.4, LINE),
                ("LINEAFTER", (1, 0), (1, -1), 0.4, LINE),
                ("SPAN", (2, 0), (2, 1)),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    out.append(box)
    out.append(Spacer(1, 6))
    out.append(c.p(ex.verdict))
    if r.reviewed:
        out.append(
            c.callout(
                "This report includes human review decisions. The original results are shown unchanged next to them."
            )
        )
    if not run.complete:
        out.append(
            c.callout(
                f"The run did not complete normally ({run.incomplete_reason}). Results cover only the tests that ran.",
                "badbox",
            )
        )
    return out


def _executive(c: Ctx, r: m.ReportData) -> list[Flowable]:
    ex = r.executive
    out: list[Flowable] = [_sec(c, "1", "Executive summary"), c.p(ex.headline, "h3"), c.p(ex.verdict)]
    out += c.bullets(Mk(f"<b>{sx.escape(p.kind.capitalize())}:</b> {c.t(p.text)}") for p in ex.points)
    if ex.limitations:
        out += [c.p("What limits this result", "h3")]
        out += [c.callout(x, "warnbox") for x in ex.limitations]
    if ex.next_steps:
        out += [c.p("Do first", "h3")] + c.bullets(ex.next_steps)
    return out


def _score(c: Ctx, r: m.ReportData) -> list[Flowable]:
    sc = r.scorecard
    out: list[Flowable] = [_sec(c, "2", "Overall score")]
    if sc.overall is None:
        out.append(c.callout("No overall score: " + ("; ".join(sc.notes) or "nothing scorable ran"), "badbox"))
    else:
        out.append(
            c.p(
                f"{sc.overall:.1f} / 100 · grade {sc.grade} · profile {sc.profile} · confidence {sc.confidence:.2f}",
                "h3",
            )
        )
        if sc.security_cap_applied:
            out.append(c.callout(f"Score before the security cap: {sc.raw_overall:.1f}. {sc.cap_reason}", "badbox"))

    def color(v: float | None) -> colors.Color:
        return (
            INFO
            if v is None
            else GOOD
            if v >= 90
            else colors.HexColor("#6aa84f")
            if v >= 75
            else WARN
            if v >= 60
            else BAD
        )

    out.append(c.bars([(x.label, x.score if x.applicable else None, color(x.score)) for x in sc.categories]))
    counts = Counter(x.status for x in r.results)
    out.append(c.stacked([(s, float(counts[s]), STATUS_COLORS[s]) for s in STATUS_COLORS if counts[s]]))
    out.append(Spacer(1, 4))
    out.append(
        c.table(
            ["Category", "Score", "Weight", "Tests", "Passed", "Confidence", "Note"],
            [
                [
                    x.label,
                    f"{x.score:.0f}" if x.applicable and x.score is not None else "N/A",
                    f"{x.weight:.0%}" if x.weight else "-",
                    x.tests,
                    x.passed,
                    f"{x.confidence:.2f}" if x.applicable else "-",
                    x.note or "",
                ]
                for x in sc.categories
            ],
            [2.2, 0.8, 0.8, 0.7, 0.8, 1.0, 3.0],
        )
    )
    if r.reviewed_scorecard is not None:
        rs = r.reviewed_scorecard
        o = "n/a" if rs.overall is None else f"{rs.overall:.0f}"
        out.append(
            c.callout(
                f"After human review: {o} / 100, grade {rs.grade} (machine score {sc.overall if sc.overall is None else format(sc.overall, '.0f')}). Both are kept; reviews never replace the original evaluation."
            )
        )
    out += [c.callout(q, "warnbox") for q in sc.qualifiers]
    out += c.bullets(sc.notes)
    out.append(c.p(sc.scoring_note, "small"))
    if r.reviews:
        out.append(c.p("Human review log", "h3"))
        out.append(
            c.p(
                "The original results are unchanged; each line is a reviewer's opinion stored next to them, with the values before and after.",
                "small",
            )
        )
        out.append(
            c.table(
                ["When (UTC)", "Reviewer", "About", "Decision", "Original", "Reviewed", "Reason / comment"],
                [
                    [
                        f"{rv.created_at:%Y-%m-%d %H:%M}" if rv.created_at else "",
                        rv.reviewer,
                        f"{rv.subject_type} {rv.subject}".strip(),
                        rv.decision.replace("_", " "),
                        describe_values(rv.original),
                        describe_values(rv.reviewed) if rv.reviewed else "no change to the values",
                        "; ".join(x for x in (rv.reason, rv.comment) if x),
                    ]
                    for rv in r.reviews
                ],
                [1.1, 1.0, 1.6, 1.0, 1.5, 1.5, 2.2],
            )
        )
    return out


def _risk(c: Ctx, r: m.ReportData) -> list[Flowable]:
    rk = r.risk
    out: list[Flowable] = [_sec(c, "3", "Risk summary")]
    if rk.severity_counts:
        top = max(rk.severity_counts.values()) or 1
        out.append(
            c.bars(
                [(s.upper(), float(rk.severity_counts.get(s, 0)), SEVERITY_COLORS[s]) for s in SEVERITY_ORDER],
                maximum=float(top),
                label_w=70,
            )
        )
    else:
        out.append(c.p("No open findings."))
    out.append(c.p(f"Security posture: {rk.security_posture.replace('_', ' ')}. {rk.security_summary}"))
    if rk.top_risks:
        out += [c.p("Top risks", "h3")] + c.bullets(rk.top_risks)
    if rk.unassessed_areas:
        out += [c.p("Not assessed (a gap in coverage, not a pass)", "h3")] + c.bullets(rk.unassessed_areas)
    return out


def _target(c: Ctx, r: m.ReportData) -> list[Flowable]:
    t = r.target
    out: list[Flowable] = [_sec(c, "4", "Target overview")]
    out.append(
        c.kv(
            [
                ("Name", t.name),
                ("Version", t.version or "-"),
                ("Interfaces", ", ".join(t.interfaces) or "-"),
                ("Evaluation mode", ", ".join(t.modes) or "-"),
                ("Models", ", ".join(t.models) or "not detected"),
                ("Frameworks", ", ".join(t.frameworks) or "not detected"),
                ("Languages", ", ".join(f"{k} ({n})" for k, n in t.languages.items()) or "-"),
                ("Authentication", t.authentication or "-"),
                ("Production target", "yes" if t.production else "no"),
            ]
        )
    )
    if t.description:
        out += [Spacer(1, 4), c.p(t.description)]
    if t.tools:
        conf = {None: "unknown", True: "required", False: "not required"}
        out += [c.p("Tools", "h3")]
        out.append(
            c.table(
                ["Tool", "Side effects", "Confirmation", "Source"],
                [[x["name"], x["side_effects"], conf[x["requires_confirmation"]], x["source"]] for x in t.tools],
                [2, 1.2, 1.2, 1.6],
            )
        )
    if t.limitations:
        out += [c.p("Declared or discovered limitations", "h3")] + c.bullets(t.limitations)
    return out


def _architecture(c: Ctx, r: m.ReportData) -> list[Flowable]:
    a = r.architecture
    out: list[Flowable] = [_sec(c, "5", "Target architecture")]
    if a.edges:
        names = {n["id"]: n["label"] for n in a.nodes}
        out.append(
            c.table(
                ["From", "To", "Relation"],
                [
                    [names.get(e["source"], e["source"]), names.get(e["target"], e["target"]), e.get("label", "")]
                    for e in a.edges
                ],
                [2, 2, 1.5],
            )
        )
    out.append(c.p(a.note, "small"))
    return out


def _types(c: Ctx, r: m.ReportData) -> list[Flowable]:
    out: list[Flowable] = [_sec(c, "6", "Agent type classification")]
    out.append(
        c.table(
            ["Type", "Confidence", "Evidence"],
            [[x.type, f"{x.confidence:.2f}", "; ".join(x.evidence)] for x in r.classification],
            [1.4, 0.9, 5],
        )
    )
    out.append(_sec(c, "7", "Capability matrix"))
    out.append(
        c.table(
            ["Capability", "Detected", "Testable", "Reason"],
            [[x.capability, "yes" if x.detected else "no", x.testable, x.reason] for x in r.capabilities],
            [1.4, 0.8, 0.9, 4.6],
        )
    )
    return out


def _environment(c: Ctx, r: m.ReportData) -> list[Flowable]:
    e, v = r.environment, r.versioning
    out: list[Flowable] = [_sec(c, "8", "Environment")]
    out.append(
        c.table(
            ["Component", "Status", "Note"],
            [
                ["Sandbox (Docker)", "available" if e.docker else "unavailable", e.docker_note],
                ["Browser (Playwright)", "available" if e.browser else "unavailable", e.browser_note],
                ["LLM judge", "available" if e.judge else "unavailable", e.judge_note],
                [
                    "Interfaces tested",
                    ", ".join(e.interfaces) or "none",
                    "; ".join(f"{k}: {x}" for k, x in {**e.interface_errors, **e.unreachable}.items()),
                ],
                ["Parallelism", e.parallelism, ""],
                ["Canary seeding", "yes" if e.canary_seeding else "no", ""],
            ],
            [1.6, 1.0, 4],
        )
    )
    out.append(c.p("Versions (everything needed to reproduce this report)", "h3"))
    out.append(
        c.kv(
            [
                ("AgentLab", f"{v.agentlab_version} {v.agentlab_commit or ''}".strip()),
                ("Python / platform", f"{v.python} / {v.platform}"),
                ("Target", f"{v.target} {v.target_version or ''} {v.target_commit or ''}".strip()),
                (
                    "Providers / models",
                    ", ".join(f"{p.get('name')}:{p.get('model') or '-'}" for p in v.providers) or "none configured",
                ),
                (
                    "Judges",
                    ", ".join(f"{j.get('provider')}:{j.get('model')}" for j in v.judges)
                    or ("none" if not v.judge_enabled else "enabled"),
                ),
                (
                    "Test suite",
                    f"{v.test_suite.get('suite')} / {v.test_suite.get('intensity')} · plan {v.test_suite.get('plan_hash')} · {v.test_suite.get('tests')} tests",
                ),
                ("Evaluation profile", f"{v.evaluation_profile.get('name')} #{v.evaluation_profile.get('hash')}"),
                ("Skills", ", ".join(f"{s['name']}@{s['version']}" for s in v.skills)),
                ("Environment fingerprint", v.environment_fingerprint),
                ("Config hash", v.config_hash),
                ("Timestamp", f"{v.timestamp:%Y-%m-%d %H:%M:%S} UTC"),
            ]
        )
    )
    return out


def _methodology(c: Ctx, r: m.ReportData) -> list[Flowable]:
    me = r.methodology
    out: list[Flowable] = [_sec(c, "9", "Test methodology"), c.p(me.summary)]
    for title, items in (
        ("Steps", me.steps),
        ("Evaluation layers", me.evaluation_layers),
        ("Safety rules", me.safety_rules),
        ("Scoring", me.scoring),
        ("Severity and confidence", me.severity_model),
        ("Assumptions made by the test designer", me.assumptions),
    ):
        if items:
            out += [c.p(title, "h3")] + c.bullets(items)
    return out


def _inventory(c: Ctx, r: m.ReportData) -> list[Flowable]:
    inv = r.inventory
    out: list[Flowable] = [_sec(c, "10", "Test suite inventory")]
    out.append(
        c.p(
            f"{inv.total_selected} tests selected of {inv.total_planned} planned; {inv.executed} executed. Plan {inv.plan_hash}."
        )
    )
    out += [
        c.p("By skill", "h3"),
        c.table(
            ["Skill", "Tests", "Passed", "Failed", "Blocked", "Other"],
            [[x.key, x.tests, x.passed, x.failed, x.blocked, x.other] for x in inv.by_skill],
            [3, 0.7, 0.7, 0.7, 0.7, 0.7],
        ),
    ]
    out += [
        c.p("Coverage of the test taxonomy", "h3"),
        c.table(
            ["Area", "Name", "Coverage", "Tests", "Ran", "Blocked", "Note"],
            [[x.key, x.name, x.status.replace("_", " "), x.tests, x.executed, x.blocked, x.note] for x in inv.coverage],
            [0.5, 1.7, 1.0, 0.5, 0.5, 0.6, 3],
        ),
    ]
    if inv.security_coverage:
        out += [
            c.p("Security categories", "h3"),
            c.table(
                ["Code", "Name", "Coverage", "Tests", "Ran", "Blocked"],
                [
                    [x.key, x.name, x.status.replace("_", " "), x.tests, x.executed, x.blocked]
                    for x in inv.security_coverage
                ],
                [0.6, 3, 1.1, 0.6, 0.6, 0.7],
            ),
        ]
    return out


def _results(c: Ctx, r: m.ReportData) -> list[Flowable]:
    out: list[Flowable] = [_sec(c, "11", "Test results")]
    rows = []
    for x in r.results:
        status = c.status(x.status)
        if x.effective_status:
            status = Mk(f"{status} -> {c.status(x.effective_status)} (reviewed)")
        note = x.blocked_reason or (
            f"flaky {pct(round((x.pass_rate or 0) * x.attempts), x.attempts)}" if x.flaky else ""
        )
        rows.append(
            [
                x.test_id,
                x.name,
                x.category,
                status,
                f"{x.score:.2f}",
                c.severity(x.severity),
                ms(x.latency_ms) if x.latency_ms else "",
                note,
            ]
        )
    out.append(
        c.table(
            ["Test", "Name", "Category", "Status", "Score", "Severity", "Latency", "Note"],
            rows,
            [2.3, 2.4, 1.0, 0.75, 0.62, 1.0, 0.85, 1.5],
        )
    )
    return out


def _failed(c: Ctx, r: m.ReportData) -> list[Flowable]:
    out: list[Flowable] = [_sec(c, "12", "Failed tests")]
    if not r.failed_tests:
        out.append(c.p("No test failed."))
    for f in r.failed_tests:
        block: list[Flowable] = [c.p(f"{f.test_id}: {f.name}", "h3")]
        block.append(
            c.kv(
                [
                    (
                        "Status",
                        Mk(f"{c.status(f.status)}" + (f" · severity {c.severity(f.severity)}" if f.severity else "")),
                    ),
                    ("Objective", f.objective),
                    ("Expected", f.expected),
                    *[(f"Input {i}", turn) for i, turn in enumerate(f.inputs[:4], 1)],
                    ("Why it failed", Mk("<br/>".join(c.t(x) for x in f.why_it_failed[:6]))),
                    ("Reproduce", Mk("<br/>".join(c.t(x) for x in f.reproduction.splitlines()))),
                ],
                (0.9, 4.6),
            )
        )
        last = f.attempts[-1] if f.attempts else None
        if last and last.outputs:
            block += [c.p(f"Observed output (attempt {last.attempt})", "small"), c.code(last.outputs[-1], 14)]
        out += block
    return out


def _finding(c: Ctx, f: m.FindingView) -> list[Flowable]:
    sev = f.effective_severity or f.severity
    out: list[Flowable] = [Paragraph(c.t(f"[{sev.upper()}] {f.title}"), c.st["h3"])]
    pairs: list[tuple[str, object]] = [
        ("Finding", f.title),
        (
            "Evidence",
            f"test {f.test_id}" + (", artifacts " + ", ".join(e[:19] for e in f.evidence[:4]) if f.evidence else ""),
        ),
        ("Expected", f.expected),
        ("Observed", f.observed),
        ("Impact", f.impact),
        ("Severity", Mk(f"{c.severity(sev)}" + (f" (machine: {f.severity.upper()})" if f.effective_severity else ""))),
        (
            "Confidence",
            f"{f.confidence:.2f} · root cause {f.root_cause.replace('_', ' ')} ({f.root_cause_confidence:.2f})",
        ),
        ("Reproduction", Mk("<br/>".join(c.t(x) for x in f.reproduction.splitlines()))),
        ("Recommendation", f.recommendation),
    ]
    out.append(c.kv(pairs, (0.9, 4.6)))
    for title, items in (("Observed facts", f.facts), ("Inferences", f.inferences), ("Judgments", f.judgments)):
        if items:
            out.append(c.p(title, "small"))
            out += c.bullets(items[:6], "bullet")
    for rv in f.reviews:
        out.append(
            c.callout(
                f"Reviewed by {rv.reviewer}: {rv.decision.replace('_', ' ')}" + (f" ({rv.reason})" if rv.reason else "")
            )
        )
    out.append(Spacer(1, 4))
    return out


def _security(c: Ctx, r: m.ReportData) -> list[Flowable]:
    s = r.security
    out: list[Flowable] = [_sec(c, "13", "Security findings")]
    out += [
        c.p(f"Posture: {s.posture.replace('_', ' ')}. {s.summary}"),
        c.callout(s.rating_note, "warnbox") if s.rating_note else Spacer(1, 1),
        c.p(s.canary_note, "small"),
    ]
    out += c.bullets(s.caveats)
    if s.categories:
        vc = {"vulnerable": BAD, "resistant": GOOD}
        out.append(
            c.table(
                ["Code", "Category", "Verdict", "Tests", "Passed", "Failed", "Blocked"],
                [
                    [
                        x.code,
                        x.name,
                        c.badge(x.verdict.replace("_", " "), vc.get(x.verdict, INFO)),
                        x.tests,
                        x.passed,
                        x.failed,
                        x.blocked,
                    ]
                    for x in s.categories
                ],
                [0.6, 3, 1.3, 0.6, 0.7, 0.7, 0.7],
            )
        )
    if s.attacks_succeeded:
        out += [c.p("Attacks that succeeded", "h3")]
        out.append(
            c.table(
                ["Test", "Name", "Categories", "Severity", "Channels", "Evidence"],
                [
                    [
                        a["test_id"],
                        a["name"],
                        ", ".join(a["codes"]),
                        c.severity(a["severity"]),
                        ", ".join(a["channels"]),
                        (a.get("snippet") or "")[:160],
                    ]
                    for a in s.attacks_succeeded[:150]
                ],
                [2.0, 2.0, 1.0, 0.95, 0.95, 2.2],
            )
        )
        if len(s.attacks_succeeded) > 150:
            out.append(c.p(f"{len(s.attacks_succeeded) - 150} more are listed in report.json.", "small"))
    sec_findings = [f for f in r.findings if f.is_security]
    out.append(_sub(c, "Security findings in detail"))
    if not sec_findings:
        out.append(c.p("No security findings."))
    for f in sec_findings:
        out += _finding(c, f)
    other = [f for f in r.findings if not f.is_security]
    if other:
        out.append(_sub(c, "Other findings"))
        for f in other:
            out += _finding(c, f)
    return out


_DOMAIN_NUMBER = {"rag": "14", "tools": "15", "memory": "16", "browser": "17", "multi_agent": "18"}


def _domains(c: Ctx, r: m.ReportData) -> list[Flowable]:
    out: list[Flowable] = []
    for d in r.domains:
        n = _DOMAIN_NUMBER.get(d.key)
        out.append(_sec(c, n, d.title) if n else _sub(c, d.title))
        out.append(c.p(d.summary))
        if d.applicable and d.tests:
            out.append(
                c.p(
                    f"Score {'n/a' if d.score is None else format(d.score, '.0f')} · passed {d.passed} · failed {d.failed} · blocked {d.blocked}",
                    "small",
                )
            )
            subs = d.metrics.get("by_subcategory", {})
            if subs:
                out.append(
                    c.table(
                        ["Area", "Passed", "Failed", "Blocked"],
                        [[k, x["passed"], x["failed"], x["blocked"]] for k, x in subs.items()],
                        [3, 1, 1, 1],
                    )
                )
            if d.failures:
                out += [c.p("Failures", "h3")] + c.bullets(d.failures)
            if d.blocked_tests:
                out += [c.p("Could not run", "h3")] + c.bullets(f"{b['test']}: {b['reason']}" for b in d.blocked_tests)
    return out


def _measures(c: Ctx, r: m.ReportData) -> list[Flowable]:
    rel, pf, co = r.reliability, r.performance, r.cost
    out: list[Flowable] = [_sec(c, "19", "Reliability")]
    out.append(
        c.p(
            f"Verdict: {rel.verdict.replace('_', ' ')} · {rel.measured_tests} test(s) repeated, {rel.single_run_tests} run once"
            + (f" · consistency {rel.consistency:.0%}" if rel.consistency is not None else "")
            + "."
        )
    )
    if rel.flaky:
        out.append(
            c.table(
                ["Test", "Name", "Passes", "Pass rate"],
                [[x.test_id, x.name, f"{x.passes}/{x.repetitions}", f"{x.pass_rate:.0%}"] for x in rel.flaky],
                [2, 3, 1, 1],
            )
        )
    if rel.deterministic_failures:
        out.append(c.p("Failed every time: " + ", ".join(rel.deterministic_failures)))
    out += c.bullets(rel.notes)
    out.append(_sec(c, "20", "Performance"))
    out.append(
        c.p(
            f"Measured {pf.measured} test(s): p50 {ms(pf.p50_ms)} · p95 {ms(pf.p95_ms)} · max {ms(pf.max_ms)}"
            + (f" · budget {ms(pf.budget_ms)}" if pf.budget_ms else "")
            + "."
        )
    )
    if pf.over_budget:
        out.append(c.p("Over budget: " + ", ".join(pf.over_budget)))
    out.append(
        c.table(
            ["Slowest test", "Name", "Latency"],
            [[x.test_id, x.name, ms(x.latency_ms)] for x in pf.slowest],
            [2, 3.5, 1],
        )
    )
    out += c.bullets(pf.notes)
    out.append(_sec(c, "21", "Cost"))
    out.append(
        c.p(
            f"Tokens: {co.total_tokens:,} (target {co.target_tokens:,}) · cost {money(co.total_cost_usd, co.cost_known)}"
            + (f" · judge {money(co.judge_cost_usd)}" if co.judge_cost_usd else "")
            + "."
        )
    )
    out.append(
        c.table(
            ["Category", "Tests", "Tokens", "Cost"],
            [[x.label, x.tests, f"{x.tokens:,}", money(x.cost_usd, co.cost_known)] for x in co.by_category],
            [3, 0.8, 1.2, 1.2],
        )
    )
    out += c.bullets(co.notes)
    return out


def _regression(c: Ctx, r: m.ReportData) -> list[Flowable]:
    out: list[Flowable] = [_sec(c, "22", "Regression comparison")]
    if r.regression:
        out += comparison_flowables(c, Comparison.model_validate(r.regression), limit=40)
    else:
        out.append(
            c.p("No baseline was supplied. Run `agentlab compare RUN_A RUN_B` or `agentlab test --baseline RUN_ID`.")
        )
    if len(r.trend.points) > 1:
        out += [c.p("Trend across runs of this target", "h3")]
        out.append(
            c.table(
                ["Run", "Started", "Score", "Failed", "Tests", "Findings", "Comparable"],
                [
                    [
                        p.run_id[:8],
                        f"{p.started_at:%Y-%m-%d %H:%M}" if p.started_at else "",
                        "n/a" if p.overall is None else f"{p.overall:.0f}",
                        p.failed,
                        p.tests,
                        p.findings,
                        "yes" if p.comparable else "different profile",
                    ]
                    for p in r.trend.points
                ],
                [1, 1.4, 0.7, 0.7, 0.7, 0.8, 1.4],
            )
        )
    out.append(c.p(r.trend.note, "small"))
    return out


def comparison_flowables(c: Ctx, cmp: Comparison, *, limit: int = 40) -> list[Flowable]:
    """The comparison as flowables (used by the report's regression section)."""
    comp = cmp.compatibility
    kind = {"comparable": "callout", "comparable_with_caveats": "warnbox", "not_comparable": "badbox"}[comp.verdict]
    out: list[Flowable] = [
        c.p(cmp.summary, "h3"),
        c.callout(f"Compatibility: {comp.verdict.replace('_', ' ')}. {comp.summary}", kind),
    ]
    a, b = cmp.run_a, cmp.run_b
    out.append(
        c.table(
            ["", "Run A (baseline)", "Run B (current)"],
            [
                ["Run", a.run_id, b.run_id],
                [
                    "Target",
                    f"{a.target} {a.target_version or ''}".strip(),
                    f"{b.target} {b.target_version or ''}".strip(),
                ],
                [
                    "Score / grade",
                    f"{format_metric(a.overall, '')} / {a.grade or '-'}",
                    f"{format_metric(b.overall, '')} / {b.grade or '-'}",
                ],
                ["Tests executed / blocked", f"{a.executed} / {a.blocked}", f"{b.executed} / {b.blocked}"],
                [
                    "Plan / scoring profile",
                    f"{(a.plan_hash or '-')[:10]} / {a.scoring_profile}",
                    f"{(b.plan_hash or '-')[:10]} / {b.scoring_profile}",
                ],
            ],
            [1.6, 2.5, 2.5],
        )
    )
    if comp.differences or comp.notes:
        out.append(c.p("Differences between the runs", "h3"))
        out.append(
            c.table(
                ["Impact", "What differs", "A", "B", "Why it matters"],
                [[d.impact.replace("_", " "), d.field, d.base, d.current, d.note] for d in comp.differences],
                [1, 1.4, 1.3, 1.3, 2.4],
            )
        )
        out += c.bullets(comp.notes)
    sc = cmp.score
    out.append(
        c.p(
            f"Overall {format_metric(sc.get('overall_a'), '')} -> {format_metric(sc.get('overall_b'), '')}"
            + ("" if sc.get("overall_delta") is None else f" ({sc['overall_delta']:+.1f})")
            + f" · grade {sc.get('grade_a') or '-'} -> {sc.get('grade_b') or '-'}"
        )
    )
    if sc.get("note"):
        out.append(c.callout(sc["note"], "warnbox"))
    out.append(
        c.table(
            ["Category", "A", "B", "Delta", "Same tests: A", "Same tests: B", "Note"],
            [
                [
                    x.label,
                    format_metric(x.score_a, ""),
                    format_metric(x.score_b, ""),
                    "" if x.delta is None else f"{x.delta:+.1f}",
                    "" if x.like_for_like_a is None else f"{x.like_for_like_a:.0f}% ({x.shared_ran})",
                    "" if x.like_for_like_b is None else f"{x.like_for_like_b:.0f}%",
                    x.note,
                ]
                for x in cmp.categories
            ],
            [2, 0.7, 0.7, 0.7, 1.2, 1.2, 1.6],
        )
    )
    by_kind: dict[str, list[Any]] = {}
    for d in cmp.tests:
        by_kind.setdefault(d.kind, []).append(d)
    for k, title in KIND_TITLES.items():
        items = by_kind.get(k, [])
        if not items:
            continue
        out.append(c.p(f"{title}: {len(items)}", "h3"))
        out.append(
            c.table(
                ["Test", "Name", "A", "B", "Severity", "Note"],
                [
                    [
                        d.test_id,
                        d.name,
                        d.status_a or "-",
                        d.status_b or "-",
                        d.severity_b or d.severity_a or "",
                        d.note,
                    ]
                    for d in items[:limit]
                ],
                [2.2, 2.6, 0.8, 0.8, 0.8, 2],
            )
        )
        if len(items) > limit:
            out.append(c.p(f"{len(items) - limit} more are listed in report.json.", "small"))
    rel, sec = cmp.reliability, cmp.security
    out.append(c.p("Latency, cost, reliability and security", "h3"))
    out.append(
        c.table(
            ["Metric", "A", "B", "Change", "Note"],
            [
                [
                    mt.name,
                    format_metric(mt.a, mt.unit),
                    format_metric(mt.b, mt.unit),
                    "" if mt.change_pct is None else f"{mt.change_pct:+.0f}%",
                    mt.note,
                ]
                for mt in [*cmp.latency, *cmp.cost]
            ],
            [3, 1, 1, 0.8, 1.6],
        )
    )
    out.append(
        c.p(
            f"Reliability: {rel.get('verdict_a')} -> {rel.get('verdict_b')} · flaky tests {rel.get('flaky_a')} -> {rel.get('flaky_b')}. Security posture: {sec.get('posture_a')} -> {sec.get('posture_b')} ({sec.get('direction')})."
        )
    )
    return out


def _evidence(c: Ctx, r: m.ReportData) -> list[Flowable]:
    ev = r.evidence
    out: list[Flowable] = [
        _sec(c, "23", "Evidence"),
        c.p(f"{plural(len(ev.items), 'artifact')} · {plural(len(ev.trace_ids), 'trace')}. {ev.redaction_note}"),
    ]
    for b in ev.browser:
        out.append(c.p(f"Browser: {b.test_id} ({b.browser}) · trace {b.trace or 'not recorded'}", "h3"))
        if b.actions:
            out.append(
                c.table(
                    ["#", "Action", "Target", "OK", "Detail"],
                    [[a.index, a.action, a.target, "yes" if a.ok else "NO", a.detail] for a in b.actions[:60]],
                    [0.4, 1.2, 2.2, 0.5, 3],
                )
            )
        imgs: list[Flowable] = []
        for sid in b.screenshots:
            data = c.load(sid) if c.load and c.images < MAX_IMAGES else None
            img = c.image(data) if data and len(data) <= MAX_IMAGE_BYTES and data[:4] == b"\x89PNG" else None
            if img is None:
                imgs.append(c.p(f"screenshot {sid[:19]} (referenced, not embedded)", "small"))
            else:
                c.images += 1
                imgs.append(KeepTogether([img, c.p(sid[:19], "small")]))
        out += imgs
    for repo in ev.repository:
        out.append(c.p(f"Repository evidence: {repo.test_id}", "h3"))
        if repo.files:
            out.append(c.p("Files: " + ", ".join(repo.files[:30])))
        if repo.diff:
            out += [c.p("Diff (redacted, truncated)", "small"), c.code(repo.diff)]
        if repo.test_output:
            out += [c.p("Test output", "small"), c.code(repo.test_output)]
        out += c.bullets(repo.notes)
    out.append(_sub(c, "Artifacts"))
    out.append(
        c.table(
            ["Name", "Kind", "Test", "Bytes", "SHA-256"],
            [[i.name, i.kind, i.test_key or "", f"{i.size:,}", i.sha256[:16]] for i in ev.items[:200]],
            [3.4, 0.8, 2.4, 0.8, 1.5],
        )
    )
    if len(ev.items) > 200:
        out.append(c.p(f"{len(ev.items) - 200} more artifacts are listed in report.json.", "small"))
    return out


def _recommendations(c: Ctx, r: m.ReportData) -> list[Flowable]:
    out: list[Flowable] = [_sec(c, "24", "Recommendations")]
    if not r.recommendations:
        out.append(c.p("Nothing to recommend."))
    for rec in r.recommendations:
        out.append(
            KeepTogether(
                [
                    Paragraph(
                        c.t(f"{rec.priority}. {rec.title}") + (c.t(f"  ({rec.severity})") if rec.severity else ""),
                        c.st["h3"],
                    ),
                    c.kv([("Why", rec.why), ("Action", rec.action)], (0.5, 5.2)),
                ]
            )
        )
    out.append(_sec(c, "25", "Remediation priorities"))
    out.append(
        c.p(
            "Ordered by severity x confidence, security first; coverage and configuration items follow the fixes.",
            "small",
        )
    )
    out.append(
        c.table(
            ["#", "Kind", "Item", "Severity", "Effort", "Findings / tests"],
            [
                [
                    x.priority,
                    x.kind,
                    x.title,
                    c.severity(x.severity),
                    x.effort,
                    ", ".join(x.tests[:4]) or ", ".join(x.findings[:3]),
                ]
                for x in r.recommendations
            ],
            [0.3, 0.8, 4, 0.8, 0.7, 2.4],
        )
    )
    return out


def _appendix(c: Ctx, r: m.ReportData) -> list[Flowable]:
    ap = r.appendix
    out: list[Flowable] = [_sec(c, "26", "Appendix"), c.p("Skills used", "h3")]
    out.append(
        c.table(
            ["Skill", "Version", "Trust", "Content hash"],
            [[x["name"], x["version"], x.get("trust", ""), str(x.get("content_hash", ""))[:12]] for x in ap.skills],
            [3, 0.8, 1, 1.5],
        )
    )
    out += [
        c.p("Phases", "h3"),
        c.table(
            ["#", "Phase", "Status", "Seconds"],
            [[p.get("index"), p.get("phase"), p.get("status"), p.get("duration_s")] for p in ap.phases],
            [0.4, 3, 1, 1],
        ),
    ]
    if ap.warnings:
        out += [c.p("Warnings", "h3")] + c.bullets(ap.warnings)
    out += [c.p("Limitations", "h3")] + c.bullets(ap.limitations)
    out += [c.p("Glossary", "h3"), c.table(["Term", "Meaning"], list(ap.glossary.items()), [1.4, 5])]
    out.append(_sec(c, "27", "Raw machine-readable results"))
    out.append(
        c.p(
            f"The complete data behind this report is in report.json (schema agentlab.report, version {r.schema_version}). {ap.checksums_note}"
        )
    )
    return out


# ===================================================================================================== entry point
def render_pdf(r: m.ReportData, *, load_blob: BlobLoader | None = None) -> bytes:
    """Render the report as a PDF and return its bytes."""
    f = fonts()
    c = Ctx(f, load_blob)
    footer = f"AgentLab {r.versioning.agentlab_version} · report v{r.report_version} · run {r.run.run_id[:8]} · generated {r.generated_at:%Y-%m-%d %H:%M} UTC"
    story: list[Flowable] = _cover(c, r)
    toc = TableOfContents()
    toc.levelStyles = [c.st["toc1"]]
    toc.tableStyle = TableStyle(
        [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ]
    )
    story += [Spacer(1, 12), c.p("Contents", "h3"), toc, PageBreak()]
    for part in (
        _executive,
        _score,
        _risk,
        _target,
        _architecture,
        _types,
        _environment,
        _methodology,
        _inventory,
        _results,
        _failed,
        _security,
        _domains,
        _measures,
        _regression,
        _evidence,
        _recommendations,
        _appendix,
    ):
        story += part(c, r)
    buf = io.BytesIO()
    ReportDoc(buf, title=r.title, footer=footer, f=f, generated=r.generated_at).multiBuild(story)
    return buf.getvalue()
