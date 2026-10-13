import { Link } from "react-router-dom";
import { useAsync } from "../../hooks/useAsync";
import { useSession } from "../../session";
import { Badge, Card } from "../ui";

/** The ways in: make a feature request, report an issue, follow what was sent, and (for a maintainer) work the queue. */
export function FeedbackSupportMenu() {
  const { api } = useSession();
  const counts = useAsync((signal) => api.feedbackSummary(signal), [api]);
  const options = useAsync((signal) => api.feedbackOptions(signal), [api]);
  const staff = options.data?.can_triage === true;
  return (
    <div className="choice-grid">
      <Link className="choice" to="/feedback/feature">
        <strong>Request a feature</strong>
        <span className="muted small">Ask for something new, or for something to work differently.</span>
      </Link>
      <Link className="choice" to="/feedback/issue">
        <strong>Report an issue</strong>
        <span className="muted small">Tell us what went wrong, with the details that help to find it.</span>
      </Link>
      <Link className="choice" to="/feedback/submissions">
        <strong>
          {staff ? "All submissions" : "My submissions"}{" "}
          {counts.data && counts.data.unread > 0 && <Badge tone="info">{counts.data.unread} with an answer</Badge>}
        </strong>
        <span className="muted small">
          {counts.data
            ? `${counts.data.total} in all, ${counts.data.open} still open.`
            : "What you sent, and what has happened to it."}
        </span>
      </Link>
      {staff && (
        <Link className="choice" to="/feedback/submissions?assignee=none&status=Submitted">
          <strong>Needs triage</strong>
          <span className="muted small">
            {counts.data && counts.data.unassigned !== null ? `${counts.data.unassigned} open and not assigned.` : "Open and not assigned."}
          </span>
        </Link>
      )}
    </div>
  );
}

export function FeedbackSupportCard() {
  return (
    <Card title="Feedback & Support">
      <FeedbackSupportMenu />
    </Card>
  );
}
