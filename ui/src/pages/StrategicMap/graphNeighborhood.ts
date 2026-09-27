import type { StrategicGraphData } from "@/services/strategicMap";

/** One-hop evidence relations only; two nodes sharing an institution are not a team relation. */
export function graphNeighborhood(data: StrategicGraphData, focusId?: string, includeNeighborEdges = false): StrategicGraphData {
  if (!focusId) return data;
  const edges = data.edges.filter(edge => edge.source === focusId || edge.target === focusId);
  const ids = new Set([focusId, ...edges.flatMap(edge => [edge.source, edge.target])]);
  const nodes = data.nodes.filter(node => ids.has(node.id));
  const valid = new Set(nodes.map(node => node.id));
  // Snapshot mode may retain evidence paths among the same one-hop nodes; never add a second-hop node.
  const retained = (includeNeighborEdges ? data.edges : edges).filter(edge => valid.has(edge.source!) && valid.has(edge.target!));
  return { ...data, nodes, edges: retained, searchResults: [],
    meta: { ...data.meta, stats: { ...data.meta.stats, Nodes: nodes.length, Edges: retained.length } } };
}
