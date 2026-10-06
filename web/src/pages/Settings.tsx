import { useState } from "react";
import type { EnvironmentCheck } from "../api/types";
import { Icon } from "../components/Icon";
import { JsonView } from "../components/JsonView";
import { Async, Badge, Card, KeyValue, Notice, PageHeader } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { useTheme, type Theme } from "../hooks/useTheme";
import { useSession } from "../session";

const LEVEL_TONE = { ok: "good", info: "info", warn: "warn", fail: "bad" } as const;

function Checks({ checks }: { checks: EnvironmentCheck[] }) {
  return (
    <ul className="stack" style={{ margin: 0, paddingLeft: 0, listStyle: "none" }}>
      {checks.map((c) => (
        <li key={c.name} className="row" style={{ alignItems: "flex-start", flexWrap: "nowrap" }}>
          <Badge tone={LEVEL_TONE[c.level]}>{c.level}</Badge>
          <div>
            <strong>{c.name}</strong>
            <div className="muted small">{c.detail}</div>
            {c.fix && c.level !== "ok" && <div className="small">{c.fix}</div>}
          </div>
        </li>
      ))}
    </ul>
  );
}

/** How this server is set up, what it can and cannot do, and how this browser looks. Configuration is read only here. */
export function Settings() {
  const { api, health, authRequired, signOut, project } = useSession();
  const { theme, setTheme } = useTheme();
  const [live, setLive] = useState(false);
  const env = useAsync((signal) => api.environment(live, signal), [api, live]);
  const settings = useAsync((signal) => api.settings(signal), [api]);

  return (
    <>
      <PageHeader title="Settings" subtitle="The server's configuration is read only here: change it in the configuration file and restart the server. Secrets are always masked." />
      <div className="stack" style={{ gap: 16 }}>
        <div className="grid two">
          <Card title="This browser">
            <div className="stack">
              <div className="field">
                <label htmlFor="theme">Theme</label>
                <select id="theme" className="select" value={theme} onChange={(e) => setTheme(e.target.value as Theme)}>
                  <option value="system">Follow the system</option>
                  <option value="light">Light</option>
                  <option value="dark">Dark</option>
                </select>
              </div>
              <KeyValue items={[["Project", project], ["Sign-in", authRequired ? "an API token is required (kept in this tab only)" : "none required: the server listens on this machine only"]]} />
              {authRequired && (
                <div>
                  <button type="button" className="btn" onClick={signOut}>
                    <Icon name="lock" size={16} /> Sign out
                  </button>
                </div>
              )}
            </div>
          </Card>
          <Card title="Server">
            <KeyValue
              items={[
                ["Version", health?.version ?? null],
                ["Status", health?.status ?? null],
                ["Queue", health ? `${health.queue}: ${health.workers} running, ${health.queued} waiting` : null],
              ]}
            />
          </Card>
        </div>

        <Card
          title="What this server can do"
          actions={
            <button type="button" className="btn small" onClick={() => (live ? env.reload() : setLive(true))}>
              <Icon name="refresh" size={14} /> {live ? "Check again" : "Run live checks"}
            </button>
          }
        >
          <Async state={env} loading={live ? "Probing Docker, the browser and the providers…" : "Checking…"}>
            {(data) => (
              <div className="stack">
                <Notice tone={data.ok ? "good" : "warn"} title={data.ok ? "Everything required is available" : "Some capabilities are missing"}>
                  Tests that need a missing capability are reported as blocked, never as failed, and nothing dangerous is ever run on the host as a fallback.
                </Notice>
                <Checks checks={data.checks} />
                {!live && <span className="muted small">These checks look at the installation. “Run live checks” also tries Docker, the browser and the model providers.</span>}
              </div>
            )}
          </Async>
        </Card>

        <Card title="Configuration">
          <Async state={settings}>
            {(s) => (
              <div className="stack">
                {s.startup_warnings.length > 0 && (
                  <Notice tone="warn" title="Warnings at start-up">
                    <ul style={{ margin: 0, paddingLeft: 18 }}>
                      {s.startup_warnings.map((w, i) => (
                        <li key={i}>{w}</li>
                      ))}
                    </ul>
                  </Notice>
                )}
                <JsonView value={s.config} />
              </div>
            )}
          </Async>
        </Card>
      </div>
    </>
  );
}
