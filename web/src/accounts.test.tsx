import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Shell } from "./App";
import { Gate } from "./pages/SignIn";
import { errorResponse, fakeServer, HEALTH_PROTECTED, project, renderWithSession } from "./test/server";

const OPTIONS = { registration: "open", min_password_length: 12, api_token_sign_in: true, password_sign_in: true };
const ME = (over: Record<string, unknown> = {}) => ({
  kind: "user",
  role: "maintainer",
  username: "alice",
  display_name: "Alice",
  must_change_password: false,
  csrf_token: "csrf-for-alice",
  operator: true,
  can_manage_accounts: false,
  ...over,
});
const account = (over: Record<string, unknown> = {}) => ({
  id: "u-1",
  username: "alice",
  display_name: "alice",
  email: null,
  role: "maintainer",
  status: "active",
  must_change_password: false,
  created_at: "2026-10-12T10:00:00Z",
  last_login_at: null,
  locked_until: null,
  ...over,
});
const renderShell = () =>
  renderWithSession(
    <Gate>
      <Shell />
    </Gate>,
  );

async function signIn(name = "alice", password = "correct-horse-battery-7") {
  await userEvent.type(await screen.findByLabelText("User name"), name);
  await userEvent.type(screen.getByLabelText("Password"), password);
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("signing in with a user name and password", () => {
  it("offers a password form first, and still offers the API token", async () => {
    fakeServer({ "GET /health": HEALTH_PROTECTED, "GET /auth/options": OPTIONS });
    renderShell();
    expect(await screen.findByLabelText("User name")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create an account" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Sign in with an API token" }));
    expect(screen.getByLabelText("API token")).toBeInTheDocument();
  });

  it("opens the interface for a maintainer, and sends the CSRF token with a change but never an API token", async () => {
    const server = fakeServer({
      "GET /health": HEALTH_PROTECTED,
      "GET /auth/options": OPTIONS,
      "POST /auth/login": { me: ME(), account: account() },
      "GET /projects": [project("default")],
      "POST /projects": (call) => project((call.body as { name: string }).name),
    });
    renderShell();
    await signIn();
    expect(await screen.findByLabelText("Project")).toBeInTheDocument();
    expect(server.to("POST /auth/login")[0]!.body).toEqual({ username: "alice", password: "correct-horse-battery-7" });
    expect(server.calls.every((c) => c.headers.get("Authorization") === null)).toBe(true);
    expect(window.sessionStorage.length).toBe(0);
  });

  it("says why it was refused and stays on the form", async () => {
    fakeServer({
      "GET /health": HEALTH_PROTECTED,
      "GET /auth/options": OPTIONS,
      "POST /auth/login": errorResponse(401, "unauthorized", "wrong user name or password"),
    });
    renderShell();
    await signIn("alice", "not-the-password-1");
    expect(await screen.findByText("wrong user name or password")).toBeInTheDocument();
    expect(screen.getByLabelText("User name")).toBeInTheDocument();
  });

  it("tells a locked person when to try again", async () => {
    fakeServer({
      "GET /health": HEALTH_PROTECTED,
      "GET /auth/options": OPTIONS,
      "POST /auth/login": errorResponse(429, "rate_limited", "this account is locked after repeated wrong passwords; try again later"),
    });
    renderShell();
    await signIn();
    expect(await screen.findByText(/locked after repeated wrong passwords/)).toBeInTheDocument();
  });

  it("goes straight in when the browser already has a session", async () => {
    fakeServer({
      "GET /health": HEALTH_PROTECTED,
      "GET /auth/options": OPTIONS,
      "GET /auth/me": ME(),
      "GET /projects": [project("default")],
    });
    renderShell();
    expect(await screen.findByLabelText("Project")).toBeInTheDocument();
    expect(screen.queryByLabelText("User name")).not.toBeInTheDocument();
  });

  it("ends the session on the server when the person signs out", async () => {
    const server = fakeServer({
      "GET /health": HEALTH_PROTECTED,
      "GET /auth/options": OPTIONS,
      "GET /auth/me": ME(),
      "GET /projects": [project("default")],
      "POST /auth/logout": new Response(null, { status: 204 }),
    });
    renderShell();
    await userEvent.click(await screen.findByRole("button", { name: /sign out/i }));
    expect(await screen.findByLabelText("User name")).toBeInTheDocument();
    expect(server.to("POST /auth/logout")).toHaveLength(1);
    expect(server.to("POST /auth/logout")[0]!.headers.get("X-CSRF-Token")).toBe("csrf-for-alice");
  });
});

describe("registering", () => {
  it("makes an account, signs in, and shows a reporter only their account", async () => {
    const reporter = ME({ role: "reporter", username: "newbie", display_name: "newbie", operator: false, csrf_token: "c2" });
    const server = fakeServer({
      "GET /health": HEALTH_PROTECTED,
      "GET /auth/options": OPTIONS,
      "POST /auth/register": { account: account({ role: "reporter", username: "newbie" }), signed_in: true, me: reporter },
    });
    renderShell();
    await userEvent.click(await screen.findByRole("button", { name: "Create an account" }));
    await userEvent.type(screen.getByLabelText("User name"), "newbie");
    await userEvent.type(screen.getByLabelText("Password"), "correct-horse-battery-7");
    await userEvent.type(screen.getByLabelText("Password again"), "correct-horse-battery-7");
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("heading", { name: "Your account" })).toBeInTheDocument();
    expect(screen.getByText(/Feedback & Support: feature requests and issue reports/)).toBeInTheDocument();
    expect(screen.getByText(/not in this interface yet/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Project")).not.toBeInTheDocument();
    expect(server.to("GET /projects"), "a reporter's interface never asks for what a reporter may not read").toHaveLength(0);
    expect(server.to("POST /auth/register")[0]!.body).toMatchObject({ username: "newbie", password: "correct-horse-battery-7" });
  });

  it("will not send two passwords that differ", async () => {
    const server = fakeServer({ "GET /health": HEALTH_PROTECTED, "GET /auth/options": OPTIONS });
    renderShell();
    await userEvent.click(await screen.findByRole("button", { name: "Create an account" }));
    await userEvent.type(screen.getByLabelText("User name"), "newbie");
    await userEvent.type(screen.getByLabelText("Password"), "correct-horse-battery-7");
    await userEvent.type(screen.getByLabelText("Password again"), "correct-horse-battery-8");
    expect(screen.getByText("The two passwords differ.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create account" })).toBeDisabled();
    expect(server.to("POST /auth/register")).toHaveLength(0);
  });

  it("says that an account waits for approval when the server asks for one", async () => {
    fakeServer({
      "GET /health": HEALTH_PROTECTED,
      "GET /auth/options": { ...OPTIONS, registration: "approval" },
      "POST /auth/register": { account: account({ status: "pending", role: "reporter" }), signed_in: false, me: null },
    });
    renderShell();
    await userEvent.click(await screen.findByRole("button", { name: "Create an account" }));
    await userEvent.type(screen.getByLabelText("User name"), "newbie");
    await userEvent.type(screen.getByLabelText("Password"), "correct-horse-battery-7");
    await userEvent.type(screen.getByLabelText("Password again"), "correct-horse-battery-7");
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText(/An administrator has to approve it/)).toBeInTheDocument();
  });

  it("offers no registration when the server has closed it", async () => {
    fakeServer({ "GET /health": HEALTH_PROTECTED, "GET /auth/options": { ...OPTIONS, registration: "closed" } });
    renderShell();
    expect(await screen.findByLabelText("User name")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create an account" })).not.toBeInTheDocument();
  });
});

describe("a temporary password", () => {
  it("is the only thing the person can use until it is changed", async () => {
    const server = fakeServer({
      "GET /health": HEALTH_PROTECTED,
      "GET /auth/options": OPTIONS,
      "GET /auth/me": ME({ must_change_password: true, operator: false }),
      "POST /auth/password": new Response(null, { status: 204 }),
    });
    renderShell();
    expect(await screen.findByText("Choose your own password")).toBeInTheDocument();
    expect(server.to("GET /projects")).toHaveLength(0);
    await userEvent.type(screen.getByLabelText("Current password"), "temporary-value-1");
    await userEvent.type(screen.getByLabelText("New password"), "correct-horse-battery-7");
    await userEvent.type(screen.getByLabelText("New password again"), "correct-horse-battery-7");
    await userEvent.click(screen.getByRole("button", { name: "Change password" }));
    await waitFor(() => expect(server.to("POST /auth/password")).toHaveLength(1));
    expect(server.to("POST /auth/password")[0]!.body).toEqual({
      current_password: "temporary-value-1",
      new_password: "correct-horse-battery-7",
    });
  });
});

describe("account administration", () => {
  const admin = ME({ role: "admin", can_manage_accounts: true });

  it("is in the navigation for an administrator and lists the accounts", async () => {
    fakeServer({
      "GET /health": HEALTH_PROTECTED,
      "GET /auth/options": OPTIONS,
      "GET /auth/me": admin,
      "GET /projects": [project("default")],
      "GET /users": [account({ role: "admin" }), account({ id: "u-2", username: "bob", role: "reporter", status: "pending" })],
      "GET /users/events": [{ event: "registered", actor: "bob", subject: "bob", client: "10.0.0.1", detail: {}, at: "2026-10-12T10:00:00Z" }],
    });
    renderWithSession(
      <Gate>
        <Shell />
      </Gate>,
      { route: "/accounts" },
    );
    const table = (await screen.findAllByRole("table"))[0]!;
    expect(within(table).getByText("bob")).toBeInTheDocument();
    expect(within(table).getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Accounts" })).toBeInTheDocument();
  });

  it("changes a role with the CSRF token, and shows a temporary password once", async () => {
    const server = fakeServer({
      "GET /health": HEALTH_PROTECTED,
      "GET /auth/options": OPTIONS,
      "GET /auth/me": admin,
      "GET /projects": [project("default")],
      "GET /users": [account({ id: "u-2", username: "bob", role: "reporter" })],
      "GET /users/events": [],
      "PATCH /users/u-2": account({ id: "u-2", username: "bob", role: "maintainer" }),
      "POST /users/u-2/reset-password": { account: account({ id: "u-2", username: "bob" }), temporary_password: "Temp-Pass-ABCDEFGH-2345" },
    });
    renderWithSession(
      <Gate>
        <Shell />
      </Gate>,
      { route: "/accounts" },
    );
    await userEvent.selectOptions(await screen.findByLabelText("Role of bob"), "maintainer");
    await waitFor(() => expect(server.to("PATCH /users/u-2")).toHaveLength(1));
    expect(server.to("PATCH /users/u-2")[0]!.body).toEqual({ role: "maintainer" });
    expect(server.to("PATCH /users/u-2")[0]!.headers.get("X-CSRF-Token")).toBe("csrf-for-alice");
    await userEvent.click(screen.getByRole("button", { name: "Reset password" }));
    expect(await screen.findByText("Temp-Pass-ABCDEFGH-2345")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "I have passed it on" }));
    expect(screen.queryByText("Temp-Pass-ABCDEFGH-2345")).not.toBeInTheDocument();
  });

  it("is not in the navigation for a maintainer", async () => {
    fakeServer({
      "GET /health": HEALTH_PROTECTED,
      "GET /auth/options": OPTIONS,
      "GET /auth/me": ME(),
      "GET /projects": [project("default")],
    });
    renderShell();
    await screen.findByLabelText("Project");
    expect(screen.queryByRole("link", { name: "Accounts" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Your account" })).toBeInTheDocument();
  });
});
