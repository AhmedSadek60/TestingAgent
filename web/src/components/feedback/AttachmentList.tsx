import { useEffect, useState } from "react";
import { describeError } from "../../api/errors";
import type { Attachment, AttachmentPreview } from "../../api/types";
import { formatBytes } from "../../lib/feedback";
import { formatTime } from "../../lib/format";
import { useSession } from "../../session";
import { Badge, ConfirmDialog, Loading, Modal, Notice, saveBlob, useToast } from "../ui";

function scanText(a: Attachment): { tone: "good" | "warn" | "neutral" | "bad"; text: string } {
  switch (a.scan.status) {
    case "clean":
      return { tone: "good", text: "Scanned: clean" };
    case "infected":
      return { tone: "bad", text: "Scanned: flagged" };
    case "error":
      return { tone: "warn", text: "Scan failed" };
    default:
      return { tone: "neutral", text: "Not scanned" };
  }
}

/** A file as maintainers and its owner may read it: masked text, or the picture. Never run, never opened by the browser itself. */
export function AttachmentPreviewModal({ submissionId, attachment, onClose }: { submissionId: string; attachment: Attachment; onClose: () => void }) {
  const { api } = useSession();
  const [preview, setPreview] = useState<AttachmentPreview | null>(null);
  const [image, setImage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let url: string | null = null;
    let cancelled = false;
    (async () => {
      try {
        const found = await api.attachmentPreview(submissionId, attachment.id);
        if (cancelled) return;
        setPreview(found);
        if (found.kind === "image") {
          const file = await api.attachmentFile(submissionId, attachment.id);
          if (cancelled) return;
          url = URL.createObjectURL(new Blob([file.blob], { type: attachment.type }));
          setImage(url);
        }
      } catch (e) {
        if (!cancelled) setError(describeError(e));
      }
    })();
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [api, submissionId, attachment.id, attachment.type]);
  return (
    <Modal title={attachment.name} onClose={onClose} footer={<button type="button" className="btn" onClick={onClose}>Close</button>}>
      {error && <Notice tone="error">{error}</Notice>}
      {!preview && !error && <Loading />}
      {preview && (
        <div className="stack">
          <p className="muted">{preview.message}</p>
          {preview.kind === "text" && (
            <pre className="codebox" style={{ whiteSpace: "pre-wrap", maxHeight: 360, overflow: "auto" }}>
              {preview.text}
            </pre>
          )}
          {preview.kind === "image" && image && <img src={image} alt={`Preview of ${attachment.name}`} style={{ maxWidth: "100%" }} />}
          {preview.truncated && <p className="muted small">Only the beginning is shown.</p>}
        </div>
      )}
    </Modal>
  );
}

/** What is attached to a submission: each file with what it was found to be, whether it was scanned, and what may be done. */
export function AttachmentList({
  submissionId,
  attachments,
  canRemove,
  onChanged,
}: {
  submissionId: string;
  attachments: Attachment[];
  canRemove: (a: Attachment) => boolean;
  onChanged: () => void;
}) {
  const { api } = useSession();
  const toast = useToast();
  const [preview, setPreview] = useState<Attachment | null>(null);
  const [removing, setRemoving] = useState<Attachment | null>(null);
  const [busy, setBusy] = useState(false);
  if (attachments.length === 0) return <p className="muted">Nothing is attached.</p>;
  const download = async (a: Attachment, version: "original" | "sanitized") => {
    try {
      const file = await api.attachmentFile(submissionId, a.id, version);
      saveBlob(file.blob, file.name ?? a.name);
    } catch (e) {
      toast.push("error", describeError(e));
    }
  };
  const remove = async () => {
    if (!removing) return;
    setBusy(true);
    try {
      await api.deleteAttachment(submissionId, removing.id);
      setRemoving(null);
      onChanged();
    } catch (e) {
      toast.push("error", describeError(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <ul className="stack tight" style={{ listStyle: "none", margin: 0, padding: 0 }} aria-label="Attached files">
        {attachments.map((a) => {
          const scan = scanText(a);
          return (
            <li key={a.id} className="list-item">
              <div className="row between" style={{ gap: 12, flexWrap: "wrap" }}>
                <div>
                  <strong className="mono">{a.name}</strong>{" "}
                  <span className="muted small">
                    {a.type}, {formatBytes(a.size)}, added {formatTime(a.created_at)} by {a.uploader}
                  </span>
                  {a.visibility === "internal" && (
                    <>
                      {" "}
                      <Badge tone="warn">Internal: maintainers only</Badge>
                    </>
                  )}
                </div>
                <div className="row">
                  <Badge tone={scan.tone}>{scan.text}</Badge>
                  <button type="button" className="btn small" onClick={() => setPreview(a)}>
                    Preview<span className="sr-only"> {a.name}</span>
                  </button>
                  <button type="button" className="btn small" onClick={() => void download(a, "original")}>
                    Download<span className="sr-only"> {a.name}</span>
                  </button>
                  {a.sanitized_available && (
                    <button type="button" className="btn small" onClick={() => void download(a, "sanitized")}>
                      Masked copy<span className="sr-only"> of {a.name}</span>
                    </button>
                  )}
                  {canRemove(a) && (
                    <button type="button" className="btn small" onClick={() => setRemoving(a)}>
                      Remove<span className="sr-only"> {a.name}</span>
                    </button>
                  )}
                </div>
              </div>
              {a.secrets.status === "redacted" && <p className="muted small" style={{ margin: "6px 0 0" }}>Secrets were found and masked in the copy.</p>}
              {a.secrets.status === "needs_review" && (
                <p className="small" style={{ margin: "6px 0 0" }}>
                  <Badge tone="warn">Needs review</Badge> It may still hold a secret: it is not to be shared outside AgentLab until someone has looked.
                </p>
              )}
              {a.archive && (
                <p className="muted small" style={{ margin: "6px 0 0" }}>
                  An archive of {a.archive.entries as number} entries. It is never opened by AgentLab.
                </p>
              )}
            </li>
          );
        })}
      </ul>
      {preview && <AttachmentPreviewModal submissionId={submissionId} attachment={preview} onClose={() => setPreview(null)} />}
      {removing && (
        <ConfirmDialog title="Remove this file?" confirmLabel="Remove it" danger busy={busy} onConfirm={() => void remove()} onClose={() => setRemoving(null)}>
          <p>
            <strong className="mono">{removing.name}</strong> will be taken off this submission and deleted.
          </p>
        </ConfirmDialog>
      )}
    </>
  );
}
