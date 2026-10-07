/** Credential forms as plain functions. Secret values are only ever held in the form while it is open and are sent once;
 * the server never returns them. */
import type { CredentialCreate, CredentialKind } from "../api/types";

export const NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;

export interface KindInfo {
  id: CredentialKind;
  title: string;
  detail: string;
  /** Fixed fields, or `rows` when the person names the fields (headers, cookies) or `references` for env. */
  fields: { key: string; label: string; multiline?: boolean }[] | "rows";
}

export const KINDS: KindInfo[] = [
  { id: "bearer", title: "Bearer token", detail: "Sent as Authorization: Bearer …", fields: [{ key: "token", label: "Token" }] },
  { id: "api_key", title: "API key", detail: "Sent in a header (X-API-Key unless you name another).", fields: [{ key: "key", label: "Key" }] },
  { id: "basic", title: "Username and password", detail: "Sent as HTTP Basic authentication.", fields: [{ key: "username", label: "Username" }, { key: "password", label: "Password" }] },
  { id: "headers", title: "Custom headers", detail: "Header names and values you choose.", fields: "rows" },
  { id: "cookies", title: "Cookies", detail: "Cookie names and values you choose.", fields: "rows" },
  { id: "oauth_token", title: "OAuth access token", detail: "Sent as Authorization: Bearer … (a token you already hold).", fields: [{ key: "token", label: "Access token" }] },
  { id: "browser_state", title: "Browser session", detail: "A Playwright storage state of a signed-in test account.", fields: [{ key: "state", label: "Storage state (JSON)", multiline: true }] },
  { id: "env", title: "Environment variables", detail: "Values read from the server's environment when used; nothing is stored.", fields: "rows" },
];

export function kindInfo(kind: string): KindInfo {
  return KINDS.find((k) => k.id === kind) ?? KINDS[0];
}

/** One secret: typed in (`value`) or read from an environment variable of the server (`env`). */
export interface SecretEntry {
  key: string;
  mode: "value" | "env";
  value: string;
}

export interface CredentialForm {
  name: string;
  kind: CredentialKind;
  description: string;
  scopes: string;
  unscoped: boolean;
  headerName: string;
  expiresAt: string;
  entries: SecretEntry[];
}

export function emptyForm(kind: CredentialKind = "bearer"): CredentialForm {
  return { name: "", kind, description: "", scopes: "", unscoped: false, headerName: "", expiresAt: "", entries: entriesFor(kind) };
}

export function entriesFor(kind: CredentialKind): SecretEntry[] {
  const info = kindInfo(kind);
  if (info.fields === "rows") return [{ key: "", mode: kind === "env" ? "env" : "value", value: "" }];
  return info.fields.map((f) => ({ key: f.key, mode: "value" as const, value: "" }));
}

export function scopeList(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export function validateCredential(form: CredentialForm): Record<string, string> {
  const p: Record<string, string> = {};
  if (!form.name.trim()) p.name = "Give the credential a name.";
  else if (!NAME_PATTERN.test(form.name.trim())) p.name = "Use letters, digits, dots, dashes and underscores (up to 64), starting with a letter or digit.";
  if (!form.unscoped && scopeList(form.scopes).length === 0) p.scopes = "Name the hosts it may be sent to (for example api.example.com), or allow any host.";
  const filled = form.entries.filter((e) => e.value.trim() !== "");
  if (filled.length === 0) p.entries = "Enter the secret value.";
  const info = kindInfo(form.kind);
  if (info.fields === "rows") {
    if (form.entries.some((e) => e.value.trim() !== "" && !e.key.trim())) p.entries = "Name each field.";
  } else {
    const missing = info.fields.filter((f) => !form.entries.find((e) => e.key === f.key && e.value.trim() !== ""));
    if (missing.length > 0 && filled.length > 0) p.entries = `Missing: ${missing.map((m) => m.label.toLowerCase()).join(", ")}.`;
  }
  const envBad = form.entries.find((e) => e.mode === "env" && e.value.trim() !== "" && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(e.value.trim().replace(/^env:/, "")));
  if (envBad) p.entries = "An environment variable name uses letters, digits and underscores.";
  if (form.expiresAt && Number.isNaN(Date.parse(form.expiresAt))) p.expiresAt = "Enter a valid date.";
  return p;
}

export function buildCredential(form: CredentialForm): CredentialCreate {
  const secrets: Record<string, string> = {};
  const references: Record<string, string> = {};
  for (const e of form.entries) {
    if (!e.value.trim()) continue;
    if (e.mode === "env") references[e.key.trim()] = `env:${e.value.trim().replace(/^env:/, "")}`;
    else secrets[e.key.trim()] = e.value;
  }
  return {
    name: form.name.trim(),
    kind: form.kind,
    description: form.description.trim(),
    scopes: form.unscoped ? [] : scopeList(form.scopes),
    unscoped: form.unscoped,
    header_name: form.kind === "api_key" && form.headerName.trim() ? form.headerName.trim() : null,
    expires_at: form.expiresAt ? new Date(form.expiresAt).toISOString() : null,
    secrets,
    references,
  };
}
