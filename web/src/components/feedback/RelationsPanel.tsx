import { useState } from "react";
import { Link } from "react-router-dom";
import { describeError } from "../../api/errors";
import type { FeedbackOptions, SubmissionDetail } from "../../api/types";
import { RELATION_LABELS, label } from "../../lib/feedback";
import { useSession } from "../../session";
import { Field, Notice } from "../ui";
import { WorkflowBadge } from "./shared";

/** Links to other submissions: this repeats that one, is related to it, was caused by it, or is delivered by it. */
export function RelationsPanel({
  submission,
  options,
  onChanged,
}: {
  submission: SubmissionDetail;
  options: FeedbackOptions;
  onChanged: (updated: SubmissionDetail) => void;
}) {
  const { api } = useSession();
  const [other, setOther] = useState("");
  const [kind, setKind] = useState("related");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const add = async () => {
    setBusy(true);
    setError(null);
    try {
      onChanged(await api.relate(submission.id, { to: other.trim(), kind: kind as "duplicate_of" | "related" | "caused_by" | "implements" }));
      setOther("");
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  };
  const remove = async (relationId: string) => {
    try {
      onChanged(await api.unrelate(submission.id, relationId));
    } catch (e) {
      setError(describeError(e));
    }
  };
  return (
    <div className="stack">
      {submission.relations.length === 0 ? (
        <p className="muted">Not linked to anything.</p>
      ) : (
        <ul className="stack tight" style={{ listStyle: "none", margin: 0, padding: 0 }} aria-label="Links">
          {submission.relations.map((r) => (
            <li key={r.id} className="list-item">
              <div className="row between" style={{ flexWrap: "wrap" }}>
                <span>
                  {r.direction === "from" ? label(RELATION_LABELS, r.kind) : `${label(RELATION_LABELS, r.kind)} (the other way round)`}{" "}
                  <Link to={`/feedback/submissions/${r.other_number}`}>{r.other_number}</Link>: {r.other_title} <WorkflowBadge status={r.other_status} />
                </span>
                <button type="button" className="btn small" onClick={() => void remove(r.id)}>
                  Remove<span className="sr-only"> the link to {r.other_number}</span>
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      <form
        className="form-grid"
        onSubmit={(event) => {
          event.preventDefault();
          if (other.trim()) void add();
        }}
      >
        <Field label="Link to (number)" htmlFor="r-other" help="For example FR-2026-00042.">
          <input id="r-other" className="input mono" value={other} onChange={(e) => setOther(e.target.value)} />
        </Field>
        <Field label="How" htmlFor="r-kind">
          <select id="r-kind" className="select" value={kind} onChange={(e) => setKind(e.target.value)}>
            {options.relation_kinds.map((k) => (
              <option key={k} value={k}>
                {label(RELATION_LABELS, k)}
              </option>
            ))}
          </select>
        </Field>
        <div style={{ alignSelf: "end" }}>
          <button type="submit" className="btn" disabled={busy || !other.trim()}>
            Add the link
          </button>
        </div>
      </form>
      {error && <Notice tone="error">{error}</Notice>}
    </div>
  );
}
