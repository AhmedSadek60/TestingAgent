import { useState } from "react";
import { describeError } from "../api/errors";
import type { Credential } from "../api/types";
import { CredentialDialog } from "../components/CredentialForm";
import { DataTable } from "../components/DataTable";
import { Icon } from "../components/Icon";
import { Async, Badge, Card, ConfirmDialog, Empty, Field, Modal, Notice, PageHeader, useToast } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { formatTime } from "../lib/format";
import { kindInfo } from "../lib/credentials";
import { useSession } from "../session";

function Rotate({ credential, onClose, onDone }: { credential: Credential; onClose: () => void; onDone: () => void }) {
  const { api } = useSession();
  const toast = useToast();
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(credential.fields.map((f) => [f, ""])));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const filled = Object.fromEntries(Object.entries(values).filter(([, v]) => v !== ""));
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.rotateCredential(credential.name, filled);
      setValues({});
      toast.push("good", `New values stored for ${credential.name}.`);
      onDone();
    } catch (e) {
      setError(describeError(e));
      setBusy(false);
    }
  };
  return (
    <Modal
      title={`Replace the values of ${credential.name}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn primary" disabled={busy || Object.keys(filled).length === 0} onClick={submit}>
            Store the new values
          </button>
        </>
      }
    >
      <p className="muted">Enter the new value for each field you want to replace. Fields left empty keep their stored value. The old values are discarded.</p>
      {credential.fields.length === 0 && <Notice tone="info">This credential keeps no stored values (it reads environment variables), so there is nothing to replace.</Notice>}
      {credential.fields.map((field) => (
        <Field key={field} label={field} htmlFor={`rot-${field}`}>
          <input id={`rot-${field}`} className="input" type="password" autoComplete="new-password" value={values[field] ?? ""} onChange={(e) => setValues({ ...values, [field]: e.target.value })} />
        </Field>
      ))}
      {error && <Notice tone="error">{error}</Notice>}
    </Modal>
  );
}

/** Test credentials: stored encrypted, scoped to hosts, and write-only. */
export function Credentials() {
  const { api } = useSession();
  const toast = useToast();
  const list = useAsync((signal) => api.credentials(signal), [api]);
  const [adding, setAdding] = useState(false);
  const [rotating, setRotating] = useState<Credential | null>(null);
  const [deleting, setDeleting] = useState<Credential | null>(null);
  const [busy, setBusy] = useState(false);

  const remove = async () => {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.deleteCredential(deleting.name);
      toast.push("info", `Credential ${deleting.name} deleted.`);
      setDeleting(null);
      list.reload();
    } catch (e) {
      toast.push("error", describeError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        title="Credentials"
        subtitle="Accounts AgentLab may use to test authenticated agents. Values are encrypted, scoped to hosts and never shown again."
        actions={
          <button type="button" className="btn primary" onClick={() => setAdding(true)}>
            <Icon name="plus" /> Add a credential
          </button>
        }
      />
      <Card padded={false}>
        <Async state={list}>
          {(data) => (
            <DataTable
              caption="Credentials"
              rows={data}
              rowKey={(c) => c.name}
              empty={<Empty title="No credential stored">Tests that need an account are reported as blocked until one is added. Add one with the button above.</Empty>}
              columns={[
                { key: "name", header: "Name", render: (c) => <><strong>{c.name}</strong>{c.description && <div className="muted small">{c.description}</div>}</>, sort: (a, b) => a.name.localeCompare(b.name) },
                { key: "kind", header: "Kind", render: (c) => kindInfo(c.kind).title },
                { key: "scopes", header: "May be sent to", render: (c) => (c.scopes.length ? c.scopes.map((s) => <span key={s} className="tag" style={{ marginRight: 4 }}>{s}</span>) : <Badge tone="warn">any host the target names</Badge>) },
                { key: "fields", header: "Stored fields", render: (c) => <span className="muted small">{[...c.fields, ...Object.keys(c.references).map((r) => `${r} → ${c.references[r]}`)].join(", ") || "none"}</span> },
                { key: "version", header: "Version", numeric: true, render: (c) => c.secret_version },
                { key: "expires", header: "Expires", render: (c) => (c.expires_at ? formatTime(c.expires_at) : <span className="muted">never</span>) },
                {
                  key: "actions",
                  header: <span className="sr-only">Actions</span>,
                  render: (c) => (
                    <div className="row" style={{ flexWrap: "nowrap" }}>
                      <button type="button" className="btn small" onClick={() => setRotating(c)}>
                        <Icon name="refresh" size={14} /> Replace values
                      </button>
                      <button type="button" className="btn small danger" onClick={() => setDeleting(c)}>
                        <Icon name="trash" size={14} /> Delete
                      </button>
                    </div>
                  ),
                },
              ]}
            />
          )}
        </Async>
      </Card>
      {adding && (
        <CredentialDialog
          onClose={() => setAdding(false)}
          onCreated={() => {
            setAdding(false);
            list.reload();
          }}
        />
      )}
      {rotating && (
        <Rotate
          credential={rotating}
          onClose={() => setRotating(null)}
          onDone={() => {
            setRotating(null);
            list.reload();
          }}
        />
      )}
      {deleting && (
        <ConfirmDialog title={`Delete ${deleting.name}?`} confirmLabel="Delete credential" danger busy={busy} onConfirm={remove} onClose={() => setDeleting(null)}>
          <p>The stored values are erased and cannot be recovered. Targets that name this credential will have their authenticated tests reported as blocked.</p>
        </ConfirmDialog>
      )}
    </>
  );
}
