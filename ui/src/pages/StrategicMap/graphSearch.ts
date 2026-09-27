import type { StrategicGraphData, StrategicGraphSearchResult } from "@/services/strategicMap";

export function searchGraph(data: StrategicGraphData, query: string, mode: "nodes" | "edges"): StrategicGraphData {
  const normal = (s: string) => s.normalize("NFKC").toLocaleLowerCase();
  const phrase = normal(query.trim());
  const label = (id?: string) => data.nodes.find(n => n.id === id)?.label || id || "";
  const hits = (mode === "nodes" ? data.nodes : data.edges).filter(item => phrase && normal(
    `${item.label || ""} ${JSON.stringify(item.data?.raw || {})} ${item.data?.label || ""} ${mode === "edges" ? `${label(item.source)} ${label(item.target)}` : ""}`
  ).includes(phrase));
  const ids = new Set(hits.flatMap(item => mode === "edges" ? [item.source, item.target] : [item.id]));
  const hitIds = new Set(hits.map(item => item.id));
  const searchResults: StrategicGraphSearchResult[] = hits.map(item => ({
    id: item.id, label: mode === "edges" ? `${label(item.source)} → ${item.data?.label || item.label || "关联"} → ${label(item.target)}` : item.label || item.id,
    level: mode === "edges" ? "关系" : String(item.data?.raw?.level || "节点"),
    description: String(item.data?.raw?.description || ""), relevance: 0, evidenceScore: 0,
    degree: data.edges.filter(e => mode === "edges" ? [e.source, e.target].some(id => id === item.source || id === item.target) : e.source === item.id || e.target === item.id).length,
    highlighted: true,
  }));
  return { ...data, searchResults, nodes: data.nodes.map(n => ({ ...n, highlighted: ids.has(n.id) })),
    edges: data.edges.map(e => ({ ...e, highlighted: mode === "edges" && hitIds.has(e.id) })) };
}
