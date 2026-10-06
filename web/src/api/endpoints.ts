import type { ApiClient } from "./client";
import type {
  Artifact,
  CancelResponse,
  Comparison,
  Credential,
  CredentialCreate,
  Discovery,
  Document,
  Environment,
  Finding,
  Health,
  Model,
  PlanOut,
  PlanRequest,
  Project,
  Provider,
  ProviderCheck,
  Report,
  ReportCreate,
  ReportFile,
  Review,
  ReviewRequest,
  RunAccepted,
  RunDetail,
  RunEvent,
  RunRequest,
  RunSummary,
  Scorecard,
  ScoringProfile,
  Settings,
  SkillDetail,
  SkillSummary,
  Target,
  TargetSpec,
  TestResult,
  Trace,
  TraceSummary,
  DiscoverRequest,
  ViewLink,
} from "./types";

const id = encodeURIComponent;

/** Every call the interface makes, in one place, typed from the API's OpenAPI document. */
export function createApi(client: ApiClient) {
  return {
    health: (signal?: AbortSignal) => client.get<Health>("/health", { signal }),
    environment: (live = false, signal?: AbortSignal) => client.get<Environment>("/environment", { query: { live }, signal }),
    settings: (signal?: AbortSignal) => client.get<Settings>("/settings", { signal }),

    projects: (signal?: AbortSignal) => client.get<Project[]>("/projects", { signal }),
    createProject: (body: { name: string; description?: string; objective?: string }) => client.post<Project>("/projects", body),

    targets: (project?: string, signal?: AbortSignal) => client.get<Target[]>("/targets", { query: { project }, signal }),
    target: (targetId: string, signal?: AbortSignal) => client.get<Target>(`/targets/${id(targetId)}`, { signal }),
    targetProfile: (targetId: string, signal?: AbortSignal) => client.get<Discovery>(`/targets/${id(targetId)}/profile`, { signal }),
    createTarget: (body: { project?: string; spec: TargetSpec }) => client.post<Target>("/targets", body),
    discover: (body: DiscoverRequest) => client.post<Discovery>("/discover", body),

    documents: (project?: string, signal?: AbortSignal) => client.get<Document[]>("/documents", { query: { project }, signal }),
    uploadDocument: (file: File, project?: string) => {
      const form = new FormData();
      form.append("file", file);
      if (project) form.append("project", project);
      return client.upload<Document>("/documents", form);
    },

    credentials: (signal?: AbortSignal) => client.get<Credential[]>("/credentials", { signal }),
    createCredential: (body: CredentialCreate) => client.post<Credential>("/credentials", body),
    rotateCredential: (name: string, secrets: Record<string, string>) =>
      client.post<Credential>(`/credentials/${id(name)}/rotate`, { secrets }),
    deleteCredential: (name: string) => client.delete<void>(`/credentials/${id(name)}`),

    providers: (signal?: AbortSignal) => client.get<Provider[]>("/providers", { signal }),
    checkProvider: (name: string, body: { model?: string | null; complete?: boolean } = {}) =>
      client.post<ProviderCheck>(`/providers/${id(name)}/check`, body),
    models: (provider?: string, signal?: AbortSignal) => client.get<Model[]>("/models", { query: { provider }, signal }),

    skills: (signal?: AbortSignal) => client.get<SkillSummary[]>("/skills", { signal }),
    skill: (name: string, signal?: AbortSignal) => client.get<SkillDetail>(`/skills/${id(name)}`, { signal }),
    scoringProfiles: (signal?: AbortSignal) => client.get<ScoringProfile[]>("/scoring-profiles", { signal }),

    createPlan: (body: PlanRequest) => client.post<PlanOut>("/test-plans", body),
    plan: (planId: string, signal?: AbortSignal) => client.get<PlanOut>(`/test-plans/${id(planId)}`, { signal }),
    runPlan: (runId: string, signal?: AbortSignal) => client.get<PlanOut>(`/test-runs/${id(runId)}/plan`, { signal }),

    startRun: (body: RunRequest) => client.post<RunAccepted>("/test-runs", body),
    runs: (query: { project?: string; status?: string; target?: string; kind?: string; limit?: number } = {}, signal?: AbortSignal) =>
      client.get<RunSummary[]>("/test-runs", { query, signal }),
    run: (runId: string, signal?: AbortSignal) => client.get<RunDetail>(`/test-runs/${id(runId)}`, { signal }),
    cancelRun: (runId: string, reason?: string) => client.post<CancelResponse>(`/test-runs/${id(runId)}/cancel`, reason ? { reason } : {}),
    results: (
      runId: string,
      query: { status?: string; category?: string; severity?: string; q?: string; limit?: number; offset?: number } = {},
      signal?: AbortSignal,
    ) => client.get<TestResult[]>(`/test-runs/${id(runId)}/results`, { query, signal }),
    result: (runId: string, testId: string, signal?: AbortSignal) =>
      client.get<TestResult>(`/test-runs/${id(runId)}/results/${id(testId)}`, { signal }),
    findings: (runId: string, query: { severity?: string; security?: boolean; status?: string } = {}, signal?: AbortSignal) =>
      client.get<Finding[]>(`/test-runs/${id(runId)}/findings`, { query, signal }),
    scorecard: (runId: string, signal?: AbortSignal) => client.get<Scorecard>(`/test-runs/${id(runId)}/scorecard`, { signal }),
    traces: (runId: string, testId?: string, signal?: AbortSignal) =>
      client.get<TraceSummary[]>(`/test-runs/${id(runId)}/traces`, { query: { test_id: testId }, signal }),
    trace: (runId: string, traceId: string, signal?: AbortSignal) => client.get<Trace>(`/test-runs/${id(runId)}/traces/${id(traceId)}`, { signal }),
    events: (
      runId: string,
      query: { after?: string; type?: string; test_id?: string; limit?: number } = {},
      signal?: AbortSignal,
    ) => client.get<RunEvent[]>(`/test-runs/${id(runId)}/events`, { query, signal }),
    artifacts: (runId: string, kind?: string, signal?: AbortSignal) =>
      client.get<Artifact[]>(`/test-runs/${id(runId)}/artifacts`, { query: { kind }, signal }),
    reviews: (runId: string, signal?: AbortSignal) => client.get<Review[]>(`/test-runs/${id(runId)}/reviews`, { signal }),
    review: (runId: string, body: ReviewRequest) => client.post<Review>(`/test-runs/${id(runId)}/reviews`, body),
    streamPath: (runId: string) => `/test-runs/${id(runId)}/stream`,

    reports: (runId: string, signal?: AbortSignal) => client.get<Report[]>(`/test-runs/${id(runId)}/reports`, { signal }),
    generateReport: (runId: string, body: ReportCreate) => client.post<Report>(`/test-runs/${id(runId)}/reports`, body),
    report: (reportId: string, signal?: AbortSignal) => client.get<Report>(`/reports/${id(reportId)}`, { signal }),
    exportReport: (reportId: string, format: ReportFile["format"], includeSensitive = false) =>
      client.post<{ report: Report; file: ReportFile; created_new_version: boolean }>(`/reports/${id(reportId)}/export`, {
        format,
        include_sensitive: includeSensitive,
      }),
    viewLink: (reportId: string) => client.post<ViewLink>(`/reports/${id(reportId)}/view-link`),
    reportFile: (reportId: string, format: string) => client.blob(`/reports/${id(reportId)}/files/${id(format)}`),
    artifact: (artifactId: string) => client.blob(`/artifacts/${id(artifactId)}`, { query: { inline: false } }),

    compare: (runA: string, runB: string, signal?: AbortSignal) =>
      client.get<Comparison>("/comparisons", { query: { run_a: runA, run_b: runB }, signal }),
  };
}

export type Api = ReturnType<typeof createApi>;
