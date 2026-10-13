import { useState } from "react";
import { describeError } from "../../api/errors";
import type { SubmissionComment } from "../../api/types";
import { formatTime } from "../../lib/format";
import { useSession } from "../../session";
import { Field, Notice } from "../ui";
import { Badge } from "../ui";
import { UserText } from "./shared";

/** One line of the discussion. An internal note is set apart in colour *and* in words, so it can never be taken for a reply. */
function Line({ comment }: { comment: SubmissionComment }) {
  const internal = comment.visibility === "internal";
  return (
    <li
      className="list-item"
      style={internal ? { borderLeft: "4px solid var(--warn)", background: "var(--surface-2)" } : undefined}
      aria-label={internal ? "Internal note" : "Comment"}
    >
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <strong>{comment.mine ? "You" : comment.author}</strong>
        <span className="muted small">{formatTime(comment.created_at)}</span>
        {internal && <Badge tone="warn">Internal note: maintainers only</Badge>}
        {comment.kind === "info_request" && <Badge tone="info">Asks for information</Badge>}
        {comment.kind === "reproduction_request" && <Badge tone="info">Asks you to try again</Badge>}
      </div>
      <div style={{ marginTop: 8 }}>
        <UserText text={comment.body} />
      </div>
    </li>
  );
}

/** The conversation, oldest first, and a box to add to it. A maintainer can also write a note only maintainers see, ask for more,
 * or ask for another attempt to reproduce. */
export function SubmissionComments({
  submissionId,
  discussion,
  staff,
  onPosted,
}: {
  submissionId: string;
  discussion: SubmissionComment[];
  staff: boolean;
  onPosted: () => void;
}) {
  const { api } = useSession();
  const [body, setBody] = useState("");
  const [mode, setMode] = useState<"public" | "internal" | "info_request" | "reproduction_request">("public");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const send = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.comment(submissionId, {
        body: body.trim(),
        visibility: mode === "internal" ? "internal" : "public",
        kind: mode === "info_request" || mode === "reproduction_request" ? mode : "comment",
      });
      setBody("");
      onPosted();
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="stack">
      {discussion.length === 0 ? (
        <p className="muted">Nobody has written anything yet.</p>
      ) : (
        <ol className="stack tight" style={{ listStyle: "none", margin: 0, padding: 0 }} aria-label="Discussion">
          {discussion.map((c) => (
            <Line key={c.id} comment={c} />
          ))}
        </ol>
      )}
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault();
          if (body.trim()) void send();
        }}
      >
        {staff && (
          <Field label="This is" htmlFor="comment-mode">
            <select id="comment-mode" className="select" value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}>
              <option value="public">A reply the reporter sees</option>
              <option value="info_request">A request for more information (the reporter sees it)</option>
              <option value="reproduction_request">A request to try again and report what happens (the reporter sees it)</option>
              <option value="internal">An internal note (maintainers only)</option>
            </select>
          </Field>
        )}
        {mode === "internal" && <Notice tone="warn">Only maintainers can see an internal note. The reporter will not.</Notice>}
        <Field label={mode === "internal" ? "Internal note" : staff ? "Your message" : "Add a comment or answer a question"} htmlFor="comment-body">
          <textarea id="comment-body" className="textarea" rows={3} value={body} onChange={(e) => setBody(e.target.value)} />
        </Field>
        {error && <Notice tone="error">{error}</Notice>}
        <div>
          <button type="submit" className="btn primary" disabled={busy || !body.trim()}>
            {mode === "internal" ? "Add the note" : "Send"}
          </button>
        </div>
      </form>
    </div>
  );
}
