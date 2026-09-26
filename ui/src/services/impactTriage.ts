import { normalizeToolBaseUrlForBrowser } from "@/utils/fileUrl";

export type ImpactDirection = {
  id: string; name: string; parent_id: string | null; level: number;
  status: string; review_status: string; ai4s_domain_id: string | null;
  ai4s_subdomain_id: string | null; event_count: number;
};
export type ImpactCandidate = { parent_id: string; name: string; event_count: number; active_dates: number; independent_publishers: number; multi_source_ready: boolean };
export type ImpactL2Candidate = { name: string; event_count: number; name_collision: number };
export type ImpactRanking = {
  id: string; name: string; kind: "institution" | "author"; country: "zn" | "gw";
  tier: "A" | "B" | "C"; total: number; scan_date: string;
  eff_achievement: number; eff_status: number; eff_trend: number;
  change_reason: string; reasons: Record<string, string>;
  event_count: number; flagship_count: number; follow_status: string;
  eligibility: string; calibration_status: string; score_source?: "source" | "calibrated_revision";
  research_type: string; eligibility_basis_url: string; mainland_confirmed?: number;
  identity_recheck_required: boolean; identity_aliases: { name: string; source_entity_id: string }[];
  match_reason?: string; match_snippet?: string;
  direction_ids: string[];
  review_cases: { id: string; kind: string; reason: string }[];
};
export type ImpactEvent = {
  id: string; title: string; summary: string; event_date: string | null;
  is_flagship: number; sources: { id: string; title: string; url: string; publisher: string }[];
  entity_name?: string; country?: string; l3_label?: string | null;
};
export type ImpactScore = {
  id: string; entity_id: string; scan_date: string; tier: string | null; eff_achievement: number | null;
  eff_status: number | null; eff_trend: number | null; change_reason: string;
  calibration_status: string; reasons: Record<string, string | number>;
  revision?: boolean; source_period_ids?: string[]; input_event_ids?: string[];
};
export type ImpactDetail = {
  id: string; name: string; kind: string; country: string; eligibility: string; follow_status: string;
  research_type: string; eligibility_basis_url: string; mainland_confirmed?: number;
  identity_aliases: { id: string; name: string; evidence_url: string }[];
  history: ImpactScore[]; events: ImpactEvent[];
  team_links: { team_id: string; relation: string; evidence_url: string; audit_id?: number }[];
};
export type ImpactDaily = {
  date: string; markdown: string; events: unknown[];
  tree_changes: unknown[]; score_changes: unknown[];
};

const base = normalizeToolBaseUrlForBrowser("");

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}/v1/impact-triage${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers
    },
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || `影响力数据请求失败（${response.status}）`);
  return (body.data ?? body) as T;
}

export const impactApi = {
  status: () => request<{ ready: boolean; source: string | null; entities: number; events: number; scores: number }>("/status"),
  directions: () => request<{ items: ImpactDirection[]; candidates: ImpactCandidate[]; l2_candidates: ImpactL2Candidate[]; unclassified_events: number }>("/directions"),
  directionEvents: (id: string, label?: string) => request<{ items: ImpactEvent[]; limit: number }>(`/directions/${encodeURIComponent(id)}/events${label ? `?l3_label=${encodeURIComponent(label)}` : ""}`),
  unclassifiedEvents: (label?: string) => request<{ items: ImpactEvent[]; limit: number }>(`/unclassified/events${label ? `?label=${encodeURIComponent(label)}` : ""}`),
  ranking: (query: URLSearchParams, signal?: AbortSignal) => request<{ items: ImpactRanking[]; score_notice: string }>(`/ranking?${query}`, { signal }),
  entity: (id: string) => request<ImpactDetail>(`/entities/${encodeURIComponent(id)}`),
  teamCandidates: (id: string, q: string) => request<{ items: { id: string; institution_name: string; team_name: string; domain_id: string }[] }>(
    `/entities/${encodeURIComponent(id)}/team-candidates?q=${encodeURIComponent(q)}`),
  linkTeam: (id: string, team_id: string, relation: "member" | "affiliated" | "same_organization", evidence_url: string, note: string) =>
    request<{ audit_id: number }>(`/entities/${encodeURIComponent(id)}/team-links`, {
      method: "POST",
      body: JSON.stringify({
        team_id,
        relation,
        evidence_url,
        note
      })
    }),
  rollbackTeamLink: (auditId: number) => request<{ rolled_back: boolean }>(`/team-links/${auditId}/rollback`, { method: "POST" }),
  reviews: () => request<{ items: { id: string; kind: string; subject_ids: string; reason: string; status: string }[] }>("/reviews"),
  audits: () => request<{ items: { id: number; direction_id: string; action: string; reviewed_on: string; reverted_on: string | null }[];
    event_mappings: { id: number; label: string; target_direction_id: string; reviewed_on: string; reverted_on: string | null }[] }>("/audits"),
  daily: (date: string) => request<ImpactDaily>(`/daily/${encodeURIComponent(date)}`),
  reviewDirection: (id: string, decision: "approve" | "reject", note: string) =>
    request<{ audit_id: number }>(`/directions/${encodeURIComponent(id)}/review`, {
      method: "POST",
      body: JSON.stringify({
        decision,
        note
      })
    }),
  reviewCandidate: (parent_id: string, name: string, decision: "approve" | "reject", note: string) =>
    request<{ audit_id: number }>("/candidates/review", {
      method: "POST",
      body: JSON.stringify({
        parent_id,
        name,
        decision,
        note
      })
    }),
  rollback: (auditId: number) => request<{ rolled_back: boolean }>(`/audits/${auditId}/rollback`, { method: "POST" }),
  eligibility: (id: string, decision: "eligible" | "excluded" | "unreviewed", note: string,
    research_type?: string, evidence_url?: string, mainland_confirmed = false) =>
    request(`/entities/${encodeURIComponent(id)}/eligibility`, {
      method: "POST",
      body: JSON.stringify({
        decision,
        note,
        research_type,
        evidence_url,
        mainland_confirmed
      })
    }),
  follow: (id: string, followed: boolean) =>
    request(`/entities/${encodeURIComponent(id)}/follow`, {
      method: "POST",
      body: JSON.stringify({ followed })
    }),
  reviewIdentity: (id: string, decision: "false_positive" | "confirmed_duplicate", note: string,
    canonical_id?: string, evidence_url?: string) =>
    request(`/reviews/${encodeURIComponent(id)}/identity`, {
      method: "POST",
      body: JSON.stringify({
        decision,
        note,
        canonical_id,
        evidence_url
      })
    }),
  resolveExisting: (label: string, direction_id: string, note: string) => request<{ audit_id: number; mapped_events: number }>("/unclassified/resolve-existing", {
    method: "POST",
    body: JSON.stringify({
      label,
      direction_id,
      note
    })
  }),
  rollbackMapping: (auditId: number) => request<{ rolled_back: boolean }>(`/event-mappings/${auditId}/rollback`, { method: "POST" }),
  rollbackIdentity: (sourceEntityId: string) => request<{ rolled_back: boolean }>(`/identity-links/${encodeURIComponent(sourceEntityId)}/rollback`, { method: "POST" }),
};
