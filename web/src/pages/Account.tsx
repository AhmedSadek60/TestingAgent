import { useState } from "react";
import { describeError } from "../api/errors";
import type { Role } from "../api/types";
import { Badge, Card, Field, Notice, PageHeader, useToast } from "../components/ui";
import { useSession } from "../session";

const ROLE_TEXT: Record<Role, string> = {
  reporter:
    "Your role is for Feedback & Support: you can request features, report issues and follow your own submissions. The rest of AgentLab is not open to this role.",
  maintainer: "You can use everything the shared API token can, and triage submissions.",
  admin: "You can use everything a maintainer can, and manage accounts.",
};

/** Who you are here, and the one thing about your account you can change yourself: the password. */
export function Account() {
  const { api, me, signOut, refreshMe } = useSession();
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isPerson = me?.kind === "user";
  const mismatch = again !== "" && again !== next;
  const change = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.changePassword(current, next);
      setCurrent("");
      setNext("");
      setAgain("");
      toast.push("good", "Your password was changed. Your other sessions were ended.");
      await refreshMe();
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <PageHeader title="Your account" subtitle={isPerson ? undefined : "You are using a shared credential, not a personal account."} />
      <div className="stack">
        {me?.must_change_password && (
          <Notice tone="warn" title="Choose your own password">
            Your password is a temporary one. Until you change it, this is the only page you can use.
          </Notice>
        )}
        <Card title="Who you are">
          <div className="stack">
            <div className="row">
              <strong>{me?.display_name ?? me?.username ?? (me?.kind === "service" ? "Shared API token" : "This machine")}</strong>
              {me && <Badge tone={me.role === "admin" ? "info" : "neutral"}>{me.role}</Badge>}
            </div>
            {me && <p className="muted">{ROLE_TEXT[me.role]}</p>}
            <div>
              <button type="button" className="btn" onClick={signOut}>
                Sign out
              </button>
            </div>
          </div>
        </Card>
        {isPerson && (
          <Card title="Change your password">
            <form
              className="stack"
              onSubmit={(event) => {
                event.preventDefault();
                void change();
              }}
            >
              <Field label="Current password" htmlFor="current-password">
                <input id="current-password" className="input" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
              </Field>
              <Field label="New password" htmlFor="new-password" help="At least 12 characters (the server may ask for more), not your user name, not a common password.">
                <input id="new-password" className="input" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
              </Field>
              <Field label="New password again" htmlFor="new-password-again" error={mismatch ? "The two passwords differ." : null}>
                <input id="new-password-again" className="input" type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} />
              </Field>
              {error && <Notice tone="error">{error}</Notice>}
              <div>
                <button type="submit" className="btn primary" disabled={busy || !current || !next || next !== again}>
                  Change password
                </button>
              </div>
            </form>
          </Card>
        )}
      </div>
    </>
  );
}
