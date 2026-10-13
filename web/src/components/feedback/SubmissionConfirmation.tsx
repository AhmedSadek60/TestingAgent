import { Link } from "react-router-dom";
import type { SubmissionDetail } from "../../api/types";
import type { UploadItem } from "../../hooks/useUploads";
import { formatTime } from "../../lib/format";
import { Card, KeyValue, Notice } from "../ui";
import { WorkflowBadge } from "./shared";

export interface DiagnosticOutcome {
  attached: boolean;
  items: number;
  error: string | null;
}

/** Shown only after the server has said it kept the submission: its number, its state, and what became of what came with it. */
export function SubmissionConfirmation({
  submission,
  uploads,
  diagnostics,
  onRetryUpload,
}: {
  submission: SubmissionDetail;
  uploads: UploadItem[];
  diagnostics: DiagnosticOutcome | null;
  onRetryUpload: (key: string) => void;
}) {
  const failed = uploads.filter((u) => u.state === "failed");
  const done = uploads.filter((u) => u.state === "done");
  const sending = uploads.filter((u) => u.state === "waiting" || u.state === "uploading");
  return (
    <div className="stack">
      <Notice tone="good" title={`${submission.type === "feature" ? "Your feature request" : "Your issue report"} was received`}>
        It is saved on the server and you can come back to it at any time.
      </Notice>
      <Card title="Submission">
        <KeyValue
          items={[
            ["Submission ID", <strong key="id">{submission.number}</strong>],
            ["Title", submission.title],
            ["Submitted", formatTime(submission.created_at)],
            ["Status", <WorkflowBadge key="s" status={submission.status} />],
            ["Details", <Link key="l" to={`/feedback/submissions/${submission.number}`}>Open {submission.number}</Link>],
          ]}
        />
        {submission.visibility === "security" && (
          <p className="muted" style={{ marginTop: 12 }}>
            This is a private report: only you and authorised maintainers can see it.
          </p>
        )}
      </Card>
      {(uploads.length > 0 || diagnostics) && (
        <Card title="What came with it">
          <div className="stack">
            {sending.length > 0 && (
              <p style={{ margin: 0 }} role="status">
                Sending files: {done.length + failed.length} of {uploads.length} done. Please stay on this page until it finishes.
              </p>
            )}
            {done.length > 0 && <p style={{ margin: 0 }}>{done.length} file{done.length === 1 ? " was" : "s were"} attached.</p>}
            {failed.length > 0 && (
              <Notice tone="warn" title={`${failed.length} file${failed.length === 1 ? "" : "s"} could not be attached`}>
                <ul style={{ margin: 0, paddingLeft: 20 }}>
                  {failed.map((u) => (
                    <li key={u.key}>
                      {u.file.name}: {u.error}{" "}
                      <button type="button" className="btn small" onClick={() => onRetryUpload(u.key)}>
                        Try again
                      </button>
                    </li>
                  ))}
                </ul>
                <p style={{ margin: "8px 0 0" }}>The report itself is saved. You can also add files from its page.</p>
              </Notice>
            )}
            {diagnostics?.attached && <p style={{ margin: 0 }}>Diagnostic information ({diagnostics.items} item{diagnostics.items === 1 ? "" : "s"}) was attached.</p>}
            {diagnostics && !diagnostics.attached && (
              <Notice tone="warn" title="Diagnostic information was not attached">
                {diagnostics.error ?? "It was not attached."} The report itself is saved; you can add diagnostic information from its page.
              </Notice>
            )}
          </div>
        </Card>
      )}
      <div className="row">
        <Link className="btn primary" to="/feedback/submissions">
          See all my submissions
        </Link>
        <Link className="btn" to="/feedback">
          Back to Feedback & Support
        </Link>
      </div>
    </div>
  );
}
