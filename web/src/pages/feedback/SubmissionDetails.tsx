import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { describeError } from "../../api/errors";
import type { Attachment, DiagnosticCollection, SubmissionDetail } from "../../api/types";
import { AttachmentList } from "../../components/feedback/AttachmentList";
import { AttachmentUploader } from "../../components/feedback/AttachmentUploader";
import {
  DiagnosticCollectionPanel,
  NO_DIAGNOSTICS,
  diagnosticsReadiness,
  type DiagnosticChoice,
} from "../../components/feedback/DiagnosticCollectionPanel";
import { DiagnosticPreview } from "../../components/feedback/DiagnosticPreview";
import { EngineeringHandoff } from "../../components/feedback/EngineeringHandoff";
import { MaintainerTriagePanel } from "../../components/feedback/MaintainerTriagePanel";
import { RelationsPanel } from "../../components/feedback/RelationsPanel";
import { PrivateBadge, Section, TypeBadge, UserText, WorkflowBadge } from "../../components/feedback/shared";
import { SubmissionComments } from "../../components/feedback/SubmissionComments";
import { SubmissionTimeline } from "../../components/feedback/SubmissionTimeline";
import { Badge, Card, ErrorNote, KeyValue, Loading, Notice, PageHeader, saveBlob, useToast } from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { useUploads } from "../../hooks/useUploads";
import { CONTEXT_LABELS, FEATURE_PRIORITY_LABELS, FREQUENCY_LABELS, IMPACT_LABELS, PRIORITY_LABELS, label, type Context } from "../../lib/feedback";
import { formatTime } from "../../lib/format";
import { useSession } from "../../session";

const CONTEXT_FIELDS: [keyof Context, keyof SubmissionDetail][] = [
  ["project_id", "project_id"],
  ["related_target_id", "related_target_id"],
  ["related_run_id", "related_run_id"],
  ["related_test_id", "related_test_id"],
  ["related_result_id", "related_result_id"],
  ["related_trace_id", "related_trace_id"],
  ["related_report_id", "related_report_id"],
];

const yesNo = (value: boolean | null) => (value === null ? "Not said" : value ? "Yes" : "No");

function Reported({ s }: { s: SubmissionDetail }) {
  const f = s.feature;
  const i = s.issue;
  return (
    <div className="stack">
      {s.description && (
        <Section title="Description">
          <UserText text={s.description} />
        </Section>
      )}
      {f && (
        <>
          <Section title="The problem or opportunity">
            <UserText text={f.problem_statement} />
          </Section>
          {f.expected_behavior && (
            <Section title="Expected behavior">
              <UserText text={f.expected_behavior} />
            </Section>
          )}
          {f.primary_use_case && (
            <Section title="Primary use case">
              <UserText text={f.primary_use_case} />
            </Section>
          )}
          {(
            [
              ["Why it is important", f.why_important],
              ["Users or teams affected", f.users_affected],
              ["Business impact", f.business_impact],
              ["How often the use case comes up", f.use_frequency],
              ["Current workaround", f.current_workaround],
              ["Proposed solution", f.proposed_solution],
              ["Acceptance criteria", f.acceptance_criteria],
              ["Related page or feature", f.related_area],
              ["Dependencies", f.dependencies],
            ] as [string, string][]
          )
            .filter(([, text]) => text)
            .map(([title, text]) => (
              <Section key={title} title={title}>
                <UserText text={text} />
              </Section>
            ))}
        </>
      )}
      {i && (
        <>
          <div className="form-grid">
            <Section title="Expected: what the reporter believes AgentLab should have done">
              <UserText text={i.expected_behavior} empty="Not stated." />
            </Section>
            <Section title="Actual: what AgentLab did instead">
              <UserText text={i.actual_behavior} empty="Not stated." />
            </Section>
          </div>
          <Section title="How it affects their work">
            <UserText text={i.impact_summary} />
          </Section>
          <Section title="How to reproduce">
            {i.not_reproducible && <p style={{ margin: 0 }}>The reporter cannot reproduce it, or does not know how.</p>}
            <UserText text={i.reproduction_steps} empty="No steps were given." />
          </Section>
          {i.workaround && (
            <Section title="Workaround">
              <UserText text={i.workaround} />
            </Section>
          )}
        </>
      )}
    </div>
  );
}

/** One submission as the person may see it: what was sent, the conversation, the files and diagnostic information, the history,
 * and, for a maintainer, the triage, the links, the audit trail and the engineering handoff. */
export function SubmissionDetails() {
  const { ident = "" } = useParams();
  const { api } = useSession();
  const toast = useToast();
  const options = useAsync((signal) => api.feedbackOptions(signal), [api]);
  const staff = options.data?.can_triage === true;
  const sub = useAsync((signal) => api.submission(ident, signal), [api, ident]);
  const s = sub.data;
  const attachments = useAsync((signal) => api.attachments(s!.id, signal), [api, s?.id], { enabled: !!s });
  const collections = useAsync((signal) => api.submissionDiagnostics(s!.id, signal), [api, s?.id], { enabled: !!s });
  const assignees = useAsync((signal) => api.assignees(signal), [api], { enabled: staff });
  const audit = useAsync((signal) => api.auditTrail(s!.id, signal), [api, s?.id, s?.version], { enabled: !!s && staff });
  const uploads = useUploads(api, s?.id ?? null);
  const [choice, setChoice] = useState<DiagnosticChoice>(NO_DIAGNOSTICS);
  const [attachingDiag, setAttachingDiag] = useState(false);
  const [diagError, setDiagError] = useState<string | null>(null);

  // the person has now read what the maintainers wrote
  useEffect(() => {
    if (s?.mine && s.unread) void api.markSeen(s.id).catch(() => undefined);
  }, [api, s?.id, s?.mine, s?.unread]);

  // files sent from this page are listed with the others once they are there
  const finished = uploads.items.filter((i) => i.state === "done").map((i) => i.key);
  useEffect(() => {
    if (finished.length === 0) return;
    attachments.reload();
    uploads.forget(finished);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finished.join("|")]);

  const context = useMemo(() => (s ? CONTEXT_FIELDS.filter(([, key]) => s[key]).map(([k, key]) => [k, String(s[key])] as const) : []), [s]);

  if (sub.loading && !s) return <Loading />;
  if (!s) {
    return (
      <>
        <PageHeader title="Submission" />
        <ErrorNote error={sub.error ?? "That submission could not be found."} onRetry={sub.reload} />
        <p>
          <Link to="/feedback/submissions">Back to the list</Link>
        </p>
      </>
    );
  }

  const readiness = diagnosticsReadiness(choice);
  const attachDiagnostics = async () => {
    if (!readiness.attach || !choice.collection?.id) return;
    setAttachingDiag(true);
    setDiagError(null);
    try {
      await api.attachDiagnostics(s.id, { collection_id: choice.collection.id, include: choice.include, consent: true });
      setChoice(NO_DIAGNOSTICS);
      collections.reload();
      toast.push("good", "Diagnostic information was attached.");
    } catch (e) {
      setDiagError(describeError(e));
    } finally {
      setAttachingDiag(false);
    }
  };
  const bundle = async (c: DiagnosticCollection) => {
    if (!c.id) return;
    try {
      const file = await api.diagnosticBundle(c.id);
      saveBlob(file.blob, file.name ?? "diagnostics.zip");
    } catch (e) {
      toast.push("error", describeError(e));
    }
  };
  const canAdd = s.mine || staff;
  const stored: Attachment[] = attachments.data ?? [];
  const refresh = () => {
    sub.reload();
    attachments.reload();
  };

  return (
    <>
      <PageHeader
        title={s.number}
        subtitle={s.title}
        actions={
          <Link className="btn" to="/feedback/submissions">
            All submissions
          </Link>
        }
      />
      <div className="stack">
        {s.visibility === "security" && (
          <Notice tone="warn" title="This is a private report">
            Only the reporter and authorised maintainers can see it. It is never published automatically.
          </Notice>
        )}
        <Card title="Summary">
          <KeyValue
            items={[
              ["Type", <TypeBadge key="t" type={s.type} />],
              ["Status", <WorkflowBadge key="s" status={s.status} />],
              ["Category", s.category],
              ["Made", `${formatTime(s.created_at)} by ${s.mine ? "you" : s.reporter}`],
              ["Last activity", formatTime(s.last_activity_at)],
              ...(s.type === "feature" && s.requested_priority
                ? ([["Requested business priority", label(FEATURE_PRIORITY_LABELS, s.requested_priority)]] as [string, string][])
                : []),
              ...(s.issue
                ? ([
                    ["Reported impact", label(IMPACT_LABELS, s.issue.reported_impact)],
                    ["How often", label(FREQUENCY_LABELS, s.issue.frequency)],
                    ["Happens consistently", yesNo(s.issue.consistent)],
                    ["Blocks their work", yesNo(s.issue.blocks_work)],
                  ] as [string, string][])
                : []),
              ...(staff
                ? ([
                    ["Priority (maintainers)", s.assigned_priority ? label(PRIORITY_LABELS, s.assigned_priority) : "Not set"],
                    ["Severity", s.severity ?? "Not set"],
                    ["Assigned to", s.assignee?.name ?? "Nobody"],
                    ["Labels", s.labels.length ? s.labels.join(", ") : "None"],
                    ["Visibility", s.visibility === "security" ? <PrivateBadge key="v" /> : "Normal"],
                  ] as [string, React.ReactNode][])
                : []),
              ["AgentLab version", s.app_version ?? "Not said"],
            ]}
          />
        </Card>
        <Card title="What was reported">
          <Reported s={s} />
          {context.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <Section title="Where in AgentLab">
                <ul className="row" style={{ flexWrap: "wrap", listStyle: "none", margin: 0, padding: 0 }}>
                  {context.map(([key, value]) => (
                    <li key={key} className="tag">
                      {CONTEXT_LABELS[key]}: <span className="mono">{value}</span>
                    </li>
                  ))}
                </ul>
              </Section>
            </div>
          )}
        </Card>
        <Card title="Discussion">
          <SubmissionComments submissionId={s.id} discussion={s.discussion} staff={staff} onPosted={sub.reload} />
        </Card>
        <Card title="Files">
          <div className="stack">
            <AttachmentList
              submissionId={s.id}
              attachments={stored}
              canRemove={(a) => staff || a.mine}
              onChanged={refresh}
            />
            {canAdd && (
              <AttachmentUploader
                items={uploads.items}
                options={options.data}
                onAdd={uploads.add}
                onRemove={(key) => void uploads.remove(key)}
                onRetry={(key) => uploads.retry(key)}
              />
            )}
          </div>
        </Card>
        {s.type === "issue" && (
          <Card title="Diagnostic information">
            <div className="stack">
              {(collections.data ?? []).length === 0 && <p className="muted">None was attached.</p>}
              {(collections.data ?? []).map((c) => (
                <div key={c.id} className="stack">
                  <div className="row between" style={{ flexWrap: "wrap" }}>
                    <span>
                      Attached {formatTime(c.consent_at)} <Badge tone={c.any_needs_review ? "warn" : "good"}>{c.any_needs_review ? "needs review" : "masked"}</Badge>
                    </span>
                    <button type="button" className="btn small" onClick={() => void bundle(c)}>
                      Download as a ZIP
                    </button>
                  </div>
                  <DiagnosticPreview collection={c} include={[]} readOnly />
                </div>
              ))}
              {canAdd && (
                <details>
                  <summary>Attach more diagnostic information</summary>
                  <div className="stack" style={{ marginTop: 12 }}>
                    <DiagnosticCollectionPanel
                      value={choice}
                      onChange={setChoice}
                      runId={s.related_run_id ?? undefined}
                      testId={s.related_test_id ?? undefined}
                      traceId={s.related_trace_id ?? undefined}
                      disabled={attachingDiag}
                    />
                    {readiness.problem && <Notice tone="warn">{readiness.problem}</Notice>}
                    {diagError && <Notice tone="error">{diagError}</Notice>}
                    <div>
                      <button type="button" className="btn primary" disabled={!readiness.attach || attachingDiag} onClick={() => void attachDiagnostics()}>
                        Attach to this report
                      </button>
                    </div>
                  </div>
                </details>
              )}
            </div>
          </Card>
        )}
        <Card title="History">
          <SubmissionTimeline history={s.history} />
        </Card>
        {staff && options.data && (
          <>
            <Card title="Triage">
              <MaintainerTriagePanel
                submission={s}
                options={options.data}
                assignees={assignees.data ?? []}
                onChanged={(updated) => (updated ? sub.setData(updated) : sub.reload())}
              />
            </Card>
            <Card title="Links to other submissions">
              <RelationsPanel submission={s} options={options.data} onChanged={(updated) => sub.setData(updated)} />
            </Card>
            <Card title="Engineering handoff">
              <EngineeringHandoff submission={s} attachments={stored} />
            </Card>
            <Card title="Audit trail">
              <details onToggle={(event) => event.currentTarget.open && audit.reload()}>
                <summary>What was done to this submission, and by whom (never the text)</summary>
                {audit.data && (
                  <ul className="stack tight" style={{ listStyle: "none", margin: "12px 0 0", padding: 0 }}>
                    {audit.data.map((e, index) => (
                      <li key={`${e.at}-${index}`} className="list-item small">
                        <span className="mono">{e.event}</span> by {e.actor} <span className="muted">{formatTime(e.at)}</span>
                        {Object.keys(e.detail).length > 0 && <div className="muted mono">{JSON.stringify(e.detail)}</div>}
                      </li>
                    ))}
                  </ul>
                )}
                {audit.error && <Notice tone="error">{audit.error}</Notice>}
              </details>
            </Card>
          </>
        )}
      </div>
    </>
  );
}
