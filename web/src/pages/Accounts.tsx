import { useState } from "react";
import { describeError } from "../api/errors";
import type { Account, Role } from "../api/types";
import { Async, Badge, Card, Empty, Field, Modal, Notice, PageHeader, useToast } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { formatTime } from "../lib/format";
import { useSession } from "../session";

const ROLES: Role[] = ["reporter", "maintainer", "admin"];
const STATUS_TONE: Record<Account["status"], "good" | "warn" | "bad"> = { active: "good", pending: "warn", disabled: "bad" };

/** What was handed out once: a temporary password has to be passed to the person, because nothing is e-mailed. */
function TemporaryPasswordDialog({ username, password, onClose }: { username: string; password: string; onClose: () => void }) {
  return (
    <Modal
      title={`Temporary password for ${username}`}
      onClose={onClose}
      footer={
        <button type="button" className="btn primary" onClick={onClose}>
          I have passed it on
        </button>
      }
    >
      <p className="muted">
        This is shown once. Hand it to the person yourself (nothing is e-mailed). It has to be changed at the first sign-in, and every session
        of the account has ended.
      </p>
      <pre className="code" aria-label="Temporary password" style={{ userSelect: "all" }}>
        {password}
      </pre>
    </Modal>
  );
}

function CreateAccount({ onClose, onCreated }: { onClose: () => void; onCreated: (username: string, temporary: string | null) => void }) {
  const { api } = useSession();
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<Role>("reporter");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const made = await api.createUser({ username: username.trim(), role, display_name: displayName.trim() });
      onCreated(made.account.username, made.temporary_password);
    } catch (e) {
      setError(describeError(e));
      setBusy(false);
    }
  };
  return (
    <Modal
      title="Create an account"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn primary" disabled={busy || username.trim().length < 3} onClick={submit}>
            Create account
          </button>
        </>
      }
    >
      <p className="muted">A temporary password is made and shown once. The person must change it when they first sign in.</p>
      <Field label="User name" htmlFor="new-username" error={error}>
        <input id="new-username" className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus maxLength={64} />
      </Field>
      <Field label="Name shown to others (optional)" htmlFor="new-display">
        <input id="new-display" className="input" value={displayName} onChange={(e) => setDisplayName(e.target.value)} maxLength={120} />
      </Field>
      <Field label="Role" htmlFor="new-role" help="A reporter can only use Feedback & Support. A maintainer has what the shared API token has. An admin also manages accounts.">
        <select id="new-role" className="select" value={role} onChange={(e) => setRole(e.target.value as Role)}>
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      </Field>
    </Modal>
  );
}

/** Account administration: who has an account, with what role, and what happened to them. */
export function Accounts() {
  const { api, me } = useSession();
  const toast = useToast();
  const users = useAsync((signal) => api.users(signal), [api]);
  const events = useAsync((signal) => api.accountEvents(signal), [api]);
  const [creating, setCreating] = useState(false);
  const [handedOut, setHandedOut] = useState<{ username: string; password: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const act = async (work: () => Promise<void>, done: string) => {
    setError(null);
    try {
      await work();
      toast.push("good", done);
      users.reload();
      events.reload();
    } catch (e) {
      setError(describeError(e));
    }
  };

  return (
    <>
      <PageHeader
        title="Accounts"
        subtitle="Who can sign in, with what role. Changing a role or a status ends that person's sessions."
        actions={
          <button type="button" className="btn primary" onClick={() => setCreating(true)}>
            Create an account
          </button>
        }
      />
      <div className="stack">
        {error && <Notice tone="error">{error}</Notice>}
        <Card title="Accounts" padded={false}>
          <Async state={users}>
            {(rows) =>
              rows.length === 0 ? (
                <Empty title="No accounts yet">Make the first one with the command line: agentlab users create NAME --role admin.</Empty>
              ) : (
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>User</th>
                        <th>Role</th>
                        <th>Status</th>
                        <th>Last sign-in</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((u) => {
                        const you = me?.username === u.username;
                        return (
                          <tr key={u.id}>
                            <td>
                              <strong>{u.username}</strong> {you && <Badge tone="neutral">you</Badge>}
                              <div className="muted small">{u.display_name !== u.username ? u.display_name : ""}{u.email ? ` · ${u.email}` : ""}</div>
                            </td>
                            <td>
                              <select
                                className="select"
                                aria-label={`Role of ${u.username}`}
                                value={u.role}
                                onChange={(event) => act(async () => void (await api.updateUser(u.id, { role: event.target.value as Role })), `${u.username} is now ${event.target.value}.`)}
                              >
                                {ROLES.map((r) => (
                                  <option key={r} value={r}>
                                    {r}
                                  </option>
                                ))}
                              </select>
                            </td>
                            <td>
                              <Badge tone={STATUS_TONE[u.status]}>{u.status}</Badge>
                              {u.locked_until && <Badge tone="warn">locked</Badge>}
                              {u.must_change_password && <Badge tone="neutral">temporary password</Badge>}
                            </td>
                            <td>{u.last_login_at ? formatTime(u.last_login_at) : "never"}</td>
                            <td>
                              <div className="row" style={{ flexWrap: "wrap", gap: 4 }}>
                                {u.status === "pending" && (
                                  <button type="button" className="btn" onClick={() => act(async () => void (await api.updateUser(u.id, { status: "active" })), `${u.username} was approved.`)}>
                                    Approve
                                  </button>
                                )}
                                {u.status === "active" && (
                                  <button type="button" className="btn" onClick={() => act(async () => void (await api.updateUser(u.id, { status: "disabled" })), `${u.username} was disabled.`)}>
                                    Disable
                                  </button>
                                )}
                                {u.status === "disabled" && (
                                  <button type="button" className="btn" onClick={() => act(async () => void (await api.updateUser(u.id, { status: "active" })), `${u.username} was enabled.`)}>
                                    Enable
                                  </button>
                                )}
                                {u.locked_until && (
                                  <button type="button" className="btn" onClick={() => act(async () => void (await api.unlockUser(u.id)), `${u.username} was unlocked.`)}>
                                    Unlock
                                  </button>
                                )}
                                <button
                                  type="button"
                                  className="btn"
                                  onClick={() =>
                                    act(async () => {
                                      const reset = await api.resetPassword(u.id);
                                      setHandedOut({ username: u.username, password: reset.temporary_password ?? "" });
                                    }, `A temporary password was set for ${u.username}.`)
                                  }
                                >
                                  Reset password
                                </button>
                              </div>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )
            }
          </Async>
        </Card>
        <Card title="What happened" padded={false}>
          <Async state={events}>
            {(rows) =>
              rows.length === 0 ? (
                <Empty title="Nothing yet" />
              ) : (
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>When</th>
                        <th>What</th>
                        <th>Who</th>
                        <th>To</th>
                        <th>From</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((e, i) => (
                        <tr key={`${e.at}-${i}`}>
                          <td>{formatTime(e.at)}</td>
                          <td>{e.event.replace(/_/g, " ")}</td>
                          <td>{e.actor || "-"}</td>
                          <td>{e.subject || "-"}</td>
                          <td className="muted">{e.client || "-"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )
            }
          </Async>
        </Card>
      </div>
      {creating && (
        <CreateAccount
          onClose={() => setCreating(false)}
          onCreated={(username, temporary) => {
            setCreating(false);
            users.reload();
            events.reload();
            if (temporary) setHandedOut({ username, password: temporary });
          }}
        />
      )}
      {handedOut && <TemporaryPasswordDialog username={handedOut.username} password={handedOut.password} onClose={() => setHandedOut(null)} />}
    </>
  );
}
