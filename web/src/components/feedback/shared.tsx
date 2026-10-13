import type { ReactNode } from "react";
import type { RedactionSummary as Redaction, SubmissionType } from "../../api/types";
import { label, workflowTone } from "../../lib/feedback";
import { Badge } from "../ui";

/** Words a person or a system wrote, shown as text: never as markup, and set apart so that it is not taken for the page's own. */
export function UserText({ text, empty = "Nothing was written." }: { text: string | null | undefined; empty?: string }) {
  if (!text) return <p className="muted">{empty}</p>;
  return (
    <div className="untrusted" style={{ whiteSpace: "pre-wrap" }}>
      {text}
    </div>
  );
}

export function TypeBadge({ type }: { type: SubmissionType }) {
  return <Badge tone={type === "feature" ? "info" : "warn"}>{type === "feature" ? "Feature request" : "Issue report"}</Badge>;
}

/** A status as a word (never a colour alone), toned by what the name says. */
export function WorkflowBadge({ status }: { status: string }) {
  return <Badge tone={workflowTone(status)}>{status}</Badge>;
}

export function PrivateBadge() {
  return (
    <Badge tone="critical" title="Only the reporter and authorised maintainers can see this">
      Private
    </Badge>
  );
}

/** What masking did to something, with no value: how many, of what kind, where. */
export function RedactionSummary({ summary }: { summary: Redaction }) {
  const total = Object.values(summary.counts).reduce((n, c) => n + c, 0);
  if (summary.status === "clean" && total === 0) return <span className="muted small">Nothing was masked.</span>;
  const kinds = Object.entries(summary.counts)
    .map(([kind, n]) => `${label({}, kind)} × ${n}`)
    .join(", ");
  return (
    <span className="small">
      <Badge tone={summary.status === "needs_review" ? "warn" : "info"}>{summary.status === "needs_review" ? "Needs review" : "Masked"}</Badge>{" "}
      {total} value{total === 1 ? "" : "s"} masked{kinds ? `: ${kinds}` : ""}
      {summary.locations.length > 0 && <span className="muted"> (at {summary.locations.slice(0, 5).join(", ")})</span>}
      {summary.external_sharing_blocked && (
        <span style={{ display: "block" }}>It is not to be shared outside AgentLab until a person has looked at it.</span>
      )}
    </span>
  );
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="stack tight">
      <h3 style={{ margin: 0, fontSize: 14 }}>{title}</h3>
      {children}
    </div>
  );
}
