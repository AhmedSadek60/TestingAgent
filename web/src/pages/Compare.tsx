import { useSearchParams, Link } from "react-router-dom";
import type { Comparison, RunSummary, TestDelta } from "../api/types";
import { DataTable, type Column } from "../components/DataTable";
import { Async, Badge, Card, Empty, Field, GradeBadge, KeyValue, Notice, PageHeader, SeverityBadge, Stat, StatusBadge } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { formatCost, formatLatency, formatNumber, formatScore, formatTime } from "../lib/format";
import { statusLabel } from "../lib/labels";
import { isActive } from "../lib/runs";
import { useSession } from "../session";

const VERDICT_TONE = { regressed: "bad", improved: "good", mixed: "warn", unchanged: "neutral", inconclusive: "warn" } as const;
const KIND_TONE: Record<string, "good" | "bad" | "warn" | "neutral" | "info"> = {
  new_failure: "bad",
  resolved: "good",
  still_failing: "warn",
  unstable: "warn",
  lost_coverage: "bad",
  gained_coverage: "good",
  new_test_failed: "bad",
  new_test_passed: "good",
  new_test_not_run: "neutral",
  removed: "neutral",
  definition_changed: "info",
};

const rec = (value: unknown): Record<string, unknown> => (value && typeof value === "object" ? (value as Record<string, unknown>) : {});
const list = (value: unknown): string[] => (Array.isArray(value) ? value.map(String) : []);
const text = (value: unknown): string | null => (typeof value === "string" && value ? value : null);
const numberOrNull = (value: unknown): number | null => (typeof value === "number" ? value : null);

function runLabel(r: RunSummary): string {
  return `${r.target ?? "target"} · ${r.suite} · ${formatTime(r.started_at ?? r.created_at)}${r.overall !== null ? ` · score ${formatScore(r.overall)}` : ""}`;
}

function Delta({ value, unit = "", inverse = false }: { value: number | null; unit?: string; inverse?: boolean }) {
  if (value === null) return <span className="muted">n/a</span>;
  if (value === 0) return <span className="muted">no change</span>;
  const good = inverse ? value < 0 : value > 0;
  return (
    <span style={{ color: good ? "var(--good)" : "var(--bad)", fontWeight: 600 }}>
      {value > 0 ? "+" : ""}
      {formatScore(value)}
      {unit}
    </span>
  );
}

function Result({ comparison: c, runA, runB }: { comparison: Comparison; runA: string; runB: string }) {
  const score = rec(c.score);
  const security = rec(c.security);
  const reliability = rec(c.reliability);
  const changes = rec(security.category_verdict_changes);
  const tests: Column<TestDelta>[] = [
    { key: "kind", header: "Change", render: (t) => <Badge tone={KIND_TONE[t.kind] ?? "neutral"}>{statusLabel(t.kind)}</Badge>, sort: (a, b) => a.kind.localeCompare(b.kind) },
    { key: "test", header: "Test", render: (t) => <><strong>{t.name}</strong><div className="muted small mono">{t.test_id}</div></>, sort: (a, b) => a.name.localeCompare(b.name) },
    { key: "cat", header: "Category", render: (t) => <span className="tag">{t.category}</span> },
    { key: "a", header: "Before", render: (t) => (t.status_a ? <StatusBadge status={t.status_a} /> : <span className="muted">not in this run</span>) },
    { key: "b", header: "Now", render: (t) => (t.status_b ? <StatusBadge status={t.status_b} /> : <span className="muted">not in this run</span>) },
    { key: "sev", header: "Severity", render: (t) => (t.severity_b ?? t.severity_a ? <SeverityBadge severity={t.severity_b ?? t.severity_a} /> : null) },
    { key: "note", header: "What happened", render: (t) => <span className="small">{t.note}</span> },
  ];
  return (
    <div className="stack" style={{ gap: 16 }}>
      <Card>
        <div className="stack">
          <div className="row">
            <Badge tone={VERDICT_TONE[c.verdict]}>{c.verdict}</Badge>
            <strong>{c.summary}</strong>
          </div>
          <div className="muted small">
            Before: <Link to={`/runs/${runA}`}>{c.run_a.target} {c.run_a.target_version ?? ""}</Link> ({c.run_a.tests} tests, {c.run_a.executed} ran, {c.run_a.blocked} blocked) · Now:{" "}
            <Link to={`/runs/${runB}`}>{c.run_b.target} {c.run_b.target_version ?? ""}</Link> ({c.run_b.tests} tests, {c.run_b.executed} ran, {c.run_b.blocked} blocked)
          </div>
        </div>
      </Card>

      <Card title="Can these runs be compared?">
        <div className="stack">
          <div className="row">
            <Badge tone={c.compatibility.verdict === "comparable" ? "good" : c.compatibility.verdict === "not_comparable" ? "bad" : "warn"}>{statusLabel(c.compatibility.verdict)}</Badge>
            <span>{c.compatibility.summary}</span>
          </div>
          <div className="muted small">
            {c.compatibility.shared_tests} tests in both · {c.compatibility.only_a} only before · {c.compatibility.only_b} only now · {c.compatibility.changed_definitions} with a changed definition
          </div>
          {c.compatibility.differences.length > 0 && (
            <DataTable
              caption="Differences between the runs"
              rows={c.compatibility.differences}
              rowKey={(d) => d.field}
              columns={[
                { key: "f", header: "What differs", render: (d) => <span className="mono small">{d.field}</span> },
                { key: "i", header: "Effect", render: (d) => <Badge tone={d.impact === "blocks_comparison" ? "bad" : d.impact === "caveat" ? "warn" : "neutral"}>{statusLabel(d.impact)}</Badge> },
                { key: "n", header: "Note", render: (d) => <span className="small">{d.note}</span> },
              ]}
            />
          )}
          {c.compatibility.notes.length > 0 && (
            <ul className="muted small" style={{ margin: 0, paddingLeft: 18 }}>
              {c.compatibility.notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          )}
        </div>
      </Card>

      <div className="grid" style={{ ["--min" as string]: "170px" }}>
        <Stat label="Overall score" value={<>{formatScore(numberOrNull(score.overall_a))} → {formatScore(numberOrNull(score.overall_b))}</>} hint={score.overall_comparable === false ? "Scored with different profiles: not comparable" : <Delta value={numberOrNull(score.overall_delta)} />} />
        <Stat label="Grade" value={<><GradeBadge grade={text(score.grade_a)} /> → <GradeBadge grade={text(score.grade_b)} /></>} />
        {Object.entries(c.counts).map(([k, v]) => (
          <Stat key={k} label={statusLabel(k)} value={formatNumber(v)} tone={KIND_TONE[k] === "bad" ? "bad" : KIND_TONE[k] === "good" ? "good" : undefined} />
        ))}
      </div>
      {text(score.note) && <Notice tone="warn">{text(score.note)}</Notice>}

      <Card title="What changed, test by test" padded={false}>
        <DataTable caption="Test changes" columns={tests} rows={c.tests} rowKey={(t) => t.test_id} pageSize={100} empty={<Empty title="No test changed" />} />
      </Card>

      <Card title="By category" padded={false}>
        <DataTable
          caption="Category changes"
          rows={c.categories}
          rowKey={(d) => d.category}
          columns={[
            { key: "c", header: "Category", render: (d) => <strong>{d.label}</strong> },
            { key: "a", header: "Before", numeric: true, render: (d) => formatScore(d.score_a) },
            { key: "b", header: "Now", numeric: true, render: (d) => formatScore(d.score_b) },
            { key: "d", header: "Change", numeric: true, render: (d) => <Delta value={d.delta} /> },
            { key: "l", header: "Same tests only", numeric: true, render: (d) => (d.like_for_like_a === null || d.like_for_like_b === null ? <span className="muted">n/a</span> : `${formatScore(d.like_for_like_a)} → ${formatScore(d.like_for_like_b)} (${d.shared_ran} tests)`) },
            { key: "n", header: "Note", render: (d) => <span className="small muted">{d.note}</span> },
          ]}
          empty={<Empty title="No category to compare" />}
        />
      </Card>

      <div className="grid two">
        <Card title="Security">
          <KeyValue
            items={[
              ["Posture", `${text(security.posture_a) ?? "n/a"} → ${text(security.posture_b) ?? "n/a"} (${text(security.direction) ?? "same"})`],
              ["Attacks that now succeed", list(security.new_attacks_succeeded).join(", ") || "none"],
              ["Attacks that no longer succeed", list(security.attacks_no_longer_succeeding).join(", ") || "none"],
              ["Categories that changed", Object.entries(changes).map(([k, v]) => `${k}: ${list(v).join(" → ")}`).join("; ") || "none"],
            ]}
          />
        </Card>
        <Card title="Reliability">
          <KeyValue
            items={[
              ["Verdict", `${text(reliability.verdict_a) ?? "n/a"} → ${text(reliability.verdict_b) ?? "n/a"}`],
              ["Flaky tests", `${numberOrNull(reliability.flaky_a) ?? 0} → ${numberOrNull(reliability.flaky_b) ?? 0}`],
              ["Newly flaky", list(reliability.newly_flaky).join(", ") || "none"],
              ["No longer flaky", list(reliability.no_longer_flaky).join(", ") || "none"],
              ["Note", text(reliability.note)],
            ]}
          />
        </Card>
      </div>

      <div className="grid two">
        <Card title="Latency and cost" padded={false}>
          <DataTable
            caption="Latency and cost"
            rows={[...c.latency, ...c.cost]}
            rowKey={(m) => m.name}
            columns={[
              { key: "n", header: "Measure", render: (m) => <>{m.name}{m.note && <div className="muted small">{m.note}</div>}</> },
              { key: "a", header: "Before", numeric: true, render: (m) => fmtMetric(m.a, m.unit) },
              { key: "b", header: "Now", numeric: true, render: (m) => fmtMetric(m.b, m.unit) },
              { key: "d", header: "Change", numeric: true, render: (m) => (m.change_pct === null ? <span className="muted">n/a</span> : <Delta value={m.change_pct} unit="%" inverse />) },
            ]}
            empty={<Empty title="Nothing measured" />}
          />
        </Card>
        <Card title="Findings" padded={false}>
          <DataTable
            caption="Finding changes"
            rows={c.findings}
            rowKey={(f) => `${f.test_id}:${f.title}`}
            columns={[
              { key: "k", header: "Change", render: (f) => <Badge tone={f.kind === "new" || f.kind === "worse" ? "bad" : f.kind === "resolved" || f.kind === "better" ? "good" : "neutral"}>{f.kind}</Badge> },
              { key: "t", header: "Finding", render: (f) => <>{f.title}<div className="muted small mono">{f.test_id}</div></> },
              { key: "s", header: "Severity", render: (f) => (f.severity_a || f.severity_b ? <>{f.severity_a ?? "–"} → {f.severity_b ?? "–"}</> : null) },
            ]}
            empty={<Empty title="No finding changed" />}
          />
        </Card>
      </div>
    </div>
  );
}

function fmtMetric(value: number | null, unit: string): string {
  if (value === null) return "n/a";
  if (unit === "usd") return formatCost(value);
  if (unit === "ms" || unit === "milliseconds") return formatLatency(value);
  if (unit === "s" || unit === "seconds") return formatLatency(value * 1000);
  return `${formatNumber(Math.round(value))}${unit && unit !== "tokens" ? ` ${unit}` : ""}`;
}

/** Regression comparison: what got better, what got worse and whether the runs can honestly be compared. */
export function Compare() {
  const { api, project } = useSession();
  const [params, setParams] = useSearchParams();
  const runs = useAsync((signal) => api.runs({ project, kind: "run", limit: 200 }, signal), [api, project]);
  const a = params.get("a") ?? "";
  const b = params.get("b") ?? "";
  const result = useAsync((signal) => api.compare(a, b, signal), [api, a, b], { enabled: Boolean(a && b) });
  const set = (key: "a" | "b", value: string) =>
    setParams(
      (old) => {
        const next = new URLSearchParams(old);
        if (value) next.set(key, value);
        else next.delete(key);
        return next;
      },
      { replace: true },
    );
  return (
    <>
      <PageHeader title="Compare runs" subtitle="Regression comparison: replay of an earlier run, test by test, with an honest statement of whether the two runs can be compared at all." />
      <div className="stack" style={{ gap: 16 }}>
        <Async state={runs}>
          {(all) => {
            const done = all.filter((r) => !isActive(r.status)).sort((x, y) => y.created_at.localeCompare(x.created_at));
            return (
              <Card>
                <div className="form-grid">
                  <Field label="Before (the baseline)" htmlFor="cmp-a">
                    <select id="cmp-a" className="select" value={a} onChange={(e) => set("a", e.target.value)}>
                      <option value="">Choose a run…</option>
                      {done.map((r) => (
                        <option key={r.id} value={r.id}>
                          {runLabel(r)}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Now (the run to judge)" htmlFor="cmp-b">
                    <select id="cmp-b" className="select" value={b} onChange={(e) => set("b", e.target.value)}>
                      <option value="">Choose a run…</option>
                      {done.map((r) => (
                        <option key={r.id} value={r.id}>
                          {runLabel(r)}
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>
              </Card>
            );
          }}
        </Async>
        {a && b && a === b && <Notice tone="info">These are the same run: nothing can differ.</Notice>}
        {a && b ? (
          <Async state={result} loading="Comparing…">
            {(c) => <Result comparison={c} runA={a} runB={b} />}
          </Async>
        ) : (
          <Card>
            <Empty title="Choose two runs">Pick the earlier run as the baseline and the run you want to judge. To replay a run's tests on a newer version of the agent, open the run and choose “Run again as regression”.</Empty>
          </Card>
        )}
      </div>
    </>
  );
}
