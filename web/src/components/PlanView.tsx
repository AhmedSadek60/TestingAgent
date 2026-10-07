import { useMemo, useState } from "react";
import type { CoverageEntry, PlanOut, PlannedTest } from "../api/types";
import { formatCost, formatDuration, formatNumber, formatPercent, plural } from "../lib/format";
import { statusLabel } from "../lib/labels";
import { blockers, byCategory, distinct, filterPlan, isSelected, NO_PLAN_FILTERS, planTotals, testState, type Deselected, type PlanFilters, type TestState } from "../lib/plan";
import { SearchBox, SelectFilter } from "./controls";
import { DataTable, type Column } from "./DataTable";
import { JsonView, RedactedText } from "./JsonView";
import { Badge, Card, Drawer, Empty, KeyValue, Notice, SeverityBadge, Stat, Tag } from "./ui";

const RISK_TONE = { safe: "good", controlled: "warn", high_impact: "bad" } as const;

function Coverage({ title, entries }: { title: string; entries: CoverageEntry[] }) {
  if (entries.length === 0) return null;
  return (
    <Card title={title} padded={false}>
      <DataTable
        caption={title}
        rows={entries}
        rowKey={(e) => e.key}
        pageSize={40}
        columns={[
          { key: "key", header: "Area", render: (e) => <><strong>{e.key}</strong> <span className="muted">{e.name}</span></> },
          { key: "status", header: "Coverage", render: (e) => <Badge tone={e.status === "covered" ? "good" : e.status === "partial" ? "warn" : e.status === "not_covered" ? "bad" : "neutral"}>{statusLabel(e.status)}</Badge> },
          { key: "tests", header: "Tests", numeric: true, render: (e) => `${e.runnable} runnable${e.blocked ? `, ${e.blocked} blocked` : ""}` },
          { key: "note", header: "Why", render: (e) => <span className="small">{e.note || (e.skills.length ? e.skills.join(", ") : "")}</span> },
        ]}
      />
    </Card>
  );
}

function TestDetail({ item, deselected, onToggle }: { item: PlannedTest; deselected: Deselected; onToggle?: (ids: string[], on: boolean) => void }) {
  const t = item.test;
  const selected = isSelected(item, deselected);
  const inputs = t.turns.length > 0 ? t.turns.map((turn) => turn.input) : t.input ? [t.input] : [];
  return (
    <>
      <div className="row">
        <Badge tone={RISK_TONE[item.risk]}>{item.risk.replace("_", " ")}</Badge>
        <SeverityBadge severity={t.severity_on_failure} />
        <Badge tone={item.predicted === "blocked" ? "warn" : "good"}>{item.predicted === "blocked" ? "will be blocked" : "can run"}</Badge>
        <Tag>{t.category}</Tag>
        <span className="muted mono small">{t.id}</span>
      </div>
      {onToggle && (
        <label className="check">
          <input type="checkbox" checked={selected} disabled={!item.selected} onChange={(e) => onToggle([t.id], e.target.checked)} />
          <span>Include this test in the run{!item.selected && item.deselected_reason ? ` (left out by the planner: ${item.deselected_reason})` : ""}</span>
        </label>
      )}
      {item.predicted === "blocked" && (
        <Notice tone="info" title="Predicted: blocked, not failed">
          {item.blocked_reason ?? "A prerequisite is missing."} It is shown in the results as blocked and does not count against the agent.
        </Notice>
      )}
      <Card title="Why this test">
        <div className="stack">
          <div>
            <h4>Objective</h4>
            <p style={{ margin: "4px 0 0" }}>{t.objective}</p>
          </div>
          {t.rationale && (
            <div>
              <h4>Rationale</h4>
              <p style={{ margin: "4px 0 0" }}>{t.rationale}</p>
            </div>
          )}
          {item.reasons.length > 0 && (
            <div>
              <h4>Chosen because</h4>
              <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                {item.reasons.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </div>
          )}
          {item.evidence.length > 0 && (
            <div>
              <h4>Evidence from the target</h4>
              <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                {item.evidence.map((r, i) => (
                  <li key={i}>
                    <RedactedText text={r} />
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </Card>
      <Card title="What is sent and what is expected">
        <div className="stack">
          {inputs.length > 0 && (
            <div>
              <h4>Input{inputs.length > 1 ? "s (turns)" : ""}</h4>
              {inputs.map((text, i) => (
                <pre key={i} className="codebox mono" style={{ marginTop: 6 }}>
                  {text}
                </pre>
              ))}
              <p className="muted small" style={{ margin: "6px 0 0" }}>
                Shown as text. Security tests use harmless synthetic canaries: nothing here is a real secret or a real attack on a third party.
              </p>
            </div>
          )}
          {t.browser_steps.length > 0 && (
            <div>
              <h4>Browser steps</h4>
              <ol style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                {t.browser_steps.map((s, i) => (
                  <li key={i} className="mono small">
                    {s.action} {s.target ?? s.name ?? ""} {s.value ? `"${s.value}"` : ""}
                  </li>
                ))}
              </ol>
            </div>
          )}
          <KeyValue
            items={[
              ["Expected behaviour", t.expected_behavior || null],
              ["Expected output", t.expected_output],
              ["Expected tool calls", t.expected_tool_calls.length ? t.expected_tool_calls.map((c) => `${c.name} (${c.match})`).join(", ") : null],
              ["Must never", t.forbidden_behavior.length ? t.forbidden_behavior.join("; ") : null],
            ]}
          />
        </div>
      </Card>
      <Card title="How it is evaluated">
        <div className="stack">
          {t.assertions.length > 0 ? (
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {t.assertions.map((a, i) => (
                <li key={i}>
                  <span className="mono">{a.type}</span>
                  {a.description ? `: ${a.description}` : ""} {!a.required && <span className="muted small">(advisory)</span>}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">No deterministic check.</p>
          )}
          {t.judge.length > 0 && (
            <div>
              <h4>Judge criteria</h4>
              <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                {t.judge.map((j) => (
                  <li key={j.metric}>
                    <strong>{j.metric}</strong> (pass at {formatPercent(j.threshold)}): {j.rubric}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <KeyValue
            items={[
              ["Skill", `${item.skill} ${item.skill_version}`],
              ["Taxonomy", item.taxonomy.join(", ") || null],
              ["Security categories", item.security_categories.join(", ") || null],
              ["Needs", [...t.required_interfaces, ...t.required_credentials.map((c) => `credential ${c}`)].join(", ") || null],
              ["Gate", item.gate_reasons.join("; ") || null],
              ["Limits", `${formatDuration(t.timeout)} · ${t.max_steps} steps · $${t.max_cost} · ${formatNumber(t.max_tokens)} tokens${t.repetitions ? ` · ${t.repetitions} repetitions` : ""}`],
              ["Estimate", `${item.est_attempts} attempt(s), ${item.est_calls} call(s), ~${formatDuration(item.est_seconds)}, ~${formatNumber(item.est_tokens)} tokens`],
              ["Origin", item.origin],
            ]}
          />
        </div>
      </Card>
    </>
  );
}

const STATE_LABEL: Record<TestState, { tone: "good" | "warn" | "neutral"; text: string }> = {
  run: { tone: "good", text: "will run" },
  blocked: { tone: "warn", text: "blocked" },
  "left-out": { tone: "neutral", text: "left out" },
  "switched-off": { tone: "neutral", text: "switched off" },
};

function StateBadge({ item, state }: { item: PlannedTest; state: TestState }) {
  const { tone, text } = STATE_LABEL[state];
  const why = state === "blocked" ? item.blocked_reason : state === "left-out" ? item.deselected_reason : state === "switched-off" ? "You switched this test off." : null;
  return (
    <Badge tone={tone} title={why ?? undefined}>
      {text}
    </Badge>
  );
}

export function PlanView({ out, deselected, onToggle, extra }: { out: PlanOut; deselected: Deselected; onToggle?: (ids: string[], on: boolean) => void; extra?: React.ReactNode }) {
  const plan = out.plan;
  const [filters, setFilters] = useState<PlanFilters>(NO_PLAN_FILTERS);
  const [open, setOpen] = useState<string | null>(null);
  const totals = useMemo(() => (plan ? planTotals(plan, deselected) : null), [plan, deselected]);
  const shown = useMemo(() => (plan ? filterPlan(plan, filters, deselected) : []), [plan, filters, deselected]);
  if (!plan || !totals) {
    return <Notice tone="error" title="There is no plan">{out.error ? JSON.stringify(out.error) : "The plan could not be designed."}</Notice>;
  }
  const set = (key: keyof PlanFilters) => (value: string) => setFilters((f) => ({ ...f, [key]: value }));
  const detail = open ? plan.tests.find((t) => t.test.id === open) : undefined;
  const budget = plan.budget;
  const stop = blockers(plan);

  const columns: Column<PlannedTest>[] = [
    ...(onToggle
      ? [
          {
            key: "pick",
            header: (
              <input
                type="checkbox"
                aria-label="Include every test shown"
                checked={shown.length > 0 && shown.every((i) => isSelected(i, deselected))}
                onChange={(e) => onToggle(shown.filter((i) => i.selected).map((i) => i.test.id), e.target.checked)}
              />
            ),
            width: "36px",
            render: (item: PlannedTest) => (
              <input
                type="checkbox"
                aria-label={`Include ${item.test.name}`}
                checked={isSelected(item, deselected)}
                disabled={!item.selected}
                onClick={(e) => e.stopPropagation()}
                onChange={(e) => onToggle([item.test.id], e.target.checked)}
              />
            ),
          } satisfies Column<PlannedTest>,
        ]
      : []),
    {
      key: "test",
      header: "Test",
      render: (item) => (
        <>
          <strong>{item.test.name}</strong>
          <div className="muted small mono">{item.test.id}</div>
        </>
      ),
      sort: (a, b) => a.test.name.localeCompare(b.test.name),
    },
    { key: "category", header: "Category", render: (i) => <Tag>{i.test.category}</Tag>, sort: (a, b) => a.test.category.localeCompare(b.test.category) },
    { key: "skill", header: "Skill", render: (i) => <span className="small">{i.skill}</span> },
    { key: "risk", header: "Risk", render: (i) => <Badge tone={RISK_TONE[i.risk]}>{i.risk.replace("_", " ")}</Badge> },
    { key: "sev", header: "If it fails", render: (i) => <SeverityBadge severity={i.test.severity_on_failure} /> },
    {
      key: "state",
      header: "Will it run?",
      render: (i) => <StateBadge item={i} state={testState(i, deselected)} />,
      sort: (a, b) => testState(a, deselected).localeCompare(testState(b, deselected)),
    },
    { key: "est", header: "Est. time", numeric: true, render: (i) => formatDuration(i.est_seconds), sort: (a, b) => a.est_seconds - b.est_seconds },
  ];

  return (
    <div className="stack" style={{ gap: 16 }}>
      {stop.length > 0 && (
        <Notice tone="error" title="Things to settle before running">
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {stop.map((m, i) => (
              <li key={i}>{m}</li>
            ))}
          </ul>
        </Notice>
      )}
      {out.warnings.length > 0 && (
        <Notice tone="warn" title="Warnings">
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {out.warnings.map((m, i) => (
              <li key={i}>{m}</li>
            ))}
          </ul>
        </Notice>
      )}
      <Card title="The plan">
        <div className="stack">
          <p style={{ margin: 0 }}>{plan.summary}</p>
          <div className="grid" style={{ ["--min" as string]: "150px" }}>
            <Stat label="Tests selected" value={`${totals.selected} of ${plan.tests.length}`} />
            <Stat label="Can run" value={totals.runnable} tone="good" />
            <Stat label="Will be blocked" value={totals.blocked} tone={totals.blocked > 0 ? "warn" : undefined} hint="Missing prerequisites; shown as blocked, not failed" />
            <Stat label="Estimated time" value={formatDuration(budget.est_wall_seconds)} hint={`${formatDuration(budget.serial_seconds)} if run one at a time`} />
            <Stat label="Estimated cost" value={budget.est_cost_usd === null ? "n/a" : formatCost(budget.est_cost_usd)} hint={budget.cost_note || `${formatNumber(budget.est_tokens)} tokens`} />
            <Stat label="Calls to the target" value={formatNumber(totals.targetCalls)} hint={`${formatNumber(totals.judgeCalls)} judge calls`} />
          </div>
          {!budget.within_limits && (
            <Notice tone="warn" title="The estimate is above a limit">
              The run would stop when it reaches the limit. Raise the limit or choose fewer tests.
            </Notice>
          )}
          {budget.notes.length > 0 && (
            <ul className="muted small" style={{ margin: 0, paddingLeft: 18 }}>
              {budget.notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          )}
          <div className="muted small">
            {plan.suite} suite · {plan.intensity} intensity · wave {plan.wave} · plan <span className="mono">{plan.plan_hash.slice(0, 12)}</span>. The same inputs always give the same plan; the second wave may add tests for what the first one finds.
          </div>
        </div>
      </Card>
      {extra}

      <Card title={`Tests (${totals.selected} selected of ${plan.tests.length})`} padded={false}>
        <div className="table-tools">
          <SearchBox value={filters.q} onChange={set("q")} label="Search planned tests" placeholder="Search tests" />
          <SelectFilter label="Category" value={filters.category} onChange={set("category")} allLabel="All categories" options={distinct(plan.tests, (t) => t.test.category).map((value) => ({ value }))} />
          <SelectFilter label="Skill" value={filters.skill} onChange={set("skill")} allLabel="All skills" options={distinct(plan.tests, (t) => t.skill).map((value) => ({ value }))} />
          <SelectFilter label="Risk" value={filters.risk} onChange={set("risk")} allLabel="Any risk" options={["safe", "controlled", "high_impact"].map((value) => ({ value, label: value.replace("_", " ") }))} />
          <SelectFilter
            label="What happens"
            value={filters.state}
            onChange={set("state")}
            allLabel="Every test"
            options={[
              { value: "run", label: "Will run" },
              { value: "blocked", label: "Will be blocked" },
              { value: "left-out", label: "Left out by the planner" },
              ...(onToggle ? [{ value: "switched-off", label: "Switched off by you" }] : []),
            ]}
          />
        </div>
        <DataTable
          caption="Planned tests"
          columns={columns}
          rows={shown}
          rowKey={(i) => i.test.id}
          onRowClick={(i) => setOpen(i.test.id)}
          pageSize={100}
          empty={<Empty title="No test matches these filters" />}
        />
      </Card>

      <Card title={`Skills (${plan.skills.filter((s) => s.selected).length} used of ${plan.skills.length})`} padded={false}>
        <DataTable
          caption="Skills considered"
          rows={plan.skills}
          rowKey={(s) => s.skill}
          pageSize={50}
          columns={[
            { key: "skill", header: "Skill", render: (s) => <><strong>{s.skill}</strong> <span className="muted small">{s.version}</span></>, sort: (a, b) => a.skill.localeCompare(b.skill) },
            { key: "used", header: "Used?", render: (s) => (s.selected ? <Badge tone="good">used</Badge> : <Badge title={s.skipped_reason ?? undefined}>not used</Badge>), sort: (a, b) => Number(b.selected) - Number(a.selected) },
            { key: "tests", header: "Tests", numeric: true, render: (s) => (s.selected ? `${s.tests}${s.predicted_blocked ? ` (${s.predicted_blocked} blocked)` : ""}` : ""), sort: (a, b) => a.tests - b.tests },
            { key: "why", header: "Why", render: (s) => <span className="small">{s.selected ? s.reasons.slice(0, 2).join("; ") : (s.skipped_reason ?? "")}</span> },
          ]}
        />
      </Card>
      <Card title="Tests by category">
        <div className="grid" style={{ ["--min" as string]: "220px" }}>
          {byCategory(plan).map((c) => (
            <div key={c.category} className="row between">
              <span>{c.category}</span>
              <span className="muted">
                {plural(c.total, "test")}
                {c.blocked ? `, ${c.blocked} blocked` : ""}
              </span>
            </div>
          ))}
        </div>
      </Card>

      <Coverage title="Coverage of the test taxonomy" entries={plan.coverage} />
      <Coverage title="Coverage of the security categories" entries={plan.security_coverage} />

      {(plan.assumptions.length > 0 || plan.proposed_skills.length > 0) && (
        <Card title="Assumptions">
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {plan.assumptions.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
            {plan.proposed_skills.length > 0 && <li>Skills that were proposed but are not installed: {plan.proposed_skills.join(", ")}.</li>}
          </ul>
        </Card>
      )}
      <details>
        <summary className="small muted" style={{ cursor: "pointer" }}>
          The plan as the API returns it
        </summary>
        <JsonView value={{ id: plan.id, suite: plan.suite, inputs: plan.inputs, limits: plan.limits, skill_notes: plan.skill_notes }} />
      </details>
      {detail && (
        <Drawer title={detail.test.name} onClose={() => setOpen(null)}>
          <TestDetail item={detail} deselected={deselected} onToggle={onToggle} />
        </Drawer>
      )}
    </div>
  );
}
