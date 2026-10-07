import { useSearchParams } from "react-router-dom";
import { ReportsPanel } from "../components/ReportsPanel";
import { Async, Card, Empty, Field, PageHeader, StatusBadge } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { formatTime } from "../lib/format";
import { isActive, runTitle } from "../lib/runs";
import { useSession } from "../session";
import { useRun } from "./RunLayout";

/** The reports of the run being looked at (a tab of the run). */
export function RunReports() {
  const { run } = useRun();
  return <ReportsPanel runId={run.id} active={isActive(run.status)} />;
}

/** The reports of any run in the project: choose the run, then read, download or generate its reports. */
export function Reports() {
  const { api, project } = useSession();
  const [params, setParams] = useSearchParams();
  const runs = useAsync((signal) => api.runs({ project, kind: "run", limit: 200 }, signal), [api, project]);
  const runId = params.get("run") ?? "";
  return (
    <>
      <PageHeader title="Reports" subtitle="Every report is versioned and kept. Open one to read it, download it or create it in another format." />
      <Async state={runs}>
        {(list) => {
          const done = [...list].filter((r) => !isActive(r.status)).sort((a, b) => b.created_at.localeCompare(a.created_at));
          const chosen = done.find((r) => r.id === runId) ?? done[0];
          if (!chosen) return <Card><Empty title="No finished run yet">A report is written when a run ends.</Empty></Card>;
          return (
            <div className="stack" style={{ gap: 16 }}>
              <Card>
                <div className="row">
                  <Field label="Run" htmlFor="report-run" className="grow">
                    <select id="report-run" className="select" value={chosen.id} onChange={(e) => setParams({ run: e.target.value }, { replace: true })}>
                      {done.map((r) => (
                        <option key={r.id} value={r.id}>
                          {runTitle(r)} · {formatTime(r.started_at ?? r.created_at)}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <StatusBadge status={chosen.status} />
                </div>
              </Card>
              <ReportsPanel key={chosen.id} runId={chosen.id} active={false} />
            </div>
          );
        }}
      </Async>
    </>
  );
}
