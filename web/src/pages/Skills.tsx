import { useMemo, useState } from "react";
import type { SkillSummary } from "../api/types";
import { SearchBox, SelectFilter } from "../components/controls";
import { DataTable } from "../components/DataTable";
import { JsonView } from "../components/JsonView";
import { Async, Badge, Card, Drawer, Empty, KeyValue, PageHeader, Tag } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { stripFrontMatter } from "../lib/format";
import { statusLabel } from "../lib/labels";
import { useSession } from "../session";

const RISK_TONE: Record<string, "good" | "warn" | "bad"> = { safe: "good", controlled: "warn", high_impact: "bad" };

function Detail({ name, onClose }: { name: string; onClose: () => void }) {
  const { api } = useSession();
  const skill = useAsync((signal) => api.skill(name, signal), [api, name]);
  return (
    <Drawer title={name} onClose={onClose}>
      <Async state={skill} loading="Loading the skill…">
        {(s) => (
          <>
            <div className="row">
              <Tag>{s.version}</Tag>
              <Tag>{s.kind}</Tag>
              <Badge tone={RISK_TONE[s.risk_class] ?? "neutral"}>{s.risk_class.replace("_", " ")}</Badge>
              <Badge tone={s.trust === "builtin" ? "good" : "warn"}>{s.trust}</Badge>
              <Badge tone={s.status === "active" ? "good" : "warn"}>{s.status}</Badge>
            </div>
            <p style={{ margin: 0 }}>{s.description}</p>
            {s.problems.length > 0 && (
              <ul style={{ margin: 0, paddingLeft: 18, color: "var(--bad)" }}>
                {s.problems.map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ul>
            )}
            <KeyValue items={[["Category", s.category], ["Taxonomy", s.taxonomy.join(", ") || null], ["Content hash", <span key="h" className="mono small">{s.content_hash}</span>]]} />
            <Card title="What it does">
              <pre className="mono" style={{ whiteSpace: "pre-wrap" }}>
                {stripFrontMatter(s.doc) || "This skill has no written description."}
              </pre>
            </Card>
            <details>
              <summary className="small muted" style={{ cursor: "pointer" }}>
                Manifest
              </summary>
              <JsonView value={s.manifest} />
            </details>
          </>
        )}
      </Async>
    </Drawer>
  );
}

/** The skills AgentLab can design tests with: each is a versioned, self-describing module. */
export function Skills() {
  const { api } = useSession();
  const skills = useAsync((signal) => api.skills(signal), [api]);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [trust, setTrust] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  const all = skills.data ?? [];
  const shown = useMemo(
    () =>
      all.filter(
        (s) =>
          (!kind || s.kind === kind) &&
          (!trust || s.trust === trust) &&
          (!q.trim() || `${s.name} ${s.title} ${s.description} ${s.taxonomy.join(" ")}`.toLowerCase().includes(q.trim().toLowerCase())),
      ),
    [all, q, kind, trust],
  );
  const distinct = (pick: (s: SkillSummary) => string) => [...new Set(all.map(pick))].sort().map((value) => ({ value }));

  return (
    <>
      <PageHeader title="Skills" subtitle="Each skill knows how to test one thing (a kind of agent, a risk, a capability). The planner picks the ones that apply and explains why." />
      <Card padded={false}>
        <div className="table-tools">
          <SearchBox value={q} onChange={setQ} label="Search skills" placeholder="Search skills" />
          <SelectFilter label="Kind" value={kind} onChange={setKind} allLabel="All kinds" options={distinct((s) => s.kind)} />
          <SelectFilter label="Trust" value={trust} onChange={setTrust} allLabel="Any source" options={distinct((s) => s.trust)} />
          <span className="muted small">
            {shown.length} of {all.length}
          </span>
        </div>
        <Async state={skills}>
          {() => (
            <DataTable
              caption="Skills"
              rows={shown}
              rowKey={(s) => s.name}
              onRowClick={(s) => setOpen(s.name)}
              pageSize={60}
              initialSort={{ key: "name" }}
              empty={<Empty title="No skill matches" />}
              columns={[
                { key: "name", header: "Skill", render: (s) => <><strong>{s.title || s.name}</strong><div className="muted small mono">{s.name} {s.version}</div></>, sort: (a, b) => a.name.localeCompare(b.name) },
                { key: "kind", header: "Kind", render: (s) => <Tag>{s.kind}</Tag>, sort: (a, b) => a.kind.localeCompare(b.kind) },
                { key: "risk", header: "Risk", render: (s) => <Badge tone={RISK_TONE[s.risk_class] ?? "neutral"}>{statusLabel(s.risk_class)}</Badge> },
                { key: "tax", header: "Covers", render: (s) => <span className="small">{s.taxonomy.join(", ")}</span> },
                { key: "trust", header: "Source", render: (s) => <Badge tone={s.trust === "builtin" ? "good" : "warn"}>{s.trust}</Badge> },
                { key: "status", header: "Status", render: (s) => (s.problems.length ? <Badge tone="bad">{s.problems.length} problem(s)</Badge> : <Badge tone={s.status === "active" ? "good" : "warn"}>{s.status}</Badge>) },
              ]}
            />
          )}
        </Async>
      </Card>
      {open && <Detail name={open} onClose={() => setOpen(null)} />}
    </>
  );
}
