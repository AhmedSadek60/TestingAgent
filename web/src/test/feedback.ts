/** Answers of the Feedback & Support API as the interface's tests need them: complete enough to render, and changed by what a
 * test is about. */
export const OPTIONS = {
  feature_categories: ["Reporting and Analytics", "User Interface / UX", "Other"],
  issue_categories: ["Application Bug", "Test Execution Failure", "Suspected Security Issue", "Other"],
  feature_statuses: ["Submitted", "Needs Triage", "Under Review", "Planned", "In Progress", "Completed", "Declined", "Duplicate"],
  issue_statuses: ["Submitted", "Needs Triage", "Investigating", "Awaiting User", "In Progress", "Resolved", "Closed", "Duplicate"],
  closed_statuses: ["Completed", "Declined", "Duplicate", "Closed"],
  feature_priorities: ["low", "normal", "high", "critical_business_need"],
  priorities: ["low", "normal", "high", "critical"],
  severities: ["info", "low", "medium", "high", "critical"],
  frequencies: ["every_time", "frequently", "occasionally", "once", "unknown"],
  impacts: ["cosmetic", "slows_work", "prevents_workflow", "blocks_project", "incorrect_results", "data_loss_or_exposure", "unavailable", "other"],
  relation_kinds: ["duplicate_of", "related", "caused_by", "implements"],
  security_category: "Suspected Security Issue",
  max_text_chars: 20000,
  can_triage: false,
  attachment_extensions: ["png", "jpg", "txt", "log", "json", "pdf", "zip"],
  attachment_max_files: 3,
  attachment_max_file_mb: 1,
  attachment_max_total_mb: 5,
};
export const STAFF_OPTIONS = { ...OPTIONS, can_triage: true };

export const COUNTS = { total: 2, open: 2, unread: 1, unassigned: null, by_status: { Submitted: 2 }, by_type: { feature: 1, issue: 1 } };

export function summary(over: Record<string, unknown> = {}) {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    number: "FR-2026-00001",
    type: "feature",
    title: "Export reports as CSV",
    category: "Reporting and Analytics",
    status: "Submitted",
    visibility: "normal",
    requested_priority: "high",
    assigned_priority: null,
    severity: null,
    reporter: "alice",
    reporter_id: null,
    mine: true,
    assignee: null,
    labels: [],
    created_at: "2026-10-12T10:00:00Z",
    updated_at: "2026-10-12T10:00:00Z",
    last_activity_at: "2026-10-12T10:00:00Z",
    unread: false,
    comments: 0,
    ...over,
  };
}

export function detail(over: Record<string, unknown> = {}) {
  const base = summary(over);
  const isIssue = base.type === "issue";
  const full = {
    ...base,
    version: 1,
    description: "A spreadsheet would help.",
    feature: isIssue
      ? null
      : {
          problem_statement: "JSON cannot be opened in a spreadsheet",
          expected_behavior: "A CSV download",
          primary_use_case: "",
          why_important: "",
          users_affected: "",
          business_impact: "",
          use_frequency: "",
          current_workaround: "",
          proposed_solution: "",
          acceptance_criteria: "",
          related_area: "",
          dependencies: "",
        },
    issue: isIssue
      ? {
          impact_summary: "I cannot get results",
          expected_behavior: "The run ends in a few minutes",
          actual_behavior: "It says Processing for an hour",
          reproduction_steps: "1. Start a run\n2. Wait",
          not_reproducible: false,
          frequency: "every_time",
          first_observed_at: null,
          consistent: true,
          blocks_work: true,
          workaround: "",
          reported_impact: "blocks_project",
          ui_screen: "Runs",
          worker_job_id: null,
          provider_config_id: null,
        }
      : null,
    project_id: null,
    related_run_id: isIssue ? "run-42" : null,
    related_test_id: null,
    related_trace_id: null,
    related_result_id: null,
    related_report_id: null,
    related_target_id: null,
    app_version: null,
    build: null,
    resolved_at: null,
    closed_at: null,
    discussion: [],
    history: [
      { id: "h1", old_status: null, new_status: "Submitted", changed_by: "alice", by_staff: false, comment: "", at: "2026-10-12T10:00:00Z" },
    ],
    relations: [],
    statuses: isIssue ? OPTIONS.issue_statuses : OPTIONS.feature_statuses,
  };
  return { ...full, ...over }; // what a test says it is about wins over the defaults
}

export function comment(over: Record<string, unknown> = {}) {
  return {
    id: "c1",
    author: "AgentLab maintainer",
    author_is_staff: true,
    mine: false,
    visibility: "public",
    kind: "comment",
    body: "Thanks, we are looking at it.",
    created_at: "2026-10-12T11:00:00Z",
    ...over,
  };
}

export function attachment(over: Record<string, unknown> = {}) {
  return {
    id: "a1",
    name: "run.log",
    extension: "log",
    type: "text/plain",
    declared_type: "text/plain",
    size: 120,
    sha256: "f".repeat(64),
    visibility: "public",
    uploader: "alice",
    uploader_is_staff: false,
    mine: true,
    created_at: "2026-10-12T10:05:00Z",
    text_like: true,
    secrets: { status: "redacted", counts: { openai_key: 1 }, external_sharing_blocked: false, note: "" },
    scan: { status: "not_scanned", detail: "no malware scanner is configured" },
    archive: null,
    sanitized_available: true,
    ...over,
  };
}

export function available(over: Record<string, unknown> = {}) {
  return {
    sources: [
      { id: "environment", label: "Environment", description: "The version and what is switched on.", available: true, manual: false, reason: null, needs: [] },
      { id: "application_logs", label: "Application logs", description: "What the server logged.", available: true, manual: false, reason: null, needs: [] },
      {
        id: "backend_logs",
        label: "Backend logs",
        description: "The worker's log.",
        available: false,
        manual: false,
        reason: "No background worker has logged anything yet.",
        needs: [],
      },
      {
        id: "run_metadata",
        label: "Run metadata",
        description: "A run's status.",
        available: false,
        manual: false,
        reason: "Evaluation data is for maintainers only.",
        needs: ["run_id"],
      },
      { id: "browser", label: "Browser", description: "Console errors this tab saw.", available: true, manual: false, reason: null, needs: [] },
      {
        id: "screenshot",
        label: "Screenshot",
        description: "",
        available: false,
        manual: true,
        reason: "AgentLab cannot take a screenshot. Attach one as a file.",
        needs: [],
      },
    ],
    windows: ["last_5_minutes", "last_15_minutes", "last_30_minutes", "custom"],
    max_window_minutes: 360,
    consent_text: "The diagnostic information you attach will be available to AgentLab maintainers who handle your report.",
    limits: {},
    ...over,
  };
}

const redaction = (status = "clean", counts: Record<string, number> = {}) => ({
  status,
  counts,
  locations: [],
  external_sharing_blocked: status === "needs_review",
});

export function collection(over: Record<string, unknown> = {}) {
  return {
    id: "col-12345678",
    status: "draft",
    created_at: "2026-10-12T10:10:00Z",
    expires_at: "2026-10-13T10:10:00Z",
    window_from: "2026-10-12T09:55:00Z",
    window_to: "2026-10-12T10:10:00Z",
    items: [
      {
        id: "item-env",
        category: "environment",
        path: "environment.json",
        source: "AgentLab",
        outcome: "collected",
        detail: "",
        time_from: null,
        time_to: null,
        size: 500,
        truncated: false,
        included: false,
        redaction: redaction(),
        preview: '{"agentlab_version": "0.1.0"}',
      },
      {
        id: "item-logs",
        category: "application_logs",
        path: "application/relevant.log",
        source: "the API's captured log",
        outcome: "collected",
        detail: "",
        time_from: "2026-10-12T09:55:00Z",
        time_to: "2026-10-12T10:10:00Z",
        size: 900,
        truncated: false,
        included: false,
        redaction: redaction("redacted", { openai_key: 1 }),
        preview: "GET /runs answered 500 in 12 ms",
      },
    ],
    warnings: [],
    total_bytes: 1400,
    any_needs_review: false,
    submission_id: null,
    consent_at: null,
    ...over,
  };
}

export function handoff(over: Record<string, unknown> = {}) {
  return {
    id: "h-12345678",
    status: "generated",
    submission_id: "11111111-1111-4111-8111-111111111111",
    number: "IR-2026-00001",
    submission_version: 1,
    created_at: "2026-10-12T12:00:00Z",
    created_by: "maria",
    files: [
      { path: "CLAUDE_CODE_TASK.md", kind: "generated", size: 6000, sha256: "a".repeat(64), source: "this package", note: "Generated for this package." },
      { path: "README.md", kind: "generated", size: 1500, sha256: "b".repeat(64), source: "this package", note: "Generated." },
      {
        path: "evidence/selected-attachments/a1-run.log",
        kind: "sanitized_copy",
        size: 100,
        sha256: "c".repeat(64),
        source: "attachment a1",
        note: "Secrets masked. The original is kept privately.",
      },
    ],
    left_out: [{ what: "attachment screen.png", reason: "it cannot be sanitized (an image, document or archive); select it to put it in as uploaded" }],
    warnings: [],
    size: 9000,
    sha256: "d".repeat(64),
    external_sharing_blocked: false,
    includes_unsanitized: false,
    task: "# Task for Claude Code: IR-2026-00001\n\nAct as a senior software engineer working on AgentLab.\n",
    ...over,
  };
}
