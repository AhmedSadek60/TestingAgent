import { describe, expect, it } from "vitest";
import { layoutGraph, NODE_H, NODE_W } from "./graph";

const node = (id: string) => ({ id, label: id, kind: "tool" });

describe("the architecture graph", () => {
  it("puts each node one column to the right of what feeds it", () => {
    const { placed } = layoutGraph(
      [node("user"), node("agent"), node("search"), node("db")],
      [
        { source: "user", target: "agent" },
        { source: "agent", target: "search" },
        { source: "search", target: "db" },
      ],
    );
    expect(Object.fromEntries(placed.map((p) => [p.id, p.layer]))).toEqual({ user: 0, agent: 1, search: 2, db: 3 });
  });

  it("uses the longest path, so a node with two parents sits after both", () => {
    const { placed } = layoutGraph(
      [node("a"), node("b"), node("c")],
      [
        { source: "a", target: "b" },
        { source: "a", target: "c" },
        { source: "b", target: "c" },
      ],
    );
    expect(placed.find((p) => p.id === "c")?.layer).toBe(2);
  });

  it("stacks nodes of one column without overlap and sizes the picture to fit", () => {
    const { placed, width, height } = layoutGraph([node("agent"), node("x"), node("y"), node("z")], [
      { source: "agent", target: "x" },
      { source: "agent", target: "y" },
      { source: "agent", target: "z" },
    ]);
    const column = placed.filter((p) => p.layer === 1).sort((a, b) => a.y - b.y);
    expect(column).toHaveLength(3);
    for (let i = 1; i < column.length; i += 1) expect(column[i].y - column[i - 1].y).toBeGreaterThanOrEqual(NODE_H);
    for (const p of placed) {
      expect(p.x + NODE_W).toBeLessThanOrEqual(width);
      expect(p.y + NODE_H).toBeLessThanOrEqual(height);
      expect(p.y).toBeGreaterThanOrEqual(0);
    }
  });

  it("survives a cycle, a self-loop and an edge to a node that is not there", () => {
    const { placed } = layoutGraph(
      [node("a"), node("b")],
      [
        { source: "a", target: "b" },
        { source: "b", target: "a" },
        { source: "a", target: "a" },
        { source: "a", target: "ghost" },
      ],
    );
    expect(placed).toHaveLength(2);
    expect(placed.every((p) => Number.isFinite(p.x) && Number.isFinite(p.y))).toBe(true);
  });

  it("lays out an empty graph", () => {
    expect(layoutGraph([], []).placed).toEqual([]);
  });
});
