import { expect, it } from "vitest";
import { graphNeighborhood } from "./graphNeighborhood";
import type { StrategicGraphData } from "@/services/strategicMap";

it("keeps one-hop sourced relations, not all units of the parent institution", () => {
  const data = { nodes: ["team:a", "team:b", "org", "claim"].map(id => ({ id })),
    edges: [{ id: "belongs-a", source: "team:a", target: "org" }, { id: "belongs-b", source: "team:b", target: "org" },
      { id: "supports", source: "claim", target: "team:a" }], provider: "test", searchResults: [], meta: { stats: { Nodes: 4, Edges: 3 } } } as unknown as StrategicGraphData;
  const scoped = graphNeighborhood(data, "team:a");
  expect(scoped.nodes.map(n => n.id)).toEqual(["team:a", "org", "claim"]);
  expect(scoped.edges.map(e => e.id)).toEqual(["belongs-a", "supports"]);
  expect(scoped.meta.stats).toEqual({ Nodes: 3, Edges: 2 });
  expect(graphNeighborhood(data)).toBe(data);
});

it("retains snapshot requirement paths among immediate neighbors without pulling in other teams", () => {
  const data: StrategicGraphData = { nodes: ["task", "criterion", "team:a", "team:b", "claim"].map(id => ({ id })),
    edges: [{ id: "candidate", source: "task", target: "team:a" }, { id: "requirement", source: "task", target: "criterion" },
      { id: "match", source: "team:a", target: "criterion" }, { id: "proof", source: "team:a", target: "claim" },
      { id: "other", source: "team:b", target: "criterion" }], provider: "test", searchResults: [], meta: {} };
  const scoped = graphNeighborhood(data, "team:a", true);
  expect(scoped.nodes.map(n => n.id)).toEqual(["task", "criterion", "team:a", "claim"]);
  expect(scoped.edges.map(e => e.id)).toEqual(["candidate", "requirement", "match", "proof"]);
  expect(graphNeighborhood(data, "team:removed", true).nodes).toEqual([]);
});
