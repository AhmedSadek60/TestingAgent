"""Human-readable rendering of a plan (used by ``agentlab plan``, the report and the web UI)."""

from __future__ import annotations

from agentlab.design.models import CoverageEntry, TestPlan

STATUS_MARK = {"covered": "covered", "partial": "partly covered", "not_covered": "NOT covered", "not_applicable": "n/a"}


def _cell(text: object, width: int = 90) -> str:
    s = str(text).replace("|", "\\|").replace("\n", " ")
    return s if len(s) <= width else s[: width - 1] + "…"


def _status_text(e: CoverageEntry) -> str:
    if e.status == "not_covered" and e.tests:
        return "planned, all BLOCKED"  # the tests exist but cannot run here: nothing is verified
    return STATUS_MARK[e.status]


def _coverage_table(entries: list[CoverageEntry]) -> list[str]:
    rows = ["| Area | Status | Tests | Blocked | Note |", "|---|---|---|---|---|"]
    for e in entries:
        rows.append(f"| {e.key} {e.name} | {_status_text(e)} | {e.tests} | {e.blocked} | {_cell(e.note, 120)} |")
    return rows


def plan_markdown(plan: TestPlan, *, detail: bool = False) -> str:
    c = plan.counts()
    out = [
        f"# Test plan for {plan.target}",
        "",
        plan.summary,
        "",
        f"- plan `{plan.id}` · hash `{plan.plan_hash}` · suite `{plan.suite}` · intensity `{plan.intensity}` · wave {plan.wave}",
        f"- {c['tests']} tests selected: {c['runnable']} runnable, {c['blocked']} predicted BLOCKED "
        f"(blocked is not failed: those areas are reported as not tested)",
    ]
    if plan.inputs:
        out.append(
            f"- built from: interfaces {plan.inputs.get('interfaces') or 'none'}, judge "
            f"{'available' if plan.inputs.get('judge_available') else 'not configured'}, docker "
            f"{'available' if plan.inputs.get('docker_available') else 'unavailable'}, browser "
            f"{'available' if plan.inputs.get('browser_available') else 'unavailable'}, "
            f"{len(plan.inputs.get('tools') or [])} tool(s), {len(plan.inputs.get('documents') or [])} document(s)"
        )
    if plan.warnings:
        out += ["", "## Warnings", ""]
        out += [f"- **{w.level}** `{w.code}`: {w.message}" for w in plan.warnings]

    out += ["", "## Skills", ""]
    out += ["| Skill | Selected | Tests | Blocked | Why |", "|---|---|---|---|---|"]
    for m in plan.skills:
        why = "; ".join(m.reasons[:2]) if m.selected else (m.skipped_reason or "")
        out.append(
            f"| {m.skill} {m.version} | {'yes' if m.selected else 'no'} | {m.tests} | {m.predicted_blocked} | {_cell(why, 140)} |"
        )

    out += ["", "## Taxonomy coverage", ""] + _coverage_table(plan.coverage)
    if plan.suite in {"full", "security", "regression"}:
        out += ["", "## Security categories (N1-N28)", ""] + _coverage_table(plan.security_coverage)

    b = plan.budget
    out += [
        "",
        "## Estimated budget (planning estimate, not a measurement)",
        "",
        f"- {b.attempts} attempts, ~{b.target_calls} target calls, ~{b.judge_calls} judge calls, ~{b.est_tokens} tokens",
        f"- time ~{b.est_wall_seconds:.0f}s with parallelism (~{b.serial_seconds:.0f}s serial)",
        f"- cost: {f'${b.est_cost_usd:.4f}' if b.est_cost_usd is not None else 'unknown'} ({b.cost_note})",
        f"- within configured limits: {'yes' if b.within_limits else 'NO - ' + '; '.join(b.notes)}",
    ]

    out += [
        "",
        "## Tests",
        "",
        "| Id | Name | Skill | Areas | Risk | Severity | Predicted |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in plan.tests:
        if not p.selected:
            continue
        areas = ",".join(dict.fromkeys([*p.taxonomy, *p.security_categories]))
        state = "runnable" if p.predicted == "runnable" else "BLOCKED"
        out.append(
            f"| {p.id} | {_cell(p.test.name, 70)} | {p.skill} | {areas} | {p.risk.value} | "
            f"{p.test.severity_on_failure.value} | {state} |"
        )
    blocked = plan.block_summary()
    if blocked:
        out += ["", "## Predicted BLOCKED (not failures)", ""]
        out += [f"- {n} x {reason}" for reason, n in blocked.items()]
    off = [p for p in plan.tests if not p.selected]
    if off:
        out += ["", "## Deselected", ""]
        out += [f"- `{p.id}`: {p.deselected_reason}" for p in off[:60]]
        if len(off) > 60:
            out.append(f"- … and {len(off) - 60} more")
    if detail:
        out += ["", "## Why each test exists", ""]
        for p in plan.selected_tests():
            out.append(f"### {p.id} {p.test.name}")
            out.append(f"- objective: {p.test.objective}")
            out += [f"- why: {r}" for r in p.reasons[:3]]
            out += [f"- evidence: {e}" for e in p.evidence[:3]]
            out.append(f"- gate: {'; '.join(p.gate_reasons)}")
            if p.blocked_reason:
                out.append(f"- predicted BLOCKED: {p.blocked_reason}")
            out.append("")
    if plan.assumptions:
        out += ["", "## Assumptions", ""] + [f"- {a}" for a in plan.assumptions]
    return "\n".join(out) + "\n"
