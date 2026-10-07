import { useState } from "react";
import { Link, Navigate, Outlet, useOutletContext, useParams } from "react-router-dom";
import { describeError } from "../api/errors";
import type { RunDetail } from "../api/types";
import { Icon } from "../components/Icon";
import { ConfirmDialog, ErrorNote, Field, Loading, NavTabs, Notice, PageHeader, ScoreRing, StatusBadge, useToast } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { useNow } from "../hooks/useNow";
import { formatDuration, formatTime } from "../lib/format";
import { splitGrade } from "../lib/labels";
import { errorMessage, isActive, runSeconds, shortId } from "../lib/runs";
import { useSession } from "../session";

export interface RunContext {
  run: RunDetail;
  reload: () => void;
  /** Ask the person to confirm stopping this run. */
  requestCancel: () => void;
}

export function useRun(): RunContext {
  return useOutletContext<RunContext>();
}

/** The frame around everything about one run: who and what it is, whether it is still going, and the tabs. */
export function RunLayout() {
  const { runId = "" } = useParams();
  const { api } = useSession();
  const toast = useToast();
  const state = useAsync((signal) => api.run(runId, signal), [api, runId], { pollMs: 1500, until: (d) => !isActive(d.status) });
  const run = state.data;
  const now = useNow(run ? isActive(run.status) : false);
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  if (!run) {
    return state.error ? <ErrorNote error={state.error} onRetry={state.reload} /> : <Loading label="Loading the run…" />;
  }
  if (run.kind === "plan") return <Navigate to={`/plans/${run.id}`} replace />;

  const active = isActive(run.status);
  const gradeNotes = splitGrade(run.grade).notes;
  const stop = async () => {
    setBusy(true);
    try {
      const out = await api.cancelRun(run.id, reason.trim() || undefined);
      toast.push("info", out.message);
      setAsking(false);
      setReason("");
      state.reload();
    } catch (e) {
      toast.push("error", describeError(e));
    } finally {
      setBusy(false);
    }
  };

  const base = `/runs/${run.id}`;
  const failure = errorMessage(run.error);
  const cancelReason = typeof (run.totals as Record<string, unknown>).cancel_reason === "string" ? String((run.totals as Record<string, unknown>).cancel_reason) : null;
  return (
    <>
      <PageHeader
        title={
          <span className="row">
            {run.target ?? "Test run"} <StatusBadge status={run.status} />
          </span>
        }
        subtitle={
          <>
            {run.suite} suite · run <span className="mono">{shortId(run.id)}</span> · started {formatTime(run.started_at ?? run.created_at)} · {active ? "running for" : "took"}{" "}
            {formatDuration(runSeconds(run, now))}
            {gradeNotes.length > 0 && <> · grade {splitGrade(run.grade).letter}: {gradeNotes.join("; ")}</>}
          </>
        }
        actions={
          <>
            {run.overall !== null && <ScoreRing score={run.overall} grade={run.grade} size={84} />}
            {active && (
              <button type="button" className="btn danger" onClick={() => setAsking(true)} disabled={run.status === "cancelling"}>
                <Icon name="stop" size={16} /> {run.status === "pending" ? "Withdraw" : run.status === "cancelling" ? "Stopping…" : "Stop run"}
              </button>
            )}
            {!active && run.target_id && (
              <Link className="btn" to={`/new?target=${encodeURIComponent(run.target_id)}&baseline=${encodeURIComponent(run.id)}`} title="Replay this run's tests and report what changed">
                <Icon name="compare" size={16} /> Run again as regression
              </Link>
            )}
          </>
        }
      />
      {failure && run.status === "failed" && (
        <div style={{ marginBottom: 16 }}>
          <Notice tone="error" title="The run failed">
            {failure}
          </Notice>
        </div>
      )}
      {run.status === "cancelled" && (
        <div style={{ marginBottom: 16 }}>
          <Notice tone="warn" title="This run was cancelled">
            {cancelReason ? `Reason: ${cancelReason}. ` : ""}What ran is kept and was analysed; tests that had not started are recorded as skipped. The report says the run is partial.
          </Notice>
        </div>
      )}
      {run.status.startsWith("stopped_due_to") && (
        <div style={{ marginBottom: 16 }}>
          <Notice tone="warn" title="The run stopped at a limit you set">
            Results so far are kept. Raise the limit and run again to cover the rest.
          </Notice>
        </div>
      )}
      <NavTabs
        label="Run"
        items={[
          { to: base, label: "Live", end: true },
          { to: `${base}/results`, label: "Results" },
          { to: `${base}/findings`, label: "Findings" },
          { to: `${base}/scorecard`, label: "Scorecard" },
          { to: `${base}/traces`, label: "Traces" },
          { to: `${base}/reports`, label: "Reports" },
          { to: `${base}/plan`, label: "Plan" },
        ]}
      />
      <Outlet context={{ run, reload: state.reload, requestCancel: () => setAsking(true) } satisfies RunContext} />
      {asking && (
        <ConfirmDialog
          title={run.status === "pending" ? "Withdraw this run?" : "Stop this run?"}
          confirmLabel={run.status === "pending" ? "Withdraw run" : "Stop run"}
          danger
          busy={busy}
          onConfirm={stop}
          onClose={() => setAsking(false)}
        >
          <p>
            {run.status === "pending"
              ? "The run has not started. It is taken out of the queue and will not run."
              : "The run stops at the next safe point. Tests that are running finish their current step, tests that have not started are recorded as skipped, and everything that ran is still analysed and reported. Nothing is deleted."}
          </p>
          <Field label="Reason (optional)" htmlFor="cancel-reason" help="Kept with the run so the next reader knows why it stopped.">
            <input id="cancel-reason" className="input" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={200} />
          </Field>
        </ConfirmDialog>
      )}
    </>
  );
}
