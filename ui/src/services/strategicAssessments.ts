import { normalizeToolBaseUrlForBrowser } from "@/utils/fileUrl";
import type { RecommendationRun, RecommendationItem, RecommendationCitation } from "./strategicRecommendations";

export type AssessmentScope = { mode: "auto" | "selected"; domainIds: string[]; subdomainId?: string | null; domesticOnly: true; domainNames?: string[]; subdomainName?: string };
export type Criterion = { id: string; kind: "goal" | "capability" | "outcome" | "constraint" | "preference" | "exclusion" | "organization" | "unresolved";
  text: string; necessity: "required" | "preferred" | "excluded" | "informational"; sourceSpan?: { start: number; end: number; text: string } | null; evaluable?: boolean; operator?: "all" | "any"; unresolvedTerms?: string[] };
export type Interpretation = { interpretationId: string; parserVersion: string; taskText: string; criteria: Criterion[];
  scope: AssessmentScope; resolvedDomainIds: string[]; resolvedDomainNames: string[]; proposedDomainIds: string[]; proposedDomainNames: string[]; scopeConflict: boolean; unknowns: string[]; notice: string; evidenceVersion: string };
export type MatrixRow = { criterionId: string; text: string; necessity: string; status: "supported" | "insufficient" | "conditional" | "not_met" | "not_observed"; claimIds: string[]; notice: string };
export type AssessmentItem = RecommendationItem & { criteriaMatrix: MatrixRow[] };
export type AssessmentRun = Omit<RecommendationRun, "items"> & { taskId: string; inputVersionId: string; inputVersion: number;
  scope: AssessmentScope; mode?: "task" | "domain"; criteria?: Criterion[]; items: AssessmentItem[];
  selection?: { comparedTeamIds: string[]; combination: { teamId: string; role: string; rationale: string }[] };
  unresolvedConditions?: { criterionId: string; text: string; reason: string }[];
  observation?: { totalUnits: number; outcomeBackedUnits: number; identityOnlyUnits: number; windowDays: number; recentCutoff: string; notice: string;
    directions: { name: string; teamCount: number; teamIds: string[] }[];
    units: { teamId: string; teamName: string; institutionName: string; outcomeCount: number; recentOutcomeCount: number; undatedOutcomeCount: number; reason: string; citations: RecommendationCitation[] }[] } };
export type FollowUp = { id: string; teamId: string; claimIds: string[]; question: string; method: string; owner: string; dueDate: string | null; result: string; status: "open" | "in_progress" | "done" | "cancelled" };
export type Assessment = { taskId: string; title: string; mode: "task" | "domain"; revision: number; createdAt: string; updatedAt: string;
  runs: { runId: string; inputVersion: number; inputVersionId: string; createdAt: string }[]; run?: AssessmentRun;
  state: { taskDraft: string; domainDraft: string; activeRunId: string | null; comparedTeamIds: string[];
    combination: { teamId: string; role: string; rationale: string }[]; followUps: FollowUp[]; internalNotes: string;
    taskScope?: AssessmentScope; domainScope?: AssessmentScope; requestedLimit?: number } };
export type AssessmentSummary = Pick<Assessment, "taskId" | "title" | "mode" | "revision" | "updatedAt">;

export function requestId() {
  // randomUUID requires HTTPS; getRandomValues also works on the existing HTTP host.
  return "req-" + Array.from(crypto.getRandomValues(new Uint32Array(4)), n => n.toString(16)).join("-");
}

let identityReady: Promise<void> | null = null;
async function establishVisitor() {
  if (!identityReady) identityReady = fetch("/api/agent/visitor/bootstrap", { credentials: "include" })
    .then(async response => { const data = await response.json(); if (!response.ok || data.code !== "0000") throw new Error("无法建立访客会话，请重试"); })
    .catch(error => { identityReady = null; throw error; });
  return identityReady;
}
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  await establishVisitor();
  const response = await fetch(`${normalizeToolBaseUrlForBrowser("")}/v1/strategic-map${path}`, {
    ...init, credentials: "include", headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}), ...init?.headers },
  });
  const body = await response.json();
  if (!response.ok) {
    if (response.status === 401) identityReady = null;
    const detail = body.detail;
    throw new Error(typeof detail === "string" ? detail : detail?.message || `保存失败（${response.status}），请检查输入或重新载入`);
  }
  return body.data as T;
}
const post = <T>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body) });
export const assessmentApi = {
  list: (page = 1) => request<{ items: AssessmentSummary[]; total: number; page: number }>(`/assessments?page=${page}`),
  get: (id: string, signal?: AbortSignal) => request<Assessment>(`/assessments/${encodeURIComponent(id)}`, { signal }),
  create: (body: { title: string; mode: "task" | "domain"; taskDraft: string; domainDraft: string; requestId: string }) => post<Assessment>("/assessments", body),
  patch: (id: string, revision: number, changes: Partial<Assessment["state"]> & { mode?: "task" | "domain"; title?: string }, idempotencyKey = requestId()) =>
    request<Assessment>(`/assessments/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ ...changes, expectedRevision: revision, requestId: idempotencyKey }) }),
  interpret: (taskText: string, scope: AssessmentScope) => post<Interpretation>("/task-interpretations", { taskText, scope }),
  confirm: (id: string, body: { expectedRevision: number; taskText: string; scope: AssessmentScope; limit: number; criteria: Criterion[]; evidenceVersion: string; scopeResolution: string | null; requestId: string }) =>
    post<Assessment>(`/assessments/${encodeURIComponent(id)}/confirm`, body),
  observe: (id: string, body: { expectedRevision: number; scope: AssessmentScope; limit: number; windowDays: number; requestId: string }) =>
    post<Assessment>(`/assessments/${encodeURIComponent(id)}/observe`, body),
  run: (id: string, runId: string) => request<AssessmentRun>(`/assessments/${encodeURIComponent(id)}/runs/${encodeURIComponent(runId)}`),
  export: (id: string, runId: string) => request<Record<string, unknown>>(`/assessments/${encodeURIComponent(id)}/export?run_id=${encodeURIComponent(runId)}`),
};
