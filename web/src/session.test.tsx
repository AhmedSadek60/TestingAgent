import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Gate } from "./pages/SignIn";
import { useSession } from "./session";
import { errorResponse, fakeServer, HEALTH_OPEN, HEALTH_PROTECTED, project, renderWithSession } from "./test/server";

const TOKEN = "unit-test-token-not-a-secret";

function Inside() {
  const session = useSession();
  return (
    <div>
      <p>inside as {session.project}</p>
      <button type="button" onClick={session.signOut}>
        sign out
      </button>
      <button type="button" onClick={() => session.setProject("other")}>
        pick other
      </button>
    </div>
  );
}

const renderGate = () =>
  renderWithSession(
    <Gate>
      <Inside />
    </Gate>,
  );

const wantsToken = (answer: unknown) => (call: { headers: Headers }) =>
  call.headers.get("Authorization") === `Bearer ${TOKEN}` ? answer : errorResponse(401, "unauthorized", "A valid API token is required.");

function allStoredText(): string {
  return [window.localStorage, window.sessionStorage].map((area) => Object.values(area).join("\n")).join("\n");
}

describe("a server that asks for no token", () => {
  it("lets the person straight in, and sends no credentials", async () => {
    const server = fakeServer({ "GET /health": HEALTH_OPEN, "GET /projects": [project("default")] });
    renderGate();
    expect(screen.getByText("Connecting to the server…")).toBeInTheDocument();
    expect(await screen.findByText("inside as default")).toBeInTheDocument();
    expect(server.calls.every((call) => call.headers.get("Authorization") === null)).toBe(true);
    expect(window.sessionStorage.length).toBe(0);
  });

  it("makes the default project on a server that has none yet", async () => {
    const server = fakeServer({
      "GET /health": HEALTH_OPEN,
      "GET /projects": [],
      "POST /projects": (call) => project((call.body as { name: string }).name),
    });
    renderGate();
    expect(await screen.findByText("inside as default")).toBeInTheDocument();
    expect(server.to("POST /projects")).toHaveLength(1);
    expect(server.to("POST /projects")[0]!.body).toMatchObject({ name: "default" });
  });

  it("does not mind that someone else made the default project first", async () => {
    fakeServer({
      "GET /health": HEALTH_OPEN,
      "GET /projects": [],
      "POST /projects": errorResponse(409, "conflict", "A project with that name exists."),
    });
    renderGate();
    expect(await screen.findByText("inside as default")).toBeInTheDocument();
  });

  it("keeps the project the person chose last time", async () => {
    window.localStorage.setItem("agentlab.project", "second");
    fakeServer({ "GET /health": HEALTH_OPEN, "GET /projects": [project("first"), project("second")] });
    renderGate();
    expect(await screen.findByText("inside as second")).toBeInTheDocument();
  });

  it("moves to the first project when the remembered one no longer exists", async () => {
    window.localStorage.setItem("agentlab.project", "gone");
    fakeServer({ "GET /health": HEALTH_OPEN, "GET /projects": [project("first"), project("second")] });
    renderGate();
    expect(await screen.findByText("inside as first")).toBeInTheDocument();
  });

  it("remembers a project the person picks", async () => {
    fakeServer({ "GET /health": HEALTH_OPEN, "GET /projects": [project("default")] });
    renderGate();
    await userEvent.click(await screen.findByRole("button", { name: "pick other" }));
    expect(screen.getByText("inside as other")).toBeInTheDocument();
    expect(window.localStorage.getItem("agentlab.project")).toBe("other");
  });
});

describe("a server that asks for a token", () => {
  it("asks for it before it asks the server for anything protected, and sends it once it has it", async () => {
    const server = fakeServer({ "GET /health": HEALTH_PROTECTED, "GET /projects": wantsToken([project("default")]) });
    renderGate();
    expect(await screen.findByRole("heading", { name: "Sign in to AgentLab" })).toBeInTheDocument();
    expect(server.to("GET /projects")).toHaveLength(0);
    expect(screen.getByLabelText("API token")).toHaveAttribute("type", "password");

    const user = userEvent.setup();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeDisabled();
    await user.type(screen.getByLabelText("API token"), TOKEN);
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("inside as default")).toBeInTheDocument();
    expect(server.to("GET /projects").at(-1)!.headers.get("Authorization")).toBe(`Bearer ${TOKEN}`);
  });

  it("keeps the token in this tab only, never where other tabs or later visits could read it", async () => {
    fakeServer({ "GET /health": HEALTH_PROTECTED, "GET /projects": wantsToken([project("default")]) });
    renderGate();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("API token"), TOKEN);
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByText("inside as default");
    expect(window.sessionStorage.getItem("agentlab.token")).toBe(TOKEN);
    expect(Object.values(window.localStorage).join("\n")).not.toContain(TOKEN);
    expect(document.body.innerHTML).not.toContain(TOKEN);
    expect(window.location.href).not.toContain(TOKEN);
  });

  it("says so when the token is wrong, keeps nothing, and lets the person try again", async () => {
    fakeServer({ "GET /health": HEALTH_PROTECTED, "GET /projects": wantsToken([project("default")]) });
    renderGate();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("API token"), "not-the-token-at-all");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("The server did not accept that token.")).toBeInTheDocument();
    expect(allStoredText()).not.toContain("not-the-token-at-all");
    expect(window.sessionStorage.getItem("agentlab.token")).toBeNull();

    await user.clear(screen.getByLabelText("API token"));
    await user.type(screen.getByLabelText("API token"), TOKEN);
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("inside as default")).toBeInTheDocument();
  });

  it("does not offer to sign in with nothing typed", async () => {
    fakeServer({ "GET /health": HEALTH_PROTECTED });
    renderGate();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("API token"), "   ");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeDisabled();
  });

  it("goes straight in with the token it was given earlier in this tab", async () => {
    window.sessionStorage.setItem("agentlab.token", TOKEN);
    const server = fakeServer({ "GET /health": HEALTH_PROTECTED, "GET /projects": wantsToken([project("default")]) });
    renderGate();
    expect(await screen.findByText("inside as default")).toBeInTheDocument();
    expect(screen.queryByLabelText("API token")).not.toBeInTheDocument();
    expect(server.to("GET /projects")[0]!.headers.get("Authorization")).toBe(`Bearer ${TOKEN}`);
  });

  it("asks again, and forgets the token, when the one it remembered is no longer accepted", async () => {
    window.sessionStorage.setItem("agentlab.token", "a-token-the-server-has-rotated");
    fakeServer({ "GET /health": HEALTH_PROTECTED, "GET /projects": wantsToken([project("default")]) });
    renderGate();
    expect(await screen.findByText("The server did not accept the API token. Enter it again.")).toBeInTheDocument();
    expect(window.sessionStorage.getItem("agentlab.token")).toBeNull();
    expect(screen.getByLabelText("API token")).toBeInTheDocument();
  });

  it("goes back to the sign-in page, forgetting the token, when the person signs out", async () => {
    window.sessionStorage.setItem("agentlab.token", TOKEN);
    fakeServer({ "GET /health": HEALTH_PROTECTED, "GET /projects": wantsToken([project("default")]) });
    renderGate();
    await userEvent.click(await screen.findByRole("button", { name: "sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in to AgentLab" })).toBeInTheDocument();
    expect(window.sessionStorage.getItem("agentlab.token")).toBeNull();
  });

  it("is signed out when the server stops accepting the token while the person is working", async () => {
    window.sessionStorage.setItem("agentlab.token", TOKEN);
    let accept = true;
    fakeServer({
      "GET /health": HEALTH_PROTECTED,
      "GET /projects": (call) => (accept && call.headers.get("Authorization") === `Bearer ${TOKEN}` ? [project("default")] : errorResponse(401, "unauthorized", "A valid API token is required.")),
    });
    function Worker() {
      const { api } = useSession();
      return (
        <button type="button" onClick={() => void api.projects().catch(() => undefined)}>
          work
        </button>
      );
    }
    renderWithSession(
      <Gate>
        <Worker />
      </Gate>,
    );
    const work = await screen.findByRole("button", { name: "work" });
    accept = false;
    await userEvent.click(work);
    expect(await screen.findByText("The server did not accept the API token. Enter it again.")).toBeInTheDocument();
    expect(window.sessionStorage.getItem("agentlab.token")).toBeNull();
  });
});

describe("a server that cannot be reached", () => {
  it("says so and offers to try again", async () => {
    let up = false;
    fakeServer({
      "GET /health": () => {
        if (!up) throw new TypeError("fetch failed");
        return HEALTH_OPEN;
      },
      "GET /projects": [project("default")],
    });
    renderGate();
    expect(await screen.findByText("Cannot reach the AgentLab server. Is it running?")).toBeInTheDocument();
    up = true;
    await userEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(await screen.findByText("inside as default")).toBeInTheDocument();
  });

  it("shows what the server said when it answers with an error", async () => {
    fakeServer({ "GET /health": errorResponse(500, "internal", "The store could not be opened.") });
    renderGate();
    expect(await screen.findByText("The store could not be opened.")).toBeInTheDocument();
  });
});
