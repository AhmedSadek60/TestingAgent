import type { StatusChange } from "../../api/types";
import { formatTime } from "../../lib/format";
import { Badge } from "../ui";
import { UserText, WorkflowBadge } from "./shared";

/** Every change of status, oldest first, with who made it, when, and what they said. Nothing is ever overwritten. */
export function SubmissionTimeline({ history }: { history: StatusChange[] }) {
  if (history.length === 0) return <p className="muted">Nothing has happened yet.</p>;
  return (
    <ol className="stack tight" style={{ listStyle: "none", margin: 0, padding: 0 }} aria-label="Status history">
      {history.map((change) => (
        <li key={change.id} className="list-item">
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            {change.old_status === null ? (
              <>
                <strong>Submitted</strong> as <WorkflowBadge status={change.new_status} />
              </>
            ) : (
              <>
                <WorkflowBadge status={change.old_status} /> <span aria-label="changed to">→</span> <WorkflowBadge status={change.new_status} />
              </>
            )}
            <span className="muted small">
              {formatTime(change.at)} by {change.changed_by}
            </span>
            {change.by_staff && <Badge>maintainer</Badge>}
          </div>
          {change.comment && (
            <div style={{ marginTop: 8 }}>
              <UserText text={change.comment} />
            </div>
          )}
        </li>
      ))}
    </ol>
  );
}
