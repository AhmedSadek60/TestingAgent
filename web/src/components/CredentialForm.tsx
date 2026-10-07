import { useState } from "react";
import { describeError } from "../api/errors";
import type { CredentialKind } from "../api/types";
import { buildCredential, emptyForm, entriesFor, kindInfo, KINDS, validateCredential, type CredentialForm as Form, type SecretEntry } from "../lib/credentials";
import { useSession } from "../session";
import { Checkbox } from "./controls";
import { Icon } from "./Icon";
import { Field, Modal, Notice, useToast } from "./ui";

function EntryRow({ entry, label, multiline, named, onChange, onRemove, id }: { entry: SecretEntry; label: string; multiline?: boolean; named: boolean; onChange: (e: SecretEntry) => void; onRemove?: () => void; id: string }) {
  const env = entry.mode === "env";
  return (
    <div className="stack tight">
      <div className="row" style={{ alignItems: "flex-end" }}>
        {named && (
          <Field label="Name" htmlFor={`${id}-key`}>
            <input id={`${id}-key`} className="input" value={entry.key} onChange={(e) => onChange({ ...entry, key: e.target.value })} autoComplete="off" spellCheck={false} />
          </Field>
        )}
        <div className="grow">
          <Field label={env ? `${label}: environment variable on the server` : label} htmlFor={`${id}-value`}>
            {multiline && !env ? (
              <textarea id={`${id}-value`} className="textarea mono" value={entry.value} onChange={(e) => onChange({ ...entry, value: e.target.value })} autoComplete="off" spellCheck={false} />
            ) : (
              <input
                id={`${id}-value`}
                className="input"
                type={env ? "text" : "password"}
                value={entry.value}
                onChange={(e) => onChange({ ...entry, value: e.target.value })}
                autoComplete="new-password"
                spellCheck={false}
                placeholder={env ? "MY_TEST_TOKEN" : undefined}
              />
            )}
          </Field>
        </div>
        <button type="button" className="btn small ghost" onClick={() => onChange({ ...entry, mode: env ? "value" : "env", value: "" })}>
          {env ? "Type the value instead" : "Use an environment variable"}
        </button>
        {onRemove && (
          <button type="button" className="btn small ghost" onClick={onRemove} aria-label="Remove this field">
            <Icon name="x" size={14} />
          </button>
        )}
      </div>
    </div>
  );
}

/** Store a test credential. Values are write-only: once saved, nothing in the interface can show them again. */
export function CredentialDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (name: string) => void }) {
  const { api } = useSession();
  const toast = useToast();
  const [form, setForm] = useState<Form>(() => emptyForm());
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const problems = validateCredential(form);
  const info = kindInfo(form.kind);
  const set = (changes: Partial<Form>) => setForm((f) => ({ ...f, ...changes }));

  const submit = async () => {
    setTouched(true);
    if (Object.keys(problems).length > 0) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.createCredential(buildCredential(form));
      setForm(emptyForm()); // the secret values leave memory as soon as they have been sent
      toast.push("good", `Credential ${created.name} stored, encrypted.`);
      onCreated(created.name);
    } catch (e) {
      setError(describeError(e));
      setBusy(false);
    }
  };

  return (
    <Modal
      title="Add a test credential"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn primary" disabled={busy} onClick={submit}>
            Store credential
          </button>
        </>
      }
    >
      <Notice tone="info" title="Use credentials meant for testing">
        Values are encrypted at rest, scoped to the hosts you name, and never shown again, logged, traced or put in a report. Do not use a personal or production account.
      </Notice>
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
        autoComplete="off"
      >
        <div className="form-grid">
          <Field label="Name" htmlFor="cred-name" error={touched ? problems.name : null} help="Targets refer to the credential by this name.">
            <input id="cred-name" className="input" value={form.name} onChange={(e) => set({ name: e.target.value })} aria-invalid={touched && Boolean(problems.name)} autoComplete="off" />
          </Field>
          <Field label="Kind" htmlFor="cred-kind" help={info.detail}>
            <select
              id="cred-kind"
              className="select"
              value={form.kind}
              onChange={(e) => {
                const kind = e.target.value as CredentialKind;
                set({ kind, entries: entriesFor(kind) });
              }}
            >
              {KINDS.map((k) => (
                <option key={k.id} value={k.id}>
                  {k.title}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Hosts it may be sent to" htmlFor="cred-scopes" error={touched ? problems.scopes : null} help="For example api.example.com. Separate several with spaces or commas." className="wide">
            <input id="cred-scopes" className="input" value={form.scopes} onChange={(e) => set({ scopes: e.target.value })} disabled={form.unscoped} aria-invalid={touched && Boolean(problems.scopes)} />
          </Field>
        </div>
        <Checkbox checked={form.unscoped} onChange={(v) => set({ unscoped: v })}>
          Allow any host the target names (not recommended)
        </Checkbox>
        {form.kind === "api_key" && (
          <Field label="Header name" htmlFor="cred-header" help="X-API-Key when left empty.">
            <input id="cred-header" className="input" value={form.headerName} onChange={(e) => set({ headerName: e.target.value })} />
          </Field>
        )}
        <div className="stack">
          {form.entries.map((entry, index) => (
            <EntryRow
              key={index}
              id={`cred-entry-${index}`}
              entry={entry}
              named={info.fields === "rows"}
              label={info.fields === "rows" ? "Value" : (info.fields[index]?.label ?? "Value")}
              multiline={info.fields !== "rows" && info.fields[index]?.multiline}
              onChange={(next) => set({ entries: form.entries.map((e, i) => (i === index ? next : e)) })}
              onRemove={info.fields === "rows" && form.entries.length > 1 ? () => set({ entries: form.entries.filter((_, i) => i !== index) }) : undefined}
            />
          ))}
          {info.fields === "rows" && (
            <div>
              <button type="button" className="btn small" onClick={() => set({ entries: [...form.entries, { key: "", mode: form.kind === "env" ? "env" : "value", value: "" }] })}>
                <Icon name="plus" size={14} /> Add a field
              </button>
            </div>
          )}
          {touched && problems.entries && (
            <span className="error-text" role="alert">
              {problems.entries}
            </span>
          )}
        </div>
        <div className="form-grid">
          <Field label="Description (optional)" htmlFor="cred-desc">
            <input id="cred-desc" className="input" value={form.description} onChange={(e) => set({ description: e.target.value })} maxLength={500} />
          </Field>
          <Field label="Expires (optional)" htmlFor="cred-exp" error={touched ? problems.expiresAt : null} help="After this day the credential is refused.">
            <input id="cred-exp" className="input" type="date" value={form.expiresAt} onChange={(e) => set({ expiresAt: e.target.value })} />
          </Field>
        </div>
        {error && <Notice tone="error">{error}</Notice>}
        <button type="submit" className="sr-only" tabIndex={-1} aria-hidden="true">
          Store credential
        </button>
      </form>
    </Modal>
  );
}
