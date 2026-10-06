import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { useAsync } from "../hooks/useAsync";
import { useTheme } from "../hooks/useTheme";
import { useSession } from "../session";
import { Icon, type IconName } from "./Icon";
import { Badge, Field, Modal, useToast } from "./ui";
import { describeError } from "../api/errors";

const NAV: { heading: string; items: { to: string; label: string; icon: IconName; end?: boolean }[] }[] = [
  {
    heading: "Overview",
    items: [
      { to: "/", label: "Dashboard", icon: "dashboard", end: true },
      { to: "/new", label: "New evaluation", icon: "plus" },
    ],
  },
  {
    heading: "Evaluate",
    items: [
      { to: "/targets", label: "Targets and discovery", icon: "target" },
      { to: "/runs", label: "Test runs", icon: "play" },
      { to: "/reports", label: "Reports", icon: "file" },
      { to: "/compare", label: "Compare runs", icon: "compare" },
    ],
  },
  {
    heading: "Library",
    items: [
      { to: "/skills", label: "Skills", icon: "book" },
      { to: "/providers", label: "Providers", icon: "cpu" },
      { to: "/credentials", label: "Credentials", icon: "key" },
    ],
  },
  { heading: "Admin", items: [{ to: "/settings", label: "Settings", icon: "gear" }] },
];

function ProjectSwitcher() {
  const { api, project, setProject } = useSession();
  const projects = useAsync((signal) => api.projects(signal), [api]);
  const [creating, setCreating] = useState(false);
  const names = projects.data?.map((p) => p.name) ?? [];
  const options = names.includes(project) ? names : [project, ...names];
  return (
    <>
      <label className="sr-only" htmlFor="project-switcher">
        Project
      </label>
      <select
        id="project-switcher"
        className="select"
        style={{ width: "auto", maxWidth: 220 }}
        value={project}
        onChange={(event) => (event.target.value === "\u0000new" ? setCreating(true) : setProject(event.target.value))}
      >
        {options.map((name) => (
          <option key={name} value={name}>
            Project: {name}
          </option>
        ))}
        <option value={"\u0000new"}>New project…</option>
      </select>
      {creating && (
        <NewProject
          onClose={() => setCreating(false)}
          onCreated={(name) => {
            setCreating(false);
            projects.reload();
            setProject(name);
          }}
        />
      )}
    </>
  );
}

export function NewProject({ onClose, onCreated }: { onClose: () => void; onCreated: (name: string) => void }) {
  const { api } = useSession();
  const toast = useToast();
  const [name, setName] = useState("");
  const [objective, setObjective] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const created = await api.createProject({ name: name.trim(), objective: objective.trim() });
      toast.push("good", `Project ${created.name} created.`);
      onCreated(created.name);
    } catch (e) {
      setError(describeError(e));
      setBusy(false);
    }
  };
  return (
    <Modal
      title="New project"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn primary" disabled={busy || !name.trim()} onClick={submit}>
            Create project
          </button>
        </>
      }
    >
      <p className="muted">A project groups targets, documents and runs that belong together.</p>
      <Field label="Name" htmlFor="project-name" error={error}>
        <input id="project-name" className="input" value={name} onChange={(e) => setName(e.target.value)} autoFocus maxLength={80} />
      </Field>
      <Field label="Objective (optional)" htmlFor="project-objective" help="What you want to learn about the agents in this project.">
        <textarea id="project-objective" className="textarea" value={objective} onChange={(e) => setObjective(e.target.value)} />
      </Field>
    </Modal>
  );
}

function ServerStatus() {
  const { api } = useSession();
  const health = useAsync((signal) => api.health(signal), [api], { pollMs: 15_000 });
  if (!health.data) return health.error ? <Badge tone="bad">server unreachable</Badge> : null;
  const { running, queued, queue } = health.data;
  return (
    <Badge tone="neutral" title={`Queue: ${queue}. ${running} run(s) in progress, ${queued} waiting.`}>
      {running} running · {queued} queued
    </Badge>
  );
}

export function Layout() {
  const { authRequired, signOut } = useSession();
  const { theme, setTheme } = useTheme();
  const [open, setOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setOpen(false), [location.pathname]);
  return (
    <div className="app">
      <aside className={`sidebar${open ? " open" : ""}`} aria-label="Main">
        <Link to="/" className="brand">
          <span className="brand-mark" aria-hidden="true">
            <Icon name="check" size={16} />
          </span>
          AgentLab
        </Link>
        {NAV.map((group) => (
          <div className="nav-group" key={group.heading}>
            <h4>{group.heading}</h4>
            <nav className="nav" aria-label={group.heading}>
              {group.items.map((item) => (
                <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => (isActive ? "active" : "")}>
                  <Icon name={item.icon} />
                  {item.label}
                </NavLink>
              ))}
            </nav>
          </div>
        ))}
      </aside>
      <div className="main">
        <header className="topbar">
          <button type="button" className="btn ghost menu" onClick={() => setOpen((v) => !v)} aria-label="Menu" aria-expanded={open}>
            <Icon name="menu" />
          </button>
          <ProjectSwitcher />
          <ServerStatus />
          <span className="grow" />
          <button
            type="button"
            className="btn ghost"
            aria-label={`Theme: ${theme}. Change theme`}
            title={`Theme: ${theme}`}
            onClick={() => setTheme(theme === "system" ? "light" : theme === "light" ? "dark" : "system")}
          >
            <Icon name={theme === "dark" ? "moon" : "sun"} />
            <span className="small theme-label">{theme}</span>
          </button>
          {authRequired && (
            <button type="button" className="btn ghost" onClick={signOut}>
              <Icon name="lock" size={16} /> Sign out
            </button>
          )}
        </header>
        <main className="content" id="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
