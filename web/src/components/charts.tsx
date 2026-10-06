/** Small SVG charts, drawn by hand: no chart library is worth shipping for these. */
import { layoutGraph, NODE_H, NODE_W, type GraphEdge, type GraphNode } from "../lib/graph";

/** A line of scores (0 to 100) over time. */
export function Sparkline({ values, width = 140, height = 36, label }: { values: number[]; width?: number; height?: number; label: string }) {
  if (values.length === 0) return <span className="muted small">no scores yet</span>;
  const pad = 4;
  const step = values.length > 1 ? (width - pad * 2) / (values.length - 1) : 0;
  const y = (v: number) => pad + (height - pad * 2) * (1 - Math.max(0, Math.min(100, v)) / 100);
  const points = values.map((v, i) => `${pad + i * step},${y(v)}`);
  const last = values[values.length - 1];
  return (
    <svg width={width} height={height} role="img" aria-label={`${label}: ${values.map((v) => Math.round(v)).join(", ")}`}>
      <polyline points={points.join(" ")} fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={pad + (values.length - 1) * step} cy={y(last)} r="3" fill="var(--accent)" />
    </svg>
  );
}

/** Horizontal stacked bar of how many items fall in each class (passed / failed / blocked ...). */
export function StackedBar({ parts, label }: { parts: { key: string; value: number; color: string; title: string }[]; label: string }) {
  const total = parts.reduce((sum, p) => sum + p.value, 0);
  if (total === 0) return <span className="muted small">nothing to show</span>;
  return (
    <div role="img" aria-label={`${label}: ${parts.filter((p) => p.value > 0).map((p) => `${p.value} ${p.title}`).join(", ")}`} style={{ display: "flex", height: 12, borderRadius: 6, overflow: "hidden", border: "1px solid var(--border)" }}>
      {parts
        .filter((p) => p.value > 0)
        .map((p) => (
          <span key={p.key} title={`${p.value} ${p.title}`} style={{ width: `${(p.value / total) * 100}%`, background: p.color }} />
        ))}
    </div>
  );
}

const KIND_COLOR: Record<string, string> = {
  agent: "var(--accent)",
  model: "var(--info)",
  tool: "var(--good)",
  data: "var(--warn)",
  memory: "var(--critical)",
  interface: "var(--neutral)",
};

/** The architecture AgentLab inferred, as boxes and arrows. */
export function ArchGraph({ nodes, edges }: { nodes: GraphNode[]; edges: GraphEdge[] }) {
  if (nodes.length === 0) return <span className="muted">No architecture could be inferred for this target.</span>;
  const { placed, width, height } = layoutGraph(nodes, edges);
  const at = new Map(placed.map((p) => [p.id, p]));
  const summary = `${nodes.length} components: ${nodes.map((n) => n.label).join(", ")}`;
  return (
    <div style={{ overflowX: "auto" }}>
      <svg width={width} height={height} role="img" aria-label={`Architecture. ${summary}`}>
        <defs>
          <marker id="arch-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0 0 L10 5 L0 10 z" fill="var(--border-strong)" />
          </marker>
        </defs>
        {edges.map((e, i) => {
          const a = at.get(e.source);
          const b = at.get(e.target);
          if (!a || !b || a.id === b.id) return null;
          const x1 = a.x + NODE_W;
          const y1 = a.y + NODE_H / 2;
          const x2 = b.x;
          const y2 = b.y + NODE_H / 2;
          const mid = (x1 + x2) / 2;
          return (
            <g key={`${e.source}-${e.target}-${i}`}>
              <path d={`M${x1} ${y1} C${mid} ${y1} ${mid} ${y2} ${x2} ${y2}`} fill="none" stroke="var(--border-strong)" strokeWidth="1.5" markerEnd="url(#arch-arrow)" />
              {e.label && (
                <text x={mid} y={(y1 + y2) / 2 - 4} fontSize="10" fill="var(--muted)" textAnchor="middle">
                  {e.label.length > 18 ? `${e.label.slice(0, 17)}…` : e.label}
                </text>
              )}
            </g>
          );
        })}
        {placed.map((n) => (
          <g key={n.id}>
            <title>{`${n.label} (${n.kind})`}</title>
            <rect x={n.x} y={n.y} width={NODE_W} height={NODE_H} rx="8" fill="var(--surface)" stroke={KIND_COLOR[n.kind] ?? "var(--border-strong)"} strokeWidth="2" />
            <text x={n.x + NODE_W / 2} y={n.y + NODE_H / 2 - 3} fontSize="12" fontWeight="600" fill="var(--text)" textAnchor="middle" dominantBaseline="middle">
              {n.label.length > 20 ? `${n.label.slice(0, 19)}…` : n.label}
            </text>
            <text x={n.x + NODE_W / 2} y={n.y + NODE_H / 2 + 11} fontSize="10" fill="var(--muted)" textAnchor="middle" dominantBaseline="middle">
              {n.kind}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}
