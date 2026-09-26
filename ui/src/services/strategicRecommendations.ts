import { normalizeToolBaseUrlForBrowser } from "@/utils/fileUrl";

export type RecommendationCitation = {
  kind: "outcome" | "capability" | "description" | "direction";
  text: string;
  quote: string;
  url: string;
};

export type RecommendationItem = {
  teamId: string;
  teamName: string;
  institutionId: string | null;
  institutionName: string;
  institutionImpact: string;
  domainId: string;
  subdomainId: string | null;
  taskMatchScore: number;
  teamScore: number;
  teamScoreVersion: string;
  matchVersion: string;
  capability: string;
  citations: RecommendationCitation[];
  unknowns: string[];
  nextStep: string;
  updatedAt: string;
};

export type RecommendationRun = {
  runId: string;
  taskText: string;
  domainId: string | null;
  subdomainId: string | null;
  requestedLimit: number;
  eligibleTeamCount: number;
  matchedTeamCount: number;
  shortfall: number;
  items: RecommendationItem[];
  pendingLeads: { teamId: string; teamName: string; institutionName: string; matchHint: number; sourceUrl: string; reason: string }[];
  dataVersion: string;
  matchVersion: string;
  createdAt: string;
  notice: string;
  expansion?: {
    jobId?: string;
    status?: string;
    state?: string;
    stage?: string;
    progress?: { done: number; total: number };
    error?: string;
    result?: { candidates?: unknown[] };
  };
};

export type IntelligenceSearchResult = {
  type: "team_claim" | "team_profile" | "institution_event";
  id: string;
  teamId?: string;
  title: string;
  snippet: string;
  url: string | null;
  domainId: string | null;
  date?: string;
  reviewNotice?: string;
};

export type IntelligenceDaily = {
  date: string;
  revision: number;
  frozen: boolean;
  frozenAt?: string;
  inputVersions?: { schema: string; impactBatchIds: string[]; impactEventIds: string[];
    taxonomyAuditIds: string[]; teamResearchRunIds: string[]; recommendationRunIds: string[];
    teamEvidenceVersion?: string | null };
  impact: { markdown: string; events: { id: string; direction_id: string | null }[];
    tree_changes: unknown[]; score_changes: unknown[] };
  teamChanges: { teamId: string; teamName: string; institutionName: string; domainId: string;
    reason: string; scoreBefore: number | null; scoreAfter: number | null; scoreVersion: string | null }[];
  recommendationChanges: { taskText: string; domainId: string | null; before: string[]; after: string[]; reason: string }[];
  domains: Record<string, { teamUpdates: number; recommendationChanges: number; followUps: string[] }>;
  domainSummaries?: Record<string, { domainName: string; institutionEvents: number;
    teamUpdates: number; recommendationChanges: number; summary: string }>;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${normalizeToolBaseUrlForBrowser("")}/v1/strategic-map${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || `战略图谱请求失败（${response.status}）`);
  return (body.data ?? body) as T;
}

export const recommendationApi = {
  verifiedTeams: (signal?: AbortSignal) => request<{ teamIds: string[]; teams?: Record<string, unknown>[]; dataVersion: string; claimCount: number }>("/intelligence/verified-teams", { signal }),
  create: (taskText: string, domainId: string, subdomainId: string, limit: number) =>
    request<RecommendationRun>("/task-recommendations", {
      method: "POST",
      body: JSON.stringify({
        taskText,
        domainId: domainId || null,
        subdomainId: subdomainId || null,
        limit,
      }),
    }),
  get: (runId: string) => request<RecommendationRun>(`/task-recommendations/${encodeURIComponent(runId)}`),
  expand: (runId: string) => request<RecommendationRun>(`/task-recommendations/${encodeURIComponent(runId)}/expand`, { method: "POST" }),
  search: (q: string, domainId: string, page = 1) => {
    const params = new URLSearchParams({
      q,
      page: String(page),
      size: "20",
      verified_only: "true",
    });
    if (domainId) params.set("domain_id", domainId);
    return request<{ items: IntelligenceSearchResult[]; total: number; page: number; size: number }>(`/intelligence/search?${params}`);
  },
  daily: (day: string) => request<IntelligenceDaily>(`/intelligence/daily/${encodeURIComponent(day)}`),
  verifiedDaily: (day: string) => request<{
    date: string; revision: number; frozen: boolean;
    events: { id: string; title: string; direction_id: string | null }[];
    teamChanges: IntelligenceDaily["teamChanges"];
    recommendationChanges: IntelligenceDaily["recommendationChanges"];
  }>(`/intelligence/verified-daily/${encodeURIComponent(day)}`),
  freezeDaily: (day: string) => request<IntelligenceDaily>(`/intelligence/daily/${encodeURIComponent(day)}/freeze`, { method: "POST" }),
};
