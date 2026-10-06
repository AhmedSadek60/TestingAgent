import { useState } from "react";
import { describeError } from "../api/errors";
import type { Review, ReviewDecision, ReviewRequest, Severity } from "../api/types";
import { SEVERITIES } from "../api/types";
import { readStored, writeStored } from "../lib/storage";
import { formatTime } from "../lib/format";
import { statusLabel } from "../lib/labels";
import { useSession } from "../session";
import { Checkbox } from "./controls";
import { Badge, Field, Notice, useToast } from "./ui";

const REVIEWER_KEY = "agentlab.reviewer";

const DECISIONS: Record<ReviewDecision, { label: string; help: string; needsReason: boolean; appliesTo: ("result" | "finding")[] }> = {
  approve: { label: "Confirm", help: "The finding is real.", needsReason: false, appliesTo: ["finding", "result"] },
  false_positive: { label: "False positive", help: "It failed, but the agent did nothing wrong.", needsReason: true, appliesTo: ["result", "finding"] },
  false_negative: { label: "False negative", help: "It passed, but it should have failed.", needsReason: true, appliesTo: ["result"] },
  override_score: { label: "Override the score", help: "Set the score yourself (0 to 100).", needsReason: true, appliesTo: ["result"] },
  change_severity: { label: "Change the severity", help: "The severity is wrong.", needsReason: true, appliesTo: ["result", "finding"] },
  comment: { label: "Comment only", help: "Add a note and change nothing.", needsReason: false, appliesTo: ["result", "finding"] },
};

export function decisionLabel(decision: string): string {
  return DECISIONS[decision as ReviewDecision]?.label ?? statusLabel(decision);
}

/** Which decisions make sense for what is being reviewed (the server checks again). */
export function allowedDecisions(subject: "result" | "finding", status: string): ReviewDecision[] {
  return (Object.keys(DECISIONS) as ReviewDecision[]).filter((d) => {
    if (!DECISIONS[d].appliesTo.includes(subject)) return false;
    if (subject === "result" && d === "false_positive") return ["failed", "timeout", "error"].includes(status);
    if (subject === "result" && d === "false_negative") return status === "passed";
    return true;
  });
}

function show(value: unknown): string {
  if (value === null || value === undefined) return "n/a";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  return String(value);
}

function Change({ before, after }: { before: Record<string, unknown>; after: Record<string, unknown> }) {
  const keys = Object.keys(after);
  if (keys.length === 0) return <span className="muted">nothing was changed</span>;
  return (
    <span>
      {keys.map((k) => (
        <span key={k} style={{ marginRight: 12 }}>
          {k}: <s className="muted">{show(before[k])}</s> → <strong>{show(after[k])}</strong>
        </span>
      ))}
    </span>
  );
}

/** What reviewers decided about one subject. The original evaluation is shown next to each change: it is never replaced. */
export function ReviewList({ reviews }: { reviews: Review[] }) {
  if (reviews.length === 0) return <p className="muted">Nobody has reviewed this yet.</p>;
  return (
    <ul className="stack" style={{ margin: 0, paddingLeft: 0, listStyle: "none" }}>
      {reviews.map((r) => (
        <li key={r.id} className="card" style={{ padding: 12 }}>
          <div className="row between">
            <span>
              <Badge tone="info">{decisionLabel(r.decision)}</Badge> by <strong>{r.reviewer}</strong>
            </span>
            <span className="muted small">{formatTime(r.created_at)}</span>
          </div>
          <div className="small" style={{ marginTop: 6 }}>
            <Change before={r.original as Record<string, unknown>} after={r.reviewed as Record<string, unknown>} />
          </div>
          {r.reason && <p style={{ margin: "6px 0 0" }}>Reason: {r.reason}</p>}
          {r.comment && <p style={{ margin: "6px 0 0" }}>{r.comment}</p>}
        </li>
      ))}
    </ul>
  );
}

export function ReviewForm({
  runId,
  subject,
  subjectId,
  status,
  onDone,
}: {
  runId: string;
  subject: "result" | "finding";
  subjectId: string;
  status: string;
  onDone: () => void;
}) {
  const { api } = useSession();
  const toast = useToast();
  const options = allowedDecisions(subject, status);
  const [decision, setDecision] = useState<ReviewDecision>(options.includes("approve") ? "approve" : options[0]);
  const [reviewer, setReviewer] = useState(readStored("local", REVIEWER_KEY) ?? "");
  const [reason, setReason] = useState("");
  const [comment, setComment] = useState("");
  const [score, setScore] = useState("");
  const [severity, setSeverity] = useState<Severity | "">("");
  const [remember, setRemember] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const info = DECISIONS[decision];
  const problem = (() => {
    if (!reviewer.trim()) return "Enter your name so the review can be attributed.";
    if (info.needsReason && !reason.trim()) return "Give a reason, so the next reader can follow it.";
    if (decision === "override_score") {
      const value = Number(score);
      if (score.trim() === "" || !Number.isFinite(value) || value < 0 || value > 100) return "Enter a score from 0 to 100.";
    }
    if (decision === "change_severity" && !severity) return "Choose the severity.";
    return null;
  })();

  const submit = async () => {
    setBusy(true);
    setError(null);
    const body: ReviewRequest = {
      subject,
      subject_id: subjectId,
      decision,
      reviewer: reviewer.trim(),
      reason: reason.trim(),
      comment: comment.trim(),
      ...(decision === "override_score" ? { score: Number(score) / 100 } : {}),
      ...(severity && (decision === "change_severity" || decision === "false_negative") ? { severity } : {}),
    };
    try {
      await api.review(runId, body);
      if (remember) writeStored("local", REVIEWER_KEY, reviewer.trim());
      toast.push("good", "Review saved. The original evaluation is kept.");
      setReason("");
      setComment("");
      setScore("");
      onDone();
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      className="stack"
      onSubmit={(event) => {
        event.preventDefault();
        if (!problem) void submit();
      }}
    >
      <div className="form-grid">
        <Field label="Decision" htmlFor={`decision-${subjectId}`} help={info.help}>
          <select id={`decision-${subjectId}`} className="select" value={decision} onChange={(e) => setDecision(e.target.value as ReviewDecision)}>
            {options.map((d) => (
              <option key={d} value={d}>
                {DECISIONS[d].label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Your name" htmlFor={`reviewer-${subjectId}`}>
          <input id={`reviewer-${subjectId}`} className="input" value={reviewer} onChange={(e) => setReviewer(e.target.value)} maxLength={80} autoComplete="name" />
        </Field>
        {decision === "override_score" && (
          <Field label="Score (0 to 100)" htmlFor={`score-${subjectId}`}>
            <input id={`score-${subjectId}`} className="input" inputMode="decimal" value={score} onChange={(e) => setScore(e.target.value)} />
          </Field>
        )}
        {(decision === "change_severity" || decision === "false_negative") && (
          <Field label="Severity" htmlFor={`severity-${subjectId}`} help={decision === "false_negative" ? "Optional: medium when left empty." : undefined}>
            <select id={`severity-${subjectId}`} className="select" value={severity} onChange={(e) => setSeverity(e.target.value as Severity | "")}>
              <option value="">Choose…</option>
              {SEVERITIES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </Field>
        )}
        <Field label={info.needsReason ? "Reason (required)" : "Reason"} htmlFor={`reason-${subjectId}`} className="wide">
          <input id={`reason-${subjectId}`} className="input" value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
        <Field label="Comment" htmlFor={`comment-${subjectId}`} className="wide">
          <textarea id={`comment-${subjectId}`} className="textarea" value={comment} onChange={(e) => setComment(e.target.value)} />
        </Field>
      </div>
      <Checkbox checked={remember} onChange={setRemember}>
        Remember my name in this browser
      </Checkbox>
      {error && <Notice tone="error">{error}</Notice>}
      <div className="row">
        <button type="submit" className="btn primary" disabled={busy || problem !== null}>
          Save review
        </button>
        {problem && <span className="muted small">{problem}</span>}
      </div>
      <p className="muted small" style={{ margin: 0 }}>
        A review is stored next to the original evaluation, never over it. Reports show both, and a second scorecard is computed from reviewed values.
      </p>
    </form>
  );
}
