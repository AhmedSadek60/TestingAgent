import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { describeError } from "../../api/errors";
import type { SimilarSubmission, SubmissionDetail } from "../../api/types";
import { AttachmentUploader } from "../../components/feedback/AttachmentUploader";
import {
  DiagnosticCollectionPanel,
  NO_DIAGNOSTICS,
  diagnosticsReadiness,
  type DiagnosticChoice,
} from "../../components/feedback/DiagnosticCollectionPanel";
import { SubmissionConfirmation, type DiagnosticOutcome } from "../../components/feedback/SubmissionConfirmation";
import { SubmissionTypeSelector } from "../../components/feedback/SubmissionTypeSelector";
import { Checkbox } from "../../components/controls";
import { Card, ConfirmDialog, Field, Notice, PageHeader } from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { useUnsavedChanges } from "../../hooks/useUnsavedChanges";
import { useUploads } from "../../hooks/useUploads";
import {
  CONTEXT_LABELS,
  FREQUENCY_LABELS,
  IMPACT_LABELS,
  clearDraft,
  contextFromSearch,
  emptyIssue,
  hasDraft,
  issueProblems,
  issueRequest,
  label,
  loadDraft,
  saveDraft,
  type Context,
  type IssueValues,
  type Problems,
} from "../../lib/feedback";
import { useSession } from "../../session";

const DRAFT = "issue";
const CONTEXT_KEYS = Object.keys(CONTEXT_LABELS) as (keyof Context)[];

/**
 * Report something that went wrong. Three things are required (a title, what happened, how it affects the work); the rest
 * helps a developer find it and none of it has to be invented. What AgentLab already knows about where the person was is
 * shown and can be removed; diagnostic information is chosen, reviewed and agreed to, never added for them.
 */
export function IssueReportForm() {
  const { api } = useSession();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const options = useAsync((signal) => api.feedbackOptions(signal), [api]);
  const fromLink = useMemo(() => contextFromSearch(params.toString()), [params]);
  const initial = useMemo(() => emptyIssue(fromLink), [fromLink]);
  const [values, setValues] = useState<IssueValues>(() => {
    const draft = loadDraft(DRAFT, initial);
    // where the person came from now wins over where an earlier draft was started
    return { ...draft, ...Object.fromEntries(CONTEXT_KEYS.filter((k) => fromLink[k]).map((k) => [k, fromLink[k]])) } as IssueValues;
  });
  const [restored] = useState(() => hasDraft(DRAFT));
  const [problems, setProblems] = useState<Problems>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<SubmissionDetail | null>(null);
  const [diagnosticOutcome, setDiagnosticOutcome] = useState<DiagnosticOutcome | null>(null);
  const [leaving, setLeaving] = useState<string | null>(null);
  const [choice, setChoice] = useState<DiagnosticChoice>(NO_DIAGNOSTICS);
  const [showDiagnostics, setShowDiagnostics] = useState(false);
  const uploads = useUploads(api, null);
  const first = useRef<HTMLInputElement>(null);

  const dirty =
    !created && (JSON.stringify(values) !== JSON.stringify(initial) || uploads.items.length > 0 || choice.selected.length > 0);
  const sending = created !== null && uploads.items.some((i) => i.state === "waiting" || i.state === "uploading");
  useUnsavedChanges(dirty || sending, sending ? "Your files are still being sent. Leave this page and lose them?" : undefined);
  useEffect(() => {
    if (!created && JSON.stringify(values) !== JSON.stringify(initial)) saveDraft(DRAFT, values);
  }, [values, initial, created]);

  const set = <K extends keyof IssueValues>(key: K, value: IssueValues[K]) => setValues((v) => ({ ...v, [key]: value }));
  const security = !!options.data && values.category === options.data.security_category;

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
        .similarSubmissions("issue", title, values.category || undefined, controller.signal)
        .then((found) => !controller.signal.aborted && setSimilar(found))
        .catch(() => undefined);
    }, 400);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [api, values.title, values.category, created]);

  const readiness = diagnosticsReadiness(choice);

  const submit = async () => {
    const found = issueProblems(values);
    setProblems(found);
    if (Object.keys(found).length > 0) {
      setError("Some of what you wrote needs a look before this can be sent.");
      first.current?.focus();
      return;
    }
    if (readiness.problem) {
      setError(readiness.problem);
      setShowDiagnostics(true);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const made = await api.createSubmission(issueRequest(values, options.data?.security_category ?? ""));
      clearDraft(DRAFT);
      setCreated(made);
      if (uploads.items.length > 0) await uploads.sendAll(made.id);
      if (readiness.attach && choice.collection?.id) {
        try {
          const attached = await api.attachDiagnostics(made.id, { collection_id: choice.collection.id, include: choice.include, consent: true });
          setDiagnosticOutcome({ attached: true, items: attached.items.filter((i) => i.included).length, error: null });
        } catch (e) {
          setDiagnosticOutcome({ attached: false, items: 0, error: describeError(e) });
        }
      }
    } catch (e) {
      setError(describeError(e));
    } finally {
      setSaving(false);
    }
  };

  if (created) {
    return (
      <>
        <PageHeader title="Report received" />
        <SubmissionConfirmation
          submission={created}
          uploads={uploads.items}
          diagnostics={diagnosticOutcome}
          onRetryUpload={(key) => uploads.retry(key, created.id)}
        />
      </>
    );
  }
  const categories = options.data?.issue_categories ?? [];
  const textMax = options.data?.max_text_chars ?? 20_000;
  const attached = CONTEXT_KEYS.filter((k) => values[k]);
  return (
    <>
      <PageHeader title="Report an issue" subtitle="Tell us what went wrong. You do not need to know why, and you do not need to fill in everything." />
      <form
        className="stack"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <SubmissionTypeSelector
          value="issue"
          onChange={(type) => {
            if (type === "feature") (dirty ? setLeaving("/feedback/feature") : navigate("/feedback/feature"));
          }}
        />
        {restored && dirty && (
          <Notice tone="info" title="Your earlier draft is back">
            What you had typed in this tab was kept (files and diagnostic information are not).{" "}
            <button
              type="button"
              className="btn small"
              onClick={() => {
                clearDraft(DRAFT);
                setValues(initial);
                uploads.reset();
                setChoice(NO_DIAGNOSTICS);
              }}
            >
              Start again
            </button>
          </Notice>
        )}
        <Card title="What happened">
          <div className="stack">
            <Field label="Issue title" htmlFor="i-title" error={problems.title} help="A few words: “A run stays on Processing forever”.">
              <input
                id="i-title"
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
              <Notice tone="info" title={`${similar.length} similar report${similar.length === 1 ? "" : "s"} may already exist`}>
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
                <p style={{ margin: "6px 0 0" }}>You can open one, or carry on with yours.</p>
              </Notice>
            )}
            <Field label="What kind of problem is it?" htmlFor="i-category" error={problems.category}>
              <select id="i-category" className="select" value={values.category} aria-required="true" onChange={(e) => set("category", e.target.value)}>
                <option value="">Choose one</option>
                {categories.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </Field>
            {security && (
              <Notice tone="warn" title="This may contain sensitive security information">
                A report in this category is private: only you and authorised maintainers can see it, and it is never published
                automatically. Do not put secrets, passwords or other people&apos;s personal data in it.
              </Notice>
            )}
            <Field label="Describe the problem" htmlFor="i-description" error={problems.description}>
              <textarea
                id="i-description"
                className="textarea"
                rows={4}
                maxLength={textMax}
                aria-required="true"
                value={values.description}
                onChange={(e) => set("description", e.target.value)}
              />
            </Field>
            <Field
              label="How is this issue affecting your work?"
              htmlFor="i-impact"
              help="This is your view of it. The maintainers decide the severity."
            >
              <select id="i-impact" className="select" value={values.reported_impact} onChange={(e) => set("reported_impact", e.target.value)}>
                {(options.data?.impacts ?? Object.keys(IMPACT_LABELS)).map((i) => (
                  <option key={i} value={i}>
                    {label(IMPACT_LABELS, i)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="In a sentence: what does that mean for you?" htmlFor="i-summary" error={problems.impact_summary}>
              <input
                id="i-summary"
                className="input"
                value={values.impact_summary}
                aria-required="true"
                onChange={(e) => set("impact_summary", e.target.value)}
              />
            </Field>
          </div>
        </Card>
        <Card title="Expected and actual">
          <div className="form-grid">
            <Field label="Expected: what you believe AgentLab should have done" htmlFor="i-expected">
              <textarea id="i-expected" className="textarea" rows={3} maxLength={textMax} value={values.expected_behavior} onChange={(e) => set("expected_behavior", e.target.value)} />
            </Field>
            <Field label="Actual: what AgentLab did instead" htmlFor="i-actual">
              <textarea id="i-actual" className="textarea" rows={3} maxLength={textMax} value={values.actual_behavior} onChange={(e) => set("actual_behavior", e.target.value)} />
            </Field>
          </div>
        </Card>
        <Card title="How to see it again">
          <div className="stack">
            <Checkbox checked={values.not_reproducible} onChange={(on) => set("not_reproducible", on)}>
              I cannot reproduce it, or I do not know how to make it happen again
            </Checkbox>
            <Field label="Steps to reproduce" htmlFor="i-steps" help="Only what you know: please do not invent steps.">
              <textarea
                id="i-steps"
                className="textarea"
                rows={4}
                maxLength={textMax}
                disabled={values.not_reproducible}
                value={values.reproduction_steps}
                onChange={(e) => set("reproduction_steps", e.target.value)}
              />
            </Field>
            <div className="form-grid">
              <Field label="How often does it happen?" htmlFor="i-frequency">
                <select id="i-frequency" className="select" value={values.frequency} onChange={(e) => set("frequency", e.target.value)}>
                  {(options.data?.frequencies ?? Object.keys(FREQUENCY_LABELS)).map((f) => (
                    <option key={f} value={f}>
                      {label(FREQUENCY_LABELS, f)}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="When did you first see it?" htmlFor="i-first" error={problems.first_observed_at} help="An approximate time is fine.">
                <input id="i-first" className="input" type="datetime-local" value={values.first_observed_at} onChange={(e) => set("first_observed_at", e.target.value)} />
              </Field>
              <Field label="Does it happen consistently?" htmlFor="i-consistent">
                <select id="i-consistent" className="select" value={values.consistent} onChange={(e) => set("consistent", e.target.value as IssueValues["consistent"])}>
                  <option value="">Not sure</option>
                  <option value="yes">Yes, consistently</option>
                  <option value="no">No, it comes and goes</option>
                </select>
              </Field>
              <Field label="Does it block your work?" htmlFor="i-blocks">
                <select id="i-blocks" className="select" value={values.blocks_work} onChange={(e) => set("blocks_work", e.target.value as IssueValues["blocks_work"])}>
                  <option value="">Not sure</option>
                  <option value="yes">Yes</option>
                  <option value="no">No</option>
                </select>
              </Field>
            </div>
            <Field label="A way around it, if you found one" htmlFor="i-workaround">
              <textarea id="i-workaround" className="textarea" rows={2} maxLength={textMax} value={values.workaround} onChange={(e) => set("workaround", e.target.value)} />
            </Field>
          </div>
        </Card>
        <Card title="Where in AgentLab">
          <div className="stack">
            {attached.length > 0 ? (
              <>
                <p className="muted" style={{ margin: 0 }}>
                  These details will be sent with your report so that the developers can find the right place. Remove any you do not
                  want to send.
                </p>
                <ul className="row" style={{ flexWrap: "wrap", listStyle: "none", margin: 0, padding: 0 }} aria-label="Details that will be sent">
                  {attached.map((key) => (
                    <li key={key} className="tag">
                      {CONTEXT_LABELS[key]}: <span className="mono">{values[key]}</span>{" "}
                      <button
                        type="button"
                        className="btn small ghost"
                        onClick={() => set(key, "")}
                        aria-label={`Do not send ${CONTEXT_LABELS[key].toLowerCase()} ${values[key]}`}
                      >
                        ×
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <p className="muted" style={{ margin: 0 }}>
                Nothing about where you were is attached. If you opened this from a run or a result, the details would be listed here.
              </p>
            )}
            <details>
              <summary>Add or change details</summary>
              <div className="form-grid" style={{ marginTop: 12 }}>
                {CONTEXT_KEYS.map((key) => (
                  <Field key={key} label={CONTEXT_LABELS[key]} htmlFor={`i-ctx-${key}`} error={problems[key]}>
                    <input
                      id={`i-ctx-${key}`}
                      className={`input${key === "ui_screen" ? "" : " mono"}`}
                      value={values[key]}
                      maxLength={key === "ui_screen" ? 200 : 64}
                      onChange={(e) => set(key, e.target.value)}
                    />
                  </Field>
                ))}
                <Field label="AgentLab version" htmlFor="i-version" help="Shown at the foot of the page, when you know it.">
                  <input id="i-version" className="input" maxLength={40} value={values.app_version} onChange={(e) => set("app_version", e.target.value)} />
                </Field>
                <Field label="Build or commit" htmlFor="i-build">
                  <input id="i-build" className="input" maxLength={80} value={values.build} onChange={(e) => set("build", e.target.value)} />
                </Field>
              </div>
            </details>
          </div>
        </Card>
        <Card title="Files, logs and screenshots">
          <AttachmentUploader
            items={uploads.items}
            options={options.data}
            onAdd={uploads.add}
            onRemove={(key) => void uploads.remove(key)}
            onRetry={(key) => uploads.retry(key)}
            disabled={saving}
          />
        </Card>
        <Card
          title="Attach diagnostic information"
          actions={
            <button type="button" className="btn small" aria-expanded={showDiagnostics} onClick={() => setShowDiagnostics((v) => !v)}>
              {showDiagnostics ? "Hide" : "Show options"}
            </button>
          }
        >
          {showDiagnostics ? (
            <DiagnosticCollectionPanel
              value={choice}
              onChange={setChoice}
              runId={values.related_run_id}
              testId={values.related_test_id}
              traceId={values.related_trace_id}
              disabled={saving}
            />
          ) : (
            <p className="muted" style={{ margin: 0 }}>
              Optional. Logs, error details and version information can help the developers a great deal. You choose what to include and
              review it before anything is shared.
              {choice.selected.length > 0 ? ` ${choice.selected.length} kind(s) chosen.` : ""}
            </p>
          )}
        </Card>
        {error && <Notice tone="error">{error}</Notice>}
        <div className="row">
          <button type="submit" className="btn primary" disabled={saving}>
            {saving ? "Sending…" : "Send the report"}
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
