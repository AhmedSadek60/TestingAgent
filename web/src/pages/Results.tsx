import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import type { TestResult } from "../api/types";
import { ChipFilter, SearchBox, SelectFilter } from "../components/controls";
import { DataTable, type Column } from "../components/DataTable";
import { ResultDetail } from "../components/ResultDetail";
import { Async, Badge, Card, Drawer, Empty, SeverityBadge, StatusBadge } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { formatLatency, formatPercent } from "../lib/format";
import { rootCauseLabel } from "../lib/labels";
import { distinctValues, filterResults, severityRank, STATUS_ORDER, statusCounts, type ResultFilters } from "../lib/results";
import { isActive } from "../lib/runs";
import { useSession } from "../session";
import { useRun } from "./RunLayout";

/** Every test result of the run, filterable, with the full story of one test in a drawer. */
export function Results() {
  const { run } = useRun();
  const { api } = useSession();
  const [params, setParams] = useSearchParams();
  const results = useAsync((signal) => api.results(run.id, { limit: 5000 }, signal), [api, run.id], {
    pollMs: 3000,
    until: () => !isActive(run.status),
  });
  const reviews = useAsync((signal) => api.reviews(run.id, signal), [api, run.id]);

  const filters: ResultFilters = { status: params.get("status") ?? "", category: params.get("category") ?? "", severity: params.get("severity") ?? "", q: params.get("q") ?? "" };
  const selectedId = params.get("test");
  const set = (key: string, value: string) =>
    setParams(
      (old) => {
        const next = new URLSearchParams(old);
        if (value) next.set(key, value);
        else next.delete(key);
        return next;
      },
      { replace: true },
    );

  const all = results.data ?? [];
  const shown = useMemo(() => filterResults(all, filters), [all, filters.status, filters.category, filters.severity, filters.q]); // eslint-disable-line react-hooks/exhaustive-deps
  const counts = useMemo(() => {
    const c = statusCounts(all);
    const known = STATUS_ORDER.filter((s) => c[s]).map((s) => ({ value: s, count: c[s] }));
    const rest = Object.keys(c).filter((s) => !(STATUS_ORDER as readonly string[]).includes(s)).map((s) => ({ value: s, count: c[s] }));
    return [...known, ...rest];
  }, [all]);
  const selected = selectedId ? all.find((r) => r.test_id === selectedId || r.id === selectedId) : undefined;

  const columns: Column<TestResult>[] = [
    {
      key: "test",
      header: "Test",
      render: (r) => (
        <>
          <strong>{r.test_name}</strong>
          <div className="muted small mono">{r.test_id}</div>
        </>
      ),
      sort: (a, b) => a.test_name.localeCompare(b.test_name),
    },
    { key: "category", header: "Category", render: (r) => <span className="tag">{r.category}</span>, sort: (a, b) => a.category.localeCompare(b.category) },
    {
      key: "status",
      header: "Status",
      render: (r) => (
        <>
          <StatusBadge status={r.status} />
          {r.review && <> <Badge tone="info">reviewed</Badge></>}
        </>
      ),
      sort: (a, b) => STATUS_ORDER.indexOf(a.status as (typeof STATUS_ORDER)[number]) - STATUS_ORDER.indexOf(b.status as (typeof STATUS_ORDER)[number]),
    },
    {
      key: "score",
      header: "Score",
      numeric: true,
      render: (r) => (r.status === "blocked" || r.status === "skipped" ? <span className="muted">n/a</span> : formatPercent(r.score)),
      sort: (a, b) => a.score - b.score,
    },
    { key: "severity", header: "Severity", render: (r) => (r.status === "passed" ? null : <SeverityBadge severity={r.severity} />), sort: (a, b) => severityRank(a.severity) - severityRank(b.severity) },
    { key: "cause", header: "Likely cause", render: (r) => (r.root_cause && r.status !== "passed" ? rootCauseLabel(r.root_cause) : null) },
    {
      key: "reliable",
      header: "Stability",
      render: (r) =>
        r.reliability && r.reliability.repetitions > 1 ? (
          <span title={`${r.reliability.passes} of ${r.reliability.repetitions} repetitions passed`}>
            {formatPercent(r.reliability.pass_rate)} {r.reliability.flaky && <Badge tone="warn">flaky</Badge>}
          </span>
        ) : null,
    },
    { key: "latency", header: "Latency", numeric: true, render: (r) => formatLatency(r.latency_ms), sort: (a, b) => a.latency_ms - b.latency_ms },
  ];

  return (
    <Card padded={false}>
      <div className="table-tools">
        <SearchBox value={filters.q} onChange={(v) => set("q", v)} label="Search results" placeholder="Search tests" />
        <SelectFilter label="Category" value={filters.category} onChange={(v) => set("category", v)} allLabel="All categories" options={distinctValues(all, (r) => r.category).map((value) => ({ value }))} />
        <SelectFilter label="Severity" value={filters.severity} onChange={(v) => set("severity", v)} allLabel="Any severity" options={["critical", "high", "medium", "low", "info"].map((value) => ({ value }))} />
        <ChipFilter label="Filter by status" options={counts} value={filters.status} onChange={(v) => set("status", v)} total={all.length} />
      </div>
      <Async state={results} loading="Loading results…">
        {() => (
          <DataTable
            caption="Test results"
            columns={columns}
            rows={shown}
            rowKey={(r) => r.id}
            onRowClick={(r) => set("test", r.test_id)}
            initialSort={{ key: "status" }}
            empty={<Empty title={all.length === 0 ? (isActive(run.status) ? "No result yet" : "This run has no results") : "No result matches these filters"} />}
          />
        )}
      </Async>
      {selected && (
        <Drawer title={selected.test_name} onClose={() => set("test", "")}>
          <ResultDetail
            runId={run.id}
            result={selected}
            reviews={reviews.data ?? []}
            onReviewed={() => {
              results.reload();
              reviews.reload();
            }}
          />
        </Drawer>
      )}
    </Card>
  );
}
