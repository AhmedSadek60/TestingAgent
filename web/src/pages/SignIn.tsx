import { useState } from "react";
import { Icon } from "../components/Icon";
import { ErrorNote, Loading, Notice } from "../components/ui";
import { useSession } from "../session";

/** What stands between a person and the interface until the server is reachable and, when it asks, they have signed in. */
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

type Mode = "password" | "register" | "token";

function Mark() {
  return (
    <span className="brand-mark" style={{ width: 32, height: 32, borderRadius: 8, background: "var(--accent)", color: "var(--accent-contrast)", display: "grid", placeItems: "center" }} aria-hidden="true">
      <Icon name="lock" size={16} />
    </span>
  );
}

function SignIn() {
  const { options, problem, notice } = useSession();
  const passwords = options?.password_sign_in ?? false; // an older server does not say: it has the token only
  const mayRegister = passwords && options?.registration !== "closed";
  const mayUseToken = options ? options.api_token_sign_in : true;
  const [mode, setMode] = useState<Mode>(passwords ? "password" : "token");
  return (
    <div className="signin">
      <div className="card">
        <div className="card-body stack">
          <div className="row">
            <Mark />
            <h1>{mode === "register" ? "Create an account" : "Sign in to AgentLab"}</h1>
          </div>
          {notice && <Notice tone="good">{notice}</Notice>}
          {problem && <Notice tone="error">{problem}</Notice>}
          {mode === "password" && <PasswordForm />}
          {mode === "register" && <RegisterForm minLength={options?.min_password_length ?? 12} approval={options?.registration === "approval"} />}
          {mode === "token" && <TokenForm />}
          <div className="stack" style={{ gap: 4 }}>
            {mode !== "password" && passwords && (
              <button type="button" className="btn ghost" onClick={() => setMode("password")}>
                Sign in with a user name and password
              </button>
            )}
            {mode !== "register" && mayRegister && (
              <button type="button" className="btn ghost" onClick={() => setMode("register")}>
                Create an account
              </button>
            )}
            {mode !== "token" && mayUseToken && passwords && (
              <button type="button" className="btn ghost" onClick={() => setMode("token")}>
                Sign in with an API token
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function PasswordForm() {
  const { signInPassword } = useSession();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <form
      className="stack"
      onSubmit={async (event) => {
        event.preventDefault();
        setBusy(true);
        await signInPassword(username, password);
        setBusy(false);
      }}
    >
      <div className="field">
        <label htmlFor="signin-username">User name</label>
        <input id="signin-username" className="input" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
      </div>
      <div className="field">
        <label htmlFor="signin-password">Password</label>
        <input id="signin-password" className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
      </div>
      <button type="submit" className="btn primary" disabled={busy || !username.trim() || !password}>
        Sign in
      </button>
    </form>
  );
}

function RegisterForm({ minLength, approval }: { minLength: number; approval: boolean }) {
  const { register } = useSession();
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const mismatch = again !== "" && again !== password;
  const ready = username.trim().length >= 3 && password.length >= minLength && password === again;
  return (
    <form
      className="stack"
      onSubmit={async (event) => {
        event.preventDefault();
        setBusy(true);
        await register({ username: username.trim(), password, display_name: displayName.trim() });
        setBusy(false);
      }}
    >
      <p className="muted">
        {approval ? "An administrator has to approve a new account before it can be used. " : ""}
        An account is for Feedback &amp; Support: you can request features, report issues and follow what happens to them. No e-mail
        is sent to you: an administrator resets a forgotten password.
      </p>
      <div className="field">
        <label htmlFor="register-username">User name</label>
        <input id="register-username" className="input" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus maxLength={64} />
        <span className="help">3 to 64 characters: letters, digits, dot, dash, underscore.</span>
      </div>
      <div className="field">
        <label htmlFor="register-display">Name shown to others (optional)</label>
        <input id="register-display" className="input" value={displayName} onChange={(e) => setDisplayName(e.target.value)} maxLength={120} />
      </div>
      <div className="field">
        <label htmlFor="register-password">Password</label>
        <input id="register-password" className="input" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        <span className="help">At least {minLength} characters, not your user name and not a common password.</span>
      </div>
      <div className="field">
        <label htmlFor="register-again">Password again</label>
        <input id="register-again" className="input" type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} />
        {mismatch && <span className="error-text" role="alert">The two passwords differ.</span>}
      </div>
      <button type="submit" className="btn primary" disabled={busy || !ready}>
        Create account
      </button>
    </form>
  );
}

function TokenForm() {
  const { signIn } = useSession();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <form
      className="stack"
      onSubmit={async (event) => {
        event.preventDefault();
        setBusy(true);
        await signIn(token);
        setBusy(false);
      }}
    >
      <p className="muted">This server asks for an API token. It is kept in this browser tab only, and is sent only to this server.</p>
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
    </form>
  );
}
