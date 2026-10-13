import { useEffect, useMemo, useState } from "react";
import { ApiError, describeError } from "../../api/errors";
import type { Assignee, FeedbackOptions, SubmissionChange, SubmissionDetail } from "../../api/types";
import { PRIORITY_LABELS, label } from "../../lib/feedback";
import { useSession } from "../../session";
import { Checkbox } from "../controls";
import { Field, Notice } from "../ui";

/**
 * What a maintainer decides: the status (and what the reporter is told with it), the priority and severity the maintainers
 * give, who has it, the category, the labels, and whether it is private. Only what changed is sent, with the version that
 * was read, so a change made by someone else in the meantime is refused and not overwritten.
 */
export function MaintainerTriagePanel({
  submission,
  options,
  assignees,
  onChanged,
}: {
  submission: SubmissionDetail;
  options: FeedbackOptions;
  assignees: Assignee[];
  onChanged: (updated: SubmissionDetail | null) => void;
}) {
  const { api } = useSession();
  const initial = useMemo(
    () => ({
      status: submission.status,
      statusComment: "",
      priority: submission.assigned_priority ?? "",
      severity: submission.severity ?? "",
      assignee: submission.assignee?.id ?? "",
      category: submission.category,
      labels: submission.labels.join(", "),
      isPrivate: submission.visibility === "security",
    }),
    [submission],
  );
  const [form, setForm] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    setForm(initial);
  }, [initial]);
  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) => {
    setSaved(false);
    setForm((f) => ({ ...f, [key]: value }));
  };
  const categories = submission.type === "feature" ? options.feature_categories : options.issue_categories;
  const labelList = (text: string) =>
    text
      .split(",")
      .map((l) => l.trim())
      .filter(Boolean);

  const change = (): SubmissionChange => {
    const patch: SubmissionChange = { version: submission.version };
    if (form.status !== initial.status) patch.status = form.status;
    if (form.statusComment.trim()) patch.status_comment = form.statusComment.trim();
    if (form.priority !== initial.priority) patch.assigned_priority = (form.priority || null) as SubmissionChange["assigned_priority"];
    if (form.severity !== initial.severity) patch.severity = (form.severity || null) as SubmissionChange["severity"];
    if (form.assignee !== initial.assignee) patch.assignee_id = form.assignee || null;
    if (form.category !== initial.category) patch.category = form.category;
    if (form.labels !== initial.labels) patch.labels = labelList(form.labels);
    if (form.isPrivate !== initial.isPrivate) patch.visibility = form.isPrivate ? "security" : "normal";
    return patch;
  };
  const changed = Object.keys(change()).length > 1;

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.changeSubmission(submission.id, change());
      setSaved(true);
      onChanged(updated);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setError("Someone else changed this submission while you were reading it. The latest version has been loaded: look at it and make your change again.");
        onChanged(null);
      } else {
        setError(describeError(e));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      className="stack"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <div className="form-grid">
        <Field label="Status" htmlFor="t-status">
          <select id="t-status" className="select" value={form.status} onChange={(e) => set("status", e.target.value)}>
            {submission.statuses.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Priority (maintainers)" htmlFor="t-priority" help="Separate from the priority the reporter asked for.">
          <select id="t-priority" className="select" value={form.priority} onChange={(e) => set("priority", e.target.value)}>
            <option value="">Not set</option>
            {options.priorities.map((p) => (
              <option key={p} value={p}>
                {label(PRIORITY_LABELS, p)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Severity" htmlFor="t-severity" help="Decided by the maintainers; the reporter only rated the impact.">
          <select id="t-severity" className="select" value={form.severity} onChange={(e) => set("severity", e.target.value)}>
            <option value="">Not set</option>
            {options.severities.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Assigned to" htmlFor="t-assignee">
          <select id="t-assignee" className="select" value={form.assignee} onChange={(e) => set("assignee", e.target.value)}>
            <option value="">Nobody</option>
            {assignees.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Category" htmlFor="t-category">
          <select id="t-category" className="select" value={form.category} onChange={(e) => set("category", e.target.value)}>
            {!categories.includes(form.category) && <option value={form.category}>{form.category}</option>}
            {categories.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Labels" htmlFor="t-labels" help="Separated by commas.">
          <input id="t-labels" className="input" value={form.labels} onChange={(e) => set("labels", e.target.value)} />
        </Field>
      </div>
      <Field
        label="Message with the status change"
        htmlFor="t-comment"
        help="The reporter sees this next to the new status. Leave it empty to say nothing."
      >
        <textarea id="t-comment" className="textarea" rows={2} value={form.statusComment} onChange={(e) => set("statusComment", e.target.value)} />
      </Field>
      <Checkbox checked={form.isPrivate} onChange={(on) => set("isPrivate", on)}>
        Private: only the reporter and authorised maintainers can see it
      </Checkbox>
      {error && <Notice tone="warn">{error}</Notice>}
      {saved && !error && <Notice tone="good">Saved.</Notice>}
      <div>
        <button type="submit" className="btn primary" disabled={busy || !changed}>
          Save changes
        </button>
      </div>
    </form>
  );
}
