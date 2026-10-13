import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { describeError } from "../../api/errors";
import type { SubmissionDetail } from "../../api/types";
import { useSession } from "../../session";
import { Checkbox } from "../controls";
import { ConfirmDialog, Field, Notice, useToast } from "../ui";

/** An administrator's way to remove a submission and everything attached to it. It asks why, and says what cannot be undone. */
export function DeleteSubmissionPanel({ submission }: { submission: SubmissionDetail }) {
  const { api } = useSession();
  const navigate = useNavigate();
  const toast = useToast();
  const [reason, setReason] = useState("");
  const [asking, setAsking] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const openSecurity = submission.visibility === "security" && !submission.closed_at;

  const remove = async () => {
    setBusy(true);
    setError(null);
    try {
      const done = await api.deleteSubmission(submission.id, reason.trim(), acknowledged);
      toast.push(
        "good",
        `${done.number} was deleted with ${done.attachments} file(s) and ${done.handoffs} package(s).` +
          (done.exported_copies > 0 ? ` ${done.exported_copies} download(s) of its packages cannot be recalled.` : ""),
      );
      navigate("/feedback/submissions", { replace: true });
    } catch (e) {
      setError(describeError(e));
      setAsking(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <p className="muted" style={{ margin: 0 }}>
        Deletes this submission and everything attached to it: the discussion, the history, the files, the diagnostic information
        and the handoff packages. What stays is a record that it existed, with counts and the reason. A package or a file that was
        downloaded, and an issue published elsewhere, cannot be recalled.
      </p>
      {openSecurity && (
        <Notice tone="warn" title="This is a private security report that is not closed">
          Its evidence may still be needed. Close it first, or confirm below that you know it is open.
        </Notice>
      )}
      <Field label="Why it is being deleted" htmlFor="delete-reason" help="Kept in the audit trail, with secrets masked.">
        <input id="delete-reason" className="input" value={reason} maxLength={300} onChange={(e) => setReason(e.target.value)} />
      </Field>
      {openSecurity && (
        <Checkbox checked={acknowledged} onChange={setAcknowledged}>
          I know this report is open and its evidence may be needed
        </Checkbox>
      )}
      {error && <Notice tone="error">{error}</Notice>}
      <div>
        <button
          type="button"
          className="btn danger"
          disabled={reason.trim().length < 3 || (openSecurity && !acknowledged)}
          onClick={() => setAsking(true)}
        >
          Delete this submission
        </button>
      </div>
      {asking && (
        <ConfirmDialog
          title={`Delete ${submission.number}?`}
          confirmLabel="Delete it for good"
          danger
          busy={busy}
          onConfirm={() => void remove()}
          onClose={() => setAsking(false)}
        >
          <p>
            <strong>{submission.number}</strong> and everything attached to it will be removed. This cannot be undone.
          </p>
        </ConfirmDialog>
      )}
    </div>
  );
}
