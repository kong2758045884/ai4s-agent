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
