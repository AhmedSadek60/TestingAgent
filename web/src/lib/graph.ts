/** A layered layout for the architecture graph: each node sits one column to the right of the nodes that feed it. */
export interface GraphNode {
  id: string;
  label: string;
  kind: string;
}
export interface GraphEdge {
  source: string;
  target: string;
  label?: string;
}
export interface Placed extends GraphNode {
  x: number;
  y: number;
  layer: number;
}

export const NODE_W = 150;
export const NODE_H = 40;
const GAP_X = 70;
const GAP_Y = 18;
const PAD = 16;

export function layoutGraph(nodes: GraphNode[], edges: GraphEdge[]): { placed: Placed[]; width: number; height: number } {
  const ids = new Set(nodes.map((n) => n.id));
  const incoming = new Map<string, string[]>();
  for (const e of edges) {
    if (!ids.has(e.source) || !ids.has(e.target) || e.source === e.target) continue;
    incoming.set(e.target, [...(incoming.get(e.target) ?? []), e.source]);
  }
  // longest path from a root, with a guard so a cycle cannot loop forever
  const layer = new Map<string, number>();
  const visiting = new Set<string>();
  const depth = (id: string): number => {
    const known = layer.get(id);
    if (known !== undefined) return known;
    if (visiting.has(id)) return 0;
    visiting.add(id);
    const parents = incoming.get(id) ?? [];
    const value = parents.length === 0 ? 0 : 1 + Math.max(...parents.map(depth));
    visiting.delete(id);
    layer.set(id, value);
    return value;
  };
  for (const n of nodes) depth(n.id);
  const columns = new Map<number, GraphNode[]>();
  for (const n of nodes) columns.set(layer.get(n.id) ?? 0, [...(columns.get(layer.get(n.id) ?? 0) ?? []), n]);
  const tallest = Math.max(1, ...[...columns.values()].map((c) => c.length));
  const height = PAD * 2 + tallest * NODE_H + (tallest - 1) * GAP_Y;
  const placed: Placed[] = [];
  for (const [index, members] of [...columns.entries()].sort((a, b) => a[0] - b[0])) {
    const used = members.length * NODE_H + (members.length - 1) * GAP_Y;
    const top = (height - used) / 2;
    members.forEach((n, row) => {
      placed.push({ ...n, layer: index, x: PAD + index * (NODE_W + GAP_X), y: top + row * (NODE_H + GAP_Y) });
    });
  }
  const layers = Math.max(0, ...placed.map((p) => p.layer)) + 1;
  return { placed, width: PAD * 2 + layers * NODE_W + (layers - 1) * GAP_X, height };
}
