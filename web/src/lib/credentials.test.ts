import { describe, expect, it } from "vitest";
import { buildCredential, emptyForm, entriesFor, kindInfo, KINDS, NAME_PATTERN, scopeList, validateCredential, type CredentialForm } from "./credentials";

// fake values, assembled from pieces so no scanner mistakes them for real ones
const FAKE = ["demo", "token", "0123456789"].join("-");

const form = (changes: Partial<CredentialForm> = {}): CredentialForm => {
  const base = emptyForm("bearer");
  return { ...base, name: "staging", scopes: "staging.example.com", entries: [{ key: "token", mode: "value", value: FAKE }], ...changes };
};

describe("credential forms", () => {
  it("starts empty with the fields the kind needs", () => {
    expect(emptyForm().entries).toEqual([{ key: "token", mode: "value", value: "" }]);
    expect(entriesFor("basic").map((e) => e.key)).toEqual(["username", "password"]);
    expect(entriesFor("env")[0].mode).toBe("env");
    expect(entriesFor("headers")[0].key).toBe("");
    expect(KINDS.map((k) => k.id)).toContain("browser_state");
    expect(kindInfo("nonsense").id).toBe("bearer");
  });

  it("accepts a complete form", () => {
    expect(validateCredential(form())).toEqual({});
  });

  it("wants a valid name", () => {
    expect(validateCredential(form({ name: "" }))).toHaveProperty("name");
    expect(validateCredential(form({ name: "has space" }))).toHaveProperty("name");
    expect(NAME_PATTERN.test("staging_api-2.v1")).toBe(true);
    expect(NAME_PATTERN.test("-starts-with-dash")).toBe(false);
  });

  it("wants the hosts a secret may go to, unless any host was allowed on purpose", () => {
    expect(validateCredential(form({ scopes: "  " }))).toHaveProperty("scopes");
    expect(validateCredential(form({ scopes: "", unscoped: true }))).toEqual({});
    expect(scopeList("a.example.com, b.example.com  c.example.com")).toEqual(["a.example.com", "b.example.com", "c.example.com"]);
  });

  it("wants every field of a kind that has several", () => {
    const basic = form({ kind: "basic", entries: [{ key: "username", mode: "value", value: "tester" }, { key: "password", mode: "value", value: "" }] });
    expect(validateCredential(basic).entries).toMatch(/password/);
    expect(validateCredential(form({ entries: [{ key: "token", mode: "value", value: "" }] }))).toHaveProperty("entries");
  });

  it("wants named fields for headers and cookies", () => {
    const headers = form({ kind: "headers", entries: [{ key: "", mode: "value", value: FAKE }] });
    expect(validateCredential(headers).entries).toMatch(/Name each field/);
  });

  it("checks the name of an environment variable", () => {
    const env = form({ kind: "env", entries: [{ key: "TOKEN", mode: "env", value: "not valid!" }] });
    expect(validateCredential(env).entries).toMatch(/environment variable/);
    expect(validateCredential(form({ kind: "env", entries: [{ key: "TOKEN", mode: "env", value: "MY_TEST_TOKEN" }] }))).toEqual({});
  });

  it("checks the expiry date", () => {
    expect(validateCredential(form({ expiresAt: "not a date" }))).toHaveProperty("expiresAt");
    expect(validateCredential(form({ expiresAt: "2027-01-31" }))).toEqual({});
  });

  it("builds the request: secrets by value, environment variables by reference", () => {
    const body = buildCredential(
      form({
        kind: "api_key",
        headerName: " X-Key ",
        expiresAt: "2027-01-31",
        entries: [
          { key: "key", mode: "value", value: FAKE },
          { key: "extra", mode: "env", value: "env:MY_VAR" },
          { key: "blank", mode: "value", value: "   " },
        ],
      }),
    );
    expect(body).toMatchObject({
      name: "staging",
      kind: "api_key",
      scopes: ["staging.example.com"],
      unscoped: false,
      header_name: "X-Key",
      secrets: { key: FAKE },
      references: { extra: "env:MY_VAR" },
    });
    expect(body.expires_at).toBe("2027-01-31T00:00:00.000Z");
    expect(Object.keys(body.secrets ?? {})).not.toContain("blank");
  });

  it("sends no host scope for an unscoped credential and no header for another kind", () => {
    const body = buildCredential(form({ unscoped: true, scopes: "ignored.example.com", headerName: "X-Ignored" }));
    expect(body.scopes).toEqual([]);
    expect(body.header_name).toBeNull();
  });
});
