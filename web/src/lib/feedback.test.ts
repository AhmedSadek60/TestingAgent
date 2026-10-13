import { describe, expect, it } from "vitest";
import {
  clearDraft,
  clearFeedbackDrafts,
  contextFromSearch,
  emptyFeature,
  emptyIssue,
  featureProblems,
  featureRequest,
  formatBytes,
  hasDraft,
  issueProblems,
  issueRequest,
  loadDraft,
  reportLink,
  saveDraft,
  workflowTone,
} from "./feedback";

describe("what a form needs before it is sent", () => {
  it("asks a feature request for a title, a category and the problem, and nothing else", () => {
    expect(Object.keys(featureProblems(emptyFeature())).sort()).toEqual(["category", "problem_statement", "title"]);
    const ok = { ...emptyFeature(), title: "Export reports", category: "Other", problem_statement: "JSON is hard to read" };
    expect(featureProblems(ok)).toEqual({});
  });

  it("asks an issue report for a title, a kind, what happened and how it affects the work", () => {
    expect(Object.keys(issueProblems(emptyIssue())).sort()).toEqual(["category", "description", "impact_summary", "title"]);
    const ok = { ...emptyIssue(), title: "A run hangs", category: "Application Bug", description: "It hangs", impact_summary: "I wait" };
    expect(issueProblems(ok)).toEqual({});
  });

  it("does not make a person invent steps: not being able to reproduce is an answer", () => {
    const values = { ...emptyIssue(), title: "A run hangs", category: "Application Bug", description: "It hangs", impact_summary: "I wait", not_reproducible: true };
    expect(issueProblems(values)).toEqual({});
    const sent = issueRequest({ ...values, reproduction_steps: "left over text" }, "Suspected Security Issue");
    expect(sent.issue).toMatchObject({ not_reproducible: true, reproduction_steps: "" });
  });

  it("refuses an identifier the server would refuse, before it is sent", () => {
    const values = { ...emptyIssue(), title: "A run hangs", category: "Other", description: "d", impact_summary: "i", related_run_id: "../etc/passwd" };
    expect(issueProblems(values).related_run_id).toMatch(/must be an identifier/);
  });
});

describe("how a form becomes a request", () => {
  it("sends only what was filled in, trimmed, with the structure the API takes", () => {
    const values = {
      ...emptyFeature({ related_run_id: "run-1" }),
      title: "  Export reports ",
      category: "Reporting and Analytics",
      problem_statement: " JSON is hard ",
      requested_priority: "critical_business_need",
    };
    const body = featureRequest(values);
    expect(body).toMatchObject({
      type: "feature",
      title: "Export reports",
      category: "Reporting and Analytics",
      related_run_id: "run-1",
      project_id: null,
      feature: { problem_statement: "JSON is hard", requested_priority: "critical_business_need" },
    });
    expect(body.issue).toBeUndefined();
  });

  it("marks an issue in the security category as a suspected security issue, and no other", () => {
    const base = { ...emptyIssue(), title: "Tokens in a page", description: "d", impact_summary: "i" };
    expect(issueRequest({ ...base, category: "Suspected Security Issue" }, "Suspected Security Issue").suspected_security).toBe(true);
    expect(issueRequest({ ...base, category: "Application Bug" }, "Suspected Security Issue").suspected_security).toBe(false);
  });

  it("turns the yes and no of a select into true, false or unknown, and a time into an ISO time", () => {
    const base = { ...emptyIssue(), title: "x y z", category: "Other", description: "d", impact_summary: "i" };
    const unknown = issueRequest(base, "S").issue!;
    expect([unknown.consistent, unknown.blocks_work, unknown.first_observed_at]).toEqual([null, null, null]);
    const known = issueRequest({ ...base, consistent: "yes", blocks_work: "no", first_observed_at: "2026-10-12T09:30" }, "S").issue!;
    expect(known.consistent).toBe(true);
    expect(known.blocks_work).toBe(false);
    expect(known.first_observed_at).toMatch(/^2026-10-12T\d\d:30:00\.000Z$/);
  });
});

describe("the context a report is opened with", () => {
  it("keeps identifiers and drops anything that is not one", () => {
    const context = contextFromSearch("run=run-42&test=CONV-001&trace=<script>&screen=Test%20result&job=../x&unknown=1");
    expect(context.related_run_id).toBe("run-42");
    expect(context.related_test_id).toBe("CONV-001");
    expect(context.related_trace_id).toBe("");
    expect(context.worker_job_id).toBe("");
    expect(context.ui_screen).toBe("Test result");
  });

  it("builds the link from identifiers only, and leaves out what is empty", () => {
    const link = reportLink({ related_run_id: "run-42", related_test_id: "", ui_screen: "Trace" });
    expect(link).toBe("/feedback/issue?run=run-42&screen=Trace");
    expect(contextFromSearch(link.split("?")[1]!)).toMatchObject({ related_run_id: "run-42", ui_screen: "Trace" });
    expect(reportLink({})).toBe("/feedback/issue");
  });
});

describe("the draft a tab keeps", () => {
  it("is kept per kind, comes back as typed, and goes with a single call", () => {
    const values = { ...emptyFeature(), title: "Half written" };
    expect(hasDraft("feature")).toBe(false);
    saveDraft("feature", values);
    expect(hasDraft("feature")).toBe(true);
    expect(loadDraft("feature", emptyFeature()).title).toBe("Half written");
    expect(loadDraft("issue", emptyIssue()).title).toBe("");
    clearDraft("feature");
    expect(loadDraft("feature", emptyFeature()).title).toBe("");
    saveDraft("issue", emptyIssue());
    clearFeedbackDrafts();
    expect(hasDraft("issue")).toBe(false);
  });

  it("ignores what is not a draft of this form: a field of the wrong kind, a field that is not there, text that is not JSON", () => {
    window.sessionStorage.setItem("agentlab.feedback.draft.feature", JSON.stringify({ title: 5, nothing: "x", category: "Other" }));
    const loaded = loadDraft("feature", emptyFeature());
    expect(loaded.title).toBe("");
    expect(loaded.category).toBe("Other");
    expect("nothing" in loaded).toBe(false);
    window.sessionStorage.setItem("agentlab.feedback.draft.feature", "{not json");
    expect(loadDraft("feature", emptyFeature()).title).toBe("");
  });
});

describe("words", () => {
  it("shows sizes in the unit a person reads", () => {
    expect(formatBytes(12)).toBe("12 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });

  it("tones a status by what its name says, so a changed workflow still reads right", () => {
    expect(workflowTone("Completed")).toBe("good");
    expect(workflowTone("Awaiting User")).toBe("warn");
    expect(workflowTone("Investigating")).toBe("info");
    expect(workflowTone("Declined")).toBe("neutral");
    expect(workflowTone("Something New")).toBe("neutral");
  });
});
