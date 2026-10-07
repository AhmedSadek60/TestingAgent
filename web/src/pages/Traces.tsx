import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { Artifact, RunEvent, TraceSummary } from "../api/types";
import { describeError } from "../api/errors";
import { SearchBox, SelectFilter } from "../components/controls";
import { JsonView, RedactedText } from "../components/JsonView";
import { Icon } from "../components/Icon";
import { Async, Badge, Card, Empty, ErrorNote, Loading, saveBlob, useToast } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { formatNumber } from "../lib/format";
import { ALERT_TYPES, describeEvent } from "../lib/live";
import { isActive } from "../lib/runs";
import { useSession } from "../session";
import { useRun } from "./RunLayout";

function offset(first: string | undefined, at: string): string {
  if (!first) return "";
  const ms = new Date(at).getTime() - new Date(first).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "+0 ms";
  return ms < 1000 ? `+${Math.round(ms)} ms` : `+${(ms / 1000).toFixed(2)} s`;
}

function EventRow({ event, first }: { event: RunEvent; first: string | undefined }) {
  return (
    <details className="event">
      <summary>
        <time className="muted mono small">{offset(first, event.timestamp)}</time>
        <span className="mono" style={{ color: ALERT_TYPES.has(event.type) ? "var(--bad)" : undefined }}>
          {event.type}
        </span>
        <span>
          <RedactedText text={describeEvent(event)} />
        </span>
      </summary>
      <div className="detail">
        <JsonView value={event.payload} />
        <div className="muted small" style={{ marginTop: 6 }}>
          recorded {new Date(event.timestamp).toLocaleString()} · redaction: {event.redaction_status}
        </div>
      </div>
    </details>
  );
}

function TraceView({ runId, traceId }: { runId: string; traceId: string }) {
  const { api } = useSession();
  const trace = useAsync((signal) => api.trace(runId, traceId, signal), [api, runId, traceId]);
  const [type, setType] = useState("");
  const [q, setQ] = useState("");
  return (
    <Async state={trace} loading="Loading the trace…">
      {(t) => {
        const types = [...new Set(t.events.map((e) => e.type))].sort();
        const needle = q.trim().toLowerCase();
        const shown = t.events.filter((e) => (!type || e.type === type) && (!needle || `${e.type} ${describeEvent(e)}`.toLowerCase().includes(needle)));
        return (
          <Card
            title={
              <span>
                {t.test_id} <span className="muted">· attempt {t.attempt}</span>
              </span>
            }
            padded={false}
            actions={<span className="muted small">{formatNumber(t.events.length)} events</span>}
          >
            <div className="table-tools">
              <SearchBox value={q} onChange={setQ} label="Search the trace" placeholder="Search events" />
              <SelectFilter label="Event type" value={type} onChange={setType} allLabel="All event types" options={types.map((value) => ({ value }))} />
            </div>
            <div className="stack tight" style={{ padding: 12 }}>
              {shown.length === 0 ? <Empty title="No event matches" /> : shown.map((e) => <EventRow key={e.event_id} event={e} first={t.events[0]?.timestamp} />)}
            </div>
          </Card>
        );
      }}
    </Async>
  );
}

function ArtifactImage({ artifact }: { artifact: Artifact }) {
  const { api } = useSession();
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => () => (url ? URL.revokeObjectURL(url) : undefined), [url]);
  if (url) return <img src={url} alt={artifact.name ?? "Screenshot"} style={{ maxWidth: "100%", border: "1px solid var(--border)", borderRadius: 6 }} />;
  return (
    <div>
      <button
        type="button"
        className="btn small"
        onClick={async () => {
          try {
            const { blob } = await api.artifact(artifact.id);
            setUrl(URL.createObjectURL(blob));
          } catch (e) {
            setError(describeError(e));
          }
        }}
      >
        <Icon name="eye" size={14} /> Show
      </button>
      {error && <span className="small" style={{ color: "var(--bad)" }}> {error}</span>}
    </div>
  );
}

function Evidence({ runId }: { runId: string }) {
  const { api } = useSession();
  const toast = useToast();
  const items = useAsync((signal) => api.artifacts(runId, undefined, signal), [api, runId]);
  return (
    <Card title="Evidence files" padded={false}>
      <Async state={items} loading="Loading evidence…">
        {(list) =>
          list.length === 0 ? (
            <Empty title="No evidence files">Traces, screenshots and other evidence are stored here as the run produces them.</Empty>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <caption className="sr-only">Evidence files</caption>
                <thead>
                  <tr>
                    <th scope="col">File</th>
                    <th scope="col">Kind</th>
                    <th scope="col" className="num">
                      Size
                    </th>
                    <th scope="col">Test</th>
                    <th scope="col">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((a) => (
                    <tr key={a.id}>
                      <td>
                        {a.name ?? a.id.slice(0, 12)} {a.sensitivity === "restricted" && <Badge tone="warn" title="May contain what a signed-in user sees; kept out of reports unless you ask">restricted</Badge>}
                        {a.media_type.startsWith("image/") && <ArtifactImage artifact={a} />}
                      </td>
                      <td>{a.kind}</td>
                      <td className="num">{a.size < 1024 ? `${a.size} B` : `${(a.size / 1024).toFixed(1)} KB`}</td>
                      <td className="mono small">{a.test_id ?? ""}</td>
                      <td>
                        <button
                          type="button"
                          className="btn small"
                          onClick={async () => {
                            try {
                              const { blob, name } = await api.artifact(a.id);
                              saveBlob(blob, name ?? a.name ?? `${a.kind}-${a.id.slice(0, 8)}`);
                            } catch (e) {
                              toast.push("error", describeError(e));
                            }
                          }}
                        >
                          <Icon name="download" size={14} /> Download
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Async>
    </Card>
  );
}

/** The recorded evidence: every event of every attempt, in order, and the files kept with the run. */
export function Traces() {
  const { run } = useRun();
  const { api } = useSession();
  const [params, setParams] = useSearchParams();
  const traces = useAsync((signal) => api.traces(run.id, undefined, signal), [api, run.id], { pollMs: 4000, until: () => !isActive(run.status) });
  const [test, setTest] = useState("");
  const selectedId = params.get("trace");

  const list = traces.data ?? [];
  const tests = useMemo(() => [...new Set(list.map((t) => t.test_id))].sort(), [list]);
  const shown = list.filter((t) => !test || t.test_id === test);
  const selected: TraceSummary | undefined = list.find((t) => t.id === selectedId) ?? undefined;

  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="split">
        <Card title={`Traces (${list.length})`} padded={false}>
          <div className="table-tools">
            <SelectFilter label="Test" value={test} onChange={setTest} allLabel="All tests" options={tests.map((value) => ({ value }))} />
          </div>
          {traces.error && !traces.data ? (
            <div style={{ padding: 12 }}>
              <ErrorNote error={traces.error} onRetry={traces.reload} />
            </div>
          ) : traces.loading && !traces.data ? (
            <Loading />
          ) : shown.length === 0 ? (
            <Empty title="No trace yet">A trace is stored for every attempt of every test that ran.</Empty>
          ) : (
            <div role="list" aria-label="Traces" style={{ maxHeight: 560, overflowY: "auto" }}>
              {shown.map((t) => (
                <button key={t.id} type="button" role="listitem" className="list-item" aria-current={selected?.id === t.id} onClick={() => setParams({ trace: t.id }, { replace: true })}>
                  <strong className="mono small">{t.test_id}</strong>
                  <div className="muted small">
                    attempt {t.attempt} · {formatNumber(t.event_count)} events
                  </div>
                </button>
              ))}
            </div>
          )}
        </Card>
        {selected ? (
          <TraceView runId={run.id} traceId={selected.id} />
        ) : (
          <Card>
            <Empty title="Choose a trace">Each trace is the ordered record of one attempt: what was asked, what the agent did, which tools it called, and what the checks saw. Secrets are masked before they are stored.</Empty>
          </Card>
        )}
      </div>
      <Evidence runId={run.id} />
    </div>
  );
}
