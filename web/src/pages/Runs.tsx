import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { describeError } from "../api/errors";
import type { RunSummary } from "../api/types";
import { ChipFilter, SearchBox } from "../components/controls";
import { DataTable, type Column } from "../components/DataTable";
import { Icon } from "../components/Icon";
import { Async, Card, ConfirmDialog, Empty, PageHeader, useToast } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { isActive, runPath } from "../lib/runs";
import { useSession } from "../session";
import { runColumns } from "./Dashboard";

/** Every run and plan in the project, newest first, kept fresh while anything is running. */
export function Runs() {
  const { api, project } = useSession();
  const navigate = useNavigate();
  const toast = useToast();
  const runs = useAsync((signal) => api.runs({ project, limit: 500 }, signal), [api, project], {
    pollMs: 3000,
    until: (data) => !data.some((r) => isActive(r.status)),
  });
  const [status, setStatus] = useState("");
  const [kind, setKind] = useState<"" | "run" | "plan">("");
  const [q, setQ] = useState("");
  const [stopping, setStopping] = useState<RunSummary | null>(null);
  const [busy, setBusy] = useState(false);

  const all = useMemo(() => [...(runs.data ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at)), [runs.data]);
  const counts = useMemo(() => {
    const out: Record<string, number> = {};
    for (const r of all) out[r.status] = (out[r.status] ?? 0) + 1;
    return Object.entries(out).map(([value, count]) => ({ value, count }));
  }, [all]);
  const shown = all.filter(
    (r) =>
      (!status || r.status === status) &&
      (!kind || r.kind === kind) &&
      (!q.trim() || `${r.target ?? ""} ${r.id} ${r.suite}`.toLowerCase().includes(q.trim().toLowerCase())),
  );

  const columns: Column<RunSummary>[] = [
    ...runColumns,
    {
      key: "actions",
      header: <span className="sr-only">Actions</span>,
      render: (r) =>
        isActive(r.status) ? (
          <button
            type="button"
            className="btn small danger"
            onClick={(event) => {
              event.stopPropagation();
              setStopping(r);
            }}
          >
            <Icon name="stop" size={14} /> {r.status === "pending" ? "Withdraw" : "Cancel"}
          </button>
        ) : null,
    },
  ];

  const cancel = async () => {
    if (!stopping) return;
    setBusy(true);
    try {
      const out = await api.cancelRun(stopping.id, "cancelled from the run list");
      toast.push("info", out.message);
      setStopping(null);
      runs.reload();
    } catch (e) {
      toast.push("error", describeError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        title="Test runs"
        subtitle="Every run and test plan in this project. A running test run can be followed live or stopped safely."
        actions={
          <Link to="/new" className="btn primary">
            <Icon name="plus" /> New evaluation
          </Link>
        }
      />
      <Card padded={false}>
        <div className="table-tools">
          <SearchBox value={q} onChange={setQ} label="Search runs" placeholder="Search by target or id" />
          <select className="select" aria-label="Kind" value={kind} onChange={(e) => setKind(e.target.value as "" | "run" | "plan")}>
            <option value="">Runs and plans</option>
            <option value="run">Test runs</option>
            <option value="plan">Plans only</option>
          </select>
          <ChipFilter label="Filter by status" options={counts} value={status} onChange={setStatus} total={all.length} />
        </div>
        <Async state={runs}>
          {() => (
            <DataTable
              caption="Test runs"
              columns={columns}
              rows={shown}
              rowKey={(r) => r.id}
              onRowClick={(r) => navigate(runPath(r))}
              initialSort={{ key: "started", descending: true }}
              empty={<Empty title={all.length === 0 ? "No runs yet" : "No run matches these filters"}>{all.length === 0 && <Link to="/new">Start a new evaluation</Link>}</Empty>}
            />
          )}
        </Async>
      </Card>
      {stopping && (
        <ConfirmDialog
          title={stopping.status === "pending" ? "Withdraw this run?" : "Stop this run?"}
          confirmLabel={stopping.status === "pending" ? "Withdraw run" : "Stop run"}
          danger
          busy={busy}
          onConfirm={cancel}
          onClose={() => setStopping(null)}
        >
          <p>
            {stopping.status === "pending"
              ? "The run has not started. It is taken out of the queue and will not run."
              : "The run stops at the next safe point: tests already running finish their current step, the rest are recorded as skipped, and what has run is analysed and reported. Nothing is deleted."}
          </p>
        </ConfirmDialog>
      )}
    </>
  );
}
