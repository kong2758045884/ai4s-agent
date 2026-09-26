import { describe, expect, it } from "vitest";

import type { StrategicGraphData } from "@/services/strategicMap";
import { aggregateGraphData } from "./graphAggregation";

function graph(nodeCount: number): StrategicGraphData {
  return {
    provider: "test",
    searchResults: [],
    meta: {
      stats: {
        Nodes: nodeCount + 2,
        Edges: nodeCount + 1
      }
    },
    nodes: [
      {
        id: "高峰",
        data: {
          raw: {
            name: "高峰",
            level: "领域大类"
          }
        },
      },
      {
        id: "化学与材料",
        data: {
          raw: {
            name: "化学与材料",
            level: "领域方向"
          }
        },
      },
      ...Array.from({length: nodeCount}, (_, index) => ({
        id: `institution-${index}`,
        data: {
          raw: {
            name: `机构${index}`,
            level: "机构"
          },
        },
      })),
    ],
    edges: [
      {
        id: "root-edge",
        source: "化学与材料",
        target: "高峰",
      },
      ...Array.from({length: nodeCount}, (_, index) => ({
        id: `edge-${index}`,
        source: `institution-${index}`,
        target: "化学与材料",
      })),
    ],
  };
}

describe("aggregateGraphData", () => {
  it("keeps small graphs unchanged", () => {
    const source = graph(20);
    const result = aggregateGraphData(source, new Set());
    expect(result.data).toBe(source);
    expect(result.hiddenNodeCount).toBe(0);
  });

  it("collapses 100 nodes into a 100+ aggregate and expands on demand", () => {
    const source = graph(120);
    const collapsed = aggregateGraphData(source, new Set());
    const aggregate = collapsed.data.nodes.find((node) =>
      node.id.startsWith("aggregate:"),
    );

    expect(collapsed.hiddenNodeCount).toBe(120);
    expect(aggregate?.data?.label).toContain("100+");

    const expanded = aggregateGraphData(source, new Set(["level:机构"]));
    expect(expanded.hiddenNodeCount).toBe(0);
    expect(expanded.data.nodes).toHaveLength(122);
  });

  it("keeps aggregates in their own subdomain instead of attaching them to the first child", () => {
    const source = graph(0);
    source.nodes.push(
      { id: "child-a", data: { raw: { name: "子领域甲", level: "子领域", subdomainId: "sub-a" } } },
      { id: "child-b", data: { raw: { name: "子领域乙", level: "子领域", subdomainId: "sub-b" } } },
    );
    for (const [subdomainId, childId] of [["sub-a", "child-a"], ["sub-b", "child-b"]]) {
      for (let index = 0; index < 6; index += 1) {
        const id = `team-${subdomainId}-${index}`;
        source.nodes.push({
          id,
          data: { raw: { name: id, level: "科研团队", subdomainId } },
        });
        source.edges.push({ id: `edge-${id}`, source: id, target: childId });
      }
    }

    const collapsed = aggregateGraphData(source, new Set(), 5);
    expect(collapsed.hiddenNodeCount).toBe(12);
    expect(collapsed.data.edges).toContainEqual(
      expect.objectContaining({ source: "aggregate:level:科研团队:subdomain:sub-a", target: "child-a" }),
    );
    expect(collapsed.data.edges).toContainEqual(
      expect.objectContaining({ source: "aggregate:level:科研团队:subdomain:sub-b", target: "child-b" }),
    );

    const expanded = aggregateGraphData(source, new Set(["level:科研团队:subdomain:sub-a"]), 5);
    expect(expanded.hiddenNodeCount).toBe(6);
    expect(expanded.data.nodes.some((node) => node.id === "team-sub-a-0")).toBe(true);
    expect(expanded.data.nodes.some((node) => node.id === "team-sub-b-0")).toBe(false);
  });

  it("keeps unclassified legacy nodes at the domain rather than guessing a child", () => {
    const source = graph(6);
    source.nodes.push({
      id: "child-a",
      data: { raw: { name: "子领域甲", level: "子领域", subdomainId: "sub-a" } },
    });
    const collapsed = aggregateGraphData(source, new Set(), 5);
    expect(collapsed.data.edges).toContainEqual(
      expect.objectContaining({ source: "aggregate:level:机构", target: "化学与材料" }),
    );
  });
});
