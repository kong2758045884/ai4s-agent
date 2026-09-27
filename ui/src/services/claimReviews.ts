import { normalizeToolBaseUrlForBrowser } from "@/utils/fileUrl";
import type { ClaimProvenance, RecommendationCitation } from "./strategicRecommendations";

export type ClaimDecision = "supported" | "conditional" | "insufficient" | "conflict" | "withdrawn";
export const claimDecisionLabels: Record<string, string> = { supported: "支持", conditional: "条件支持", insufficient: "证据不足", conflict: "存在冲突", withdrawn: "撤回", unreviewed: "恢复来源检查状态" };
export type HumanReview = ClaimProvenance["humanReview"];
export type ClaimReviewView = { claim: RecommendationCitation & { id: string; teamId: string; fingerprint: string }; teamName: string;
  available: boolean; revision: number; review: HumanReview; linkStatus: string;
  history?: (HumanReview & { actorId: string; linkStatus: string })[] };
export type ClaimReviewBody = { requestId: string; expectedRevision: number; sourceFingerprint: string; signedName: string;
  decision: ClaimDecision; scope: string; reason: string; linkStatus: string;
  relatedEvidence: { title: string; url: string; quote: string }[] };

async function request<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${normalizeToolBaseUrlForBrowser("")}/v1/strategic-map${path}`, {
    signal, credentials: "include", ...(body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : result.detail?.message || "核验未保存，请检查输入并重试");
  return result.data;
}
export const claimReviews = {
  get: (id: string, signal?: AbortSignal) => request<ClaimReviewView>(`/claims/${encodeURIComponent(id)}/review`, undefined, signal),
  list: (team: string, status: string, page: number, signal?: AbortSignal) => request<{ items: ClaimReviewView[]; total: number; page: number; pageSize: number; dataVersion: string }>(`/claim-reviews?${new URLSearchParams({ team_id: team, status, page: String(page) })}`, undefined, signal),
  save: (id: string, body: ClaimReviewBody) => request<HumanReview>(`/claims/${encodeURIComponent(id)}/reviews`, body),
  revert: (id: string, reviewId: string, body: Pick<ClaimReviewBody, "requestId" | "expectedRevision" | "sourceFingerprint" | "signedName" | "reason">) => request<HumanReview>(`/claims/${encodeURIComponent(id)}/reviews/${encodeURIComponent(reviewId)}/revert`, body),
};
