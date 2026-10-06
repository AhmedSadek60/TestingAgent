import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";
import { errorResponse, fakeServer, HEALTH_OPEN, project, renderWithSession, type Handler } from "../test/server";
import { allowedDecisions, decisionLabel, ReviewForm, ReviewList } from "./Review";

const SAVE = "POST /test-runs/r1/reviews";

function serverFor(answer: Handler = { id: "rv1" }) {
  return fakeServer({ "GET /health": HEALTH_OPEN, "GET /projects": [project("default")], [SAVE]: answer });
}

function renderForm(props: Partial<ComponentProps<typeof ReviewForm>> = {}) {
  const onDone = vi.fn();
  renderWithSession(<ReviewForm runId="r1" subject="finding" subjectId="f1" status="open" onDone={onDone} {...props} />);
  return { onDone, user: userEvent.setup() };
}

const named = () => window.localStorage.setItem("agentlab.reviewer", "Sam");

describe("allowedDecisions", () => {
  it("offers a finding the decisions that apply to a finding", () => {
    expect(allowedDecisions("finding", "open")).toEqual(["approve", "false_positive", "change_severity", "comment"]);
  });

  it("offers a result that failed a false-positive decision, and one that passed a false-negative decision", () => {
    expect(allowedDecisions("result", "failed")).toEqual(["approve", "false_positive", "override_score", "change_severity", "comment"]);
    expect(allowedDecisions("result", "timeout")).toContain("false_positive");
    expect(allowedDecisions("result", "error")).toContain("false_positive");
    expect(allowedDecisions("result", "passed")).toEqual(["approve", "false_negative", "override_score", "change_severity", "comment"]);
  });

  it("offers neither to a result that never ran, which was not a failure to begin with", () => {
    const blocked = allowedDecisions("result", "blocked");
    expect(blocked).not.toContain("false_positive");
    expect(blocked).not.toContain("false_negative");
  });
});

describe("decisionLabel", () => {
  it("names the decisions in plain words", () => {
    expect(decisionLabel("approve")).toBe("Confirm");
    expect(decisionLabel("false_positive")).toBe("False positive");
    expect(decisionLabel("override_score")).toBe("Override the score");
  });

  it("copes with a decision it has not heard of", () => {
    expect(decisionLabel("something_new")).not.toContain("_");
  });
});

describe("ReviewForm", () => {
  it("asks who is reviewing before anything can be saved", () => {
    renderForm();
    expect(screen.getByText("Enter your name so the review can be attributed.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save review" })).toBeDisabled();
  });

  it("remembers the reviewer's name from last time", () => {
    named();
    renderForm();
    expect(screen.getByLabelText("Your name")).toHaveValue("Sam");
    expect(screen.getByRole("button", { name: "Save review" })).toBeEnabled();
  });

  it("saves a confirmation with what was typed, says the original is kept, and tells the screen", async () => {
    const server = serverFor();
    const { onDone, user } = renderForm();
    await user.type(screen.getByLabelText("Your name"), "  Sam Reviewer ");
    await user.type(screen.getByLabelText("Comment"), "Looks right to me");
    await user.click(screen.getByRole("button", { name: "Save review" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(server.to(SAVE)[0]!.body).toEqual({
      subject: "finding",
      subject_id: "f1",
      decision: "approve",
      reviewer: "Sam Reviewer",
      reason: "",
      comment: "Looks right to me",
    });
    expect(await screen.findByText("Review saved. The original evaluation is kept.")).toBeInTheDocument();
    expect(window.localStorage.getItem("agentlab.reviewer")).toBe("Sam Reviewer");
  });

  it("does not remember the name when asked not to", async () => {
    serverFor();
    const { onDone, user } = renderForm();
    await user.type(screen.getByLabelText("Your name"), "Sam");
    await user.click(screen.getByRole("checkbox", { name: "Remember my name in this browser" }));
    await user.click(screen.getByRole("button", { name: "Save review" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(window.localStorage.getItem("agentlab.reviewer")).toBeNull();
  });

  it("empties what was typed for the next review once one is saved", async () => {
    serverFor();
    named();
    const { onDone, user } = renderForm();
    await user.type(screen.getByLabelText("Reason"), "because");
    await user.type(screen.getByLabelText("Comment"), "and more");
    await user.click(screen.getByRole("button", { name: "Save review" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(screen.getByLabelText("Reason")).toHaveValue("");
    expect(screen.getByLabelText("Comment")).toHaveValue("");
    expect(screen.getByLabelText("Your name")).toHaveValue("Sam");
  });

  it("wants a reason for a false positive", async () => {
    named();
    const server = serverFor();
    const { onDone, user } = renderForm();
    await user.selectOptions(screen.getByLabelText("Decision"), "false_positive");
    expect(screen.getByText("Give a reason, so the next reader can follow it.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save review" })).toBeDisabled();
    await user.type(screen.getByLabelText("Reason (required)"), "The agent refused");
    await user.click(screen.getByRole("button", { name: "Save review" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(server.to(SAVE)[0]!.body).toMatchObject({ decision: "false_positive", reason: "The agent refused" });
  });

  it("sends an overridden score as a fraction, once it is a number from 0 to 100", async () => {
    named();
    const server = serverFor();
    const { onDone, user } = renderForm({ subject: "result", status: "failed", subjectId: "t1" });
    await user.selectOptions(screen.getByLabelText("Decision"), "override_score");
    await user.type(screen.getByLabelText("Reason (required)"), "Partly right");
    const score = screen.getByLabelText("Score (0 to 100)");
    expect(screen.getByText("Enter a score from 0 to 100.")).toBeInTheDocument();
    for (const wrong of ["140", "-5", "abc"]) {
      await user.clear(score);
      await user.type(score, wrong);
      expect(screen.getByText("Enter a score from 0 to 100.")).toBeInTheDocument();
    }
    await user.clear(score);
    await user.type(score, "85");
    expect(screen.queryByText("Enter a score from 0 to 100.")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save review" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(server.to(SAVE)[0]!.body).toMatchObject({ subject: "result", subject_id: "t1", decision: "override_score", score: 0.85 });
  });

  it("needs the new severity for a change of severity, and sends it", async () => {
    named();
    const server = serverFor();
    const { onDone, user } = renderForm();
    await user.selectOptions(screen.getByLabelText("Decision"), "change_severity");
    await user.type(screen.getByLabelText("Reason (required)"), "Not exploitable here");
    expect(screen.getByText("Choose the severity.")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Severity"), "low");
    await user.click(screen.getByRole("button", { name: "Save review" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(server.to(SAVE)[0]!.body).toMatchObject({ decision: "change_severity", severity: "low" });
  });

  it("lets a false negative carry a severity without needing one", async () => {
    named();
    const server = serverFor();
    const { onDone, user } = renderForm({ subject: "result", status: "passed", subjectId: "t2" });
    await user.selectOptions(screen.getByLabelText("Decision"), "false_negative");
    await user.type(screen.getByLabelText("Reason (required)"), "It leaked the canary");
    await user.click(screen.getByRole("button", { name: "Save review" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(server.to(SAVE)[0]!.body).not.toHaveProperty("severity");

    await user.selectOptions(screen.getByLabelText("Severity"), "high");
    await user.type(screen.getByLabelText("Reason (required)"), "It leaked the canary");
    await user.click(screen.getByRole("button", { name: "Save review" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(2));
    expect(server.to(SAVE)[1]!.body).toMatchObject({ decision: "false_negative", severity: "high" });
  });

  it("shows why the server refused it, and does not say it was saved", async () => {
    named();
    serverFor(errorResponse(422, "validation", "That decision does not apply to this finding."));
    const { onDone, user } = renderForm();
    await user.click(screen.getByRole("button", { name: "Save review" }));
    expect(await screen.findByText("That decision does not apply to this finding.")).toBeInTheDocument();
    expect(onDone).not.toHaveBeenCalled();
    expect(screen.queryByText("Review saved. The original evaluation is kept.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save review" })).toBeEnabled();
  });
});

describe("ReviewList", () => {
  const review = (overrides: Record<string, unknown> = {}) => ({
    id: "rv1",
    decision: "false_positive",
    reviewer: "Sam",
    created_at: "2026-10-06T10:00:00Z",
    original: { status: "failed", score: 0.2 },
    reviewed: { status: "passed", score: 1 },
    reason: "The agent refused",
    comment: "See the trace",
    ...overrides,
  });

  it("says when nobody has reviewed it yet", () => {
    render(<ReviewList reviews={[]} />);
    expect(screen.getByText("Nobody has reviewed this yet.")).toBeInTheDocument();
  });

  it("shows each change beside what the evaluation said, which stays on record", () => {
    render(<ReviewList reviews={[review()] as never} />);
    expect(screen.getByText("False positive")).toBeInTheDocument();
    expect(screen.getByText("Sam")).toBeInTheDocument();
    expect(screen.getByText("failed").tagName).toBe("S");
    expect(screen.getByText("passed").tagName).toBe("STRONG");
    expect(screen.getByText("0.20").tagName).toBe("S");
    expect(screen.getByText("1").tagName).toBe("STRONG");
    expect(screen.getByText("Reason: The agent refused")).toBeInTheDocument();
    expect(screen.getByText("See the trace")).toBeInTheDocument();
  });

  it("says so when a review changed nothing", () => {
    render(<ReviewList reviews={[review({ decision: "comment", original: {}, reviewed: {}, reason: "" })] as never} />);
    expect(screen.getByText("nothing was changed")).toBeInTheDocument();
  });

  it("shows what a reviewer typed as text", () => {
    const hostile = '<img src=x onerror="window.__pwned = true"><b>bold</b>';
    const { container } = render(<ReviewList reviews={[review({ comment: hostile })] as never} />);
    expect(container.querySelector("img, b")).toBeNull();
    expect(container.textContent).toContain(hostile);
  });
});
