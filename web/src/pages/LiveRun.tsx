import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Icon } from "../components/Icon";
import { JsonView, RedactedText, Untrusted } from "../components/JsonView";
import { Badge, Card, Empty, Notice, ProgressBar, Stat, StateTabs } from "../components/ui";
import { useNow } from "../hooks/useNow";
import { useRunFeed } from "../hooks/useRunFeed";
import { formatCost, formatDuration, formatLatency, formatNumber, formatPercent, parseTime, plural, pretty, truncate } from "../lib/format";
import { PHASES, statusLabel } from "../lib/labels";
import { ALERT_TYPES, describeEvent } from "../lib/live";
import { effectiveLimits, isActive, runSeconds } from "../lib/runs";
import { useSession } from "../session";
import { useRun } from "./RunLayout";

type FeedFilter = "all" | "alerts" | "tools" | "browser";

const FEED_TYPES: Record<FeedFilter, (type: string) => boolean> = {
  all: () => true,
  alerts: (type) => ALERT_TYPES.has(type),
  tools: (type) => type === "ToolCalled" || type === "ToolReturned",
  browser: (type) => type === "BrowserAction",
};

function PhaseTimeline({ current, doneCount, phases }: { current: string | null; doneCount: number; phases: Record<string, { status: string; durationS?: number; note?: string }> }) {
  return (
    <ol className="phases" aria-label="Phases" style={{ listStyle: "none", margin: 0, padding: 0 }}>
      {PHASES.map((phase, index) => {
        const known = phases[phase.id];
        const status = known?.status ?? (phase.id === current ? "running" : index < doneCount ? "completed" : "pending");
        const css = status === "completed" ? "done" : status === "running" ? "running" : status === "skipped" ? "skipped" : status === "failed" ? "failed" : "";
        const mark = status === "completed" ? "✓" : status === "skipped" ? "–" : status === "failed" ? "!" : status === "running" ? "•" : String(index + 1);
        return (
          <li key={phase.id} className={`phase ${css}`} aria-current={status === "running" ? "step" : undefined} title={known?.note ?? phase.detail}>
            <span className="mark" aria-hidden="true">
              {mark}
            </span>
            <span className="grow">
              {phase.label}
              <span className="sr-only"> ({status})</span>
            </span>
            {known?.durationS !== undefined && <span className="muted small">{formatDuration(known.durationS)}</span>}
          </li>
        );
      })}
    </ol>
  );
}

/** The live view of a run: where it is, what the agent is doing right now, and everything notable as it happens. */
export function LiveRun() {
  const { run, requestCancel } = useRun();
  const { client, api } = useSession();
  const feed = useRunFeed(client, run.id, api.streamPath(run.id));
  const { state } = feed;
  const [filter, setFilter] = useState<FeedFilter>("all");
  const active = isActive(run.status);
  const now = useNow(active);
  const p = run.progress;
  const limits = effectiveLimits(run);
  const elapsed = runSeconds(run, now);

  const events = useMemo(() => {
    const keep = FEED_TYPES[filter];
    return state.events.filter((e) => keep(e.type)).slice(-250).reverse();
  }, [state.events, filter]);

  const runningNow = p.running_tests.length > 0 ? p.running_tests : state.running;
  const phase = PHASES.find((x) => x.id === p.phase);
  const lastTool = state.tools[state.tools.length - 1];

  return (
    <div className="stack" style={{ gap: 16 }}>
      {run.status === "cancelling" && (
        <Notice tone="warn" title="Stopping at the next safe point">
          Running tests finish their current step. Nothing more is started.
        </Notice>
      )}
      {!active && (
        <Notice tone={run.status === "completed" ? "good" : "info"} title={`The run is ${statusLabel(run.status).toLowerCase()}`}>
          <Link to={`/runs/${run.id}/results`}>Results</Link> · <Link to={`/runs/${run.id}/findings`}>Findings</Link> · <Link to={`/runs/${run.id}/scorecard`}>Scorecard</Link> ·{" "}
          <Link to={`/runs/${run.id}/reports`}>Reports</Link>
        </Notice>
      )}

      <Card
        title="Progress"
        actions={
          <>
            {feed.connected ? <Badge tone="info" pulse>live</Badge> : active ? <Badge tone="warn">reconnecting</Badge> : <Badge>replay of the recorded events</Badge>}
            {active && run.status !== "cancelling" && (
              <button type="button" className="btn small danger" onClick={requestCancel}>
                <Icon name="stop" size={14} /> Stop
              </button>
            )}
          </>
        }
      >
        <div className="stack">
          <div className="row between">
            <span>
              <strong>{phase ? phase.label : p.phase ? p.phase.replace(/_/g, " ") : active ? "Waiting to start" : "Finished"}</strong>
              {phase && <span className="muted"> · {phase.detail}</span>}
            </span>
            <span className="muted">
              {formatNumber(p.tests_done)} of {p.tests_total > 0 ? formatNumber(p.tests_total) : "?"} tests
              {p.tests_total > 0 && ` · ${formatPercent(p.tests_done / p.tests_total)}`}
            </span>
          </div>
          <ProgressBar value={p.tests_done} max={Math.max(p.tests_total, 1)} label="Tests done" tone={p.failed > 0 ? "warn" : undefined} />
          <PhaseTimeline current={p.phase} doneCount={p.phases_done} phases={state.phases} />
        </div>
      </Card>

      <div className="grid" style={{ ["--min" as string]: "140px" }}>
        <Stat label="Passed" value={formatNumber(p.passed)} tone={p.passed > 0 ? "good" : undefined} />
        <Stat label="Failed" value={formatNumber(p.failed)} tone={p.failed > 0 ? "bad" : undefined} />
        <Stat label="Blocked" value={formatNumber(p.blocked)} tone={p.blocked > 0 ? "warn" : undefined} hint="A prerequisite was missing; not a failure" />
        <Stat label="Errors" value={formatNumber(p.errors)} tone={p.errors > 0 ? "bad" : undefined} />
        <Stat label="Skipped" value={formatNumber(p.skipped + p.stopped)} />
        <Stat label="Findings" value={formatNumber(p.findings)} />
        <Stat label="Security alerts" value={formatNumber(p.security_alerts)} tone={p.security_alerts > 0 ? "bad" : undefined} />
        <Stat label="Tool calls" value={formatNumber(p.tool_calls)} />
        <Stat label="Browser actions" value={formatNumber(p.browser_actions)} />
        <Stat label="Model calls" value={formatNumber(p.llm_calls)} />
        <Stat label="Tokens" value={formatNumber(p.tokens)} hint={limits.tokens ? `limit ${formatNumber(limits.tokens)}` : undefined} />
        <Stat label="Cost" value={formatCost(p.cost_usd)} hint={limits.costUsd ? `limit ${formatCost(limits.costUsd)}` : undefined} />
        <Stat label="Average latency" value={formatLatency(p.latency_ms_avg)} />
        <Stat label="Elapsed" value={formatDuration(elapsed)} hint={limits.seconds ? `limit ${formatDuration(limits.seconds)}` : undefined} />
      </div>

      <div className="grid two">
        <Card title={`Running now${runningNow.length ? ` (${runningNow.length})` : ""}`}>
          {runningNow.length === 0 ? (
            <p className="muted">{active ? "No test is running at this moment." : "Nothing is running."}</p>
          ) : (
            <ul className="stack tight" style={{ margin: 0, paddingLeft: 0, listStyle: "none" }}>
              {runningNow.map((id) => {
                const t = state.tests[id];
                const started = parseTime(t?.startedAt);
                return (
                  <li key={id} className="row between">
                    <span>
                      <strong>{t?.name ?? id}</strong> {t?.category && <span className="tag">{t.category}</span>}
                    </span>
                    <span className="muted small">{started ? formatDuration((now.getTime() - started.getTime()) / 1000) : ""}</span>
                  </li>
                );
              })}
            </ul>
          )}
          <h3 style={{ marginTop: 16 }}>The agent right now</h3>
          {state.lastRequest || state.lastResponse ? (
            <div className="stack tight">
              {state.lastRequest && <Untrusted label="Last test input (sent by AgentLab)" text={truncate(state.lastRequest.text, 600)} />}
              {state.lastResponse && (
                <Untrusted
                  label={`Last answer${state.lastResponse.latencyMs !== null ? ` · ${formatLatency(state.lastResponse.latencyMs)}` : ""}`}
                  text={truncate(state.lastResponse.error ? `ERROR: ${state.lastResponse.error}` : state.lastResponse.text, 900)}
                />
              )}
              {lastTool && (
                <div className="small">
                  Last tool call: <span className="mono">{lastTool.tool}</span> {lastTool.status && <Badge tone={lastTool.status === "ok" ? "good" : "warn"}>{lastTool.status}</Badge>}
                </div>
              )}
            </div>
          ) : (
            <p className="muted">Nothing yet. Requests and answers appear here as tests run.</p>
          )}
        </Card>

        <Card title={`Security alerts${state.alerts.length ? ` (${state.alerts.length})` : ""}`}>
          {state.alerts.length === 0 ? (
            <p className="muted">No security alert so far. Secrets in this view are always masked.</p>
          ) : (
            <ul className="stack tight" style={{ margin: 0, paddingLeft: 0, listStyle: "none" }}>
              {[...state.alerts].reverse().map((a, i) => (
                <li key={`${a.at}-${i}`} className="alert error">
                  <Icon name="shield" size={16} />
                  <div>
                    <RedactedText text={truncate(a.text, 300)} />
                    {a.testId && <div className="small muted mono">{a.testId}</div>}
                  </div>
                </li>
              ))}
            </ul>
          )}
          {state.limits.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <Notice tone="warn" title="Limit reached">
                {state.limits[state.limits.length - 1]}
              </Notice>
            </div>
          )}
        </Card>
      </div>

      <div className="grid two">
        <Card title="Tool calls" padded={false}>
          {state.tools.length === 0 ? (
            <Empty title="No tool call yet" />
          ) : (
            <div className="feed" role="log" aria-label="Tool calls">
              {[...state.tools].reverse().map((t, i) => (
                <div className="line" key={`${t.at}-${i}`}>
                  <time>{new Date(t.at).toLocaleTimeString()}</time>
                  <span className="mono">{t.tool}</span>
                  <span className="mono">
                    <RedactedText text={truncate(pretty(t.args ?? {}), 160)} /> {t.status && <Badge tone={t.status === "ok" ? "good" : "warn"}>{t.status}</Badge>}
                  </span>
                </div>
              ))}
            </div>
          )}
        </Card>
        <Card title="Browser activity" padded={false}>
          {state.browser.length === 0 ? (
            <Empty title="No browser activity">Browser steps appear when the target is a web application.</Empty>
          ) : (
            <div className="feed" role="log" aria-label="Browser activity">
              {[...state.browser].reverse().map((b, i) => (
                <div className="line" key={`${b.at}-${i}`}>
                  <time>{new Date(b.at).toLocaleTimeString()}</time>
                  <span className="muted mono">{b.testId ?? ""}</span>
                  <span className="mono">{b.text}</span>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      {state.findings.length > 0 && (
        <Card title={`Findings so far (${state.findings.length})`}>
          <ul className="stack tight" style={{ margin: 0, paddingLeft: 0, listStyle: "none" }}>
            {[...state.findings].reverse().map((f, i) => (
              <li key={`${f.at}-${i}`}>
                <Badge tone={f.severity === "critical" ? "critical" : f.severity === "high" ? "bad" : f.severity === "medium" ? "warn" : "neutral"}>{f.severity}</Badge> {f.title}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card
        title="Event log"
        actions={
          <StateTabs
            label="Event filter"
            value={filter}
            onChange={setFilter}
            items={[
              { id: "all", label: `All (${formatNumber(state.seen)})` },
              { id: "alerts", label: "Alerts" },
              { id: "tools", label: "Tools" },
              { id: "browser", label: "Browser" },
            ]}
          />
        }
        padded={false}
      >
        {feed.error && (
          <div style={{ padding: 12 }}>
            <Notice tone="error" title="The live connection failed">
              {feed.error}
            </Notice>
          </div>
        )}
        {events.length === 0 ? (
          <Empty title={active ? "Waiting for events" : "No events of this kind"}>{plural(state.seen, "event")} received.</Empty>
        ) : (
          <div className="feed" role="log" aria-label="Event log" aria-live="off">
            {events.map((e) => (
              <div key={e.event_id} className={`line${ALERT_TYPES.has(e.type) ? " alert-line" : ""}`}>
                <time>{new Date(e.timestamp).toLocaleTimeString()}</time>
                <span className="type mono">{e.type}</span>
                <span>
                  <RedactedText text={describeEvent(e)} />
                </span>
              </div>
            ))}
          </div>
        )}
        <details style={{ padding: "8px 16px" }}>
          <summary className="small muted" style={{ cursor: "pointer" }}>
            Latest event as recorded (structured)
          </summary>
          {state.events.length > 0 && <JsonView value={state.events[state.events.length - 1]} />}
        </details>
      </Card>
    </div>
  );
}
