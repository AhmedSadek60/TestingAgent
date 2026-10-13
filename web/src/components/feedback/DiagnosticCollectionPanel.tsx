import { useEffect, useMemo, useState } from "react";
import { describeError } from "../../api/errors";
import type { DiagnosticCollection, DiagnosticsRequest } from "../../api/types";
import { useAsync } from "../../hooks/useAsync";
import { browserCounts, browserSnapshot } from "../../lib/browserErrors";
import { CATEGORY_LABELS, WINDOW_LABELS, label } from "../../lib/feedback";
import { useSession } from "../../session";
import { Checkbox } from "../controls";
import { Field, Loading, Notice } from "../ui";
import { DiagnosticPreview } from "./DiagnosticPreview";

/** What the person has decided about diagnostics: which kinds, what was collected and kept, which items, and that they agree. */
export interface DiagnosticChoice {
  selected: string[];
  collection: DiagnosticCollection | null;
  include: string[];
  consent: boolean;
}

export const NO_DIAGNOSTICS: DiagnosticChoice = { selected: [], collection: null, include: [], consent: false };

/** Whether the choice can be sent as it is, and if not, what the person has still to do. */
export function diagnosticsReadiness(choice: DiagnosticChoice): { attach: boolean; problem: string | null } {
  if (choice.selected.length === 0) return { attach: false, problem: null };
  if (!choice.collection) {
    return { attach: false, problem: "You chose diagnostic information: collect it and review it, or untick it, before you send." };
  }
  if (choice.include.length === 0) return { attach: false, problem: null };
  if (!choice.consent) return { attach: false, problem: "Tick the box to agree to share the diagnostic information, or untick the items." };
  return { attach: true, problem: null };
}

/**
 * Choose what to attach from what AgentLab can really supply, look at what it collected, and agree. Nothing is ticked at the
 * start, nothing is collected until the person asks, and what is collected is shown masked before anything is shared.
 */
export function DiagnosticCollectionPanel({
  value,
  onChange,
  runId,
  testId,
  traceId,
  disabled = false,
}: {
  value: DiagnosticChoice;
  onChange: (value: DiagnosticChoice) => void;
  runId?: string;
  testId?: string;
  traceId?: string;
  disabled?: boolean;
}) {
  const { api } = useSession();
  const available = useAsync(
    (signal) => api.diagnosticsAvailable({ run_id: runId || undefined, test_id: testId || undefined, trace_id: traceId || undefined }, signal),
    [api, runId, testId, traceId],
  );
  const [window, setWindow] = useState("last_15_minutes");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const counts = browserCounts();
  const sources = available.data?.sources ?? [];
  const usable = useMemo(() => sources.filter((s) => s.available && !s.manual).map((s) => s.id), [sources]);
  const logSelected = value.selected.some((c) => c === "application_logs" || c === "backend_logs" || c === "errors");

  // a category that is no longer available (the run was removed from the form) cannot stay ticked
  useEffect(() => {
    if (!available.data) return;
    const still = value.selected.filter((c) => usable.includes(c));
    if (still.length !== value.selected.length) onChange({ ...value, selected: still, collection: null, include: [], consent: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [available.data]);

  const set = (selected: string[]) => onChange({ selected, collection: null, include: [], consent: false });
  const toggle = (id: string, on: boolean) => set(on ? [...value.selected, id] : value.selected.filter((c) => c !== id));

  const collect = async () => {
    setBusy(true);
    setError(null);
    try {
      const body: DiagnosticsRequest = {
        categories: value.selected as DiagnosticsRequest["categories"],
        window: window as DiagnosticsRequest["window"],
        run_id: runId || null,
        test_id: testId || null,
        trace_id: traceId || null,
        ...(window === "custom" ? { window_from: new Date(from).toISOString(), window_to: new Date(to).toISOString() } : {}),
        ...(value.selected.includes("browser") ? { browser: browserSnapshot() } : {}),
      };
      const collection = await api.collectDiagnostics(body);
      onChange({
        ...value,
        collection,
        include: collection.items.filter((i) => i.outcome === "collected").map((i) => i.id),
        consent: false,
      });
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  };

  if (available.loading && !available.data) return <Loading label="Finding out what can be collected…" />;
  if (available.error && !available.data) {
    return (
      <Notice tone="warn" title="Diagnostic information is not available right now">
        {available.error} You can still send your report without it.
      </Notice>
    );
  }
  const data = available.data!;
  const customReady = window !== "custom" || (from !== "" && to !== "" && Date.parse(to) > Date.parse(from));
  return (
    <div className="stack">
      <p className="muted">
        Diagnostic information can help the AgentLab development team reproduce and resolve your issue. Review the information selected
        for sharing before submitting. Nothing is collected until you ask, and you see it, masked, before it is shared.
      </p>
      <fieldset className="stack tight" style={{ border: 0, padding: 0, margin: 0 }} disabled={disabled || busy}>
        <legend className="sr-only">What to include</legend>
        {sources.map((source) => {
          const name = label(CATEGORY_LABELS, source.id);
          const unavailable = !source.available || source.manual;
          return (
            <div key={source.id} className="stack tight">
              <Checkbox
                checked={value.selected.includes(source.id)}
                disabled={unavailable}
                onChange={(on) => toggle(source.id, on)}
              >
                Include {name.charAt(0).toLowerCase() + name.slice(1)}
                {source.id === "browser" && source.available && (
                  <span className="muted small">
                    {" "}
                    ({counts.console} console message{counts.console === 1 ? "" : "s"} and {counts.network} failed request
                    {counts.network === 1 ? "" : "s"} seen in this tab)
                  </span>
                )}
              </Checkbox>
              <span className="muted small" style={{ marginLeft: 26 }}>
                {unavailable && source.reason ? source.reason : source.description}
              </span>
            </div>
          );
        })}
      </fieldset>
      <div className="row">
        <button
          type="button"
          className="btn small"
          disabled={disabled || busy || usable.length === 0}
          onClick={() => set(value.selected.length === usable.length ? [] : usable)}
        >
          {value.selected.length === usable.length && usable.length > 0 ? "Unselect all" : "Select all available diagnostics"}
        </button>
        <span className="muted small">You still review what was collected before it is shared.</span>
      </div>
      {logSelected && (
        <div className="form-grid">
          <Field label="How far back" htmlFor="diag-window" help="Only your own requests and system-level lines are collected, whatever the period.">
            <select id="diag-window" className="select" value={window} onChange={(e) => setWindow(e.target.value)} disabled={busy}>
              {data.windows.map((w) => (
                <option key={w} value={w}>
                  {label(WINDOW_LABELS, w)}
                </option>
              ))}
            </select>
          </Field>
          {window === "custom" && (
            <>
              <Field label="From" htmlFor="diag-from" help={`At most ${data.max_window_minutes} minutes.`}>
                <input id="diag-from" className="input" type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} />
              </Field>
              <Field label="To" htmlFor="diag-to" error={from && to && !customReady ? "The end must be after the start." : null}>
                <input id="diag-to" className="input" type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} />
              </Field>
            </>
          )}
        </div>
      )}
      <div className="row">
        <button
          type="button"
          className="btn"
          disabled={disabled || busy || value.selected.length === 0 || !customReady}
          onClick={() => void collect()}
        >
          {value.collection ? "Collect again" : "Collect and review"}
        </button>
        {busy && <span className="muted">Collecting…</span>}
      </div>
      {error && (
        <Notice tone="warn" title="It could not be collected">
          {error} You can still send your report without diagnostic information.
        </Notice>
      )}
      {value.collection && (
        <div className="stack">
          <h3 style={{ margin: 0, fontSize: 14 }}>What would be shared</h3>
          <DiagnosticPreview
            collection={value.collection}
            include={value.include}
            onToggle={(itemId, on) =>
              onChange({
                ...value,
                include: on ? [...value.include, itemId] : value.include.filter((i) => i !== itemId),
                consent: false,
              })
            }
          />
          {value.include.length > 0 ? (
            <div className="alert info">
              <div className="stack tight">
                <p style={{ margin: 0 }}>{data.consent_text}</p>
                <Checkbox checked={value.consent} onChange={(consent) => onChange({ ...value, consent })}>
                  I have reviewed the diagnostic information above and I agree to share it with the AgentLab maintainers.
                </Checkbox>
              </div>
            </div>
          ) : (
            <p className="muted">Nothing is ticked, so no diagnostic information will be attached.</p>
          )}
        </div>
      )}
    </div>
  );
}
