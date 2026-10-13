import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { describeError } from "../../api/errors";
import type { SimilarSubmission, SubmissionDetail } from "../../api/types";
import { AttachmentUploader } from "../../components/feedback/AttachmentUploader";
import { SubmissionConfirmation } from "../../components/feedback/SubmissionConfirmation";
import { SubmissionTypeSelector } from "../../components/feedback/SubmissionTypeSelector";
import { ConfirmDialog, Field, Notice, PageHeader } from "../../components/ui";
import { Card } from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { useUnsavedChanges } from "../../hooks/useUnsavedChanges";
import { useUploads } from "../../hooks/useUploads";
import {
  FEATURE_PRIORITY_LABELS,
  clearDraft,
  contextFromSearch,
  emptyFeature,
  featureProblems,
  featureRequest,
  hasDraft,
  label,
  loadDraft,
  saveDraft,
  type FeatureValues,
  type Problems,
} from "../../lib/feedback";
import { useSession } from "../../session";

const DRAFT = "feature";

/** Ask for something new. Two things are required (a title and the problem); everything else helps and can be left. */
export function FeatureRequestForm() {
  const { api } = useSession();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const options = useAsync((signal) => api.feedbackOptions(signal), [api]);
  const initial = useMemo(() => emptyFeature(contextFromSearch(params.toString())), [params]);
  const [values, setValues] = useState<FeatureValues>(() => loadDraft(DRAFT, initial));
  const [restored] = useState(() => hasDraft(DRAFT));
  const [attempted, setAttempted] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<SubmissionDetail | null>(null);
  const [leaving, setLeaving] = useState<string | null>(null);
  const uploads = useUploads(api, null);
  const first = useRef<HTMLInputElement>(null);

  const dirty = !created && (JSON.stringify(values) !== JSON.stringify(initial) || uploads.items.length > 0);
  const sending = created !== null && uploads.items.some((i) => i.state === "waiting" || i.state === "uploading");
  useUnsavedChanges(dirty || sending, sending ? "Your files are still being sent. Leave this page and lose them?" : undefined);
  useEffect(() => {
    if (!created && JSON.stringify(values) !== JSON.stringify(initial)) saveDraft(DRAFT, values);
  }, [values, initial, created]);

  const set = <K extends keyof FeatureValues>(key: K, value: FeatureValues[K]) => setValues((v) => ({ ...v, [key]: value }));

  // possible duplicates: a suggestion, never an obstacle
  const [similar, setSimilar] = useState<SimilarSubmission[]>([]);
  useEffect(() => {
    const title = values.title.trim();
    if (title.length < 3 || created) {
      setSimilar([]);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api
        .similarSubmissions("feature", title, values.category || undefined, controller.signal)
        .then((found) => !controller.signal.aborted && setSimilar(found))
        .catch(() => undefined);
    }, 400);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [api, values.title, values.category, created]);

  // what is missing is said next to the field, and goes away as soon as it is there (after a first attempt to send)
  const problems: Problems = attempted ? featureProblems(values) : {};
  const submit = async () => {
    setAttempted(true);
    const found = featureProblems(values);
    if (Object.keys(found).length > 0) {
      setError("Some of what you wrote needs a look before this can be sent.");
      first.current?.focus();
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const made = await api.createSubmission(featureRequest(values));
      clearDraft(DRAFT);
      setCreated(made);
      if (uploads.items.length > 0) await uploads.sendAll(made.id);
    } catch (e) {
      setError(describeError(e));
    } finally {
      setSaving(false);
    }
  };

  if (created) {
    return (
      <>
        <PageHeader title="Request received" />
        <SubmissionConfirmation submission={created} uploads={uploads.items} diagnostics={null} onRetryUpload={(key) => uploads.retry(key, created.id)} />
      </>
    );
  }
  const categories = options.data?.feature_categories ?? [];
  const textMax = options.data?.max_text_chars ?? 20_000;
  return (
    <>
      <PageHeader title="Request a feature" subtitle="Tell us what you would like AgentLab to do, and why. A title and the problem are enough to start." />
      <form
        className="stack"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <SubmissionTypeSelector
          value="feature"
          onChange={(type) => {
            if (type === "issue") (dirty ? setLeaving("/feedback/issue") : navigate("/feedback/issue"));
          }}
        />
        {restored && dirty && (
          <Notice tone="info" title="Your earlier draft is back">
            What you had typed in this tab was kept.{" "}
            <button
              type="button"
              className="btn small"
              onClick={() => {
                clearDraft(DRAFT);
                setValues(initial);
                uploads.reset();
              }}
            >
              Start again
            </button>
          </Notice>
        )}
        <Card title="The request">
          <div className="stack">
            <Field label="Feature title" htmlFor="f-title" error={problems.title} help="A few words: “Export reports as CSV”.">
              <input
                id="f-title"
                ref={first}
                className="input"
                value={values.title}
                maxLength={200}
                aria-required="true"
                aria-invalid={problems.title ? true : undefined}
                onChange={(e) => set("title", e.target.value)}
              />
            </Field>
            {similar.length > 0 && (
              <Notice tone="info" title={`${similar.length} similar request${similar.length === 1 ? "" : "s"} may already exist`}>
                <ul style={{ margin: 0, paddingLeft: 20 }}>
                  {similar.map((s) => (
                    <li key={s.id}>
                      <Link to={`/feedback/submissions/${s.number}`} target="_blank" rel="noopener">
                        {s.number}: {s.title}
                      </Link>{" "}
                      <span className="muted">({s.status})</span>
                    </li>
                  ))}
                </ul>
                <p style={{ margin: "6px 0 0" }}>You can open one, or carry on with yours: nothing is merged for you.</p>
              </Notice>
            )}
            <Field label="Category" htmlFor="f-category" error={problems.category}>
              <select id="f-category" className="select" value={values.category} aria-required="true" onChange={(e) => set("category", e.target.value)}>
                <option value="">Choose a category</option>
                {categories.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </Field>
            <Field
              label="Problem or opportunity"
              htmlFor="f-problem"
              error={problems.problem_statement}
              help="What is hard, slow or missing today? Who runs into it?"
            >
              <textarea
                id="f-problem"
                className="textarea"
                rows={4}
                maxLength={textMax}
                aria-required="true"
                value={values.problem_statement}
                onChange={(e) => set("problem_statement", e.target.value)}
              />
            </Field>
            <Field label="Description" htmlFor="f-description" help="Anything else the team should know.">
              <textarea id="f-description" className="textarea" rows={3} maxLength={textMax} value={values.description} onChange={(e) => set("description", e.target.value)} />
            </Field>
            <Field
              label="Expected behavior"
              htmlFor="f-expected"
              help="Describe what you should be able to do once this feature is implemented. Include measurable acceptance criteria when possible."
            >
              <textarea id="f-expected" className="textarea" rows={3} maxLength={textMax} value={values.expected_behavior} onChange={(e) => set("expected_behavior", e.target.value)} />
            </Field>
            <Field label="Primary use case" htmlFor="f-use" help="The main situation in which you would use it.">
              <textarea id="f-use" className="textarea" rows={2} maxLength={textMax} value={values.primary_use_case} onChange={(e) => set("primary_use_case", e.target.value)} />
            </Field>
            <Field
              label="Requested business priority"
              htmlFor="f-priority"
              help="How important this is to you. The maintainers set their own engineering priority when they review it."
            >
              <select id="f-priority" className="select" value={values.requested_priority} onChange={(e) => set("requested_priority", e.target.value)}>
                {(options.data?.feature_priorities ?? ["low", "normal", "high", "critical_business_need"]).map((p) => (
                  <option key={p} value={p}>
                    {label(FEATURE_PRIORITY_LABELS, p)}
                  </option>
                ))}
              </select>
            </Field>
          </div>
        </Card>
        <details className="card">
          <summary className="card-header" style={{ cursor: "pointer" }}>
            <h2 style={{ margin: 0 }}>More detail (optional)</h2>
          </summary>
          <div className="card-body stack">
            <div className="form-grid">
              <Field label="Why it is important" htmlFor="f-why">
                <textarea id="f-why" className="textarea" rows={2} value={values.why_important} onChange={(e) => set("why_important", e.target.value)} />
              </Field>
              <Field label="Users or teams affected" htmlFor="f-users">
                <textarea id="f-users" className="textarea" rows={2} value={values.users_affected} onChange={(e) => set("users_affected", e.target.value)} />
              </Field>
              <Field label="Business impact" htmlFor="f-impact">
                <textarea id="f-impact" className="textarea" rows={2} value={values.business_impact} onChange={(e) => set("business_impact", e.target.value)} />
              </Field>
              <Field label="How often the use case comes up" htmlFor="f-frequency">
                <input id="f-frequency" className="input" maxLength={120} value={values.use_frequency} onChange={(e) => set("use_frequency", e.target.value)} />
              </Field>
              <Field label="Current workaround" htmlFor="f-workaround">
                <textarea id="f-workaround" className="textarea" rows={2} value={values.current_workaround} onChange={(e) => set("current_workaround", e.target.value)} />
              </Field>
              <Field label="Proposed solution" htmlFor="f-solution">
                <textarea id="f-solution" className="textarea" rows={2} value={values.proposed_solution} onChange={(e) => set("proposed_solution", e.target.value)} />
              </Field>
              <Field label="Acceptance criteria" htmlFor="f-criteria" help="One per line: how will we know it is done?" className="wide">
                <textarea id="f-criteria" className="textarea" rows={3} value={values.acceptance_criteria} onChange={(e) => set("acceptance_criteria", e.target.value)} />
              </Field>
              <Field label="Related AgentLab page or feature" htmlFor="f-area">
                <input id="f-area" className="input" maxLength={200} value={values.related_area} onChange={(e) => set("related_area", e.target.value)} />
              </Field>
              <Field label="Dependencies" htmlFor="f-deps">
                <textarea id="f-deps" className="textarea" rows={2} value={values.dependencies} onChange={(e) => set("dependencies", e.target.value)} />
              </Field>
              <Field label="Related project" htmlFor="f-project" error={problems.project_id}>
                <input id="f-project" className="input mono" value={values.project_id} onChange={(e) => set("project_id", e.target.value)} />
              </Field>
              <Field label="Related evaluation run" htmlFor="f-run" error={problems.related_run_id}>
                <input id="f-run" className="input mono" value={values.related_run_id} onChange={(e) => set("related_run_id", e.target.value)} />
              </Field>
              <Field label="Related test case" htmlFor="f-test" error={problems.related_test_id}>
                <input id="f-test" className="input mono" value={values.related_test_id} onChange={(e) => set("related_test_id", e.target.value)} />
              </Field>
              <Field label="Related report" htmlFor="f-report" error={problems.related_report_id}>
                <input id="f-report" className="input mono" value={values.related_report_id} onChange={(e) => set("related_report_id", e.target.value)} />
              </Field>
            </div>
          </div>
        </details>
        <Card title="Supporting files">
          <AttachmentUploader
            items={uploads.items}
            options={options.data}
            onAdd={uploads.add}
            onRemove={(key) => void uploads.remove(key)}
            onRetry={(key) => uploads.retry(key)}
            disabled={saving}
          />
          <p className="muted small" style={{ marginTop: 8 }}>
            Mockups, screenshots, documents and examples are sent right after the request is saved.
          </p>
        </Card>
        {error && <Notice tone="error">{error}</Notice>}
        <div className="row">
          <button type="submit" className="btn primary" disabled={saving}>
            {saving ? "Sending…" : "Send the request"}
          </button>
          <button type="button" className="btn" disabled={saving} onClick={() => (dirty ? setLeaving("/feedback") : navigate("/feedback"))}>
            Cancel
          </button>
        </div>
      </form>
      {leaving && (
        <ConfirmDialog
          title="Leave without sending?"
          confirmLabel="Leave and lose it"
          danger
          onConfirm={() => {
            clearDraft(DRAFT);
            navigate(leaving);
          }}
          onClose={() => setLeaving(null)}
        >
          <p>What you wrote has not been sent. If you leave, it is lost.</p>
        </ConfirmDialog>
      )}
    </>
  );
}
