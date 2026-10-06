import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { Finding } from "../api/types";
import { SEVERITIES } from "../api/types";
import { StackedBar } from "../components/charts";
import { Checkbox, SearchBox, SelectFilter, ChipFilter } from "../components/controls";
import { JsonView, RedactedText } from "../components/JsonView";
import { ReviewForm, ReviewList } from "../components/Review";
import { Async, Badge, Card, Empty, KeyValue, SeverityBadge } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { formatPercent } from "../lib/format";
import { rootCauseLabel, statusLabel } from "../lib/labels";
import { filterFindings, severityCounts, type FindingFilters } from "../lib/results";
import { isActive } from "../lib/runs";
import { useSession } from "../session";
import { useRun } from "./RunLayout";

const SEVERITY_COLOR: Record<string, string> = {
  critical: "var(--critical)",
  high: "var(--bad)",
  medium: "var(--warn)",
  low: "var(--info)",
  info: "var(--neutral)",
};

function Section({ title, kind, items }: { title: string; kind: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <h4>
        {title} <span className="tag">{kind}</span>
      </h4>
      <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
        {items.map((text, i) => (
          <li key={i}>
            <RedactedText text={text} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function FindingDetail({ runId, finding, reviews, onReviewed }: { runId: string; finding: Finding; reviews: ReturnType<typeof useReviews>; onReviewed: () => void }) {
  const mine = (reviews.data ?? []).filter((r) => r.subject_type === "finding" && (r.subject_id === finding.id || r.subject_label === finding.test_id));
  const wasChanged = finding.original !== null && Object.keys(finding.original).length > 0;
  return (
    <Card
      title={
        <span className="row">
          <SeverityBadge severity={finding.severity} /> {finding.title}
        </span>
      }
    >
      <div className="stack">
        <div className="row">
          {finding.is_security ? <Badge tone="bad">security</Badge> : <Badge>quality</Badge>}
          <Badge tone={finding.status === "false_positive" ? "neutral" : finding.status === "confirmed" ? "good" : "info"}>{statusLabel(finding.status)}</Badge>
          <span className="tag">{finding.category}</span>
          <span className="muted small">confidence {formatPercent(finding.confidence)}</span>
          <Link className="small" to={`/runs/${runId}/results?test=${encodeURIComponent(finding.test_id)}`}>
            Test {finding.test_id}
          </Link>
        </div>
        {wasChanged && (
          <p className="muted small" style={{ margin: 0 }}>
            A reviewer changed this finding. Originally: <span className="mono">{JSON.stringify(finding.original)}</span>
          </p>
        )}
        <div className="grid two">
          <div>
            <h4>Expected</h4>
            <p style={{ margin: "6px 0 0" }}>{finding.expected}</p>
          </div>
          <div>
            <h4>
              Observed <span className="tag">fact</span>
            </h4>
            <p style={{ margin: "6px 0 0" }}>
              <RedactedText text={finding.observed} />
            </p>
          </div>
        </div>
        <Section title="Facts the evidence shows" kind="observed" items={finding.facts} />
        <Section title="What this suggests" kind="inference" items={finding.inferences} />
        <Section title="Opinions" kind="judgment" items={finding.judgments} />
        <div>
          <h4>Impact</h4>
          <p style={{ margin: "6px 0 0" }}>{finding.impact}</p>
        </div>
        <div>
          <h4>
            Recommendation <span className="tag">recommendation</span>
          </h4>
          <p style={{ margin: "6px 0 0" }}>{finding.recommendation}</p>
        </div>
        <KeyValue
          items={[
            ["Likely root cause", `${rootCauseLabel(finding.root_cause)} (${formatPercent(finding.root_cause_confidence)} sure)`],
            ["How it is reproduced", finding.reproduction ? <RedactedText text={finding.reproduction} /> : null],
            ["Evidence", finding.evidence.length ? finding.evidence.map((e) => <div key={e} className="mono small"><RedactedText text={e} /></div>) : "none recorded"],
          ]}
        />
        {Object.keys(finding.severity_breakdown).length > 0 && (
          <details>
            <summary className="small muted" style={{ cursor: "pointer" }}>
              How the severity was decided
            </summary>
            <JsonView value={finding.severity_breakdown} />
          </details>
        )}
        <h3>Human review</h3>
        <ReviewList reviews={mine} />
        <ReviewForm key={`${finding.id}:${finding.status}`} runId={runId} subject="finding" subjectId={finding.id} status={finding.status} onDone={onReviewed} />
      </div>
    </Card>
  );
}

function useReviews(runId: string) {
  const { api } = useSession();
  return useAsync((signal) => api.reviews(runId, signal), [api, runId]);
}

/** Findings: what went wrong, how bad it is, and how sure the evaluation is, with facts kept apart from opinions. */
export function Findings() {
  const { run } = useRun();
  const { api } = useSession();
  const [params, setParams] = useSearchParams();
  const findings = useAsync((signal) => api.findings(run.id, {}, signal), [api, run.id], { pollMs: 3000, until: () => !isActive(run.status) });
  const reviews = useReviews(run.id);
  const filters: FindingFilters = {
    severity: params.get("severity") ?? "",
    category: params.get("category") ?? "",
    kind: (params.get("kind") as FindingFilters["kind"]) || "all",
    showRejected: params.get("rejected") === "1",
    q: params.get("q") ?? "",
  };
  const selectedId = params.get("finding");
  const set = (key: string, value: string) =>
    setParams(
      (old) => {
        const next = new URLSearchParams(old);
        if (value && value !== "all") next.set(key, value);
        else next.delete(key);
        return next;
      },
      { replace: true },
    );

  const all = findings.data ?? [];
  const shown = useMemo(() => filterFindings(all, filters), [all, filters.severity, filters.category, filters.kind, filters.showRejected, filters.q]); // eslint-disable-line react-hooks/exhaustive-deps
  const counts = severityCounts(all);
  const categories = [...new Set(all.map((f) => f.category))].sort();
  const selected = shown.find((f) => f.id === selectedId) ?? shown[0];

  return (
    <div className="stack" style={{ gap: 16 }}>
      <Async state={findings} loading="Loading findings…">
        {() => (
          <>
            <Card title="Severity distribution">
              <div className="stack">
                <StackedBar label="Findings by severity" parts={SEVERITIES.map((s) => ({ key: s, value: counts[s] ?? 0, color: SEVERITY_COLOR[s], title: s }))} />
                <ChipFilter
                  label="Filter by severity"
                  value={filters.severity}
                  onChange={(v) => set("severity", v)}
                  options={SEVERITIES.filter((s) => counts[s]).map((s) => ({ value: s, count: counts[s] }))}
                  total={all.filter((f) => f.status !== "false_positive").length}
                />
              </div>
            </Card>
            <Card padded={false}>
              <div className="table-tools">
                <SearchBox value={filters.q} onChange={(v) => set("q", v)} label="Search findings" placeholder="Search findings" />
                <SelectFilter label="Kind" value={filters.kind === "all" ? "" : filters.kind} onChange={(v) => set("kind", v)} allLabel="Security and quality" options={[{ value: "security", label: "Security only" }, { value: "quality", label: "Quality only" }]} />
                <SelectFilter label="Category" value={filters.category} onChange={(v) => set("category", v)} allLabel="All categories" options={categories.map((value) => ({ value }))} />
                <Checkbox checked={filters.showRejected} onChange={(v) => set("rejected", v ? "1" : "")}>
                  Show findings a reviewer rejected
                </Checkbox>
              </div>
              {shown.length === 0 ? (
                <Empty title={all.length === 0 ? (isActive(run.status) ? "No finding yet" : "No findings") : "No finding matches these filters"}>
                  {all.length === 0 && !isActive(run.status) && <p>This run produced no findings. That is a statement about the tests that ran, not a guarantee: see the scorecard for coverage.</p>}
                </Empty>
              ) : (
                <div className="split" style={{ padding: 16 }}>
                  <div className="card" style={{ overflow: "hidden" }} role="list" aria-label="Findings">
                    {shown.map((f) => (
                      <button key={f.id} type="button" role="listitem" className="list-item" aria-current={selected?.id === f.id} onClick={() => set("finding", f.id)}>
                        <SeverityBadge severity={f.severity} /> {f.is_security && <Badge tone="bad">security</Badge>}
                        <div style={{ marginTop: 4 }}>{f.title}</div>
                        <div className="muted small">{f.category}</div>
                      </button>
                    ))}
                  </div>
                  {selected && <FindingDetail runId={run.id} finding={selected} reviews={reviews} onReviewed={() => { findings.reload(); reviews.reload(); }} />}
                </div>
              )}
            </Card>
          </>
        )}
      </Async>
    </div>
  );
}

