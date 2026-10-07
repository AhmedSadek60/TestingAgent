import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, describeError } from "../api/errors";
import type { Discovery, RunSummary } from "../api/types";
import { Checkbox } from "../components/controls";
import { DataTable } from "../components/DataTable";
import { Icon } from "../components/Icon";
import { JsonView } from "../components/JsonView";
import { ProfileView } from "../components/ProfileView";
import { Async, Badge, Card, Empty, ErrorNote, Loading, Notice, PageHeader, Tag, useToast } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { runColumns } from "./Dashboard";
import { runPath } from "../lib/runs";
import { useSession } from "../session";

type Found = { found: true; discovery: Discovery } | { found: false };

const INTERFACE_KEYS = ["api", "web", "mcp", "command", "llm", "mock", "repository"] as const;

/** One target: how it is reached, what discovery learned about it, and the runs it has had. */
export function TargetDetail() {
  const { targetId = "" } = useParams();
  const { api, project } = useSession();
  const navigate = useNavigate();
  const toast = useToast();
  const target = useAsync((signal) => api.target(targetId, signal), [api, targetId]);
  const profile = useAsync<Found>(
    async (signal) => {
      try {
        return { found: true, discovery: await api.targetProfile(targetId, signal) };
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return { found: false };
        throw e;
      }
    },
    [api, targetId],
  );
  const runs = useAsync((signal) => api.runs({ target: targetId, limit: 100 }, signal), [api, targetId]);
  const [probe, setProbe] = useState(true);
  const [busy, setBusy] = useState(false);
  const [warnings, setWarnings] = useState<string[]>([]);

  const discover = async () => {
    setBusy(true);
    try {
      const out = await api.discover({ project, target_id: targetId, probe });
      profile.setData({ found: true, discovery: out });
      setWarnings(out.warnings);
      toast.push("good", "Discovery finished.");
    } catch (e) {
      toast.push("error", describeError(e));
    } finally {
      setBusy(false);
    }
  };

  if (!target.data) return target.error ? <ErrorNote error={target.error} onRetry={target.reload} /> : <Loading label="Loading the target…" />;
  const t = target.data;
  const spec = t.spec as unknown as Record<string, unknown>;
  const interfaces = INTERFACE_KEYS.filter((k) => spec[k]);
  const safety = t.spec.safety;

  return (
    <>
      <PageHeader
        title={
          <span className="row">
            {t.name} {t.target_version && <span className="muted">{t.target_version}</span>} <Tag>{t.kind}</Tag>
          </span>
        }
        subtitle={t.spec.description ?? "Registered target"}
        actions={
          <Link className="btn primary" to={`/new?target=${encodeURIComponent(t.id)}`}>
            <Icon name="play" size={16} /> Evaluate
          </Link>
        }
      />
      <div className="stack" style={{ gap: 16 }}>
        <div className="grid two">
          <Card title="How it is reached">
            <div className="stack">
              <div className="row">
                {interfaces.length === 0 ? <Badge tone="warn">no interface to call</Badge> : interfaces.map((k) => <Tag key={k}>{k}</Tag>)}
              </div>
              <dl className="kv">
                <dt>Registered</dt>
                <dd>{new Date(t.created_at).toLocaleString()}</dd>
                <dt>Credentials</dt>
                <dd>{t.spec.credentials.length ? t.spec.credentials.join(", ") : "none"}</dd>
                <dt>Documents</dt>
                <dd>{t.spec.documents.length ? `${t.spec.documents.length} uploaded` : "none"}</dd>
                <dt>Authorised tests</dt>
                <dd>{safety.authorized_risk_classes.map((r) => r.replace("_", " ")).join(", ")}</dd>
                <dt>Environment</dt>
                <dd>
                  {safety.production ? <Badge tone="warn">production</Badge> : safety.disposable_environment ? <Badge tone="good">disposable</Badge> : "not declared"}
                </dd>
                <dt>Authorisation</dt>
                <dd>{safety.authorization_note ?? <span className="muted">none written</span>}</dd>
              </dl>
              <details>
                <summary className="small muted" style={{ cursor: "pointer" }}>
                  The definition as registered (no secrets are ever stored in it)
                </summary>
                <JsonView value={t.spec} />
              </details>
            </div>
          </Card>
          <Card
            title="Discovery"
            actions={
              <>
                <Checkbox checked={probe} onChange={setProbe}>
                  Send harmless probes
                </Checkbox>
                <button type="button" className="btn" disabled={busy} onClick={discover}>
                  <Icon name="search" size={16} /> {busy ? "Discovering…" : profile.data?.found ? "Discover again" : "Discover"}
                </button>
              </>
            }
          >
            <p className="muted" style={{ margin: 0 }}>
              Reads the repository and documents, looks at the interface, and, if you allow it, sends a few harmless messages to see how the agent behaves. Nothing is changed in the target.
            </p>
            {warnings.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <Notice tone="warn" title="Discovery warnings">
                  <ul style={{ margin: 0, paddingLeft: 18 }}>
                    {warnings.map((w, i) => (
                      <li key={i}>{w}</li>
                    ))}
                  </ul>
                </Notice>
              </div>
            )}
          </Card>
        </div>

        <Async state={profile} loading="Loading what is known…">
          {(found) =>
            found.found ? (
              <ProfileView profile={found.discovery.profile} />
            ) : (
              <Card>
                <Empty title="This target has not been discovered yet">Use Discover above, or evaluate it: every run starts with discovery.</Empty>
              </Card>
            )
          }
        </Async>

        <Card title="Runs of this target" padded={false}>
          <Async state={runs}>
            {(list) => (
              <DataTable<RunSummary>
                caption="Runs of this target"
                columns={runColumns}
                rows={[...list].sort((a, b) => b.created_at.localeCompare(a.created_at))}
                rowKey={(r) => r.id}
                onRowClick={(r) => navigate(runPath(r))}
                empty={<Empty title="Not evaluated yet" />}
              />
            )}
          </Async>
        </Card>
      </div>
    </>
  );
}
