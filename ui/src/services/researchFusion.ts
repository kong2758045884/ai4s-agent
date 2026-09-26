import { normalizeToolBaseUrlForBrowser } from "@/utils/fileUrl";
import type { IntelligenceDaily } from "./strategicRecommendations";
import type { StrategicGraphData } from "./strategicMap";

export type FusionStatus = {
  knowledgeGraph: { scope: string; nodes: number; edges: number; sha256: string; updatedAt: string }[];
  triage: { ready: boolean; entities: number; events: number; periods: number; dates: string[] };
  automaticPaidRefresh: boolean;
};
export type FusionDaily = {
  date: string; inputHash: string; frozen: boolean; revision: number;
  sourceEvents: { id: string; title: string; summary: string; origin: string; sources: { title: string; url: string }[] }[];
  scoreChanges: { id: string; name: string; tier: string; change_reason: string; calibration_status: string }[];
  treeChanges: { id: number; action?: string; label?: string; reviewed_on: string; reverted_on: string | null }[];
  teamChanges: IntelligenceDaily["teamChanges"];
  recommendationChanges: IntelligenceDaily["recommendationChanges"];
};
async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${normalizeToolBaseUrlForBrowser("")}/v1/strategic-map/integration${path}`, { signal });
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || "融合数据读取失败");
  return body as T;
}
export const fusionApi = {
  status: (signal?: AbortSignal) => request<FusionStatus>("/status", signal),
  daily: (day: string, domainId: string, subdomainId: string, signal?: AbortSignal) => {
    const q = new URLSearchParams();
    if (domainId) q.set("domain_id", domainId);
    if (subdomainId) q.set("subdomain_id", subdomainId);
    return request<FusionDaily>(`/daily/${day}?${q}`, signal);
  },
  graph: (context: { scope: string; cluster: string; domainId?: string; subdomainId?: string }, options?: { signal?: AbortSignal }) => {
    const q = new URLSearchParams({ scope: context.scope, cluster: context.cluster });
    if (context.domainId) q.set("domain_id", context.domainId);
    if (context.subdomainId) q.set("subdomain_id", context.subdomainId);
    return request<StrategicGraphData>(`/graph?${q}`, options?.signal);
  },
};
