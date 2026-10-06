import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { App } from "./App";
import { errorResponse, fakeServer, HEALTH_OPEN, project } from "./test/server";

/** A server with nothing in it yet: what a person sees the first time they open the interface. */
function emptyServer() {
  return fakeServer({
    "GET /health": HEALTH_OPEN,
    "GET /projects": [project("default")],
    "GET /targets": [],
    "GET /test-runs": [],
    "GET /providers": [],
    "GET /skills": [],
    "GET /credentials": [],
    "GET /documents": [],
    "GET /scoring-profiles": [],
    "GET /models": [],
  });
}

function open(hash: string) {
  window.location.hash = hash;
  return render(<App />);
}

afterEach(() => {
  window.location.hash = "";
  document.title = "";
});

const SCREENS: [string, string][] = [
  ["#/", "Dashboard"],
  ["#/new", "New evaluation"],
  ["#/targets", "Targets and discovery"],
  ["#/runs", "Test runs"],
  ["#/reports", "Reports"],
  ["#/compare", "Compare runs"],
  ["#/skills", "Skills"],
  ["#/providers", "Providers"],
  ["#/credentials", "Credentials"],
  ["#/settings", "Settings"],
];

describe("the interface on a server with nothing in it", () => {
  it.each(SCREENS)("opens %s and shows its heading", async (hash, heading) => {
    emptyServer();
    open(hash);
    expect(await screen.findByRole("heading", { level: 1, name: heading })).toBeInTheDocument();
    await waitFor(() => expect(document.title).toBe(`${heading} · AgentLab`));
  });

  it("has the landmarks a keyboard or screen reader user needs", async () => {
    emptyServer();
    open("#/");
    await screen.findByRole("heading", { level: 1, name: "Dashboard" });
    expect(screen.getByRole("link", { name: "Skip to the content" })).toHaveAttribute("href", "#content");
    expect(screen.getByRole("main")).toBeInTheDocument();
    for (const group of ["Overview", "Evaluate", "Library", "Admin"]) expect(screen.getByRole("navigation", { name: group })).toBeInTheDocument();
  });

  it("goes to another screen from the navigation, and marks where the person is", async () => {
    emptyServer();
    open("#/");
    await screen.findByRole("heading", { level: 1, name: "Dashboard" });
    const overview = screen.getByRole("navigation", { name: "Overview" });
    const evaluate = screen.getByRole("navigation", { name: "Evaluate" });
    expect(within(overview).getByRole("link", { name: /Dashboard/ })).toHaveAttribute("aria-current", "page");
    await userEvent.click(within(evaluate).getByRole("link", { name: /Test runs/ }));
    expect(await screen.findByRole("heading", { level: 1, name: "Test runs" })).toBeInTheDocument();
    expect(within(evaluate).getByRole("link", { name: /Test runs/ })).toHaveAttribute("aria-current", "page");
    expect(within(overview).getByRole("link", { name: /Dashboard/ })).not.toHaveAttribute("aria-current");
  });

  it("says there is nothing at an address that leads nowhere, and offers the way back", async () => {
    emptyServer();
    open("#/no/such/screen");
    expect(await screen.findByText("There is nothing here")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the dashboard" })).toHaveAttribute("href", "#/");
  });

  it("says plainly that a run does not exist instead of breaking", async () => {
    const server = emptyServer();
    server.set("GET /test-runs/nope", errorResponse(404, "not_found", "No run with that id."));
    open("#/runs/nope");
    expect(await screen.findByText("No run with that id.")).toBeInTheDocument();
  });
});
