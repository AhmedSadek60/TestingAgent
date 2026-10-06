import { useRef, useState } from "react";
import { describeError } from "../../api/errors";
import type { Document } from "../../api/types";
import { Icon } from "../../components/Icon";
import { Notice } from "../../components/ui";
import { formatNumber } from "../../lib/format";
import type { Problems, WizardState } from "../../lib/wizard";
import { useSession } from "../../session";

export interface StepProps {
  state: WizardState;
  patch: (changes: Partial<WizardState>) => void;
  /** Problems with this step's answers; shown only after the person has tried to continue. */
  problems: Problems;
  showProblems: boolean;
}

export const UPLOAD_ACCEPT = ".pdf,.docx,.txt,.md,.csv,.json,.yaml,.yml,.html,.htm,.png,.jpg,.jpeg,.gif,.webp";
export const ARCHIVE_ACCEPT = ".zip,.tar,.gz,.tgz";

export function fileSize(bytes: number): string {
  return bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Uploads files to the server and reports what it learned about each. The files are treated as untrusted data. */
export function Uploader({
  accept,
  multiple,
  label,
  onUploaded,
}: {
  accept: string;
  multiple: boolean;
  label: string;
  onUploaded: (documents: Document[]) => void;
}) {
  const { api, project } = useSession();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const upload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setBusy(true);
    setError(null);
    const done: Document[] = [];
    try {
      for (const file of Array.from(files)) done.push(await api.uploadDocument(file, project));
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
      if (done.length > 0) onUploaded(done);
    }
  };

  return (
    <div className="stack tight">
      <input ref={input} type="file" accept={accept} multiple={multiple} className="sr-only" id={`upload-${label}`} onChange={(e) => void upload(e.target.files)} aria-label={label} />
      <div className="row">
        <button type="button" className="btn" disabled={busy} onClick={() => input.current?.click()}>
          <Icon name="upload" size={16} /> {busy ? "Uploading…" : label}
        </button>
        <span className="muted small">Up to the server's upload limit. Files are analysed and kept under a name made from their content.</span>
      </div>
      {error && <Notice tone="error">{error}</Notice>}
    </div>
  );
}

export function DocumentList({ documents, onRemove }: { documents: Document[]; onRemove: (ref: string) => void }) {
  if (documents.length === 0) return null;
  return (
    <ul className="stack tight" style={{ margin: 0, paddingLeft: 0, listStyle: "none" }} aria-label="Uploaded documents">
      {documents.map((d) => (
        <li key={d.ref} className="card" style={{ padding: 10 }}>
          <div className="row between">
            <span>
              <strong>{d.name}</strong> <span className="muted small">{fileSize(d.size)} · {d.media_type}{d.summary.pages ? ` · ${d.summary.pages} page(s)` : ""}{d.summary.knowledge_items ? ` · ${formatNumber(d.summary.knowledge_items)} knowledge items` : ""}</span>
            </span>
            <button type="button" className="btn small ghost" onClick={() => onRemove(d.ref)} aria-label={`Remove ${d.name}`}>
              <Icon name="x" size={14} /> Remove
            </button>
          </div>
          {d.summary.unsupported && <p className="small" style={{ color: "var(--warn)", margin: "4px 0 0" }}>{d.summary.unsupported}</p>}
          {d.summary.injection_indicators.length > 0 && (
            <p className="small" style={{ color: "var(--warn)", margin: "4px 0 0" }}>
              This file contains text that looks like instructions to an AI ({d.summary.injection_indicators.length}). It is treated as data: AgentLab shows it, never obeys it, and uses it to test whether the agent does.
            </p>
          )}
          {d.summary.hidden_content > 0 && <p className="small muted" style={{ margin: "4px 0 0" }}>{d.summary.hidden_content} hidden element(s) were found and are reported separately.</p>}
        </li>
      ))}
    </ul>
  );
}
