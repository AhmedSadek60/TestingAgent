"""Self-contained, interactive HTML report.

One file, no network: styles, charts (inline SVG) and a small script are all inside it, so it opens offline and can be
attached to a ticket. Every dynamic value is HTML-escaped, and a Content-Security-Policy pins the only script by its
hash, so even a hostile string in a target's output cannot run. Without JavaScript the report is still complete;
the script only adds filtering, sorting, expanding and the theme switch.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from agentlab.reporting import model as m
from agentlab.reporting import svg
from agentlab.reporting.compare import KIND_TITLES, Comparison, MetricDelta, format_metric
from agentlab.reporting.util import SEVERITY_ORDER, describe_values, label, money, ms, pct, plural

E = svg.esc
TEMPLATES = Path(__file__).parent / "templates"
STATUS_ORDER = ("passed", "failed", "timeout", "error", "blocked", "skipped")
MAX_EMBEDDED_JSON = 6_000_000
MAX_IMAGE_BYTES = 220_000
MAX_IMAGES = 16

BlobLoader = Callable[[str], bytes | None]


# ================================================================================================ html helpers
def badge(status: str, text: str | None = None) -> str:
    return f'<span class="badge b-{E(status)}">{E(text or status.replace("_", " "))}</span>'


def sev(value: str | None) -> str:
    return f'<span class="sev sev-{E(value)}">{E(value)}</span>' if value else ""


def table(
    headers: Sequence[str],
    rows: Iterable[Sequence[str]],
    *,
    empty: str = "Nothing to show.",
    table_id: str | None = None,
    wrap: bool = True,
) -> str:
    """``rows`` hold ready HTML cells (escape with :func:`E` when building them)."""
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    if not body:
        return f'<p class="muted">{E(empty)}</p>'
    idattr = f' id="{table_id}"' if table_id else ""
    head = "".join(f"<th>{E(h)}</th>" for h in headers)
    html = f"<table{idattr}><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    return f'<div class="tablewrap">{html}</div>' if wrap else html


def kv(pairs: Iterable[tuple[str, str]]) -> str:
    return "<dl class='kv'>" + "".join(f"<dt>{E(k)}</dt><dd>{v}</dd>" for k, v in pairs) + "</dl>"


def ul(items: Iterable[str], *, raw: bool = False) -> str:
    li = "".join(f"<li>{i if raw else E(i)}</li>" for i in items if i)
    return f"<ul class='tight'>{li}</ul>" if li else ""


def callout(text: str, kind: str = "") -> str:
    return f'<div class="callout {kind}">{E(text)}</div>'


def pre(text: str) -> str:
    return f"<pre>{E(text)}</pre>"


def section(sid: str, number: str, title: str, body: str) -> str:
    return f'<section id="{sid}"><h2><span class="n">{E(number)}</span>{E(title)}</h2>{body}</section>'


def tags(items: Iterable[str]) -> str:
    return "".join(f'<span class="tag">{E(i)}</span>' for i in items)


def fmt_score(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.0f}"


# ===================================================================================================== sections
def s_executive(r: m.ReportData) -> str:
    ex, sc = r.executive, r.scorecard
    grade_letter = (sc.grade or "–")[:1]
    gcls = svg.score_class(sc.overall)
    icon = {"strength": "✔", "concern": "⚠", "gap": "○", "note": "ℹ"}
    hero = (
        '<div class="hero">'
        f'<div><div class="score">{E(fmt_score(sc.overall))}<span class="muted small"> / 100</span></div>'
        f'<div class="small muted">{E(sc.profile)} profile · confidence {sc.confidence:.2f}</div></div>'
        f'<div class="grade {gcls}" style="font-size:1.8rem">{E(grade_letter)}</div>'
        f'<div style="flex:1;min-width:240px"><strong>{E(ex.headline)}</strong><p>{E(ex.verdict)}</p></div></div>'
    )
    parts = [hero]
    if sc.grade and "(" in sc.grade:
        parts.append(callout("Grade: " + sc.grade, "info"))
    if ex.points:
        parts.append(
            ul(
                (f"<strong>{icon.get(p.kind, '•')} {E(p.kind.capitalize())}:</strong> {E(p.text)}" for p in ex.points),
                raw=True,
            )
        )
    if ex.limitations:
        parts.append("<h3>What limits this result</h3>" + "".join(callout(x, "") for x in ex.limitations))
    if ex.next_steps:
        parts.append("<h3>Do first</h3>" + ul(ex.next_steps))
    if not r.run.complete:
        parts.insert(
            0,
            callout(
                f"The run did not complete normally ({r.run.incomplete_reason}). Results cover only the tests that ran.",
                "bad",
            ),
        )
    return section("executive", "1", "Executive summary", "".join(parts))


def s_score(r: m.ReportData) -> str:
    sc = r.scorecard
    counts = Counter(x.status for x in r.results)
    segs = [(s, float(counts[s]), svg.STATUS_CLASS.get(s, "s-skipped")) for s in STATUS_ORDER if counts[s]]
    executed = counts["passed"] + counts["failed"] + counts["timeout"]
    center = pct(counts["passed"], executed) if executed else "–"
    bars = [(c.label, c.score if c.applicable else None, svg.score_class(c.score)) for c in sc.categories]
    legend = "".join(
        f'<span><i class="{svg.STATUS_CLASS.get(s, "s-skipped")}"></i>{E(s)} {counts[s]}</span>'
        for s in STATUS_ORDER
        if counts[s]
    )
    rows = [
        [
            E(c.label),
            f'<span class="grade {svg.score_class(c.score)}">{E(fmt_score(c.score))}</span>'
            if c.applicable
            else '<span class="muted">N/A</span>',
            f"{c.weight:.0%}" if c.weight else "–",
            str(c.tests),
            str(c.passed),
            f"{c.confidence:.2f}" if c.applicable else "–",
            E(c.note or ""),
        ]
        for c in sc.categories
    ]
    body = ""
    if sc.overall is None:
        body += callout("No overall score. " + " ".join(sc.notes), "bad")
    else:
        body += (
            '<div class="grid g2"><div>' + str(svg.bar_chart(bars, label="Score by category")) + "</div>"
            f'<div style="text-align:center">{svg.donut(segs, center=center, sub="passed", label="Test outcomes")}<div class="legend" style="justify-content:center">{legend}</div></div></div>'
        )
        if sc.security_cap_applied:
            body += callout(f"Score before the security cap: {sc.raw_overall:.1f}. {sc.cap_reason}", "bad")
    body += table(["Category", "Score", "Weight", "Tests", "Passed", "Confidence", "Note"], rows)
    if r.reviewed_scorecard is not None:
        rs = r.reviewed_scorecard
        body += callout(
            f"After human review: {fmt_score(rs.overall)} / 100, grade {rs.grade} (machine score {fmt_score(sc.overall)}). "
            "Both are kept; reviews never replace the original evaluation.",
            "info",
        )
    body += (
        "".join(callout(q) for q in sc.qualifiers) + ul(sc.notes) + f'<p class="small muted">{E(sc.scoring_note)}</p>'
    )
    if r.reviews:
        body += (
            "<h3>Human review log</h3>"
            '<p class="small muted">The original results are unchanged; each line is a reviewer\'s opinion stored next '
            "to them, with the values before and after.</p>"
            + table(
                ["When (UTC)", "Reviewer", "About", "Decision", "Original", "Reviewed", "Reason / comment"],
                [
                    [
                        E(f"{rv.created_at:%Y-%m-%d %H:%M}" if rv.created_at else ""),
                        E(rv.reviewer),
                        E(f"{rv.subject_type} {rv.subject}".strip()),
                        badge("info", rv.decision.replace("_", " ")),
                        E(describe_values(rv.original)),
                        E(describe_values(rv.reviewed) if rv.reviewed else "no change to the values"),
                        E("; ".join(x for x in (rv.reason, rv.comment) if x)),
                    ]
                    for rv in r.reviews
                ],
            )
        )
    return section("score", "2", "Overall score", body)


def s_risk(r: m.ReportData) -> str:
    rk = r.risk
    items = [(s, float(rk.severity_counts.get(s, 0)), svg.SEVERITY_CLASS[s]) for s in SEVERITY_ORDER]
    body = (
        '<div class="grid g2"><div>'
        + str(svg.column_chart(items, label="Open findings by severity", height=190, width=420))
        + "</div><div>"
    )
    body += kv(
        [
            ("Open findings", str(rk.open_findings)),
            ("Security findings", str(rk.security_findings)),
            (
                "Security posture",
                badge(
                    "warn" if rk.security_posture != "no_vulnerabilities_observed" else "good",
                    rk.security_posture.replace("_", " "),
                ),
            ),
        ]
    )
    body += f"<p>{E(rk.security_summary)}</p></div></div>"
    if rk.top_risks:
        body += "<h3>Top risks</h3>" + ul(rk.top_risks)
    if rk.unassessed_areas:
        body += (
            "<h3>Not assessed</h3><p class='muted small'>These areas have no result. That is a gap in coverage, not a pass.</p>"
            + ul(rk.unassessed_areas)
        )
    return section("risk", "3", "Risk summary", body)


def s_target(r: m.ReportData) -> str:
    t = r.target
    body = kv(
        [
            ("Name", E(t.name)),
            ("Version", E(t.version or "–")),
            ("Interfaces", tags(t.interfaces) or "–"),
            ("Evaluation mode", E(", ".join(t.modes) or "–")),
            ("Models", E(", ".join(t.models) or "not detected")),
            ("Frameworks", E(", ".join(t.frameworks) or "not detected")),
            ("Languages", E(", ".join(f"{k} ({n})" for k, n in t.languages.items()) or "–")),
            ("Authentication", E(t.authentication or "–")),
            ("Production target", "yes" if t.production else "no"),
        ]
    )
    if t.description:
        body += f"<p>{E(t.description)}</p>"
    if t.tools:
        body += "<h3>Tools</h3>" + table(
            ["Tool", "Side effects", "Confirmation", "Source", "Description"],
            [
                [
                    f"<code>{E(x['name'])}</code>",
                    E(x["side_effects"]),
                    {None: "unknown", True: "required", False: "not required"}[x["requires_confirmation"]],
                    E(x["source"]),
                    E(x["description"]),
                ]
                for x in t.tools
            ],
        )
    if t.data_sources:
        body += "<h3>Data sources</h3>" + table(
            ["Name", "Kind", "Source"], [[E(d["name"]), E(d["kind"]), E(d["source"])] for d in t.data_sources]
        )
    if t.limitations:
        body += "<h3>Declared or discovered limitations</h3>" + ul(t.limitations)
    return section("target", "4", "Target overview", body)


def s_architecture(r: m.ReportData) -> str:
    a = r.architecture
    body = str(svg.architecture(a.nodes, a.edges)) if a.nodes else ""
    body += f'<p class="small muted">{E(a.note)}</p>'
    if a.mermaid:
        body += (
            f"<details><summary>Diagram source (Mermaid)</summary><div class='body'>{pre(a.mermaid)}</div></details>"
        )
    return section("architecture", "5", "Target architecture", body)


def s_classification(r: m.ReportData) -> str:
    items = [(c.type, c.confidence * 100, svg.score_class(c.confidence * 100)) for c in r.classification]
    body = (
        str(svg.bar_chart(items, label="Agent type confidence", unit="%"))
        if items
        else '<p class="muted">No type reached the reporting threshold.</p>'
    )
    body += "".join(
        f"<details><summary>{E(c.type)} <span class='muted small'>{c.confidence:.2f}</span></summary><div class='body'>{ul(c.evidence)}</div></details>"
        for c in r.classification
        if c.evidence
    )
    return section("classification", "6", "Agent type classification", body)


def s_capabilities(r: m.ReportData) -> str:
    cls = {"supported": "passed", "partial": "warn", "unsupported": "blocked"}
    rows = [
        [E(c.capability), "yes" if c.detected else "no", badge(cls.get(c.testable, "info"), c.testable), E(c.reason)]
        for c in r.capabilities
    ]
    return section(
        "capabilities",
        "7",
        "Capability matrix",
        table(
            ["Capability", "Detected", "Testable by AgentLab", "Reason"],
            rows,
            empty="No capability matrix was recorded.",
        ),
    )


def s_environment(r: m.ReportData) -> str:
    e, v = r.environment, r.versioning
    rows = [
        [
            "Sandbox (Docker)",
            badge("passed" if e.docker else "blocked", "available" if e.docker else "unavailable"),
            E(e.docker_note),
        ],
        [
            "Browser (Playwright)",
            badge("passed" if e.browser else "blocked", "available" if e.browser else "unavailable"),
            E(e.browser_note),
        ],
        [
            "LLM judge",
            badge("passed" if e.judge else "blocked", "available" if e.judge else "unavailable"),
            E(e.judge_note),
        ],
        [
            "Interfaces tested",
            E(", ".join(e.interfaces) or "none"),
            E("; ".join(f"{k}: {x}" for k, x in {**e.interface_errors, **e.unreachable}.items())),
        ],
        ["Parallelism / canary seeding", f"{e.parallelism} / {'yes' if e.canary_seeding else 'no'}", ""],
    ]
    body = table(["Component", "Status", "Note"], rows) + "".join(callout(w) for w in e.warnings)
    ver = [
        ("AgentLab", E(f"{v.agentlab_version} {v.agentlab_commit or ''}".strip())),
        ("Python / platform", E(f"{v.python} / {v.platform}")),
        ("Target", E(f"{v.target} {v.target_version or ''} {(v.target_commit or '')[:12]}".strip())),
        (
            "Providers / models",
            E(", ".join(f"{p.get('name')}:{p.get('model') or '-'}" for p in v.providers) or "none configured"),
        ),
        (
            "Judges",
            E(
                ", ".join(f"{j.get('provider')}:{j.get('model')}" for j in v.judges)
                or ("none" if not v.judge_enabled else "enabled")
            ),
        ),
        (
            "Test suite",
            E(
                f"{v.test_suite.get('suite')} / {v.test_suite.get('intensity')} · plan {v.test_suite.get('plan_hash')} · {v.test_suite.get('tests')} tests"
            ),
        ),
        ("Evaluation profile", E(f"{v.evaluation_profile.get('name')} #{v.evaluation_profile.get('hash')}")),
        ("Skills", tags(f"{s['name']}@{s['version']}" for s in v.skills)),
        ("Environment fingerprint", f"<code>{E(v.environment_fingerprint)}</code>"),
        ("Timestamp", E(f"{v.timestamp:%Y-%m-%d %H:%M:%S} UTC")),
    ]
    body += "<h3>Versions (what makes this report reproducible)</h3>" + kv(ver)
    return section("environment", "8", "Environment", body)


def s_methodology(r: m.ReportData) -> str:
    me = r.methodology
    body = (
        f"<p>{E(me.summary)}</p><h3>Steps</h3>" + ul(me.steps) + "<h3>Evaluation layers</h3>" + ul(me.evaluation_layers)
    )
    body += (
        "<h3>Safety rules</h3>"
        + ul(me.safety_rules)
        + "<h3>Scoring</h3>"
        + ul(me.scoring)
        + "<h3>Severity and confidence</h3>"
        + ul(me.severity_model)
    )
    if me.assumptions:
        body += "<h3>Assumptions made by the test designer</h3>" + ul(me.assumptions)
    return section("methodology", "9", "Test methodology", body)


def s_inventory(r: m.ReportData) -> str:
    inv = r.inventory
    rows = [
        (x.label or x.key, {"passed": x.passed, "failed": x.failed, "blocked": x.blocked, "skipped": x.other})
        for x in inv.by_skill
    ]
    body = (
        f"<p>{inv.total_selected} tests selected of {inv.total_planned} planned; {inv.executed} executed. "
        f"Plan <code>{E(inv.plan_hash)}</code>.</p>"
    )
    body += (
        str(svg.stacked_bars(rows[:30], ["passed", "failed", "blocked", "skipped"], label="Tests by skill"))
        if rows
        else ""
    )
    st = {"covered": "passed", "partial": "warn", "not_covered": "failed", "not_applicable": "skipped"}

    def cov(entries: Sequence[m.CoverageRow]) -> str:
        return table(
            ["Area", "Name", "Coverage", "Tests", "Ran", "Blocked", "Note"],
            [
                [
                    E(x.key),
                    E(x.name),
                    badge(st.get(x.status, "info"), x.status.replace("_", " ")),
                    str(x.tests),
                    str(x.executed),
                    str(x.blocked),
                    E(x.note),
                ]
                for x in entries
            ],
            empty="No coverage data was recorded.",
        )

    body += "<h3>Coverage of the test taxonomy</h3>" + cov(inv.coverage)
    if inv.security_coverage:
        body += f"<details><summary>Security categories ({len(inv.security_coverage)})</summary><div class='body'>{cov(inv.security_coverage)}</div></details>"
    if inv.skills:
        body += "<h3>Skills selected</h3>" + table(
            ["Skill", "Version", "Tests", "Predicted blocked", "Why"],
            [
                [
                    E(s["name"]),
                    E(s["version"]),
                    str(s["tests"]),
                    str(s["predicted_blocked"]),
                    E("; ".join(s["reasons"])),
                ]
                for s in inv.skills
            ],
        )
    if inv.deselected:
        body += f"<details><summary>{len(inv.deselected)} planned test(s) were deselected</summary><div class='body'>{ul(f'{d["test"]}: {d["reason"]}' for d in inv.deselected)}</div></details>"
    return section("inventory", "10", "Test suite inventory", body)


def s_results(r: m.ReportData) -> str:
    cats = sorted({x.category for x in r.results})
    cells: dict[tuple[str, str], tuple[int, str]] = {}
    for c in cats:
        for s in ("passed", "failed", "blocked", "error", "skipped"):
            n = sum(
                1 for x in r.results if x.category == c and (x.status == s or (s == "failed" and x.status == "timeout"))
            )
            if n:
                cells[(c, s)] = (n, svg.STATUS_CLASS[s])
    body = ""
    if cats:
        body += "<h3>Test matrix</h3><p class='small muted'>Click a cell to filter the table below.</p>"
        body += str(
            svg.heat_matrix(
                cats, ["passed", "failed", "blocked", "error", "skipped"], cells, label="Tests by category and status"
            )
        )
    chips = "".join(
        f'<button class="chip" type="button" data-status="{s}" aria-pressed="false">{s}</button>' for s in STATUS_ORDER
    )
    options = "".join(f'<option value="{E(c)}">{E(c)}</option>' for c in cats)
    body += (
        '<div class="tools"><input id="q" type="search" placeholder="Search tests…" aria-label="Search tests">'
        f'<select id="f-cat" aria-label="Category"><option value="">All categories</option>{options}</select>'
        f'<span class="chips" id="status-chips">{chips}</span><span id="result-count" class="muted small"></span></div>'
    )
    head = (
        '<th class="sortable" data-col="0">Test</th><th class="sortable" data-col="1">Name</th>'
        '<th class="sortable" data-col="2">Category</th><th class="sortable" data-col="3">Status</th>'
        '<th class="sortable" data-col="4" data-type="num">Score</th><th class="sortable" data-col="5">Severity</th>'
        '<th class="sortable" data-col="6" data-type="num">Latency</th><th>Note</th>'
    )
    out = []
    for x in r.results:
        text = f"{x.test_id} {x.name} {x.category} {x.status} {x.objective}".lower()
        status_cell = badge(x.status) + (
            f" → {badge(x.effective_status)} <span class='small muted'>reviewed</span>" if x.effective_status else ""
        )
        note = x.blocked_reason or (
            "flaky: " + pct(round((x.pass_rate or 0) * x.attempts), x.attempts) + " of attempts passed"
            if x.flaky
            else ""
        )
        out.append(
            f'<tr class="row" tabindex="0" aria-expanded="false" data-status="{E(x.status)}" data-cat="{E(x.category)}" data-text="{E(text)}">'
            f"<td><code>{E(x.test_id)}</code></td><td>{E(x.name)}</td><td>{E(x.category)}</td><td data-v='{E(x.status)}'>{status_cell}</td>"
            f"<td data-v='{x.score:.3f}'>{x.score:.2f}</td><td data-v='{E(x.severity or '')}'>{sev(x.severity)}</td>"
            f"<td data-v='{x.latency_ms:.1f}'>{E(ms(x.latency_ms) if x.latency_ms else '')}</td><td class='small'>{E(note)}</td></tr>"
            f'<tr class="detail" hidden><td colspan="8">{_result_detail(x)}</td></tr>'
        )
    body += f'<div class="tablewrap"><table id="results"><thead><tr>{head}</tr></thead><tbody>{"".join(out)}</tbody></table></div>'
    if not r.results:
        body = '<p class="muted">No test results were recorded.</p>'
    return section("results", "11", "Test results", body)


def _result_detail(x: m.ResultRow) -> str:
    pairs = [("Objective", E(x.objective)), ("Expected", E(x.expected))]
    if x.skill:
        pairs.append(("Skill", E(x.skill)))
    if x.taxonomy or x.security_categories:
        pairs.append(("Taxonomy", tags([*x.taxonomy, *x.security_categories])))
    for i, turn in enumerate(x.inputs, 1):
        pairs.append((f"Input {i}", pre(turn)))
    if x.failed_checks:
        pairs.append(("Why it failed", ul(x.failed_checks)))
    if x.blocked_reason:
        pairs.append(("Why it did not run", E(x.blocked_reason)))
    if x.root_cause:
        pairs.append(("Root cause", E(x.root_cause)))
    if x.attempts > 1:
        pairs.append(
            ("Attempts", E(f"{x.attempts}" + (f", pass rate {x.pass_rate:.0%}" if x.pass_rate is not None else "")))
        )
    if x.finding_id:
        pairs.append(("Finding", f'<a href="#f-{E(x.finding_id)}">open finding</a>'))
    for rv in x.reviews:
        pairs.append(("Review", E(f"{rv.reviewer}: {rv.decision}" + (f" ({rv.reason})" if rv.reason else ""))))
    return kv(pairs)


def s_failed(r: m.ReportData) -> str:
    if not r.failed_tests:
        return section(
            "failed",
            "12",
            "Failed tests",
            "<p>No test failed.</p>"
            if r.scorecard.counts.get("passed")
            else '<p class="muted">No test failed (and few or none ran).</p>',
        )
    out = []
    for f in r.failed_tests:
        last = f.attempts[-1] if f.attempts else None
        inner = [("Objective", E(f.objective)), ("Expected", E(f.expected))]
        inner += [(f"Input {i}", pre(t)) for i, t in enumerate(f.inputs, 1)]
        inner.append(("Why it failed", ul(f.why_it_failed)))
        if last and last.outputs:
            inner.append((f"Observed (attempt {last.attempt})", pre(last.outputs[-1])))
        if last and last.checks:
            inner.append(
                (
                    "Checks",
                    ul(
                        (f"{'✔' if c.passed else '✘'} <code>{E(c.type)}</code> {E(c.message)}" for c in last.checks),
                        raw=True,
                    ),
                )
            )
        for j in last.judge if last else []:
            inner.append(
                (
                    f"Judge: {j['metric']}",
                    E(
                        f"score {j['score']}, confidence {j['confidence']}, agreement {j['agreement']}"
                        + (" (uncertain)" if j["uncertain"] else "")
                    ),
                )
            )
        inner.append(("Reproduce", f"<code>{E(f.reproduction)}</code>"))
        out.append(
            f'<details id="t-{E(f.test_id)}"><summary>{badge(f.status)} {sev(f.severity)} <code>{E(f.test_id)}</code> {E(f.name)}</summary><div class="body">{kv(inner)}</div></details>'
        )
    return section(
        "failed",
        "12",
        "Failed tests",
        f"<p class='muted small'>{len(r.failed_tests)} test(s), most serious first.</p>" + "".join(out),
    )


def _finding_html(f: m.FindingView) -> str:
    s = f.effective_severity or f.severity
    pairs = [
        ("Finding", E(f.title)),
        (
            "Evidence",
            f'test <a href="#t-{E(f.test_id)}"><code>{E(f.test_id)}</code></a>'
            + (" · " + tags(e[:19] for e in f.evidence[:4]) if f.evidence else ""),
        ),
        ("Expected", E(f.expected)),
        ("Observed", E(f.observed)),
        ("Impact", E(f.impact)),
        ("Severity", sev(f.severity) + (f" → reviewed: {sev(f.effective_severity)}" if f.effective_severity else "")),
        ("Confidence", f"{f.confidence:.2f} · root cause {E(f.root_cause)} ({f.root_cause_confidence:.2f})"),
        ("Reproduction", E(f.reproduction)),
        ("Recommendation", E(f.recommendation)),
    ]
    if f.facts:
        pairs.append(("Observed facts", ul(f.facts)))
    if f.inferences:
        pairs.append(("Inferences", f'<span class="infer">{ul(f.inferences)}</span>'))
    if f.judgments:
        pairs.append(("Judgments", f'<span class="judge">{ul(f.judgments)}</span>'))
    if f.severity_breakdown:
        pairs.append(("Severity breakdown", E(json.dumps(f.severity_breakdown, sort_keys=True))))
    for rv in f.reviews:
        pairs.append(("Review", E(f"{rv.reviewer}: {rv.decision}" + (f" ({rv.reason})" if rv.reason else ""))))
    status = f" {badge('warn', f.status)}" if f.status not in {"open"} else ""
    return (
        f'<details class="finding f-{E(s)}" id="f-{E(f.id)}" data-sev="{E(s)}"><summary>{sev(s)} {E(f.title)}{status} '
        f'<span class="muted small">{E(f.test_id)}</span></summary><div class="body">{kv(pairs)}</div></details>'
    )


def s_security(r: m.ReportData) -> str:
    s = r.security
    cls = {
        "vulnerabilities_observed": "bad",
        "no_vulnerabilities_observed": "good",
        "partially_tested": "",
        "not_tested": "",
    }.get(s.posture, "")
    body = callout(f"Security posture: {s.posture.replace('_', ' ')}. {s.summary} {s.rating_note}", cls)
    body += f'<p class="small muted">{E(s.canary_note)}</p>' + ul(s.caveats)
    vcls = {
        "vulnerable": "failed",
        "resistant": "passed",
        "partially_tested": "warn",
        "not_tested": "blocked",
        "not_covered": "blocked",
        "not_applicable": "skipped",
    }
    if s.categories:
        body += "<h3>Categories</h3>" + table(
            ["Code", "Category", "Verdict", "Tests", "Passed", "Failed", "Blocked", "Note"],
            [
                [
                    E(c.code),
                    E(c.name),
                    badge(vcls.get(c.verdict, "info"), c.verdict.replace("_", " ")),
                    str(c.tests),
                    str(c.passed),
                    str(c.failed),
                    str(c.blocked),
                    E(c.note),
                ]
                for c in s.categories
            ],
        )
    if s.attacks_succeeded:
        body += "<h3>Attacks that succeeded against the target</h3>" + table(
            ["Test", "Attack", "Categories", "Severity", "Channels", "Evidence (redacted excerpt)"],
            [
                [
                    f"<code>{E(a['test_id'])}</code>",
                    E(a["name"]),
                    E(", ".join(a["codes"])),
                    sev(a["severity"]),
                    E(", ".join(a["channels"])),
                    E(a.get("snippet") or ""),
                ]
                for a in s.attacks_succeeded
            ],
        )
    sec = [f for f in r.findings if f.is_security]
    other = [f for f in r.findings if not f.is_security]
    chips = "".join(
        f'<button class="chip" type="button" data-sev="{x}" aria-pressed="false">{x}</button>' for x in SEVERITY_ORDER
    )
    body += f'<h3>Findings</h3><div class="tools"><span class="chips" id="sev-chips">{chips}</span><button class="btn" id="btn-expand" type="button">Expand all</button></div>'
    body += "".join(_finding_html(f) for f in sec) or '<p class="muted">No security finding.</p>'
    if other:
        body += f"<h3>Other findings ({len(other)})</h3>" + "".join(_finding_html(f) for f in other)
    return section("security", "13", "Security findings", body)


def s_domain(r: m.ReportData, d: m.DomainSection, number: str) -> str:
    sid = f"domain-{d.key}"
    if not d.tests and not d.blocked_tests:
        return section(sid, number, d.title, f"<p class='muted'>{E(d.summary)}</p>")
    body = f"<p>{E(d.summary)}</p>"
    subs = d.metrics.get("by_subcategory", {})
    if subs:
        rows = [(k, {"passed": v["passed"], "failed": v["failed"], "blocked": v["blocked"]}) for k, v in subs.items()]
        body += str(svg.stacked_bars(rows, ["passed", "failed", "blocked"], label=f"{d.title} by area"))
    if d.failures:
        body += "<h3>Failures</h3>" + ul(d.failures)
    if d.blocked_tests:
        body += "<h3>Could not run (BLOCKED, not failed)</h3>" + ul(
            f"{b['test']}: {b['reason']}" for b in d.blocked_tests
        )
    return section(sid, number, d.title, body)


def s_reliability(r: m.ReportData) -> str:
    rel = r.reliability
    body = (
        f"<p>Verdict: {badge('passed' if rel.verdict == 'stable' else 'warn' if rel.verdict in {'mostly_stable', 'measured'} else 'failed' if rel.verdict == 'unstable' else 'blocked', rel.verdict.replace('_', ' '))} "
        f"· {rel.measured_tests} test(s) repeated, {rel.single_run_tests} run once"
        + (f" · consistency {rel.consistency:.0%}" if rel.consistency is not None else "")
        + "</p>"
    )
    if rel.flaky:
        body += "<h3>Flaky tests</h3>" + str(
            svg.bar_chart(
                [(f"{x.test_id}", x.pass_rate * 100, svg.score_class(x.pass_rate * 100)) for x in rel.flaky[:15]],
                label="Pass rate of flaky tests",
                unit="%",
            )
        )
    if rel.deterministic_failures:
        body += "<h3>Failed every time</h3><p>" + tags(rel.deterministic_failures) + "</p>"
    body += ul(rel.notes)
    return section("reliability", "19", "Reliability", body)


def s_performance(r: m.ReportData) -> str:
    p = r.performance
    body = kv(
        [
            ("Tests measured", str(p.measured)),
            ("p50 / p95 / max", E(f"{ms(p.p50_ms)} / {ms(p.p95_ms)} / {ms(p.max_ms)}")),
            ("Budget", E(ms(p.budget_ms) if p.budget_ms else "none")),
            ("Over budget", E(", ".join(p.over_budget) or "none")),
            ("Timeouts", str(p.timeouts)),
        ]
    )
    if p.histogram:
        body += (
            '<div class="grid g2"><div>'
            + str(
                svg.column_chart(
                    [(h["bucket"].replace(" ms", ""), float(h["count"]), "q-ok") for h in p.histogram],
                    label="Latency distribution (ms)",
                    height=210,
                    width=420,
                )
            )
            + "</div><div>"
        )
        top = p.slowest[:8]
        body += (
            str(
                svg.bar_chart(
                    [
                        (x.test_id, x.latency_ms, "q-warn" if p.budget_ms and x.latency_ms > p.budget_ms else "q-ok")
                        for x in top
                    ],
                    maximum=max([x.latency_ms for x in top] + [1.0]),
                    unit=" ms",
                    label="Slowest tests",
                    label_width=170,
                    width=420,
                )
            )
            + "</div></div>"
        )
    body += ul(p.notes)
    return section("performance", "20", "Performance", body)


def s_cost(r: m.ReportData) -> str:
    c = r.cost
    body = kv(
        [
            ("Total tokens", f"{c.total_tokens:,} (target {c.target_tokens:,}, judge {c.judge_tokens:,})"),
            (
                "Total cost",
                E(
                    money(c.total_cost_usd, c.cost_known)
                    + (f" (judge {money(c.judge_cost_usd)})" if c.judge_cost_usd else "")
                ),
            ),
        ]
    )
    rows = [(x.label, x.tokens) for x in c.by_category if x.tokens]
    if rows:
        top = max(v for _n, v in rows)
        body += str(
            svg.bar_chart(
                [(n, float(v), "q-ok") for n, v in rows[:12]],
                maximum=float(top),
                label="Tokens by category",
                value_fmt="{:,.0f}",
            )
        )
    if c.most_expensive:
        body += "<h3>Most expensive tests</h3>" + table(
            ["Test", "Tokens", "Cost"],
            [[E(x.label), f"{x.tokens:,}", E(money(x.cost_usd, c.cost_known))] for x in c.most_expensive],
        )
    body += ul(c.notes)
    return section("cost", "21", "Cost", body)


def s_regression(r: m.ReportData) -> str:
    body = ""
    if r.regression:
        body += comparison_html(Comparison.model_validate(r.regression))
    else:
        body += "<p class='muted'>No baseline was supplied. Compare two runs with <code>agentlab compare RUN_A RUN_B</code> or run with <code>--baseline RUN_ID</code>.</p>"
    pts = r.trend.points
    if len(pts) > 1:
        body += "<h3>Trend across runs of this target</h3>" + str(
            svg.line_chart([(p.run_id[:6], p.overall) for p in pts], label="Overall score by run")
        )
        body += str(
            svg.column_chart(
                [(p.run_id[:6], float(p.failed), "s-failed") for p in pts],
                label="Failed tests by run",
                height=170,
                width=720,
            )
        )
        body += table(
            ["Run", "Started", "Score", "Failed", "Tests", "Findings", "Comparable"],
            [
                [
                    f"<code>{E(p.run_id[:8])}</code>",
                    E(f"{p.started_at:%Y-%m-%d %H:%M}" if p.started_at else ""),
                    fmt_score(p.overall),
                    str(p.failed),
                    str(p.tests),
                    str(p.findings),
                    "yes" if p.comparable else badge("warn", "different profile"),
                ]
                for p in pts
            ],
        )
    body += f'<p class="small muted">{E(r.trend.note)}</p>'
    return section("regression", "22", "Regression comparison", body)


def s_evidence(r: m.ReportData, load: BlobLoader | None) -> str:
    ev = r.evidence
    body = f"<p>{plural(len(ev.items), 'artifact')} · {plural(len(ev.trace_ids), 'trace')}. <span class='muted small'>{E(ev.redaction_note)}</span></p>"
    shots = 0
    for b in ev.browser:
        body += f"<h3>Browser: <code>{E(b.test_id)}</code> <span class='muted small'>{E(b.browser)}</span></h3>"
        body += (
            f"<p class='small'>Trace: <code>{E(b.trace or 'not recorded')}</code>"
            + (f" · video <code>{E(b.video)}</code>" if b.video else "")
            + "</p>"
        )
        if b.actions:
            body += table(
                ["#", "Action", "Target", "OK", "Detail"],
                [
                    [
                        str(a.index),
                        E(a.action),
                        E(a.target),
                        badge("passed", "ok") if a.ok else badge("failed", "failed"),
                        E(a.detail),
                    ]
                    for a in b.actions
                ],
            )
        figs = []
        for sid in b.screenshots:
            if load is None or shots >= MAX_IMAGES:
                figs.append(
                    f"<figure><figcaption>screenshot <code>{E(sid[:19])}</code> (referenced, not embedded)</figcaption></figure>"
                )
                continue
            data = load(sid)
            if data and len(data) <= MAX_IMAGE_BYTES and data[:4] == b"\x89PNG":
                figs.append(
                    f'<figure><img alt="Browser screenshot for {E(b.test_id)}" src="data:image/png;base64,{base64.b64encode(data).decode()}"><figcaption>{E(sid[:19])}</figcaption></figure>'
                )
                shots += 1
            else:
                figs.append(
                    f"<figure><figcaption>screenshot <code>{E(sid[:19])}</code> (too large to embed)</figcaption></figure>"
                )
        if figs:
            body += f'<div class="gallery">{"".join(figs)}</div>'
    for repo in ev.repository:
        body += f"<h3>Repository evidence: <code>{E(repo.test_id)}</code></h3>"
        if repo.files:
            body += "<p>Files: " + tags(repo.files) + "</p>"
        if repo.diff:
            body += "<h4>Diff (redacted, truncated)</h4>" + pre(repo.diff)
        if repo.test_output:
            body += "<h4>Test output</h4>" + pre(repo.test_output)
        body += ul(repo.notes)
    rows = [
        [E(i.name), E(i.kind), E(i.test_key or ""), E(i.media_type), f"{i.size:,}", f"<code>{E(i.sha256[:16])}</code>"]
        for i in ev.items[:200]
    ]
    body += f"<details><summary>Artifacts ({len(ev.items)})</summary><div class='body'>{table(['Name', 'Kind', 'Test', 'Type', 'Bytes', 'SHA-256'], rows)}</div></details>"
    return section("evidence", "23", "Evidence", body)


def s_recommendations(r: m.ReportData) -> str:
    out = []
    for rec in r.recommendations:
        out.append(
            f"<details {'open' if rec.priority <= 3 else ''}><summary><strong>#{rec.priority}</strong> {sev(rec.severity)} {E(rec.title)} "
            f"<span class='tag'>{E(rec.kind)}</span></summary><div class='body'>"
            + kv(
                [
                    ("Why", E(rec.why)),
                    ("Action", E(rec.action)),
                    ("Effort", E(rec.effort)),
                    ("Tests", tags(rec.tests[:12])),
                    ("Findings", tags(f[:8] for f in rec.findings[:8])),
                ]
            )
            + "</div></details>"
        )
    return section(
        "recommendations", "24", "Recommendations", "".join(out) or "<p class='muted'>Nothing to recommend.</p>"
    )


def s_priorities(r: m.ReportData) -> str:
    rows = [
        [
            str(x.priority),
            E(x.kind),
            E(x.title),
            sev(x.severity),
            E(x.effort),
            f"{x.confidence:.2f}" if x.confidence is not None else "",
        ]
        for x in r.recommendations
    ]
    body = "<p class='muted small'>Ordered by severity × confidence with security first; coverage, configuration and verification items follow the fixes.</p>"
    return section(
        "priorities",
        "25",
        "Remediation priorities",
        body
        + table(["#", "Kind", "Item", "Severity", "Effort", "Confidence"], rows, empty="No remediation is needed."),
    )


def s_appendix(r: m.ReportData) -> str:
    ap = r.appendix
    body = "<h3>Skills used</h3>" + table(
        ["Skill", "Version", "Trust", "Content hash"],
        [
            [
                E(x["name"]),
                E(x["version"]),
                E(x.get("trust", "")),
                f"<code>{E(str(x.get('content_hash', ''))[:12])}</code>",
            ]
            for x in ap.skills
        ],
    )
    body += "<h3>Pipeline phases</h3>" + table(
        ["#", "Phase", "Status", "Seconds"],
        [[str(p.get("index")), E(p.get("phase")), E(p.get("status")), E(p.get("duration_s"))] for p in ap.phases],
    )
    if ap.warnings:
        body += "<h3>Warnings</h3>" + ul(ap.warnings)
    body += "<h3>Limitations</h3>" + ul(ap.limitations)
    body += "<h3>Glossary</h3>" + kv((k, E(v)) for k, v in ap.glossary.items())
    body += f"<details><summary>Configuration fingerprint (no secrets)</summary><div class='body'>{pre(json.dumps(ap.config, indent=2, sort_keys=True, default=str))}</div></details>"
    return section("appendix", "26", "Appendix", body)


def s_raw(r: m.ReportData, json_name: str, embedded: bool, preview: str) -> str:
    body = (
        f"<p>The complete data behind this report is machine-readable (schema <code>{E(r.schema_id)}</code>, version {r.schema_version}). "
        f"{E(r.appendix.checksums_note)}</p>"
    )
    body += (
        f'<p><button class="btn" id="btn-json" type="button" data-name="{E(json_name)}"'
        + ("" if embedded else " disabled")
        + ">Download JSON</button></p>"
    )
    if not embedded:
        body += callout(f"The data is too large to embed in this page; use {json_name} from the report bundle.")
    body += f"<details><summary>Preview</summary><div class='body'>{pre(preview)}</div></details>"
    return section("raw", "27", "Raw machine-readable results", body)


# ================================================================================================= the page
NAV = [
    ("executive", "1 Executive summary"),
    ("score", "2 Overall score"),
    ("risk", "3 Risk summary"),
    ("target", "4 Target overview"),
    ("architecture", "5 Architecture"),
    ("classification", "6 Agent type"),
    ("capabilities", "7 Capabilities"),
    ("environment", "8 Environment"),
    ("methodology", "9 Methodology"),
    ("inventory", "10 Test suite"),
    ("results", "11 Test results"),
    ("failed", "12 Failed tests"),
    ("security", "13 Security findings"),
    ("domain-rag", "14 RAG"),
    ("domain-tools", "15 Tools"),
    ("domain-memory", "16 Memory"),
    ("domain-browser", "17 Browser"),
    ("domain-multi_agent", "18 Multi-agent"),
    ("domain-other", "18b Other"),
    ("reliability", "19 Reliability"),
    ("performance", "20 Performance"),
    ("cost", "21 Cost"),
    ("regression", "22 Regression"),
    ("evidence", "23 Evidence"),
    ("recommendations", "24 Recommendations"),
    ("priorities", "25 Priorities"),
    ("appendix", "26 Appendix"),
    ("raw", "27 Raw data"),
]


def _shell(title: str, body: str, nav: Sequence[tuple[str, str]], *, subtitle: str = "", badge_html: str = "") -> str:
    css = (TEMPLATES / "report.css").read_text(encoding="utf-8")
    js = (TEMPLATES / "report.js").read_text(encoding="utf-8")
    digest = base64.b64encode(hashlib.sha256(js.encode("utf-8")).digest()).decode()
    csp = (
        "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
        f"script-src 'sha256-{digest}'; base-uri 'none'; form-action 'none'"
    )
    toc = "".join(f'<a href="#{E(i)}">{E(t)}</a>' for i, t in nav)
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta http-equiv="Content-Security-Policy" content="{E(csp)}">'
        '<meta name="color-scheme" content="light dark">'
        f"<title>{E(title)}</title><style>{css}</style></head><body>"
        f'<header class="top"><span class="title">{E(title)}</span>{badge_html}<span class="muted small">{E(subtitle)}</span><span class="spacer"></span>'
        '<button class="btn" id="btn-expand" type="button">Expand all</button>'
        '<button class="btn" id="btn-theme" type="button" title="Switch light/dark">Theme</button>'
        '<button class="btn" id="btn-print" type="button">Print</button></header>'
        f'<div class="layout"><nav class="toc" aria-label="Sections">{toc}</nav><main>{body}</main></div>'
        "<footer>Generated by AgentLab. Statements are labelled observed / inferred / judged / recommended; "
        "BLOCKED tests are coverage gaps, never failures.</footer>"
        "@@DATA@@"
        f"<script>{js}</script></body></html>"
    )


def render_html(r: m.ReportData, *, load_blob: BlobLoader | None = None, json_name: str = "report.json") -> str:
    data = json.dumps(r.to_json_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)
    embedded = len(data) <= MAX_EMBEDDED_JSON
    others = [d for d in r.domains if d.key in {"planning", "coding", "mcp", "document"}]
    sections = [
        s_executive(r),
        s_score(r),
        s_risk(r),
        s_target(r),
        s_architecture(r),
        s_classification(r),
        s_capabilities(r),
        s_environment(r),
        s_methodology(r),
        s_inventory(r),
        s_results(r),
        s_failed(r),
        s_security(r),
    ]
    numbers = {"rag": "14", "tools": "15", "memory": "16", "browser": "17", "multi_agent": "18"}
    for d in r.domains:
        if d.key in numbers:
            sections.append(s_domain(r, d, numbers[d.key]))
    other_html = "".join(
        f"<h3>{E(d.title)}</h3>"
        + (
            f"<p>{E(d.summary)}</p>"
            + (ul(d.failures) if d.failures else "")
            + (ul(f"{b['test']}: {b['reason']}" for b in d.blocked_tests) if d.blocked_tests else "")
        )
        for d in others
        if d.tests or d.blocked_tests or d.applicable
    )
    sections.append(
        section(
            "domain-other",
            "18b",
            "Other evaluations",
            other_html or "<p class='muted'>No planning, coding, MCP or document tests applied to this target.</p>",
        )
    )
    sections += [
        s_reliability(r),
        s_performance(r),
        s_cost(r),
        s_regression(r),
        s_evidence(r, load_blob),
        s_recommendations(r),
        s_priorities(r),
        s_appendix(r),
        s_raw(r, json_name, embedded, data[:6000] + ("…" if len(data) > 6000 else "")),
    ]
    sc = r.scorecard
    gcls = svg.score_class(sc.overall)
    badge_html = f'<span class="grade {gcls}">{E((sc.grade or "–")[:1])}</span><span class="muted small">{E(fmt_score(sc.overall))}/100</span>'
    page = _shell(
        r.title,
        "".join(sections),
        NAV,
        subtitle=f"run {r.run.run_id[:8]} · {r.run.status} · {r.generated_at:%Y-%m-%d %H:%M} UTC",
        badge_html=badge_html,
    )
    payload = (
        data.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    block = f'<script id="agentlab-data" type="application/json">{payload}</script>' if embedded else ""
    return page.replace("@@DATA@@", block)


# ============================================================================================== comparisons
def comparison_html(c: Comparison) -> str:
    comp = c.compatibility
    kind = {"comparable": "good", "comparable_with_caveats": "", "not_comparable": "bad"}[comp.verdict]
    head = {"regressed": "bad", "improved": "good", "mixed": "", "unchanged": "info", "inconclusive": ""}[c.verdict]
    out = [callout(c.summary, head), callout("Compatibility: " + comp.summary, kind)]
    out.append(
        table(
            ["", "Run A (baseline)", "Run B (current)"],
            [
                ["Run", f"<code>{E(c.run_a.run_id[:8])}</code>", f"<code>{E(c.run_b.run_id[:8])}</code>"],
                [
                    "Target / version",
                    E(f"{c.run_a.target} {c.run_a.target_version or ''}"),
                    E(f"{c.run_b.target} {c.run_b.target_version or ''}"),
                ],
                ["Commit", E((c.run_a.target_commit or "–")[:12]), E((c.run_b.target_commit or "–")[:12])],
                [
                    "Score / grade",
                    E(f"{fmt_score(c.run_a.overall)} / {c.run_a.grade or '–'}"),
                    E(f"{fmt_score(c.run_b.overall)} / {c.run_b.grade or '–'}"),
                ],
                [
                    "Executed / blocked",
                    f"{c.run_a.executed} / {c.run_a.blocked}",
                    f"{c.run_b.executed} / {c.run_b.blocked}",
                ],
                [
                    "Models / judges",
                    E(", ".join(c.run_a.models + c.run_a.judges) or "–"),
                    E(", ".join(c.run_b.models + c.run_b.judges) or "–"),
                ],
            ],
        )
    )
    if comp.differences:
        icon = {
            "blocks_comparison": badge("failed", "blocks comparison"),
            "caveat": badge("warn", "caveat"),
            "info": badge("info", "expected"),
        }
        out.append(
            "<h3>What differs between the runs</h3>"
            + table(
                ["Impact", "Field", "A", "B", "Why it matters"],
                [[icon[d.impact], E(d.field), E(d.base), E(d.current), E(d.note)] for d in comp.differences],
            )
        )
    out.append(ul(comp.notes))
    counts = [
        (
            KIND_TITLES[k].split(" (")[0],
            float(n),
            {
                "new_failure": "s-failed",
                "resolved": "s-passed",
                "still_failing": "s-failed",
                "lost_coverage": "s-blocked",
                "gained_coverage": "s-passed",
                "unstable": "s-error",
            }.get(k, "s-skipped"),
        )
        for k, n in c.counts.items()
    ]
    if counts:
        out.append(
            "<h3>Test changes</h3>"
            + str(
                svg.bar_chart(
                    counts,
                    maximum=max(n for _l, n, _c in counts),
                    label="Test changes",
                    value_fmt="{:.0f}",
                    label_width=230,
                )
            )
        )
    for k, title in KIND_TITLES.items():
        items = [d for d in c.tests if d.kind == k]
        if items:
            out.append(
                f"<details {'open' if k in {'new_failure', 'resolved'} else ''}><summary>{E(title)}: {len(items)}</summary><div class='body'>"
                + table(
                    ["Test", "Name", "A", "B", "Severity", "Note"],
                    [
                        [
                            f"<code>{E(d.test_id)}</code>",
                            E(d.name),
                            badge(d.status_a or "skipped", d.status_a or "–"),
                            badge(d.status_b or "skipped", d.status_b or "–"),
                            sev(d.severity_b or d.severity_a),
                            E(d.note),
                        ]
                        for d in items[:100]
                    ],
                )
                + "</div></details>"
            )
    out.append("<h3>Score changes</h3>")
    sc = c.score
    out.append(
        f"<p>Overall {E(fmt_score(sc.get('overall_a')))} → {E(fmt_score(sc.get('overall_b')))}"
        + (f" ({sc['overall_delta']:+.1f})" if sc.get("overall_delta") is not None else "")
        + f" · grade {E(sc.get('grade_a') or '–')} → {E(sc.get('grade_b') or '–')}</p>"
    )
    if sc.get("note"):
        out.append(callout(sc["note"], "bad"))
    out.append(
        table(
            ["Category", "A", "B", "Δ", "Same tests: A", "Same tests: B", "Note"],
            [
                [
                    E(x.label),
                    fmt_score(x.score_a),
                    fmt_score(x.score_b),
                    "" if x.delta is None else f"{x.delta:+.1f}",
                    "" if x.like_for_like_a is None else f"{x.like_for_like_a:.0f}% ({x.shared_ran})",
                    "" if x.like_for_like_b is None else f"{x.like_for_like_b:.0f}%",
                    E(x.note),
                ]
                for x in c.categories
            ],
        )
    )

    def metrics(items: Sequence[MetricDelta], unit_fmt: Callable[[float | None, str], str]) -> str:
        return table(
            ["Metric", "A", "B", "Change", "Note"],
            [
                [
                    E(i.name),
                    E(unit_fmt(i.a, i.unit)),
                    E(unit_fmt(i.b, i.unit)),
                    "" if i.change_pct is None else f"{i.change_pct:+.0f}%",
                    E(i.note),
                ]
                for i in items
            ],
        )

    out.append("<h3>Latency changes</h3>" + metrics(c.latency, format_metric))
    if c.latency_changes:
        out.append(
            table(
                ["Test", "Name", "A", "B", ""],
                [
                    [
                        f"<code>{E(d.test_id)}</code>",
                        E(d.name),
                        E(ms(d.latency_a)),
                        E(ms(d.latency_b)),
                        badge("warn" if d.direction == "slower" else "passed", d.direction),
                    ]
                    for d in c.latency_changes
                ],
            )
        )
    out.append("<h3>Cost changes</h3>" + metrics(c.cost, format_metric))
    rel = c.reliability
    out.append(
        f"<h3>Reliability changes</h3><p>Verdict {E(rel.get('verdict_a'))} → {E(rel.get('verdict_b'))} · flaky tests {rel.get('flaky_a')} → {rel.get('flaky_b')}</p>"
        + (f"<p>Newly flaky: {tags(rel['newly_flaky'])}</p>" if rel.get("newly_flaky") else "")
        + (f"<p class='muted small'>{E(rel['note'])}</p>" if rel.get("note") else "")
    )
    sec = c.security
    out.append(
        f"<h3>Security changes</h3><p>Posture {E(sec.get('posture_a'))} → {E(sec.get('posture_b'))} ({E(sec.get('direction'))})</p>"
    )
    if sec.get("new_attacks_succeeded"):
        out.append("<p>New attacks that succeeded: " + tags(sec["new_attacks_succeeded"]) + "</p>")
    changed = [f for f in c.findings if f.kind != "unchanged"]
    if changed:
        out.append(
            "<h3>Findings</h3>"
            + table(
                ["Change", "Test", "Finding", "A", "B"],
                [
                    [badge(f.kind), f"<code>{E(f.test_id)}</code>", E(f.title), sev(f.severity_a), sev(f.severity_b)]
                    for f in changed
                ],
            )
        )
    return "".join(out)


def render_comparison_html(c: Comparison) -> str:
    title = f"Regression comparison {c.run_a.run_id[:8]} → {c.run_b.run_id[:8]}"
    data = json.dumps(c.to_json_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)
    body = section("comparison", "", title, comparison_html(c))
    page = _shell(
        title,
        body,
        [("comparison", title)],
        subtitle=c.verdict,
        badge_html=badge({"regressed": "failed", "improved": "passed"}.get(c.verdict, "info"), c.verdict),
    )
    payload = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return page.replace("@@DATA@@", f'<script id="agentlab-data" type="application/json">{payload}</script>')


__all__ = ["comparison_html", "label", "render_comparison_html", "render_html"]
