import { useState } from "react";
import { describeError } from "../../api/errors";
import type { Integration, PublicationPreview, SubmissionDetail } from "../../api/types";
import { useAsync } from "../../hooks/useAsync";
import { formatTime } from "../../lib/format";
import { useSession } from "../../session";
import { Checkbox } from "../controls";
import { Badge, Notice } from "../ui";

/**
 * Publish a sanitized summary of a submission as an issue in the tracker the owner connected. Nothing is sent until the
 * person has read exactly what would be, and what they read is what goes. A private security report is never offered.
 */
export function PublishPanel({ submission, integration }: { submission: SubmissionDetail; integration: Integration }) {
  const { api } = useSession();
  const links = useAsync((signal) => api.publications(submission.id, signal), [api, submission.id]);
  const [preview, setPreview] = useState<PublicationPreview | null>(null);
  const [understood, setUnderstood] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const [busy, setBusy] = useState<"preview" | "publish" | "sync" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const published = (links.data ?? []).find((l) => l.state === "published");
  const security = submission.visibility === "security";

  if (security) {
    return <p className="muted">A private security report is never published to an external service.</p>;
  }
  if (!integration.may_publish) {
    return <p className="muted">Your role may not publish to {integration.repository}. An administrator can.</p>;
  }

  const run = async (what: "preview" | "publish" | "sync") => {
    setBusy(what);
    setError(null);
    try {
      if (what === "preview") {
        setPreview(await api.previewPublication(submission.id, integration.provider));
        setUnderstood(false);
        setReviewed(false);
      } else if (what === "publish" && preview) {
        await api.publish(submission.id, {
          provider: integration.provider,
          content_hash: preview.content_hash,
          acknowledge_unreviewed: preview.needs_review ? reviewed : false,
        });
        setPreview(null);
        links.reload();
      } else if (what === "sync") {
        await api.syncPublication(submission.id, integration.provider);
        links.reload();
      }
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="stack">
      <p className="muted" style={{ margin: 0 }}>
        Makes an issue in <strong>{integration.repository}</strong> from a sanitized summary. Files, diagnostic information, handoff
        packages, the reporter&apos;s name, internal notes and your ratings are never sent. Once it is public AgentLab cannot take it
        back.
      </p>
      {(links.data ?? []).map((link) => (
        <div key={link.id} className="list-item">
          <div className="row between" style={{ flexWrap: "wrap" }}>
            <span>
              {link.state === "published" ? (
                <>
                  Published as{" "}
                  <a href={link.url} target="_blank" rel="noopener noreferrer">
                    {link.repository}#{link.number}
                  </a>{" "}
                  <Badge tone={link.external_state === "closed" ? "neutral" : "good"}>{link.external_state}</Badge>
                  <span className="muted small"> by {link.published_by}, {formatTime(link.published_at)}</span>
                </>
              ) : link.state === "failed" ? (
                <>
                  <Badge tone="bad">Not published</Badge> {link.error_detail}
                </>
              ) : (
                <Badge tone="warn">In progress</Badge>
              )}
            </span>
            {link.state === "published" && integration.status_sync && (
              <button type="button" className="btn small" disabled={busy !== null} onClick={() => void run("sync")}>
                Push the status to the issue
              </button>
            )}
          </div>
        </div>
      ))}
      {error && <Notice tone="error">{error}</Notice>}
      {!published && (
        <div className="row">
          <button type="button" className="btn" disabled={busy !== null} onClick={() => void run("preview")}>
            {busy === "preview" ? "Preparing…" : "Preview what would be published"}
          </button>
        </div>
      )}
      {preview && !published && (
        <div className="stack">
          <Notice tone="warn" title="Read it: this is exactly what will be public">
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              {preview.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </Notice>
          <div>
            <strong>Title</strong>
            <div className="untrusted">{preview.title}</div>
          </div>
          <div>
            <strong>Text</strong>
            <pre className="codebox" style={{ whiteSpace: "pre-wrap", maxHeight: 360, overflow: "auto" }}>
              {preview.body}
            </pre>
          </div>
          <p className="muted small" style={{ margin: 0 }}>
            Labels: {preview.labels.length ? preview.labels.join(", ") : "none"}. Assigned to:{" "}
            {preview.assignees.length ? preview.assignees.join(", ") : "nobody"}. Left out on purpose: {preview.left_out.join("; ")}.
          </p>
          {preview.needs_review && (
            <Checkbox checked={reviewed} onChange={setReviewed}>
              I have read the text above: something in it still looks like a secret after masking, and I want it published anyway
            </Checkbox>
          )}
          <Checkbox checked={understood} onChange={setUnderstood}>
            I have read what will be published in {preview.repository}, and I understand it will be visible there
          </Checkbox>
          <div>
            <button
              type="button"
              className="btn primary"
              disabled={busy !== null || !understood || (preview.needs_review && !reviewed)}
              onClick={() => void run("publish")}
            >
              {busy === "publish" ? "Publishing…" : "Publish it"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
