import { useEffect, useState } from "react";
import { normalizeToolBaseUrlForBrowser } from "@/utils/fileUrl";

export type StrategicAccess = { role: string; permissions: string[]; visitorId: string; notice: string };
export type InternalTeam = { teamId: string; updatedAt: string; fields: Record<string, string>; audit: {
  id: string; createdAt: string; actorRecorded: boolean; actorId?: string; fields: string[];
  before: Record<string, string>; changes: Record<string, string>;
}[] };
export async function accessRequest<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${normalizeToolBaseUrlForBrowser("")}/v1/strategic-map${path}`, { signal });
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail?.message || body.detail || "权限信息暂不可用");
  return body.data;
}
export const readInternalTeam = (id: string, signal?: AbortSignal) => accessRequest<InternalTeam>(`/teams/${encodeURIComponent(id)}/internal`, signal);

export function useStrategicAccess() {
  const [value, setValue] = useState<StrategicAccess>({ role: "visitor", permissions: [], visitorId: "", notice: "正在确认维护权限" });
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    void accessRequest<StrategicAccess>("/access", controller.signal).then(setValue).catch(error => {
      if (!controller.signal.aborted) setValue({ role: "visitor", permissions: [], visitorId: "", notice: String(error.message) });
    }).finally(() => { if (!controller.signal.aborted) setReady(true); });
    return () => controller.abort();
  }, []);
  return { ...value, ready, canEdit: value.permissions.includes("team:write"),
    canReview: value.permissions.includes("review:write"), canCollect: value.permissions.includes("collection:run"),
    canMaintain: value.permissions.includes("maintenance:run") };
}
