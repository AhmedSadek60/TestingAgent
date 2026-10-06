import { useEffect, useState } from "react";
import { describeError } from "../api/errors";
import type { Report, ReportFile, ReportFormat } from "../api/types";
import { useAsync } from "../hooks/useAsync";
import { formatTime } from "../lib/format";
import { useSession } from "../session";
import { Checkbox, SelectFilter } from "./controls";
import { Icon } from "./Icon";
import { JsonView } from "./JsonView";
import { Async, Card, CopyButton, Empty, ErrorNote, Loading, Notice, saveBlob, Spinner, StateTabs, useToast } from "./ui";

const FORMAT_LABEL: Record<ReportFormat, string> = { json: "JSON", md: "Markdown", html: "HTML (interactive)", pdf: "PDF" };
const ALL_FORMATS: ReportFormat[] = ["html", "md", "json", "pdf"];

function size(bytes: number): string {
  return bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** The interactive HTML report, shown in a frame with no access to this page: it runs its own script but cannot read the
 * API token or call the API as the signed-in person. The link that opens it is short-lived and needs no token. */
function HtmlViewer({ report }: { report: Report }) {
  const { api } = useSession();
  const link = useAsync(() => api.viewLink(report.id), [api, report.id]);
  return (
    <Async state={link} loading="Preparing the report…">
      {(l) => (
        <div className="stack tight">
          <iframe className="frame" title={`Report version ${report.report_version}`} src={l.url} sandbox="allow-scripts" referrerPolicy="no-referrer" />
          <div className="row between">
            <span className="muted small">The report runs in an isolated frame and cannot reach your session. The link is valid for {Math.round(l.expires_in / 60)} minutes.</span>
            <button type="button" className="btn small" onClick={link.reload}>
              <Icon name="refresh" size={14} /> Reload
            </button>
          </div>
        </div>
      )}
    </Async>
  );
}

function TextViewer({ report, format }: { report: Report; format: "md" | "json" }) {
  const { api } = useSession();
  const text = useAsync(async () => (await api.reportFile(report.id, format)).blob.text(), [api, report.id, format]);
  return (
    <Async state={text} loading="Loading…">
      {(content) => {
        if (format === "json") {
          try {
            return <JsonView value={JSON.parse(content)} />;
          } catch {
            /* not valid JSON: show the text */
          }
        }
        return (
          <div className="stack tight">
            <div className="row">
              <CopyButton text={content} />
            </div>
            <pre className="codebox mono" style={{ maxHeight: 640 }}>
              {content}
            </pre>
          </div>
        );
      }}
    </Async>
  );
}

function Viewer({ report, format }: { report: Report; format: ReportFormat }) {
  if (format === "html") return <HtmlViewer report={report} />;
  if (format === "pdf") return <Notice tone="info">PDF reports open in your PDF viewer: download the file.</Notice>;
  return <TextViewer report={report} format={format} />;
}

function VersionCard({ report, onChange, selected, onSelect }: { report: Report; onChange: () => void; selected: ReportFormat | null; onSelect: (format: ReportFormat) => void }) {
  const { api } = useSession();
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const missing = ALL_FORMATS.filter((f) => !report.formats.some((x) => x.format === f));
  const redactions = Object.entries(report.redactions).reduce((n, [, v]) => n + v, 0);

  const download = async (file: ReportFile) => {
    setBusy(file.format);
    try {
      const { blob, name } = await api.reportFile(report.id, file.format);
      saveBlob(blob, name ?? `report-v${report.report_version}.${file.format}`);
    } catch (e) {
      toast.push("error", describeError(e));
    } finally {
      setBusy(null);
    }
  };
  const create = async (format: ReportFormat) => {
    setBusy(format);
    try {
      await api.exportReport(report.id, format);
      toast.push("good", `${FORMAT_LABEL[format]} report created.`);
      onChange();
    } catch (e) {
      toast.push("error", describeError(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card
      title={
        <span>
          Version {report.report_version} <span className="muted small">· {formatTime(report.created_at)}</span>
        </span>
      }
    >
      <div className="stack">
        <div className="row">
          {report.formats.map((f) => (
            <span key={f.format} className="row" style={{ gap: 4 }}>
              <button type="button" className={`btn small${selected === f.format ? " primary" : ""}`} aria-pressed={selected === f.format} onClick={() => onSelect(f.format)} disabled={f.format === "pdf"} title={f.format === "pdf" ? "Download to read it" : "View"}>
                <Icon name="eye" size={14} /> {FORMAT_LABEL[f.format]}
              </button>
              <button type="button" className="btn small ghost" onClick={() => void download(f)} disabled={busy === f.format} aria-label={`Download the ${FORMAT_LABEL[f.format]} report`}>
                <Icon name="download" size={14} /> {size(f.size)}
              </button>
            </span>
          ))}
        </div>
        {missing.length > 0 && (
          <div className="row">
            <span className="muted small">Also create:</span>
            {missing.map((f) => (
              <button key={f} type="button" className="btn small" disabled={busy === f} onClick={() => void create(f)}>
                {busy === f ? <Spinner label={`Creating ${f}`} /> : <Icon name="plus" size={14} />} {FORMAT_LABEL[f]}
              </button>
            ))}
          </div>
        )}
        <div className="muted small">
          {redactions > 0 ? `${redactions} secret(s) were masked in this report. ` : "No secret was found to mask. "}
          {report.baseline_run_id ? "Includes a comparison with an earlier run. " : ""}
          Each version is kept; a new report never replaces an old one.
        </div>
        {report.warnings.length > 0 && (
          <Notice tone="warn" title="Notes on this report">
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {report.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </Notice>
        )}
      </div>
    </Card>
  );
}

function Generate({ runId, onDone }: { runId: string; onDone: (report: Report) => void }) {
  const { api, project } = useSession();
  const toast = useToast();
  const [formats, setFormats] = useState<ReportFormat[]>(["html", "md", "json"]);
  const [sensitive, setSensitive] = useState(false);
  const [baseline, setBaseline] = useState("");
  const [busy, setBusy] = useState(false);
  const runs = useAsync((signal) => api.runs({ project, kind: "run", limit: 100 }, signal), [api, project]);
  const others = (runs.data ?? []).filter((r) => r.id !== runId && r.status === "completed");

  const submit = async () => {
    setBusy(true);
    try {
      const report = await api.generateReport(runId, { formats, include_sensitive: sensitive, baseline_run_id: baseline || null });
      toast.push("good", `Report version ${report.report_version} created.`);
      onDone(report);
    } catch (e) {
      toast.push("error", describeError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Generate a report">
      <div className="stack">
        <fieldset className="row" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="small muted">Formats</legend>
          {ALL_FORMATS.map((f) => (
            <Checkbox key={f} checked={formats.includes(f)} onChange={(v) => setFormats(v ? [...formats, f] : formats.filter((x) => x !== f))}>
              {FORMAT_LABEL[f]}
            </Checkbox>
          ))}
        </fieldset>
        <div className="row">
          <SelectFilter label="Compare with" value={baseline} onChange={setBaseline} allLabel="No comparison" options={others.map((r) => ({ value: r.id, label: `${r.suite} · ${formatTime(r.started_at ?? r.created_at)}${r.overall !== null ? ` · ${Math.round(r.overall)}` : ""}` }))} />
        </div>
        <Checkbox checked={sensitive} onChange={setSensitive}>
          Include restricted evidence (screenshots taken while signed in). Do not share such a report.
        </Checkbox>
        <div>
          <button type="button" className="btn primary" disabled={busy || formats.length === 0} onClick={submit}>
            <Icon name="file" size={16} /> {busy ? "Generating…" : "Generate report"}
          </button>
        </div>
      </div>
    </Card>
  );
}

/** Everything about the reports of one run: versions, formats, viewing, downloading and generating more. */
export function ReportsPanel({ runId, active }: { runId: string; active: boolean }) {
  const { api } = useSession();
  const reports = useAsync((signal) => api.reports(runId, signal), [api, runId], { pollMs: 3000, until: (list) => !active || list.length > 0 });
  const [view, setView] = useState<{ id: string; format: ReportFormat } | null>(null);
  const list = reports.data ?? [];
  const sorted = [...list].sort((a, b) => b.report_version - a.report_version);
  useEffect(() => {
    if (view === null && sorted.length > 0) {
      const first = sorted[0];
      const fmt = (["html", "md", "json"] as ReportFormat[]).find((f) => first.formats.some((x) => x.format === f));
      if (fmt) setView({ id: first.id, format: fmt });
    }
  }, [sorted, view]);
  const shown = view ? sorted.find((r) => r.id === view.id) : undefined;

  if (reports.error && !reports.data) return <ErrorNote error={reports.error} onRetry={reports.reload} />;
  if (!reports.data) return <Loading label="Loading reports…" />;
  return (
    <div className="stack" style={{ gap: 16 }}>
      {sorted.length === 0 ? (
        <Card>
          <Empty title={active ? "The report is written when the run ends" : "No report yet"}>{!active && "Generate one below."}</Empty>
        </Card>
      ) : (
        <>
          {shown && view && (
            <Card
              title={`Version ${shown.report_version}: ${FORMAT_LABEL[view.format]}`}
              actions={
                <StateTabs
                  label="Format"
                  value={view.format}
                  onChange={(f) => setView({ id: shown.id, format: f })}
                  items={shown.formats.filter((f) => f.format !== "pdf").map((f) => ({ id: f.format, label: FORMAT_LABEL[f.format] }))}
                />
              }
            >
              <Viewer report={shown} format={view.format} />
            </Card>
          )}
          <div className="stack">
            {sorted.map((r) => (
              <VersionCard key={r.id} report={r} onChange={reports.reload} selected={view?.id === r.id ? view.format : null} onSelect={(format) => setView({ id: r.id, format })} />
            ))}
          </div>
        </>
      )}
      <Generate runId={runId} onDone={reports.reload} />
      <p className="muted small" style={{ margin: 0 }}>
        Reports separate what was observed from what is inferred, judged and recommended, and show the scorecard, security findings, evidence and the exact versions used.
      </p>
    </div>
  );
}
