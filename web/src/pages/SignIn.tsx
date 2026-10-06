import { useState } from "react";
import { Icon } from "../components/Icon";
import { ErrorNote, Loading, Notice } from "../components/ui";
import { useSession } from "../session";

/** What stands between a person and the interface until the server is reachable and, when it asks, the token is accepted. */
export function Gate({ children }: { children: React.ReactNode }) {
  const session = useSession();
  if (session.phase === "ready") return <>{children}</>;
  if (session.phase === "connecting") return <div className="signin"><Loading label="Connecting to the server…" /></div>;
  if (session.phase === "unreachable") {
    return (
      <div className="signin">
        <div className="card">
          <div className="card-body stack">
            <h1>AgentLab</h1>
            <ErrorNote error={session.problem ?? "Cannot reach the server."} onRetry={session.retry} />
          </div>
        </div>
      </div>
    );
  }
  return <SignIn />;
}

function SignIn() {
  const { signIn, problem } = useSession();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <div className="signin">
      <form
        className="card"
        onSubmit={async (event) => {
          event.preventDefault();
          setBusy(true);
          await signIn(token);
          setBusy(false);
        }}
      >
        <div className="card-body stack">
          <div className="row">
            <span className="brand-mark" style={{ width: 32, height: 32, borderRadius: 8, background: "var(--accent)", color: "var(--accent-contrast)", display: "grid", placeItems: "center" }} aria-hidden="true">
              <Icon name="lock" size={16} />
            </span>
            <h1>Sign in to AgentLab</h1>
          </div>
          <p className="muted">This server asks for an API token. It is kept in this browser tab only, and is sent only to this server.</p>
          {problem && <Notice tone="error">{problem}</Notice>}
          <div className="field">
            <label htmlFor="api-token">API token</label>
            <input
              id="api-token"
              className="input"
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={token}
              onChange={(event) => setToken(event.target.value)}
              autoFocus
            />
            <span className="help">The value of the server's configured token (server.token_ref).</span>
          </div>
          <button type="submit" className="btn primary" disabled={busy || !token.trim()}>
            Sign in
          </button>
        </div>
      </form>
    </div>
  );
}
