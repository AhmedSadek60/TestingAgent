import { useId, useRef, useState } from "react";
import type { FeedbackOptions } from "../../api/types";
import type { UploadItem } from "../../hooks/useUploads";
import { formatBytes } from "../../lib/feedback";
import { Badge, ProgressBar } from "../ui";

const STATE_TEXT: Record<UploadItem["state"], string> = {
  waiting: "Waiting to be sent",
  uploading: "Uploading",
  done: "Uploaded",
  failed: "Failed",
};

function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot < 0 ? "" : name.slice(dot + 1).toLowerCase();
}

/** What can be said about a file before it is sent: the server decides, but these save a trip. */
export function fileProblem(file: File, options: FeedbackOptions | null, already: number): string | null {
  if (!options) return null;
  const extension = extensionOf(file.name);
  if (!options.attachment_extensions.includes(extension)) {
    return `${file.name}: files of this type cannot be attached (allowed: ${options.attachment_extensions.join(", ")}).`;
  }
  if (file.size > options.attachment_max_file_mb * 1024 * 1024) {
    return `${file.name}: it is larger than ${options.attachment_max_file_mb} MB.`;
  }
  if (already >= options.attachment_max_files) return `${file.name}: a submission can have at most ${options.attachment_max_files} files.`;
  return null;
}

/**
 * The files for a submission: choose them, see what each is and how it is going, take one out, try a failed one again.
 * Nothing is sent from here: the owner of the list sends them (a form after it saves; a submission that exists at once).
 */
export function AttachmentUploader({
  items,
  options,
  onAdd,
  onRemove,
  onRetry,
  onPreview,
  disabled = false,
}: {
  items: UploadItem[];
  options: FeedbackOptions | null;
  onAdd: (files: File[]) => void;
  onRemove: (key: string) => void;
  onRetry: (key: string) => void;
  /** Show a file that is already on the server, masked. Without it only what the browser knows is shown. */
  onPreview?: (item: UploadItem) => void;
  disabled?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const id = useId();
  const [problems, setProblems] = useState<string[]>([]);
  const choose = (list: FileList | null) => {
    if (!list) return;
    const accepted: File[] = [];
    const refused: string[] = [];
    for (const file of Array.from(list)) {
      const problem = fileProblem(file, options, items.length + accepted.length);
      if (problem) refused.push(problem);
      else accepted.push(file);
    }
    setProblems(refused);
    if (accepted.length) onAdd(accepted);
    if (input.current) input.current.value = "";
  };
  return (
    <div className="stack">
      <div>
        <label htmlFor={id} className="btn">
          Add files
        </label>
        <input
          id={id}
          ref={input}
          className="sr-only"
          type="file"
          multiple
          disabled={disabled}
          accept={options ? options.attachment_extensions.map((e) => `.${e}`).join(",") : undefined}
          onChange={(event) => choose(event.target.files)}
        />
        {options && (
          <p className="muted small" style={{ marginTop: 8 }}>
            Screenshots, logs and examples help most. Up to {options.attachment_max_files} files, each at most {options.attachment_max_file_mb} MB
            ({options.attachment_extensions.join(", ")}). Text files are checked for secrets, which are masked in the copy maintainers read.
          </p>
        )}
      </div>
      {problems.length > 0 && (
        <ul className="alert warn" role="alert" style={{ margin: 0, paddingLeft: 24 }}>
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      {items.length > 0 && (
        <ul className="stack tight" style={{ listStyle: "none", margin: 0, padding: 0 }} aria-label="Files to attach">
          {items.map((item) => (
            <li key={item.key} className="list-item">
              <div className="row between" style={{ gap: 12, flexWrap: "wrap" }}>
                <div>
                  <strong className="mono">{item.file.name}</strong>{" "}
                  <span className="muted small">
                    {item.attachment ? `${item.attachment.type}, ` : item.file.type ? `${item.file.type} (as your browser says), ` : ""}
                    {formatBytes(item.file.size)}
                  </span>
                </div>
                <div className="row">
                  <Badge tone={item.state === "done" ? "good" : item.state === "failed" ? "bad" : "neutral"}>{STATE_TEXT[item.state]}</Badge>
                  {item.state === "done" && onPreview && (
                    <button type="button" className="btn small" onClick={() => onPreview(item)}>
                      Preview
                    </button>
                  )}
                  {item.state === "failed" && (
                    <button type="button" className="btn small" onClick={() => onRetry(item.key)}>
                      Try again
                    </button>
                  )}
                  <button type="button" className="btn small" disabled={item.state === "uploading"} onClick={() => onRemove(item.key)}>
                    Remove<span className="sr-only"> {item.file.name}</span>
                  </button>
                </div>
              </div>
              {item.state === "uploading" && (
                <ProgressBar value={item.loaded} max={Math.max(1, item.file.size)} label={`Uploading ${item.file.name}`} />
              )}
              {item.error && (
                <p className="error-text" role="alert" style={{ margin: "6px 0 0" }}>
                  {item.error}
                </p>
              )}
              {item.attachment && item.attachment.secrets.status === "redacted" && (
                <p className="muted small" style={{ margin: "6px 0 0" }}>
                  Secrets were found in this file and masked in the copy maintainers read.
                </p>
              )}
              {item.attachment && item.attachment.secrets.status === "needs_review" && (
                <p className="small" style={{ margin: "6px 0 0" }}>
                  <Badge tone="warn">Needs review</Badge> This file may still hold a secret. Remove it if you do not want to share it.
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
