import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { errorResponse, fakeServer, HEALTH_OPEN, project, renderWithSession, type Handler } from "../test/server";
import { CredentialDialog } from "./CredentialForm";

const CREATE = "POST /credentials";
// Assembled from pieces, so that no scanner takes this test's own stand-in for a secret for a real one.
const SECRET = ["not", "a", "real", "secret", "value", "0123"].join("-");

function setup(answer: Handler = { name: "ci-token", kind: "bearer" }) {
  const server = fakeServer({ "GET /health": HEALTH_OPEN, "GET /projects": [project("default")], [CREATE]: answer });
  const onCreated = vi.fn();
  const onClose = vi.fn();
  renderWithSession(<CredentialDialog onClose={onClose} onCreated={onCreated} />);
  return { server, onCreated, onClose, user: userEvent.setup() };
}

async function fillIn(user: ReturnType<typeof userEvent.setup>, { name = "ci-token", hosts = "api.example.com", secret = SECRET } = {}) {
  await user.type(screen.getByLabelText("Name", { selector: "#cred-name" }), name);
  await user.type(screen.getByLabelText("Hosts it may be sent to"), hosts);
  await user.type(screen.getByLabelText("Token"), secret);
}

describe("CredentialDialog", () => {
  it("says to use credentials meant for testing, and hides what is typed", () => {
    setup();
    expect(screen.getByText("Use credentials meant for testing")).toBeInTheDocument();
    const token = screen.getByLabelText("Token");
    expect(token).toHaveAttribute("type", "password");
    expect(token).toHaveAttribute("autocomplete", "new-password");
  });

  it("says what is missing, and sends nothing, when asked to store an empty form", async () => {
    const { server, user } = setup();
    await user.click(screen.getByRole("button", { name: "Store credential", hidden: false }));
    expect(await screen.findByText("Give the credential a name.")).toBeInTheDocument();
    expect(screen.getByText(/Name the hosts it may be sent to/)).toBeInTheDocument();
    expect(screen.getByText("Enter the secret value.")).toBeInTheDocument();
    expect(server.to(CREATE)).toHaveLength(0);
  });

  it("sends the secret once, to be stored, and empties the field", async () => {
    const { server, user, onCreated } = setup();
    await fillIn(user);
    await user.click(screen.getByRole("button", { name: "Store credential", hidden: false }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("ci-token"));
    const body = server.to(CREATE)[0]!.body as Record<string, unknown>;
    expect(body).toMatchObject({ name: "ci-token", kind: "bearer", scopes: ["api.example.com"], unscoped: false, secrets: { token: SECRET }, references: {} });
    expect(await screen.findByText("Credential ci-token stored, encrypted.")).toBeInTheDocument();
    expect(screen.getByLabelText("Token")).toHaveValue("");
    expect(document.body.innerHTML).not.toContain(SECRET);
  });

  it("sends the name of an environment variable, not a value, when told to read one from the server", async () => {
    const { server, user, onCreated } = setup();
    await user.type(screen.getByLabelText("Name", { selector: "#cred-name" }), "from-env");
    await user.type(screen.getByLabelText("Hosts it may be sent to"), "api.example.com");
    await user.click(screen.getByRole("button", { name: "Use an environment variable" }));
    const field = screen.getByLabelText("Token: environment variable on the server");
    expect(field).toHaveAttribute("type", "text");
    await user.type(field, "MY_TEST_TOKEN");
    await user.click(screen.getByRole("button", { name: "Store credential", hidden: false }));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(server.to(CREATE)[0]!.body).toMatchObject({ secrets: {}, references: { token: "env:MY_TEST_TOKEN" } });
  });

  it("does not take a value that cannot be a variable name for one", async () => {
    const { server, user } = setup();
    await user.type(screen.getByLabelText("Name", { selector: "#cred-name" }), "from-env");
    await user.type(screen.getByLabelText("Hosts it may be sent to"), "api.example.com");
    await user.click(screen.getByRole("button", { name: "Use an environment variable" }));
    await user.type(screen.getByLabelText("Token: environment variable on the server"), "not a name!");
    await user.click(screen.getByRole("button", { name: "Store credential", hidden: false }));
    expect(await screen.findByText("An environment variable name uses letters, digits and underscores.")).toBeInTheDocument();
    expect(server.to(CREATE)).toHaveLength(0);
  });

  it("lets a credential be sent to any host only when that is chosen", async () => {
    const { server, user, onCreated } = setup();
    await user.type(screen.getByLabelText("Name", { selector: "#cred-name" }), "anywhere");
    await user.type(screen.getByLabelText("Token"), SECRET);
    await user.click(screen.getByRole("checkbox", { name: /Allow any host/ }));
    expect(screen.getByLabelText("Hosts it may be sent to")).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Store credential", hidden: false }));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(server.to(CREATE)[0]!.body).toMatchObject({ unscoped: true, scopes: [] });
  });

  it("shows why the server refused it, and keeps what was typed so it can be fixed", async () => {
    const { user, onCreated } = setup(errorResponse(409, "conflict", "A credential with that name exists."));
    await fillIn(user);
    await user.click(screen.getByRole("button", { name: "Store credential", hidden: false }));
    expect(await screen.findByText("A credential with that name exists.")).toBeInTheDocument();
    expect(onCreated).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Token")).toHaveValue(SECRET);
    expect(screen.getByRole("button", { name: "Store credential", hidden: false })).toBeEnabled();
  });

  it("stores nothing when it is cancelled", async () => {
    const { server, user, onClose } = setup();
    await fillIn(user);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(server.to(CREATE)).toHaveLength(0);
  });
});
