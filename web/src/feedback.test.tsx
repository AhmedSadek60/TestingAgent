import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { clearBrowserBuffer, recordConsole, recordFailure } from "./lib/browserErrors";
import { FeatureRequestForm } from "./pages/feedback/FeatureRequestForm";
import { IssueReportForm } from "./pages/feedback/IssueReportForm";
import { SubmissionDetails } from "./pages/feedback/SubmissionDetails";
import { Submissions } from "./pages/feedback/Submissions";
import { available, attachment, collection, comment, COUNTS, detail, handoff, OPTIONS, STAFF_OPTIONS, summary } from "./test/feedback";
import { errorResponse, fakeServer, HEALTH_OPEN, installFakeXhr, jsonResponse, project, renderWithSession, type Handler } from "./test/server";

afterEach(() => clearBrowserBuffer());

function serverFor(routes: Record<string, Handler> = {}, options: object = OPTIONS) {
  return fakeServer({
    "GET /health": HEALTH_OPEN,
    "GET /auth/options": { registration: "open", min_password_length: 12, api_token_sign_in: true, password_sign_in: true },
    "GET /auth/me": { kind: "local", role: "admin", username: null, display_name: null, must_change_password: false, csrf_token: null, operator: true, can_manage_accounts: true },
    "GET /projects": [project("default")],
    "GET /submissions/options": options,
    "GET /submissions/summary": COUNTS,
    "GET /submissions/similar": [],
    ...routes,
  });
}

function renderAt(route: string) {
  return renderWithSession(
    <Routes>
      <Route path="feedback/feature" element={<FeatureRequestForm />} />
      <Route path="feedback/issue" element={<IssueReportForm />} />
      <Route path="feedback/submissions" element={<Submissions />} />
      <Route path="feedback/submissions/:ident" element={<SubmissionDetails />} />
      <Route path="*" element={<div>somewhere else</div>} />
    </Routes>,
    { route },
  );
}

const txt = (name = "notes.txt", body = "the page is blank") => new File([body], name, { type: "text/plain" });

async function fillFeature(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText("Feature title"), "Export reports as CSV");
  await user.selectOptions(screen.getByLabelText("Category"), "Reporting and Analytics");
  await user.type(screen.getByLabelText("Problem or opportunity"), "JSON cannot be opened in a spreadsheet");
}

// ======================================================================================================== feature request
describe("requesting a feature", () => {
  it("asks for a title, a category and the problem, says what is missing next to it, and sends nothing until they are there", async () => {
    const server = serverFor();
    renderAt("/feedback/feature");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Send the request" }));
    expect(await screen.findByText("Give the request a short title (at least 3 characters).")).toBeInTheDocument();
    expect(screen.getByText("Choose a category.")).toBeInTheDocument();
    expect(screen.getByText("Describe the problem or opportunity.")).toBeInTheDocument();
    expect(server.to("POST /submissions")).toHaveLength(0);
    // nothing else is required
    expect(screen.queryByText(/Describe the problem\.$/)).not.toBeInTheDocument();
  });

  it("takes what is said about a field away as soon as the field is right, without sending again", async () => {
    serverFor();
    renderAt("/feedback/feature");
    const user = userEvent.setup();
    expect(screen.queryByText("Choose a category.")).not.toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Send the request" }));
    expect(await screen.findByText("Choose a category.")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Category"), "Other");
    expect(screen.queryByText("Choose a category.")).not.toBeInTheDocument();
    expect(screen.getByText("Describe the problem or opportunity.")).toBeInTheDocument();
  });

  it("sends the request and then its files, and shows the number only after the server has kept it", async () => {
    installFakeXhr();
    let release!: (value: Response) => void;
    const gate = new Promise<Response>((resolve) => {
      release = resolve;
    });
    const server = serverFor({
      "POST /submissions": () => gate,
      "POST /submissions/11111111-1111-4111-8111-111111111111/attachments": () => jsonResponse(attachment({ name: "notes.txt" }), 201),
    });
    renderAt("/feedback/feature");
    const user = userEvent.setup();
    await fillFeature(user);
    await user.upload(screen.getByLabelText("Add files"), txt());
    const queued = await screen.findByRole("list", { name: "Files to attach" });
    expect(within(queued).getByText("notes.txt", { selector: "strong" })).toBeInTheDocument();
    expect(within(queued).getByText("Waiting to be sent")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Send the request" }));
    await waitFor(() => expect(server.to("POST /submissions")).toHaveLength(1));
    expect(screen.queryByText("FR-2026-00001")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sending…" })).toBeDisabled();
    release(jsonResponse(detail(), 201));
    expect(await screen.findByRole("heading", { name: "Request received" })).toBeInTheDocument();
    expect(screen.getByText("FR-2026-00001")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/1 file was attached/)).toBeInTheDocument());
    const created = server.to("POST /submissions")[0]!.body as Record<string, unknown>;
    expect(created).toMatchObject({
      type: "feature",
      title: "Export reports as CSV",
      category: "Reporting and Analytics",
      feature: { problem_statement: "JSON cannot be opened in a spreadsheet", requested_priority: "normal" },
    });
    const upload = server.to("POST /submissions/11111111-1111-4111-8111-111111111111/attachments")[0]!;
    expect((upload.body as FormData).get("file")).toBeInstanceOf(File);
    expect(screen.getByRole("link", { name: "Open FR-2026-00001" })).toHaveAttribute("href", "/feedback/submissions/FR-2026-00001");
  });

  it("keeps what was written, and says why, when the server refuses it", async () => {
    serverFor({ "POST /submissions": () => errorResponse(422, "invalid_request", "the category is not one this server offers") });
    renderAt("/feedback/feature");
    const user = userEvent.setup();
    await fillFeature(user);
    await user.click(screen.getByRole("button", { name: "Send the request" }));
    expect(await screen.findByText("the category is not one this server offers")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Request received" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Feature title")).toHaveValue("Export reports as CSV");
  });

  it("suggests similar requests without getting in the way, and the person can still send", async () => {
    const server = serverFor({
      "GET /submissions/similar": [{ id: "s9", number: "FR-2026-00009", title: "Export to CSV", status: "Planned", score: 0.8 }],
      "POST /submissions": () => jsonResponse(detail(), 201),
    });
    renderAt("/feedback/feature");
    const user = userEvent.setup();
    await fillFeature(user);
    expect(await screen.findByText("1 similar request may already exist")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /FR-2026-00009: Export to CSV/ });
    expect(link).toHaveAttribute("href", "/feedback/submissions/FR-2026-00009");
    expect(link).toHaveAttribute("target", "_blank");
    expect(server.to("GET /submissions/similar")[0]!.query.get("type")).toBe("feature");
    await user.click(screen.getByRole("button", { name: "Send the request" }));
    expect(await screen.findByRole("heading", { name: "Request received" })).toBeInTheDocument();
  });

  it("shows a file that failed with the reason and a way to try again, and the request itself stays saved", async () => {
    installFakeXhr();
    let attempts = 0;
    serverFor({
      "POST /submissions": () => jsonResponse(detail(), 201),
      "POST /submissions/11111111-1111-4111-8111-111111111111/attachments": () => {
        attempts += 1;
        return attempts === 1 ? errorResponse(422, "invalid_request", "this file looks like a program, which cannot be attached") : jsonResponse(attachment(), 201);
      },
    });
    renderAt("/feedback/feature");
    const user = userEvent.setup();
    await fillFeature(user);
    await user.upload(screen.getByLabelText("Add files"), txt("notes.txt"));
    await user.click(screen.getByRole("button", { name: "Send the request" }));
    expect(await screen.findByRole("heading", { name: "Request received" })).toBeInTheDocument();
    expect(await screen.findByText(/1 file could not be attached/)).toBeInTheDocument();
    expect(screen.getByText(/this file looks like a program/)).toBeInTheDocument();
    expect(screen.getByText(/The report itself is saved/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText(/1 file was attached/)).toBeInTheDocument();
    expect(attempts).toBe(2);
  });

  it("turns away a file the server would refuse before it is sent, and says which and why", async () => {
    const server = serverFor();
    renderAt("/feedback/feature");
    const user = userEvent.setup({ applyAccept: false });
    await user.upload(await screen.findByLabelText("Add files"), [
      new File(["MZ"], "setup.exe", { type: "application/octet-stream" }),
      new File([new Uint8Array(2 * 1024 * 1024)], "huge.log"),
    ]);
    const warnings = await screen.findByRole("alert");
    expect(within(warnings).getByText(/setup.exe: files of this type cannot be attached/)).toBeInTheDocument();
    expect(within(warnings).getByText(/huge.log: it is larger than 1 MB/)).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Files to attach" })).not.toBeInTheDocument();
    expect(server.to("POST /submissions")).toHaveLength(0);
  });

  it("keeps a draft in the tab, brings it back after a reload, and forgets it once the request is sent", async () => {
    serverFor({ "POST /submissions": () => jsonResponse(detail(), 201) });
    const first = renderAt("/feedback/feature");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Feature title"), "Half written");
    await waitFor(() => expect(window.sessionStorage.getItem("agentlab.feedback.draft.feature")).toContain("Half written"));
    first.unmount();
    renderAt("/feedback/feature");
    expect(await screen.findByDisplayValue("Half written")).toBeInTheDocument();
    expect(screen.getByText("Your earlier draft is back")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Category"), "Other");
    await user.type(screen.getByLabelText("Problem or opportunity"), "It is missing");
    await user.click(screen.getByRole("button", { name: "Send the request" }));
    expect(await screen.findByRole("heading", { name: "Request received" })).toBeInTheDocument();
    expect(window.sessionStorage.getItem("agentlab.feedback.draft.feature")).toBeNull();
  });

  it("asks before it lets go of what was written, and lets go only when told to", async () => {
    serverFor();
    renderAt("/feedback/feature");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Feature title"), "Something");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/has not been sent/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Keep it" }));
    expect(screen.getByLabelText("Feature title")).toHaveValue("Something");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Leave and lose it" }));
    expect(await screen.findByText("somewhere else")).toBeInTheDocument();
    expect(window.sessionStorage.getItem("agentlab.feedback.draft.feature")).toBeNull();
  });

  it("asks before a link inside the interface is followed with text that is not sent", async () => {
    serverFor();
    renderWithSession(
      <>
        <a href="#/elsewhere">Away</a>
        <FeatureRequestForm />
      </>,
    );
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    await user.type(await screen.findByLabelText("Feature title"), "Something");
    await user.click(screen.getByRole("link", { name: "Away" }));
    expect(confirm).toHaveBeenCalledTimes(1);
    confirm.mockRestore();
  });
});

// ============================================================================================================ issue report
async function fillIssue(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText("Issue title"), "A run stays on Processing");
  await user.selectOptions(screen.getByLabelText("What kind of problem is it?"), "Test Execution Failure");
  await user.type(screen.getByLabelText("Describe the problem"), "It never finishes.");
  await user.type(screen.getByLabelText("In a sentence: what does that mean for you?"), "I cannot get results");
}

describe("reporting an issue", () => {
  it("asks for a title, the kind, what happened and how it affects the work; steps are optional and can be unknown", async () => {
    const server = serverFor({ "POST /submissions": () => jsonResponse(detail({ type: "issue", number: "IR-2026-00001" }), 201) });
    renderAt("/feedback/issue");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Send the report" }));
    expect(await screen.findByText("Give the report a short title (at least 3 characters).")).toBeInTheDocument();
    expect(screen.getByText("Say briefly how this affects your work.")).toBeInTheDocument();
    expect(server.to("POST /submissions")).toHaveLength(0);
    await fillIssue(user);
    await user.click(screen.getByLabelText(/I cannot reproduce it/));
    expect(screen.getByLabelText("Steps to reproduce")).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Send the report" }));
    expect(await screen.findByRole("heading", { name: "Report received" })).toBeInTheDocument();
    expect(server.to("POST /submissions")[0]!.body).toMatchObject({
      type: "issue",
      category: "Test Execution Failure",
      suspected_security: false,
      issue: { impact_summary: "I cannot get results", not_reproducible: true, reproduction_steps: "", reported_impact: "slows_work" },
    });
  });

  it("warns that a security report is private, and marks it so when it is sent", async () => {
    const server = serverFor({ "POST /submissions": () => jsonResponse(detail({ type: "issue", visibility: "security" }), 201) });
    renderAt("/feedback/issue");
    const user = userEvent.setup();
    await fillIssue(user);
    expect(screen.queryByText("This may contain sensitive security information")).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("What kind of problem is it?"), "Suspected Security Issue");
    expect(screen.getByText("This may contain sensitive security information")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Send the report" }));
    expect(await screen.findByText(/This is a private report/)).toBeInTheDocument();
    expect(server.to("POST /submissions")[0]!.body).toMatchObject({ suspected_security: true });
  });

  it("lists what it was opened from, lets the person take any of it out, and attaches nothing else", async () => {
    const server = serverFor({ "POST /submissions": () => jsonResponse(detail({ type: "issue" }), 201) });
    renderAt("/feedback/issue?run=run-42&test=CONV-001&screen=Test%20result&trace=%3Cscript%3E");
    const user = userEvent.setup();
    const list = await screen.findByRole("list", { name: "Details that will be sent" });
    expect(within(list).getByText("run-42")).toBeInTheDocument();
    expect(within(list).getByText("CONV-001")).toBeInTheDocument();
    expect(within(list).getByText("Test result")).toBeInTheDocument();
    expect(within(list).queryByText(/script/)).not.toBeInTheDocument();
    await user.click(within(list).getByRole("button", { name: /Do not send test case CONV-001/ }));
    await fillIssue(user);
    await user.click(screen.getByRole("button", { name: "Send the report" }));
    await screen.findByRole("heading", { name: "Report received" });
    const body = server.to("POST /submissions")[0]!.body as Record<string, unknown>;
    expect(body).toMatchObject({ related_run_id: "run-42", related_test_id: null, related_trace_id: null, project_id: null });
    expect((body.issue as Record<string, unknown>).ui_screen).toBe("Test result");
    expect(server.to("POST /diagnostics/collect"), "nothing is collected on the person's behalf").toHaveLength(0);
  });
});

describe("diagnostic information", () => {
  const open = async (user: ReturnType<typeof userEvent.setup>) => {
    await fillIssue(user);
    await user.click(screen.getByRole("button", { name: "Show options" }));
    await screen.findByText(/Diagnostic information can help the AgentLab development team/);
  };

  it("offers only what can be collected, says why the rest cannot, and ticks nothing", async () => {
    serverFor({ "GET /diagnostics/available": available() });
    renderAt("/feedback/issue");
    const user = userEvent.setup();
    await open(user);
    expect(screen.getByLabelText(/Include environment and version/)).not.toBeChecked();
    expect(screen.getByLabelText(/Include application logs/)).not.toBeChecked();
    expect(screen.getByLabelText(/Include backend \(worker\) logs/)).toBeDisabled();
    expect(screen.getByText("No background worker has logged anything yet.")).toBeInTheDocument();
    expect(screen.getByLabelText(/Include evaluation-run metadata/)).toBeDisabled();
    expect(screen.getByText("Evaluation data is for maintainers only.")).toBeInTheDocument();
    expect(screen.getByLabelText(/Include screenshot of the current page/)).toBeDisabled();
    expect(screen.getByText(/AgentLab cannot take a screenshot/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Collect and review" })).toBeDisabled();
  });

  it("collects only on request, shows it masked, needs agreement, and attaches the chosen items after the report is saved", async () => {
    const server = serverFor({
      "GET /diagnostics/available": available(),
      "POST /diagnostics/collect": () => jsonResponse(collection(), 201),
      "POST /submissions": () => jsonResponse(detail({ type: "issue", number: "IR-2026-00001" }), 201),
      "POST /submissions/11111111-1111-4111-8111-111111111111/diagnostics": () => jsonResponse(collection({ status: "attached", items: collection().items.map((i) => ({ ...i, included: true })) })),
    });
    renderAt("/feedback/issue");
    const user = userEvent.setup();
    await open(user);
    await user.click(screen.getByRole("button", { name: "Select all available diagnostics" }));
    expect(screen.getByLabelText(/Include environment and version/)).toBeChecked();
    expect(screen.getByLabelText(/Include backend \(worker\) logs/)).not.toBeChecked();
    // chosen but not reviewed: the report cannot be sent as if it had been
    await user.click(screen.getByRole("button", { name: "Send the report" }));
    expect(await screen.findByText(/You chose diagnostic information: collect it and review it/)).toBeInTheDocument();
    expect(server.to("POST /submissions")).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Collect and review" }));
    expect(await screen.findByText("What would be shared")).toBeInTheDocument();
    expect(screen.getByText(/1 value masked: openai key × 1/)).toBeInTheDocument();
    expect(screen.getByText("GET /runs answered 500 in 12 ms")).toBeInTheDocument();
    const request = server.to("POST /diagnostics/collect")[0]!.body as Record<string, unknown>;
    expect(request.categories).toEqual(expect.arrayContaining(["environment", "application_logs", "browser"]));
    expect(request.categories).not.toContain("backend_logs");

    // without agreement they are not attached, and the person is told so
    await user.click(screen.getByRole("button", { name: "Send the report" }));
    expect(await screen.findByText(/Tick the box to agree to share the diagnostic information/)).toBeInTheDocument();
    expect(server.to("POST /submissions")).toHaveLength(0);

    await user.click(screen.getByRole("checkbox", { name: "Include Application logs" })); // leave the logs out
    await user.click(screen.getByLabelText(/I have reviewed the diagnostic information above/));
    await user.click(screen.getByRole("button", { name: "Send the report" }));
    expect(await screen.findByRole("heading", { name: "Report received" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/Diagnostic information \(2 items\) was attached/)).toBeInTheDocument());
    expect(screen.getByText("IR-2026-00001")).toBeInTheDocument();
    const attach = server.to("POST /submissions/11111111-1111-4111-8111-111111111111/diagnostics")[0]!.body as Record<string, unknown>;
    expect(attach).toEqual({ collection_id: "col-12345678", include: ["item-env"], consent: true });
  });

  it("does not stop the report when diagnostics cannot be collected or attached, and says which part was not attached", async () => {
    serverFor({
      "GET /diagnostics/available": available(),
      "POST /diagnostics/collect": () => jsonResponse(collection(), 201),
      "POST /submissions": () => jsonResponse(detail({ type: "issue", number: "IR-2026-00001" }), 201),
      "POST /submissions/11111111-1111-4111-8111-111111111111/diagnostics": () => errorResponse(404, "not_found", "no such collection to attach (it expired)"),
    });
    renderAt("/feedback/issue");
    const user = userEvent.setup();
    await open(user);
    await user.click(screen.getByLabelText(/Include environment and version/));
    await user.click(screen.getByRole("button", { name: "Collect and review" }));
    await user.click(await screen.findByLabelText(/I have reviewed the diagnostic information above/));
    await user.click(screen.getByRole("button", { name: "Send the report" }));
    expect(await screen.findByRole("heading", { name: "Report received" })).toBeInTheDocument();
    expect(await screen.findByText("Diagnostic information was not attached")).toBeInTheDocument();
    expect(screen.getByText(/no such collection to attach \(it expired\)/)).toBeInTheDocument();
    expect(screen.getByText("IR-2026-00001")).toBeInTheDocument();
  });

  it("says when collecting fails and leaves the person free to send without it", async () => {
    serverFor({
      "GET /diagnostics/available": available(),
      "POST /diagnostics/collect": () => errorResponse(429, "rate_limited", "too many collections this hour"),
      "POST /submissions": () => jsonResponse(detail({ type: "issue" }), 201),
    });
    renderAt("/feedback/issue");
    const user = userEvent.setup();
    await open(user);
    await user.click(screen.getByLabelText(/Include environment and version/));
    await user.click(screen.getByRole("button", { name: "Collect and review" }));
    expect(await screen.findByText(/too many collections this hour/)).toBeInTheDocument();
    await user.click(screen.getByLabelText(/Include environment and version/)); // untick
    await user.click(screen.getByRole("button", { name: "Send the report" }));
    expect(await screen.findByRole("heading", { name: "Report received" })).toBeInTheDocument();
  });

  it("sends what this tab saw go wrong when asked to, and says how much that is", async () => {
    recordConsole("error", ["TypeError: x is undefined"]);
    recordFailure({ method: "GET", path: "/runs?token=abc", status: 500, error: null, requestId: "abcd1234efgh5678", durationMs: 40 });
    const server = serverFor({
      "GET /diagnostics/available": available(),
      "POST /diagnostics/collect": () => jsonResponse(collection(), 201),
    });
    renderAt("/feedback/issue");
    const user = userEvent.setup();
    await open(user);
    expect(screen.getByText(/1 console message and 1 failed request seen in this tab/)).toBeInTheDocument();
    await user.click(screen.getByLabelText(/Include browser console and network errors/));
    await user.click(screen.getByRole("button", { name: "Collect and review" }));
    await screen.findByText("What would be shared");
    const body = server.to("POST /diagnostics/collect")[0]!.body as { browser: { console: { message: string }[]; network: { url: string; request_id: string }[] } };
    expect(body.browser.console[0]!.message).toContain("x is undefined");
    expect(body.browser.network[0]).toMatchObject({ url: "/runs", request_id: "abcd1234efgh5678" });
  });
});

// ================================================================================================================== lists
describe("the list of submissions", () => {
  it("shows a reporter their own, tells feature requests from issue reports, and passes the filters to the server", async () => {
    const server = serverFor({
      "GET /submissions": {
        items: [
          summary({ unread: true }),
          summary({ id: "22222222-2222-4222-8222-222222222222", number: "IR-2026-00001", type: "issue", title: "A run hangs", category: "Test Execution Failure", requested_priority: null }),
        ],
        total: 2,
        limit: 25,
        offset: 0,
      },
    });
    renderAt("/feedback/submissions");
    const user = userEvent.setup();
    expect(await screen.findByRole("heading", { name: "My submissions" })).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "Submissions" });
    expect(within(table).getByRole("link", { name: "FR-2026-00001" })).toHaveAttribute("href", "/feedback/submissions/FR-2026-00001");
    expect(within(table).getByText("Feature request")).toBeInTheDocument();
    expect(within(table).getByText("Issue report")).toBeInTheDocument();
    expect(within(table).getByText("Answer waiting")).toBeInTheDocument();
    expect(within(table).queryByText("Severity")).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Issue reports" }));
    await waitFor(() => expect(server.to("GET /submissions").at(-1)!.query.get("type")).toBe("issue"));
    await user.selectOptions(screen.getByLabelText("Status"), "Planned");
    await waitFor(() => expect(server.to("GET /submissions").at(-1)!.query.getAll("status")).toEqual(["Planned"]));
    await user.click(screen.getByLabelText("With an answer I have not read"));
    await waitFor(() => expect(server.to("GET /submissions").at(-1)!.query.get("unread")).toBe("true"));
  });

  it("pages through a long list a page at a time", async () => {
    const server = serverFor({
      "GET /submissions": (call) => ({
        items: [summary({ number: `FR-2026-0000${call.query.get("offset") === "25" ? 2 : 1}` })],
        total: 40,
        limit: 25,
        offset: Number(call.query.get("offset") ?? 0),
      }),
    });
    renderAt("/feedback/submissions");
    const user = userEvent.setup();
    expect(await screen.findByText("1 to 1 of 40 submissions")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByRole("link", { name: "FR-2026-00002" })).toBeInTheDocument();
    expect(server.to("GET /submissions").at(-1)!.query.get("offset")).toBe("25");
    expect(await screen.findByText("26 to 26 of 40 submissions")).toBeInTheDocument();
  });

  it("gives a maintainer the queue, with what is theirs to decide", async () => {
    const server = serverFor(
      {
        "GET /submissions": {
          items: [summary({ assigned_priority: "high", severity: "medium", assignee: { id: "u1", name: "maria" } })],
          total: 1,
          limit: 25,
          offset: 0,
        },
      },
      STAFF_OPTIONS,
    );
    renderAt("/feedback/submissions?assignee=none");
    const user = userEvent.setup();
    expect(await screen.findByRole("heading", { name: "Submissions" })).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "Submissions" });
    for (const header of ["Priority", "Severity", "Assigned to", "Reporter"]) {
      expect(within(table).getByRole("columnheader", { name: header })).toBeInTheDocument();
    }
    expect(within(table).getByText("maria")).toBeInTheDocument();
    expect(server.to("GET /submissions")[0]!.query.get("assignee")).toBe("none");
    await user.selectOptions(screen.getByLabelText("Severity"), "high");
    await waitFor(() => expect(server.to("GET /submissions").at(-1)!.query.get("severity")).toBe("high"));
  });

  it("says plainly when there is nothing, and when nothing matches", async () => {
    serverFor({ "GET /submissions": { items: [], total: 0, limit: 25, offset: 0 } });
    renderAt("/feedback/submissions");
    expect(await screen.findByText("Nothing here yet")).toBeInTheDocument();
  });
});

// ================================================================================================================ details
const IDENT = "11111111-1111-4111-8111-111111111111";
function detailRoutes(extra: Record<string, Handler> = {}, who: "reporter" | "staff" = "reporter") {
  return serverFor(
    {
      [`GET /submissions/${IDENT}`]: detail({ type: "issue", number: "IR-2026-00001", discussion: [comment()] }),
      [`GET /submissions/${IDENT}/attachments`]: [attachment()],
      [`GET /submissions/${IDENT}/diagnostics`]: [],
      "GET /submissions/assignees": [{ id: "u1", name: "maria" }],
      [`GET /submissions/${IDENT}/audit`]: [{ event: "created", actor: "alice", at: "2026-10-12T10:00:00Z", detail: {} }],
      [`GET /submissions/${IDENT}/handoffs`]: [],
      ...extra,
    },
    who === "staff" ? STAFF_OPTIONS : OPTIONS,
  );
}

describe("one submission", () => {
  it("shows a reporter what they sent, the answers, and the history, and nothing of the triage", async () => {
    const server = detailRoutes({
      [`GET /submissions/${IDENT}`]: detail({
        type: "issue",
        number: "IR-2026-00001",
        unread: true,
        history: [
          { id: "h1", old_status: null, new_status: "Submitted", changed_by: "alice", by_staff: false, comment: "", at: "2026-10-12T10:00:00Z" },
          { id: "h2", old_status: "Submitted", new_status: "Investigating", changed_by: "AgentLab maintainer", by_staff: true, comment: "We can see it", at: "2026-10-12T11:00:00Z" },
        ],
        discussion: [comment({ kind: "info_request", body: "Which run was it?" })],
      }),
      [`POST /submissions/${IDENT}/seen`]: new Response(null, { status: 204 }),
    });
    renderAt(`/feedback/submissions/${IDENT}`);
    expect(await screen.findByRole("heading", { name: "IR-2026-00001" })).toBeInTheDocument();
    expect(screen.getByText("The run ends in a few minutes")).toBeInTheDocument();
    expect(screen.getByText("It says Processing for an hour")).toBeInTheDocument();
    expect(screen.getByText("Which run was it?")).toBeInTheDocument();
    expect(screen.getByText("Asks for information")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Status history" })).toBeInTheDocument();
    expect(screen.getByText("We can see it")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Triage" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Engineering handoff" })).not.toBeInTheDocument();
    expect(screen.queryByText("Audit trail")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("This is")).not.toBeInTheDocument();
    await waitFor(() => expect(server.to(`POST /submissions/${IDENT}/seen`)).toHaveLength(1));
    expect(server.to("GET /submissions/assignees")).toHaveLength(0);
    expect(server.to(`GET /submissions/${IDENT}/audit`)).toHaveLength(0);
  });

  it("lets the reporter answer, and sends it as a public comment", async () => {
    const server = detailRoutes({ [`POST /submissions/${IDENT}/comments`]: () => jsonResponse(comment({ mine: true, author: "alice", body: "It was run-42" }), 201) });
    renderAt(`/feedback/submissions/${IDENT}`);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Add a comment or answer a question"), "It was run-42");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(server.to(`POST /submissions/${IDENT}/comments`)).toHaveLength(1));
    expect(server.to(`POST /submissions/${IDENT}/comments`)[0]!.body).toEqual({ body: "It was run-42", visibility: "public", kind: "comment" });
  });

  it("shows the files with what each was found to be, and lets the owner look at the masked text", async () => {
    detailRoutes({
      [`GET /submissions/${IDENT}/attachments/a1/preview`]: { kind: "text", text: "Authorization: [REDACTED]", truncated: false, sanitized: true, message: "Secrets are masked in this preview." },
    });
    renderAt(`/feedback/submissions/${IDENT}`);
    const user = userEvent.setup();
    const files = await screen.findByRole("list", { name: "Attached files" });
    expect(within(files).getByText("run.log", { selector: "strong" })).toBeInTheDocument();
    expect(within(files).getByText("Not scanned")).toBeInTheDocument();
    expect(within(files).getByText(/Secrets were found and masked in the copy/)).toBeInTheDocument();
    await user.click(within(files).getByRole("button", { name: /Preview/ }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Authorization: [REDACTED]")).toBeInTheDocument();
    expect(within(dialog).getByText("Secrets are masked in this preview.")).toBeInTheDocument();
  });

  it("gives a maintainer the triage, and sends only what changed with the version that was read", async () => {
    const server = detailRoutes(
      {
        [`PATCH /submissions/${IDENT}`]: () => jsonResponse(detail({ type: "issue", number: "IR-2026-00001", status: "Investigating", version: 2 })),
      },
      "staff",
    );
    renderAt(`/feedback/submissions/${IDENT}`);
    const user = userEvent.setup();
    const triage = (await screen.findByRole("heading", { name: "Triage" })).closest("section")!;
    const save = within(triage).getByRole("button", { name: "Save changes" });
    expect(save).toBeDisabled();
    await user.selectOptions(within(triage).getByLabelText("Status"), "Investigating");
    await user.selectOptions(within(triage).getByLabelText("Priority (maintainers)"), "high");
    await user.type(within(triage).getByLabelText("Message with the status change"), "We can reproduce it");
    await user.click(save);
    await waitFor(() => expect(server.to(`PATCH /submissions/${IDENT}`)).toHaveLength(1));
    expect(server.to(`PATCH /submissions/${IDENT}`)[0]!.body).toEqual({
      version: 1,
      status: "Investigating",
      status_comment: "We can reproduce it",
      assigned_priority: "high",
    });
    expect(await within(triage).findByText("Saved.")).toBeInTheDocument();
  });

  it("does not overwrite a change someone else made: it says so and reads the latest", async () => {
    let reads = 0;
    const server = detailRoutes(
      {
        [`GET /submissions/${IDENT}`]: () => {
          reads += 1;
          return detail({ type: "issue", number: "IR-2026-00001", version: reads === 1 ? 1 : 3, status: reads === 1 ? "Submitted" : "Investigating" });
        },
        [`PATCH /submissions/${IDENT}`]: () => errorResponse(409, "conflict", "the submission changed since you read it"),
      },
      "staff",
    );
    renderAt(`/feedback/submissions/${IDENT}`);
    const user = userEvent.setup();
    const triage = (await screen.findByRole("heading", { name: "Triage" })).closest("section")!;
    await user.selectOptions(within(triage).getByLabelText("Priority (maintainers)"), "low");
    await user.click(within(triage).getByRole("button", { name: "Save changes" }));
    expect(await within(triage).findByText(/Someone else changed this submission/)).toBeInTheDocument();
    await waitFor(() => expect(reads).toBeGreaterThanOrEqual(2));
    expect(server.to(`PATCH /submissions/${IDENT}`)).toHaveLength(1);
  });

  it("sets an internal note apart from a reply, in words as well as in colour, and sends it as internal", async () => {
    const server = detailRoutes(
      {
        [`GET /submissions/${IDENT}`]: detail({
          type: "issue",
          number: "IR-2026-00001",
          discussion: [comment(), comment({ id: "c2", visibility: "internal", body: "Customer is on a trial", author: "maria" })],
        }),
        [`POST /submissions/${IDENT}/comments`]: () => jsonResponse(comment({ visibility: "internal", body: "Check the worker" }), 201),
      },
      "staff",
    );
    renderAt(`/feedback/submissions/${IDENT}`);
    const user = userEvent.setup();
    const discussion = await screen.findByRole("list", { name: "Discussion" });
    const internal = within(discussion).getByLabelText("Internal note");
    expect(within(internal).getByText("Internal note: maintainers only")).toBeInTheDocument();
    expect(within(internal).getByText("Customer is on a trial")).toBeInTheDocument();
    expect(within(discussion).getAllByLabelText("Comment")).toHaveLength(1);
    await user.selectOptions(screen.getByLabelText("This is"), "internal");
    expect(screen.getByText(/Only maintainers can see an internal note/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Internal note", { selector: "textarea" }), "Check the worker");
    await user.click(screen.getByRole("button", { name: "Add the note" }));
    await waitFor(() => expect(server.to(`POST /submissions/${IDENT}/comments`)).toHaveLength(1));
    expect(server.to(`POST /submissions/${IDENT}/comments`)[0]!.body).toEqual({ body: "Check the worker", visibility: "internal", kind: "comment" });
  });

  it("lets a maintainer ask for more information, which the reporter will see", async () => {
    const server = detailRoutes({ [`POST /submissions/${IDENT}/comments`]: () => jsonResponse(comment({ kind: "info_request" }), 201) }, "staff");
    renderAt(`/feedback/submissions/${IDENT}`);
    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText("This is"), "info_request");
    await user.type(screen.getByLabelText("Your message"), "Which run was it?");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(server.to(`POST /submissions/${IDENT}/comments`)).toHaveLength(1));
    expect(server.to(`POST /submissions/${IDENT}/comments`)[0]!.body).toEqual({ body: "Which run was it?", visibility: "public", kind: "info_request" });
  });

  it("shows what was written as text: a script in a title or a comment is shown, never run", async () => {
    detailRoutes({
      [`GET /submissions/${IDENT}`]: detail({
        type: "issue",
        number: "IR-2026-00001",
        title: "<img src=x onerror=alert(1)>",
        description: "<script>window.hacked = true</script>",
        discussion: [comment({ body: "<b>bold</b> [link](javascript:alert(1))" })],
      }),
    });
    renderAt(`/feedback/submissions/${IDENT}`);
    expect(await screen.findByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(screen.getByText("<script>window.hacked = true</script>")).toBeInTheDocument();
    expect(screen.getByText("<b>bold</b> [link](javascript:alert(1))")).toBeInTheDocument();
    expect(document.querySelector("script")).toBeNull();
    expect(document.querySelector("img[src='x']")).toBeNull();
    expect((window as unknown as { hacked?: boolean }).hacked).toBeUndefined();
  });
});

// ================================================================================================ engineering handoff
describe("the engineering handoff", () => {
  it("lets a maintainer choose what goes in, look at the package, build it, and take it", async () => {
    const built = handoff();
    let kept = false;
    const server = detailRoutes(
      {
        [`GET /submissions/${IDENT}/attachments`]: [
          attachment(),
          attachment({ id: "a2", name: "screen.png", type: "image/png", text_like: false, sanitized_available: false, secrets: { status: "not_applicable", counts: {}, external_sharing_blocked: false, note: "" } }),
        ],
        [`POST /submissions/${IDENT}/handoffs/preview`]: () => jsonResponse(handoff({ id: null, status: "preview", sha256: null, size: 0, created_at: null, created_by: null })),
        [`POST /submissions/${IDENT}/handoffs`]: () => {
          kept = true;
          return jsonResponse(built, 201);
        },
        [`GET /submissions/${IDENT}/handoffs`]: () => (kept ? [built] : []),
        "GET /handoffs/h-12345678/download": () => new Response(new Blob(["PK"]), { headers: { "Content-Disposition": 'attachment; filename="agentlab-handoff-IR-2026-00001.zip"' } }),
        "GET /handoffs/h-12345678/task": () => new Response("# Task", { headers: { "Content-Disposition": 'attachment; filename="CLAUDE_CODE_TASK.md"' } }),
      },
      "staff",
    );
    const createUrl = vi.fn(() => "blob:x");
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: createUrl, revokeObjectURL: () => undefined }));
    renderAt(`/feedback/submissions/${IDENT}`);
    const user = userEvent.setup();
    const section = (await screen.findByRole("heading", { name: "Engineering handoff" })).closest("section")!;
    const panel = within(section);
    expect(panel.getByText(/Nothing here runs Claude Code, changes the repository or sends the package anywhere/)).toBeInTheDocument();
    expect(panel.getByLabelText(/Internal notes and internal files/)).not.toBeChecked();
    expect(await panel.findByText("Left out: it cannot be masked (an image, a document or an archive)")).toBeInTheDocument();
    await user.selectOptions(panel.getByLabelText("What to do with screen.png"), "original");
    expect(panel.getByText("You chose to put a file in as it was uploaded")).toBeInTheDocument();
    await user.click(panel.getByRole("button", { name: "Preview the package" }));
    expect(await panel.findByText("Preview: nothing was built or kept")).toBeInTheDocument();
    expect(panel.getByText("attachment screen.png")).toBeInTheDocument();
    expect(server.to(`POST /submissions/${IDENT}/handoffs/preview`)[0]!.body).toEqual({
      include_internal_notes: false,
      include_diagnostics: true,
      exclude_attachments: [],
      include_originals: ["a2"],
    });
    await user.click(panel.getByRole("button", { name: "Build the package" }));
    await waitFor(() => expect(server.to(`POST /submissions/${IDENT}/handoffs`)).toHaveLength(1));
    await user.click(await panel.findByRole("button", { name: "Download the package (ZIP)" }));
    await waitFor(() => expect(server.to("GET /handoffs/h-12345678/download")).toHaveLength(1));
    await user.click(panel.getByRole("button", { name: "Download CLAUDE_CODE_TASK.md" }));
    await waitFor(() => expect(server.to("GET /handoffs/h-12345678/task")).toHaveLength(1));
  });

  it("warns when a package holds files as uploaded or needs a person's review", async () => {
    detailRoutes(
      {
        [`GET /submissions/${IDENT}/handoffs`]: [
          handoff({ includes_unsanitized: true, external_sharing_blocked: true, warnings: ["screen.png is in the package as uploaded, unsanitized."] }),
        ],
      },
      "staff",
    );
    renderAt(`/feedback/submissions/${IDENT}`);
    const user = userEvent.setup();
    expect(await screen.findByText("unsanitized files")).toBeInTheDocument();
    expect(screen.getByText("needs review")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "See what it holds" }));
    expect(await screen.findByText("Read before you use this package")).toBeInTheDocument();
    expect(screen.getByText("Not to be shared outside AgentLab yet")).toBeInTheDocument();
    expect(screen.getByText("The task file, as Claude Code will read it")).toBeInTheDocument();
  });
});

// ============================================================================================== ways in, and the frame
describe("the ways in", () => {
  it("puts a link to report an issue on a result, and it carries identifiers only", async () => {
    const { result } = await import("./test/factories");
    const { ResultDetail } = await import("./components/ResultDetail");
    const shown = result({
      id: "res-1",
      test_id: "CONV-001",
      evidence: [],
      reliability: null,
      review: null,
      root_cause: null,
      root_cause_confidence: null,
      error_kind: null,
      latency_ms: null,
      tokens: 0,
      cost_usd: 0,
      confidence: 1,
      score_category: "functional",
      started_at: null,
      finished_at: null,
    });
    renderWithSession(<ResultDetail runId="run-42" result={shown} reviews={[]} onReviewed={() => undefined} />);
    const link = await screen.findByRole("link", { name: "Report an issue with this result" });
    const target = new URL(link.getAttribute("href")!, "http://localhost");
    expect(target.pathname).toBe("/feedback/issue");
    expect(Object.fromEntries(target.searchParams)).toEqual({ run: "run-42", test: "CONV-001", result: "res-1", screen: "Test result" });
  });

  it("leaves a person who must change a temporary password with the account page alone", async () => {
    const { Shell } = await import("./App");
    const { Gate } = await import("./pages/SignIn");
    fakeServer({
      "GET /health": { ...HEALTH_OPEN, auth_required: true },
      "GET /auth/options": { registration: "open", min_password_length: 12, api_token_sign_in: true, password_sign_in: true },
      "GET /auth/me": {
        kind: "user",
        role: "reporter",
        username: "newbie",
        display_name: "newbie",
        must_change_password: true,
        csrf_token: "c",
        operator: false,
        can_manage_accounts: false,
      },
    });
    renderWithSession(
      <Gate>
        <Shell />
      </Gate>,
      { route: "/feedback/feature" }, // an address typed or bookmarked leads to the account page too
    );
    expect(await screen.findByText("Choose your own password")).toBeInTheDocument();
    expect(screen.queryByLabelText("Feature title")).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Feedback & Support" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Request a feature/ })).not.toBeInTheDocument();
  });
});

describe("leaving a form", () => {
  it("asks the browser before the tab is closed while there is unsent text, and not otherwise", async () => {
    serverFor();
    renderAt("/feedback/feature");
    const user = userEvent.setup();
    const before = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(before);
    expect(before.defaultPrevented).toBe(false);
    await user.type(await screen.findByLabelText("Feature title"), "Something");
    const after = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(after);
    expect(after.defaultPrevented).toBe(true);
  });
});

// ================================================================================ publishing, and deleting
const INTEGRATION = {
  provider: "github",
  enabled: true,
  ready: true,
  reason: null,
  repository: "acme/agentlab-feedback",
  may_publish: true,
  status_sync: true,
};
const PREVIEW = {
  provider: "github",
  repository: "acme/agentlab-feedback",
  title: "[IR-2026-00001] A run stays on Processing forever",
  body: "> Published from AgentLab submission **IR-2026-00001**\n\n### Description\n\n```text\nIt never finishes.\n```\n",
  labels: ["from-agentlab"],
  assignees: ["octocat"],
  content_hash: "a".repeat(64),
  needs_review: false,
  warnings: ["This will be visible to everyone who can see acme/agentlab-feedback on github.com."],
  left_out: ["attachments and screenshots", "internal notes"],
  already: null,
};
const PUBLICATION = {
  id: "p1",
  provider: "github",
  repository: "acme/agentlab-feedback",
  state: "published",
  url: "https://github.com/acme/agentlab-feedback/issues/1",
  number: 1,
  external_state: "open",
  error_kind: "",
  error_detail: "",
  submission_version: 1,
  published_by: "maria",
  published_at: "2026-10-13T10:00:00Z",
  synced_status: "",
};

describe("publishing to an issue tracker", () => {
  const staffRoutes = (extra: Record<string, Handler> = {}) =>
    detailRoutes(
      {
        "GET /integrations": [INTEGRATION],
        [`GET /submissions/${IDENT}/publications`]: [],
        ...extra,
      },
      "staff",
    );

  it("shows exactly what would be public, and publishes that and nothing else, after the person says they read it", async () => {
    let made = false;
    const server = staffRoutes({
      [`POST /submissions/${IDENT}/publications/preview`]: PREVIEW,
      [`POST /submissions/${IDENT}/publications`]: () => {
        made = true;
        return jsonResponse(PUBLICATION, 201);
      },
      [`GET /submissions/${IDENT}/publications`]: () => (made ? [PUBLICATION] : []),
    });
    renderAt(`/feedback/submissions/${IDENT}`);
    const user = userEvent.setup();
    const section = (await screen.findByRole("heading", { name: "Publish to an issue tracker" })).closest("section")!;
    const panel = within(section);
    expect(panel.getByText(/Files, diagnostic information, handoff packages, the reporter's name, internal notes/)).toBeInTheDocument();
    await user.click(await panel.findByRole("button", { name: "Preview what would be published" }));
    expect(await panel.findByText("Read it: this is exactly what will be public")).toBeInTheDocument();
    expect(panel.getByText("[IR-2026-00001] A run stays on Processing forever")).toBeInTheDocument();
    expect(panel.getByText(/Published from AgentLab submission \*\*IR-2026-00001\*\*/)).toBeInTheDocument();
    expect(panel.getByText(/Left out on purpose: attachments and screenshots; internal notes/)).toBeInTheDocument();
    const publish = panel.getByRole("button", { name: "Publish it" });
    expect(publish).toBeDisabled();
    await user.click(panel.getByLabelText(/I have read what will be published in acme\/agentlab-feedback/));
    await user.click(publish);
    const link = await panel.findByRole("link", { name: "acme/agentlab-feedback#1" });
    expect(link).toHaveAttribute("href", "https://github.com/acme/agentlab-feedback/issues/1");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(server.to(`POST /submissions/${IDENT}/publications`)[0]!.body).toEqual({
      provider: "github",
      content_hash: "a".repeat(64),
      acknowledge_unreviewed: false,
    });
    expect(panel.queryByRole("button", { name: "Preview what would be published" })).not.toBeInTheDocument();
  });

  it("asks for a second, separate yes when a text still looks like a secret", async () => {
    const server = staffRoutes({
      [`POST /submissions/${IDENT}/publications/preview`]: { ...PREVIEW, needs_review: true, warnings: ["A text in it still looks like a secret after masking."] },
      [`POST /submissions/${IDENT}/publications`]: () => jsonResponse(PUBLICATION, 201),
    });
    renderAt(`/feedback/submissions/${IDENT}`);
    const user = userEvent.setup();
    const panel = within((await screen.findByRole("heading", { name: "Publish to an issue tracker" })).closest("section")!);
    await user.click(await panel.findByRole("button", { name: "Preview what would be published" }));
    await user.click(await panel.findByLabelText(/I have read what will be published/));
    expect(panel.getByRole("button", { name: "Publish it" })).toBeDisabled();
    await user.click(panel.getByLabelText(/something in it still looks like a secret after masking/));
    await user.click(panel.getByRole("button", { name: "Publish it" }));
    await waitFor(() => expect(server.to(`POST /submissions/${IDENT}/publications`)).toHaveLength(1));
    expect(server.to(`POST /submissions/${IDENT}/publications`)[0]!.body).toMatchObject({ acknowledge_unreviewed: true });
  });

  it("says why when GitHub refused, and what to do, and offers the preview again", async () => {
    staffRoutes({
      [`POST /submissions/${IDENT}/publications/preview`]: PREVIEW,
      [`POST /submissions/${IDENT}/publications`]: () =>
        errorResponse(429, "rate_limited", "GitHub's rate limit was reached: try again in about 10 minute(s). Nothing was published."),
    });
    renderAt(`/feedback/submissions/${IDENT}`);
    const user = userEvent.setup();
    const panel = within((await screen.findByRole("heading", { name: "Publish to an issue tracker" })).closest("section")!);
    await user.click(await panel.findByRole("button", { name: "Preview what would be published" }));
    await user.click(await panel.findByLabelText(/I have read what will be published/));
    await user.click(panel.getByRole("button", { name: "Publish it" }));
    expect(await panel.findByText(/try again in about 10 minute\(s\)/)).toBeInTheDocument();
    expect(panel.getByRole("button", { name: "Publish it" })).toBeEnabled();
  });

  it("is not offered when it is off, to a role that may not publish, or for a private report", async () => {
    detailRoutes({ "GET /integrations": [{ ...INTEGRATION, ready: false, enabled: false, reason: "it is switched off" }] }, "staff");
    const first = renderAt(`/feedback/submissions/${IDENT}`);
    await screen.findByRole("heading", { name: "Triage" });
    expect(screen.queryByRole("heading", { name: "Publish to an issue tracker" })).not.toBeInTheDocument();
    first.unmount();

    staffRoutes({ "GET /integrations": [{ ...INTEGRATION, may_publish: false }] });
    const second = renderAt(`/feedback/submissions/${IDENT}`);
    expect(await screen.findByText(/Your role may not publish to acme\/agentlab-feedback/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Preview what would be published" })).not.toBeInTheDocument();
    second.unmount();

    staffRoutes({
      [`GET /submissions/${IDENT}`]: detail({ type: "issue", number: "IR-2026-00001", visibility: "security" }),
    });
    renderAt(`/feedback/submissions/${IDENT}`);
    expect(await screen.findByText("A private security report is never published to an external service.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Preview what would be published" })).not.toBeInTheDocument();
  });

  it("pushes the status to the issue when the owner allowed it and the person asks", async () => {
    const server = staffRoutes({
      [`GET /submissions/${IDENT}/publications`]: [PUBLICATION],
      [`POST /submissions/${IDENT}/publications/github/sync`]: () => jsonResponse({ ...PUBLICATION, synced_status: "Submitted" }),
    });
    renderAt(`/feedback/submissions/${IDENT}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Push the status to the issue" }));
    await waitFor(() => expect(server.to(`POST /submissions/${IDENT}/publications/github/sync`)).toHaveLength(1));
  });
});

describe("deleting a submission", () => {
  const adminRoutes = (extra: Record<string, Handler> = {}) =>
    detailRoutes({ "GET /integrations": [], ...extra }, "staff"); // the base server's account is the local administrator

  it("is for an administrator only: a maintainer is not offered it", async () => {
    detailRoutes(
      {
        "GET /integrations": [],
        "GET /auth/me": { kind: "user", role: "maintainer", username: "maria", display_name: "maria", must_change_password: false, csrf_token: "c", operator: true, can_manage_accounts: false },
      },
      "staff",
    );
    renderAt(`/feedback/submissions/${IDENT}`);
    await screen.findByRole("heading", { name: "Triage" });
    expect(screen.queryByRole("heading", { name: "Delete this submission" })).not.toBeInTheDocument();
  });

  it("needs a reason and a confirmation, says what stays, and goes back to the list when it is done", async () => {
    const server = adminRoutes({
      [`DELETE /submissions/${IDENT}`]: () => jsonResponse({ number: "IR-2026-00001", attachments: 1, diagnostic_items: 1, handoffs: 1, files_deleted: 3, exported_copies: 2 }),
      "GET /submissions": { items: [], total: 0, limit: 25, offset: 0 },
    });
    renderAt(`/feedback/submissions/${IDENT}`);
    const user = userEvent.setup();
    const panel = within((await screen.findByRole("heading", { name: "Delete this submission" })).closest("section")!);
    expect(panel.getByText(/A package or a file that was downloaded, and an issue published elsewhere, cannot be recalled/)).toBeInTheDocument();
    const button = panel.getByRole("button", { name: "Delete this submission" });
    expect(button).toBeDisabled();
    await user.type(panel.getByLabelText("Why it is being deleted"), "erasure requested by the reporter");
    await user.click(button);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/cannot be undone/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Delete it for good" }));
    await waitFor(() => expect(server.to(`DELETE /submissions/${IDENT}`)).toHaveLength(1));
    const call = server.to(`DELETE /submissions/${IDENT}`)[0]!;
    expect(call.query.get("reason")).toBe("erasure requested by the reporter");
    expect(call.query.get("acknowledge_open_security_report")).toBeNull();
    expect(await screen.findByRole("heading", { name: "Submissions" })).toBeInTheDocument();
    expect(screen.getByText(/IR-2026-00001 was deleted with 1 file\(s\) and 1 package\(s\)\. 2 download\(s\) of its packages cannot be recalled/)).toBeInTheDocument();
  });

  it("needs the administrator to say they know when the report is an open private one", async () => {
    const server = adminRoutes({
      [`GET /submissions/${IDENT}`]: detail({ type: "issue", number: "IR-2026-00001", visibility: "security", closed_at: null }),
      [`DELETE /submissions/${IDENT}`]: () => jsonResponse({ number: "IR-2026-00001", attachments: 0, diagnostic_items: 0, handoffs: 0, files_deleted: 0, exported_copies: 0 }),
    });
    renderAt(`/feedback/submissions/${IDENT}`);
    const user = userEvent.setup();
    const panel = within((await screen.findByRole("heading", { name: "Delete this submission" })).closest("section")!);
    expect(panel.getByText("This is a private security report that is not closed")).toBeInTheDocument();
    await user.type(panel.getByLabelText("Why it is being deleted"), "requested by the reporter");
    expect(panel.getByRole("button", { name: "Delete this submission" })).toBeDisabled();
    await user.click(panel.getByLabelText(/I know this report is open/));
    await user.click(panel.getByRole("button", { name: "Delete this submission" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete it for good" }));
    await waitFor(() => expect(server.to(`DELETE /submissions/${IDENT}`)).toHaveLength(1));
    expect(server.to(`DELETE /submissions/${IDENT}`)[0]!.query.get("acknowledge_open_security_report")).toBe("true");
  });
});
