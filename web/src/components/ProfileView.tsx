import type { AgentProfile } from "../api/types";
import { formatNumber, formatPercent } from "../lib/format";
import { statusLabel } from "../lib/labels";
import { ArchGraph } from "./charts";
import { DataTable } from "./DataTable";
import { JsonView, RedactedText } from "./JsonView";
import { Badge, Card, Empty, KeyValue, ProgressBar, Tag } from "./ui";

function List({ items, empty }: { items: string[]; empty: string }) {
  if (items.length === 0) return <span className="muted">{empty}</span>;
  return (
    <ul style={{ margin: 0, paddingLeft: 18 }}>
      {items.map((x, i) => (
        <li key={i}>
          <RedactedText text={x} />
        </li>
      ))}
    </ul>
  );
}

function Details({ title, value }: { title: string; value: object }) {
  if (Object.keys(value).length === 0) return null;
  return (
    <details>
      <summary style={{ cursor: "pointer" }}>{title}</summary>
      <JsonView value={value} />
    </details>
  );
}

/** What discovery learned about an agent: what it is, what it can do, what can be tested and how it is built. */
export function ProfileView({ profile }: { profile: AgentProfile }) {
  const types = [...profile.types].sort((a, b) => b.confidence - a.confidence);
  return (
    <div className="stack" style={{ gap: 16 }}>
      <Card title="What AgentLab found">
        <div className="stack">
          <p style={{ margin: 0 }}>{profile.summary || "No summary could be written."}</p>
          <div className="row">
            {profile.modes.map((m) => (
              <Badge key={m} tone="info">
                {m.replace("_", " ")} testing
              </Badge>
            ))}
            {profile.interfaces.map((i) => (
              <Tag key={i}>{i}</Tag>
            ))}
          </div>
          <p className="muted small" style={{ margin: 0 }}>
            Discovery reports what the evidence shows, with the confidence of each conclusion. Anything it could not establish is listed under limitations rather than guessed.
          </p>
        </div>
      </Card>

      <div className="grid two">
        <Card title="Agent type">
          {types.length === 0 ? (
            <Empty title="Not classified" />
          ) : (
            <div className="stack">
              {types.map((t) => (
                <div key={t.type}>
                  <div className="row between">
                    <strong>{statusLabel(t.type)}</strong>
                    <span className="muted small">{formatPercent(t.confidence)} confident</span>
                  </div>
                  <ProgressBar value={t.confidence * 100} label={`${t.type} confidence`} />
                  {t.evidence.length > 0 && (
                    <ul className="muted small" style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                      {t.evidence.slice(0, 4).map((e, i) => (
                        <li key={i}>
                          <RedactedText text={`${e.source}: ${e.detail}`} />
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ))}
            </div>
          )}
        </Card>
        <Card title="Capabilities and what can be tested" padded={false}>
          <DataTable
            caption="Capability matrix"
            rows={profile.capability_matrix}
            rowKey={(c) => c.capability}
            pageSize={40}
            columns={[
              { key: "c", header: "Capability", render: (c) => c.capability.replace(/_/g, " "), sort: (a, b) => a.capability.localeCompare(b.capability) },
              { key: "d", header: "Found", render: (c) => (c.detected ? <Badge tone="good">yes</Badge> : <Badge>no</Badge>) },
              { key: "t", header: "Testable", render: (c) => <Badge tone={c.testable === "supported" ? "good" : c.testable === "partial" ? "warn" : "bad"} title={c.reason}>{c.testable}</Badge> },
              { key: "r", header: "Why", render: (c) => <span className="small muted">{c.reason}</span> },
            ]}
            empty={<Empty title="Nothing to show" />}
          />
        </Card>
      </div>

      <Card title="Architecture">
        <ArchGraph nodes={profile.architecture.nodes} edges={profile.architecture.edges} />
      </Card>

      <div className="grid two">
        <Card title={`Tools (${profile.tools.length})`} padded={false}>
          <DataTable
            caption="Tools"
            rows={profile.tools}
            rowKey={(t) => t.name}
            columns={[
              { key: "n", header: "Tool", render: (t) => <><strong className="mono">{t.name}</strong>{t.description && <div className="muted small">{t.description}</div>}</>, sort: (a, b) => a.name.localeCompare(b.name) },
              { key: "s", header: "Side effects", render: (t) => <Badge tone={t.side_effects === "none" || t.side_effects === "read" ? "good" : t.side_effects === "unknown" ? "neutral" : "warn"}>{t.side_effects}</Badge> },
              { key: "c", header: "Asks first", render: (t) => (t.requires_confirmation === null ? <span className="muted">unknown</span> : t.requires_confirmation ? "yes" : "no") },
              { key: "o", header: "Learned from", render: (t) => <span className="small">{t.source}</span> },
            ]}
            empty={<Empty title="No tool was found" />}
          />
        </Card>
        <Card title={`Data sources (${profile.data_sources.length})`} padded={false}>
          <DataTable
            caption="Data sources"
            rows={profile.data_sources}
            rowKey={(d) => `${d.kind}:${d.name}`}
            columns={[
              { key: "n", header: "Name", render: (d) => <strong>{d.name}</strong> },
              { key: "k", header: "Kind", render: (d) => <Tag>{d.kind}</Tag> },
              { key: "s", header: "Source", render: (d) => <span className="small">{d.source}</span> },
            ]}
            empty={<Empty title="No data source was found" />}
          />
        </Card>
      </div>

      <div className="grid two">
        <Card title="Expected workflows">
          <List items={profile.expected_workflows} empty="None were identified." />
        </Card>
        <Card title="Attack surfaces">
          <List items={profile.attack_surfaces} empty="None were identified." />
        </Card>
        <Card title="Testing strategy">
          <List items={profile.strategy} empty="No strategy was written." />
        </Card>
        <Card title="Limitations of this discovery">
          <List items={profile.limitations} empty="None recorded." />
        </Card>
      </div>

      <Card title="Technology">
        <KeyValue
          items={[
            ["Models", profile.models.join(", ") || null],
            ["Frameworks", profile.frameworks.join(", ") || null],
            ["Languages", Object.entries(profile.languages).map(([k, v]) => `${k} (${formatNumber(v)} files)`).join(", ") || null],
            ["Knowledge items", profile.knowledge_items ? formatNumber(profile.knowledge_items) : null],
            ["Documents", profile.documents.join(", ") || null],
          ]}
        />
        <div className="stack tight" style={{ marginTop: 12 }}>
          <Details title="Authentication" value={profile.authentication} />
          <Details title="Memory" value={profile.memory} />
          <Details title="Retrieval (RAG)" value={profile.rag} />
          <Details title="Browser" value={profile.browser} />
          <Details title="Multiple agents" value={profile.multi_agent} />
          <Details title="MCP" value={profile.mcp} />
          <Details title="Repository" value={profile.repository} />
          <Details title="Raw signals" value={profile.raw_signals} />
        </div>
      </Card>
    </div>
  );
}
