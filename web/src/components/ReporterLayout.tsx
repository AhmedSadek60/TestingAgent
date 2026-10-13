import { NavLink, Outlet } from "react-router-dom";
import { useTheme } from "../hooks/useTheme";
import { useSession } from "../session";
import { Icon } from "./Icon";

/** The frame of a person who may use Feedback & Support only: no project, no evaluations, no server status. */
export function ReporterLayout() {
  const { me, signOut } = useSession();
  const { theme, setTheme } = useTheme();
  return (
    <div className="app">
      <div className="main" style={{ marginLeft: 0 }}>
        <header className="topbar">
          <span className="brand" style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
            <span className="brand-mark" aria-hidden="true">
              <Icon name="check" size={16} />
            </span>
            AgentLab
          </span>
          {!me?.must_change_password && (
            <nav className="row" aria-label="Feedback & Support">
              <NavLink to="/" end className={({ isActive }) => `btn ghost${isActive ? " active" : ""}`}>
                Feedback & Support
              </NavLink>
              <NavLink to="/feedback/submissions" className={({ isActive }) => `btn ghost${isActive ? " active" : ""}`}>
                My submissions
              </NavLink>
              <NavLink to="/account" className={({ isActive }) => `btn ghost${isActive ? " active" : ""}`}>
                Your account
              </NavLink>
            </nav>
          )}
          <span className="grow" />
          {me?.username && <span className="muted small">Signed in as {me.display_name ?? me.username}</span>}
          <button
            type="button"
            className="btn ghost"
            aria-label={`Theme: ${theme}. Change theme`}
            onClick={() => setTheme(theme === "system" ? "light" : theme === "light" ? "dark" : "system")}
          >
            <Icon name={theme === "dark" ? "moon" : "sun"} />
          </button>
          <button type="button" className="btn ghost" onClick={signOut}>
            <Icon name="lock" size={16} /> Sign out
          </button>
        </header>
        <main className="content" id="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
