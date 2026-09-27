import { describe, expect, it } from "vitest";
import { searchGraph } from "./graphSearch";
import type { StrategicGraphData } from "@/services/strategicMap";

const graph: StrategicGraphData = { provider: "test", meta: {}, searchResults: [],
  nodes: [{ id: "a", label: "量子研究组" }, { id: "b", label: "大学" }],
  edges: [{ id: "e", source: "a", target: "b", label: "所属机构" }] };
describe("current graph search", () => {
  it("finds relations by predicate or endpoint and reveals both nodes", () => {
    for (const q of ["所属机构", "量子"]) {
      const next = searchGraph(graph, q, "edges");
      expect(next.searchResults.map(r => r.id)).toEqual(["e"]);
      expect(next.nodes.every(n => n.highlighted)).toBe(true);
      expect(next.edges[0].highlighted).toBe(true);
    }
  });
  it("keeps node searches distinct and clears previous highlights on zero results", () => {
    const next = searchGraph(graph, "量子", "nodes");
    expect(next.searchResults.map(r => r.id)).toEqual(["a"]);
    const empty = searchGraph(next, "不存在", "nodes");
    expect(empty.searchResults).toEqual([]);
    expect(empty.nodes.some(n => n.highlighted)).toBe(false);
    expect(graph.nodes.some(n => n.highlighted)).toBe(false);
  });
});
