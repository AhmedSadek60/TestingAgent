import { useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { EnvironmentCheck, RunSummary } from "../api/types";
import { Sparkline } from "../components/charts";
import { DataTable, type Column } from "../components/DataTable";
import { Icon } from "../components/Icon";
import { Async, Badge, Card, Empty, GradeBadge, PageHeader, Stat, StatusBadge } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { formatDuration, formatScore, relativeTime } from "../lib/format";
import { splitGrade } from "../lib/labels";
import { isActive, runPath, runSeconds, shortId } from "../lib/runs";
import { useSession } from "../session";

export const runColumns: Column<RunSummary>[] = [
  {
    key: "target",
    header: "Target",
    render: (r) => (
      <>
        <strong>{r.target ?? "Unnamed target"}</strong>
        <div className="muted small mono">{shortId(r.id)}</div>
      </>
    ),
    sort: (a, b) => (a.target ?? "").localeCompare(b.target ?? ""),
  },
  { key: "suite", header: "Suite", render: (r) => (r.kind === "plan" ? <span className="tag">plan</span> : r.suite), sort: (a, b) => a.suite.localeCompare(b.suite) },
  { key: "status", header: "Status", render: (r) => <StatusBadge status={r.status} />, sort: (a, b) => a.status.localeCompare(b.status) },
  {
    key: "score",
    header: "Score",
    numeric: true,
    render: (r) => (r.overall === null ? <span className="muted">n/a</span> : <><strong>{formatScore(r.overall)}</strong> <GradeBadge grade={r.grade} /></>),
    sort: (a, b) => (a.overall ?? -1) - (b.overall ?? -1),
  },
  {
    key: "started",
    header: "Started",
    render: (r) => <span title={r.created_at}>{relativeTime(r.started_at ?? r.created_at)}</span>,
    sort: (a, b) => (a.started_at ?? a.created_at).localeCompare(b.started_at ?? b.created_at),
  },
  { key: "took", header: "Took", numeric: true, render: (r) => formatDuration(r.started_at ? runSeconds(r) : null) },
];

function Readiness({ checks }: { checks: EnvironmentCheck[] }) {
  const attention = checks.filter((c) => c.level === "warn" || c.level === "fail");
  if (attention.length === 0) return <p className="muted">Everything AgentLab needs on this server is available.</p>;
  return (
    <ul className="stack tight" style={{ margin: 0, paddingLeft: 0, listStyle: "none" }}>
      {attention.map((c) => (
        <li key={c.name}>
          <Badge tone={c.level === "fail" ? "bad" : "warn"}>{c.level}</Badge> <strong>{c.name}</strong>
          <div className="muted small">{c.detail}</div>
          {c.fix && <div className="small">{c.fix}</div>}
        </li>
      ))}
    </ul>
  );
}

export function Dashboard() {
  const { api, project } = useSession();
  const navigate = useNavigate();
  const runs = useAsync((signal) => api.runs({ project, limit: 100 }, signal), [api, project], {
    pollMs: 4000,
    until: (data) => !data.some((r) => isActive(r.status)),
  });
  const targets = useAsync((signal) => api.targets(project, signal), [api, project]);
  const env = useAsync((signal) => api.environment(false, signal), [api]);

  const stats = useMemo(() => {
    const all = (runs.data ?? []).filter((r) => r.kind === "run");
    const finished = all.filter((r) => r.status === "completed" && r.overall !== null);
    const latest = [...finished].sort((a, b) => (b.finished_at ?? b.created_at).localeCompare(a.finished_at ?? a.created_at))[0];
    const history = new Map<string, number[]>();
    for (const r of [...finished].sort((a, b) => (a.finished_at ?? a.created_at).localeCompare(b.finished_at ?? b.created_at))) {
      const key = r.target ?? "Unnamed target";
      history.set(key, [...(history.get(key) ?? []), r.overall as number].slice(-12));
    }
    return {
      total: all.length,
      active: all.filter((r) => isActive(r.status)).length,
      problems: all.filter((r) => r.status === "failed" || r.status.startsWith("stopped")).length,
      latest,
      history: [...history.entries()],
    };
  }, [runs.data]);

  return (
    <>
      <PageHeader
        title="Dashboard"
        subtitle={`Project ${project}: what has been evaluated, what is running, and how this server is set up.`}
        actions={
          <Link to="/new" className="btn primary">
            <Icon name="plus" /> New evaluation
          </Link>
        }
      />
      <div className="stack" style={{ gap: 16 }}>
        <div className="grid" style={{ ["--min" as string]: "180px" }}>
          <Stat label="Runs in this project" value={runs.data ? stats.total : "…"} hint={stats.problems > 0 ? `${stats.problems} ended with a problem` : undefined} />
          <Stat label="Running now" value={runs.data ? stats.active : "…"} tone={stats.active > 0 ? "info" : undefined} />
          <Stat label="Registered targets" value={targets.data ? targets.data.length : "…"} />
          <Stat
            label="Latest score"
            value={stats.latest ? formatScore(stats.latest.overall) : "n/a"}
            hint={stats.latest ? `${stats.latest.target ?? "target"} · grade ${splitGrade(stats.latest.grade).letter || "n/a"}` : "No scored run yet"}
          />
        </div>

        <Card title="Recent runs" padded={false} actions={<Link to="/runs">All runs</Link>}>
          <Async state={runs}>
            {(data) => (
              <DataTable
                caption="Recent runs"
                columns={runColumns}
                rows={[...data].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 10)}
                rowKey={(r) => r.id}
                onRowClick={(r) => navigate(runPath(r))}
                empty={
                  <Empty title="Nothing has been evaluated yet">
                    <p>
                      Start with <Link to="/new">a new evaluation</Link>. The built-in demonstration agent needs no network and no keys.
                    </p>
                  </Empty>
                }
              />
            )}
          </Async>
        </Card>

        <div className="grid two">
          <Card title="Score history by target">
            {stats.history.length === 0 ? (
              <p className="muted">Scores appear here once a run has completed.</p>
            ) : (
              <div className="stack">
                {stats.history.map(([name, values]) => (
                  <div key={name} className="row between">
                    <div>
                      <strong>{name}</strong>
                      <div className="muted small">
                        {values.length} run{values.length === 1 ? "" : "s"} · latest {formatScore(values[values.length - 1])}
                      </div>
                    </div>
                    <Sparkline values={values} label={`Scores of ${name}`} />
                  </div>
                ))}
              </div>
            )}
          </Card>
          <Card title="Server readiness" actions={<Link to="/settings">Details</Link>}>
            <Async state={env}>{(data) => <Readiness checks={data.checks} />}</Async>
          </Card>
        </div>
      </div>
    </>
  );
}
