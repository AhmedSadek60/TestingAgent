"""Markdown rendering of a :class:`ReportData` (readable in a terminal, a pull request or a wiki)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from agentlab.reporting import model as m
from agentlab.reporting.util import describe_values, money, ms, pct, plural

SEVERITY_BADGE = {"critical": "CRITICAL", "high": "HIGH", "medium": "MEDIUM", "low": "LOW", "info": "INFO"}


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def table(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    body = [f"| {' | '.join(_cell(c) for c in row)} |" for row in rows]
    if not body:
        return "_none_\n"
    head = f"| {' | '.join(headers)} |\n| {' | '.join('---' for _ in headers)} |\n"
    return head + "\n".join(body) + "\n"


def bullets(items: Iterable[str]) -> str:
    out = [f"- {line}" for line in items if line]
    return "\n".join(out) + "\n" if out else "_none_\n"


def _nested(text: str) -> str:
    """Multi-line text as a nested list (a bare newline would end the surrounding list item)."""
    return "".join(f"  - {line.strip()}\n" for line in text.splitlines() if line.strip()) or "  - none\n"


# ---------------------------------------------------------------------------------------------- untrusted text
# Everything the target said, every test name a user wrote and every file a repository contains ends up in the
# report. Markdown viewers differ in what they let through, so raw HTML and link syntax in that text is defused in the
# finished document: text inside code fences and code spans is left exactly as it is (it is never interpreted).
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")
_TAG_START = re.compile(r"<(?=[A-Za-z/!?])")


def _defuse(text: str) -> str:
    return _TAG_START.sub("&lt;", text).replace("](", "]\\(")


def _defuse_line(line: str) -> str:
    out: list[str] = []
    pos = 0
    for span in _CODE_SPAN.finditer(line):
        out.append(_defuse(line[pos : span.start()]))
        out.append(span.group(0))
        pos = span.end()
    out.append(_defuse(line[pos:]))
    return "".join(out)


def neutralise_markup(text: str) -> str:
    """Escape HTML tags and ``](`` link syntax outside code, so untrusted text cannot become markup in a viewer."""
    out: list[str] = []
    fence: tuple[str, int] | None = None
    for line in text.split("\n"):
        m = _FENCE.match(line)
        if fence is not None:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] and not m.group(2).strip():
                fence = None
            out.append(line)
            continue
        if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):  # a backtick fence's info string has no backtick
            fence = (m.group(1)[0], len(m.group(1)))
            out.append(line)
            continue
        out.append(_defuse_line(line))
    return "\n".join(out)


def _fence(text: str, lang: str = "") -> str:
    ticks = "```"
    while ticks in text:  # a code block must not be closed by the text it holds
        ticks += "`"
    return f"{ticks}{lang}\n{text}\n{ticks}\n"


def _score(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.0f}"


def render_markdown(r: m.ReportData) -> str:
    out: list[str] = []
    w = out.append
    run, v = r.run, r.versioning
    w(f"# {r.title}\n")
    w(
        f"- **Run:** `{run.run_id}` ({run.status}), suite `{run.suite}`, intensity `{run.intensity}`\n"
        f"- **Generated:** {r.generated_at:%Y-%m-%d %H:%M:%S} UTC · report version {r.report_version} · "
        f"AgentLab {v.agentlab_version}{f' ({v.agentlab_commit})' if v.agentlab_commit else ''}\n"
        f"- **Target:** {r.target.name}{f' {r.target.version}' if r.target.version else ''}"
        f"{f' @ {v.target_commit[:12]}' if v.target_commit else ''}\n"
    )
    if r.reviewed:
        w("- **Human review:** this report includes reviewer decisions; original results are shown unchanged.\n")
    if not run.complete:
        w(f"> ⚠ The run did not complete normally ({run.incomplete_reason}). Results cover only the tests that ran.\n")

    # 1
    ex = r.executive
    w("\n## 1. Executive summary\n")
    w(f"**{ex.headline}**\n\n{ex.verdict}\n")
    if ex.points:
        w(bullets(f"**{p.kind.capitalize()}:** {p.text}" for p in ex.points))
    if ex.limitations:
        w("\n**What limits this result**\n\n" + bullets(ex.limitations))
    if ex.next_steps:
        w("\n**Do first**\n\n" + bullets(ex.next_steps))

    # 2
    sc = r.scorecard
    w("\n## 2. Overall score\n")
    if sc.overall is None:
        w("_No overall score: " + ("; ".join(sc.notes) or "nothing scorable ran") + "._\n")
    else:
        w(f"**{sc.overall:.1f} / 100 · grade {sc.grade}** (profile `{sc.profile}`, confidence {sc.confidence:.2f})\n")
        if sc.security_cap_applied:
            w(f"> Score before the security cap: {sc.raw_overall:.1f}. {sc.cap_reason}\n")
    w(
        table(
            ["Category", "Score", "Weight", "Tests", "Passed", "Confidence", "Note"],
            (
                (
                    c.label,
                    _score(c.score) if c.applicable else "N/A",
                    f"{c.weight:.0%}" if c.weight else "-",
                    c.tests,
                    c.passed,
                    f"{c.confidence:.2f}" if c.applicable else "-",
                    c.note or "",
                )
                for c in sc.categories
            ),
        )
    )
    if r.reviewed_scorecard is not None:
        rs = r.reviewed_scorecard
        w(
            f"\n**After human review:** {_score(rs.overall)} / 100, grade {rs.grade} (machine score {_score(sc.overall)}).\n"
        )
    w("\n" + bullets(sc.qualifiers + sc.notes + [sc.scoring_note]))
    if r.reviews:
        w(
            "\n**Human review log** (the original results above are unchanged; each line is a reviewer's opinion "
            "stored next to them)\n\n"
            + table(
                ["When (UTC)", "Reviewer", "About", "Decision", "Original", "Reviewed", "Reason / comment"],
                (
                    (
                        f"{rv.created_at:%Y-%m-%d %H:%M}" if rv.created_at else "",
                        rv.reviewer,
                        f"{rv.subject_type} {rv.subject}".strip(),
                        rv.decision,
                        describe_values(rv.original),
                        describe_values(rv.reviewed) if rv.reviewed else "no change to the values",
                        "; ".join(x for x in (rv.reason, rv.comment) if x),
                    )
                    for rv in r.reviews
                ),
            )
        )

    # 3
    rk = r.risk
    w("\n## 3. Risk summary\n")
    w(
        table(["Severity", "Open findings"], ((s.upper(), n) for s, n in rk.severity_counts.items()))
        if rk.severity_counts
        else "_No open findings._\n"
    )
    w(f"\nSecurity posture: **{rk.security_posture}**. {rk.security_summary}\n")
    if rk.top_risks:
        w("\n**Top risks**\n\n" + bullets(rk.top_risks))
    if rk.unassessed_areas:
        w("\n**Not assessed**\n\n" + bullets(rk.unassessed_areas))

    # 4
    t = r.target
    w("\n## 4. Target overview\n")
    w(
        table(
            ["Property", "Value"],
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
            ],
        )
    )
    if t.description:
        w(f"\n{t.description}\n")
    if t.tools:
        w(
            "\n**Tools**\n\n"
            + table(
                ["Tool", "Side effects", "Confirmation", "Source"],
                (
                    (
                        x["name"],
                        x["side_effects"],
                        {None: "unknown", True: "required", False: "not required"}[x["requires_confirmation"]],
                        x["source"],
                    )
                    for x in t.tools
                ),
            )
        )
    if t.limitations:
        w("\n**Declared or discovered limitations**\n\n" + bullets(t.limitations))

    # 5
    w("\n## 5. Target architecture\n")
    if r.architecture.mermaid:
        w(_fence(r.architecture.mermaid, "mermaid"))
    w(f"_{r.architecture.note}_\n")

    # 6, 7
    w("\n## 6. Agent type classification\n")
    w(
        table(
            ["Type", "Confidence", "Evidence"],
            ((c.type, f"{c.confidence:.2f}", "; ".join(c.evidence)) for c in r.classification),
        )
    )
    w("\n## 7. Capability matrix\n")
    w(
        table(
            ["Capability", "Detected", "Testable", "Reason"],
            ((c.capability, "yes" if c.detected else "no", c.testable, c.reason) for c in r.capabilities),
        )
    )

    # 8
    e = r.environment
    w("\n## 8. Environment\n")
    w(
        table(
            ["Component", "Status", "Note"],
            [
                ("Sandbox (Docker)", "available" if e.docker else "unavailable", e.docker_note),
                ("Browser (Playwright)", "available" if e.browser else "unavailable", e.browser_note),
                ("LLM judge", "available" if e.judge else "unavailable", e.judge_note),
                (
                    "Interfaces tested",
                    ", ".join(e.interfaces) or "none",
                    "; ".join(f"{k}: {x}" for k, x in {**e.interface_errors, **e.unreachable}.items()),
                ),
                ("Parallelism", e.parallelism, ""),
                ("Canary seeding", "yes" if e.canary_seeding else "no", ""),
            ],
        )
    )
    w(
        "\n**Versions** (spec: reproducible reports)\n\n"
        + table(
            ["Item", "Value"],
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
            ],
        )
    )

    # 9
    me = r.methodology
    w("\n## 9. Test methodology\n")
    w(
        me.summary
        + "\n\n**Steps**\n\n"
        + bullets(me.steps)
        + "\n**Evaluation layers**\n\n"
        + bullets(me.evaluation_layers)
    )
    w("\n**Safety rules**\n\n" + bullets(me.safety_rules) + "\n**Scoring**\n\n" + bullets(me.scoring))
    w("\n**Severity and confidence**\n\n" + bullets(me.severity_model))
    if me.assumptions:
        w("\n**Assumptions made by the test designer**\n\n" + bullets(me.assumptions))

    # 10
    inv = r.inventory
    w("\n## 10. Test suite inventory\n")
    w(
        f"{inv.total_selected} tests selected of {inv.total_planned} planned; {inv.executed} executed. Plan `{inv.plan_hash}`.\n"
    )
    w(
        "\n**By skill**\n\n"
        + table(
            ["Skill", "Tests", "Passed", "Failed", "Blocked", "Other"],
            ((x.key, x.tests, x.passed, x.failed, x.blocked, x.other) for x in inv.by_skill),
        )
    )
    w(
        "\n**Coverage of the test taxonomy**\n\n"
        + table(
            ["Area", "Name", "Coverage", "Tests", "Ran", "Blocked", "Note"],
            ((x.key, x.name, x.status, x.tests, x.executed, x.blocked, x.note) for x in inv.coverage),
        )
    )
    if inv.security_coverage:
        w(
            "\n**Security categories**\n\n"
            + table(
                ["Code", "Name", "Coverage", "Tests", "Ran", "Blocked"],
                ((x.key, x.name, x.status, x.tests, x.executed, x.blocked) for x in inv.security_coverage),
            )
        )

    # 11
    w("\n## 11. Test results\n")
    w(
        table(
            ["Test", "Name", "Category", "Status", "Score", "Severity", "Latency", "Note"],
            (
                (
                    x.test_id,
                    x.name,
                    x.category,
                    x.status + (f" → {x.effective_status} (reviewed)" if x.effective_status else ""),
                    f"{x.score:.2f}",
                    x.severity or "",
                    ms(x.latency_ms) if x.latency_ms else "",
                    (
                        x.blocked_reason
                        or ("flaky " + pct(round((x.pass_rate or 0) * x.attempts), x.attempts) if x.flaky else "")
                    ),
                )
                for x in r.results
            ),
        )
    )

    # 12
    w("\n## 12. Failed tests\n")
    if not r.failed_tests:
        w("_No test failed._\n")
    for f in r.failed_tests:
        w(f"\n### {f.test_id}: {f.name}\n")
        w(
            f"- **Status:** {f.status}{f' · severity {f.severity}' if f.severity else ''}\n- **Objective:** {f.objective}\n- **Expected:** {f.expected}\n"
        )
        for i, turn in enumerate(f.inputs, 1):
            w(f"- **Input {i}:** {turn}\n")
        w("- **Why it failed:**\n" + "".join(f"  - {x}\n" for x in f.why_it_failed))
        last = f.attempts[-1] if f.attempts else None
        if last and last.outputs:
            w(f"- **Observed output (attempt {last.attempt}):**\n\n" + _fence(last.outputs[-1]))
        w("- **Reproduce:**\n" + _nested(f.reproduction))

    # 13
    s = r.security
    w("\n## 13. Security findings\n")
    w(f"Posture: **{s.posture}**. {s.summary}\n\n{s.rating_note}\n\n_{s.canary_note}_\n")
    if s.caveats:
        w("\n" + bullets(s.caveats))
    if s.categories:
        w(
            "\n"
            + table(
                ["Code", "Category", "Verdict", "Tests", "Passed", "Failed", "Blocked"],
                ((c.code, c.name, c.verdict, c.tests, c.passed, c.failed, c.blocked) for c in s.categories),
            )
        )
    if s.attacks_succeeded:
        w(
            "\n**Attacks that succeeded**\n\n"
            + table(
                ["Test", "Name", "Categories", "Severity", "Channels", "Evidence"],
                (
                    (
                        a["test_id"],
                        a["name"],
                        ", ".join(a["codes"]),
                        a["severity"],
                        ", ".join(a["channels"]),
                        a.get("snippet") or "",
                    )
                    for a in s.attacks_succeeded
                ),
            )
        )
    w("\n### Findings\n")
    w(_findings_md([f for f in r.findings if f.is_security]) or "_No security findings._\n")
    other = [f for f in r.findings if not f.is_security]
    if other:
        w("\n### Other findings\n")
        w(_findings_md(other))

    # 14-18 + others
    names = {"rag": 14, "tools": 15, "memory": 16, "browser": 17, "multi_agent": 18}
    for d in r.domains:
        n = names.get(d.key)
        w(f"\n## {n}. {d.title}\n" if n else f"\n### {d.title}\n")
        w(d.summary + "\n")
        if d.applicable and d.tests:
            w(f"\nScore: {_score(d.score)} · passed {d.passed} · failed {d.failed} · blocked {d.blocked}\n")
            subs = d.metrics.get("by_subcategory", {})
            if subs:
                w(
                    "\n"
                    + table(
                        ["Area", "Passed", "Failed", "Blocked"],
                        ((k, x["passed"], x["failed"], x["blocked"]) for k, x in subs.items()),
                    )
                )
            if d.failures:
                w("\n**Failures**\n\n" + bullets(d.failures))
            if d.blocked_tests:
                w("\n**Could not run**\n\n" + bullets(f"{b['test']}: {b['reason']}" for b in d.blocked_tests))

    # 19-21
    rel = r.reliability
    w("\n## 19. Reliability\n")
    w(
        f"Verdict: **{rel.verdict}** · {rel.measured_tests} test(s) repeated, {rel.single_run_tests} run once"
        f"{f' · consistency {rel.consistency:.0%}' if rel.consistency is not None else ''}.\n"
    )
    if rel.flaky:
        w(
            "\n**Flaky tests**\n\n"
            + table(
                ["Test", "Name", "Passes", "Pass rate"],
                ((x.test_id, x.name, f"{x.passes}/{x.repetitions}", f"{x.pass_rate:.0%}") for x in rel.flaky),
            )
        )
    if rel.deterministic_failures:
        w("\n**Failed every time:** " + ", ".join(rel.deterministic_failures) + "\n")
    w("\n" + bullets(rel.notes))
    pf = r.performance
    w("\n## 20. Performance\n")
    w(
        f"Measured {pf.measured} test(s): p50 {ms(pf.p50_ms)} · p95 {ms(pf.p95_ms)} · max {ms(pf.max_ms)}"
        f"{f' · budget {ms(pf.budget_ms)}' if pf.budget_ms else ''}.\n"
    )
    if pf.over_budget:
        w(f"\nOver budget: {', '.join(pf.over_budget)}\n")
    w("\n" + table(["Slowest test", "Name", "Latency"], ((x.test_id, x.name, ms(x.latency_ms)) for x in pf.slowest)))
    c = r.cost
    w("\n## 21. Cost\n")
    w(
        f"Tokens: {c.total_tokens:,} (target {c.target_tokens:,}) · cost {money(c.total_cost_usd, c.cost_known)}"
        f"{f' · judge {money(c.judge_cost_usd)}' if c.judge_cost_usd else ''}.\n"
    )
    w(
        "\n"
        + table(
            ["Category", "Tests", "Tokens", "Cost"],
            ((x.label, x.tests, f"{x.tokens:,}", money(x.cost_usd, c.cost_known)) for x in c.by_category),
        )
    )
    w("\n" + bullets(c.notes))

    # 22
    w("\n## 22. Regression comparison\n")
    if r.regression:
        w(_regression_md(r.regression))
    else:
        w("_No baseline was supplied. Run `agentlab compare RUN_A RUN_B` or `agentlab test --baseline RUN_ID`._\n")
    if len(r.trend.points) > 1:
        w(
            "\n**Trend across runs of this target**\n\n"
            + table(
                ["Run", "Started", "Score", "Failed", "Tests", "Findings", "Comparable"],
                (
                    (
                        p.run_id[:8],
                        f"{p.started_at:%Y-%m-%d %H:%M}" if p.started_at else "",
                        _score(p.overall),
                        p.failed,
                        p.tests,
                        p.findings,
                        "yes" if p.comparable else "different profile",
                    )
                    for p in r.trend.points
                ),
            )
        )
    w(f"\n_{r.trend.note}_\n")

    # 23
    ev = r.evidence
    w("\n## 23. Evidence\n")
    w(f"{plural(len(ev.items), 'artifact')} · {plural(len(ev.trace_ids), 'trace')}. {ev.redaction_note}\n")
    for b in ev.browser:
        w(
            f"\n**Browser: {b.test_id}** ({b.browser}) · trace `{b.trace or '-'}` · {plural(len(b.screenshots), 'screenshot')}\n\n"
        )
        w(
            table(
                ["#", "Action", "Target", "OK", "Detail"],
                ((a.index, a.action, a.target, "yes" if a.ok else "NO", a.detail) for a in b.actions),
            )
        )
    for repo in ev.repository:
        w(f"\n**Repository evidence: {repo.test_id}**\n\nFiles: {', '.join(repo.files) or '-'}\n")
        if repo.diff:
            w("\n" + _fence(repo.diff, "diff"))
        if repo.test_output:
            w("\n" + _fence(repo.test_output))
    w(
        "\n"
        + table(
            ["Artifact", "Kind", "Test", "Size", "SHA-256"],
            ((i.name, i.kind, i.test_key or "", i.size, i.sha256[:16]) for i in ev.items[:100]),
        )
    )
    if len(ev.items) > 100:
        w(f"\n_{len(ev.items) - 100} more artifacts are listed in report.json._\n")

    # 24, 25
    w("\n## 24. Recommendations\n")
    for rec in r.recommendations:
        w(
            f"\n{rec.priority}. **{rec.title}**"
            + (f" ({rec.severity})" if rec.severity else "")
            + f"\n   - Why: {rec.why}\n   - Action: {rec.action}\n"
        )
    if not r.recommendations:
        w("_Nothing to recommend._\n")
    w("\n## 25. Remediation priorities\n")
    w("Ordered by severity × confidence, security first; coverage and configuration items follow the fixes.\n\n")
    w(
        table(
            ["#", "Kind", "Item", "Severity", "Effort", "Findings / tests"],
            (
                (
                    x.priority,
                    x.kind,
                    x.title,
                    x.severity or "",
                    x.effort,
                    ", ".join(x.tests[:5]) or ", ".join(x.findings[:3]),
                )
                for x in r.recommendations
            ),
        )
    )

    # 26, 27
    ap = r.appendix
    w("\n## 26. Appendix\n")
    w(
        "**Skills used**\n\n"
        + table(
            ["Skill", "Version", "Trust", "Content hash"],
            ((x["name"], x["version"], x.get("trust", ""), str(x.get("content_hash", ""))[:12]) for x in ap.skills),
        )
    )
    w(
        "\n**Phases**\n\n"
        + table(
            ["#", "Phase", "Status", "Seconds"],
            ((p.get("index"), p.get("phase"), p.get("status"), p.get("duration_s")) for p in ap.phases),
        )
    )
    if ap.warnings:
        w("\n**Warnings**\n\n" + bullets(ap.warnings))
    w("\n**Limitations**\n\n" + bullets(ap.limitations))
    w("\n**Glossary**\n\n" + table(["Term", "Meaning"], ap.glossary.items()))
    w("\n## 27. Raw machine-readable results\n")
    w(
        "The complete data behind this report is in `report.json` (schema `agentlab.report`, version "
        f"{r.schema_version}); {ap.checksums_note}\n"
    )
    return neutralise_markup("\n".join(out).rstrip() + "\n")


def _findings_md(findings: Sequence[m.FindingView]) -> str:
    out: list[str] = []
    for f in findings:
        sev = (f.effective_severity or f.severity).upper()
        out.append(f"\n#### [{sev}] {f.title}  (`{f.test_id}`)\n")
        if f.status != "open":
            out.append(f"_Status: {f.status}_\n")
        out.append(
            f"- **Finding:** {f.title}\n- **Evidence:** test {f.test_id}"
            f"{', artifacts ' + ', '.join(e[:19] for e in f.evidence[:4]) if f.evidence else ''}\n"
            f"- **Expected:** {f.expected}\n- **Observed:** {f.observed}\n- **Impact:** {f.impact}\n"
            f"- **Severity:** {sev}{f' (machine: {f.severity.upper()})' if f.effective_severity else ''}\n"
            f"- **Confidence:** {f.confidence:.2f} · **Root cause:** {f.root_cause} ({f.root_cause_confidence:.2f})\n"
            f"- **Reproduction:**\n{_nested(f.reproduction)}- **Recommendation:** {f.recommendation}\n"
        )
        if f.facts:
            out.append("- **Observed facts:**\n" + "".join(f"  - {x}\n" for x in f.facts))
        if f.inferences:
            out.append("- **Inferences:**\n" + "".join(f"  - {x}\n" for x in f.inferences))
        if f.judgments:
            out.append("- **Judgments:**\n" + "".join(f"  - {x}\n" for x in f.judgments))
        for rv in f.reviews:
            out.append(f"- **Reviewed by {rv.reviewer}:** {rv.decision}{f' ({rv.reason})' if rv.reason else ''}\n")
    return "".join(out)


def _regression_md(reg: dict[str, object]) -> str:
    from agentlab.reporting.compare import comparison_markdown  # local import: compare builds on the report model

    return comparison_markdown(reg)
