import { trimTrailingSlash } from "@/pages/WorkspaceImageGeneration/utils";
import { normalizeToolBaseUrlForBrowser } from "@/utils/fileUrl";

export type StrategicSubdomain = {
  id: string;
  name: string;
  label: string;
  description: string;
  parentId: string;
};

export type StrategicDomain = {
  id: string;
  name: string;
  label: string;
  description: string;
  parentId?: string | null;
  subdomains: StrategicSubdomain[];
};

export type StrategicTeam = {
  id: string;
  domainId: string;
  subdomainId?: string | null;
  name: string;
  /** API 明确返回的机构与团队线索；旧快照没有时回退到 name/focus。 */
  organization?: string;
  teamName?: string;
  focus: string;
  aiLevel: string;
  scienceLevel: string;
  attention: string;
  contact: string;
  coreDirection: string;
  dualJudgement: string;
  contactRecord: string;
  internalReview: string;
  recentUpdate: string;
  nextAction: string;
  source: string;
  sourceUrls: string[];
  evidenceSummary: string;
  reportId: string;
  reportTitle: string;
  updatedAt: string;
};

export type StrategicMapSource = {
  provider: string;
  refreshed?: boolean;
  reportCount?: number;
  candidateCount?: number;
  updatedAt?: string;
};

export type StrategicMapSnapshot = {
  domains: StrategicDomain[];
  teams: StrategicTeam[];
  source: StrategicMapSource;
};

type WrappedResponse<T> = {
  code?: number | string;
  data?: T;
  detail?: string;
  message?: string;
  msg?: string;
};

type RawRecord = Record<string, unknown>;

function toolBaseUrl(): string {
  return normalizeToolBaseUrlForBrowser(trimTrailingSlash(AI4S_TOOL_BASE_URL || ""));
}

function text(value: unknown, fallback = ""): string {
  const next = String(value ?? "").trim();
  return next || fallback;
}

function record(value: unknown): RawRecord {
  return value && typeof value === "object" ? (value as RawRecord) : {};
}

function rawValue(raw: RawRecord, camel: string, snake: string): unknown {
  return raw[camel] ?? raw[snake];
}

function mapSubdomain(rawValueItem: unknown, parentId: string): StrategicSubdomain {
  const raw = record(rawValueItem);
  return {
    id: text(raw.id || raw.subdomain_id || raw.subdomainId),
    name: text(rawValue(raw, "name", "subdomain_name")),
    label: text(raw.label || raw.name || raw.subdomain_name),
    description: text(raw.description || raw.subdomain_desc),
    parentId: text(raw.parentId || raw.parent_id, parentId),
  };
}

function mapDomain(rawValueItem: unknown): StrategicDomain {
  const raw = record(rawValueItem);
  const id = text(raw.id || raw.domain_id || raw.domainId);
  const children = Array.isArray(raw.subdomains)
    ? raw.subdomains.map((item) => mapSubdomain(item, id)).filter((item) => item.id)
    : [];
  return {
    id,
    name: text(raw.name || raw.label || raw.domain_name),
    label: text(raw.label || raw.name || raw.domain_name),
    description: text(raw.description || raw.domain_desc),
    parentId: raw.parentId == null && raw.parent_id == null ? null : text(raw.parentId || raw.parent_id),
    subdomains: children,
  };
}

function mapTeam(rawValueItem: unknown): StrategicTeam {
  const raw = record(rawValueItem);
  return {
    id: text(raw.id || raw.team_id),
    domainId: text(rawValue(raw, "domainId", "domain_id")),
    subdomainId: raw.subdomainId == null && raw.subdomain_id == null
      ? null
      : text(raw.subdomainId || raw.subdomain_id),
    name: text(raw.name || raw.team_name, "待命名候选"),
    organization: text(raw.organization || raw.organization_name || raw.name || raw.team_name),
    teamName: text(raw.teamName || raw.team_name_label || raw.team_name || raw.focus || raw.team_focus),
    focus: text(raw.focus || raw.team_focus),
    aiLevel: text(rawValue(raw, "aiLevel", "ai_level"), "待核实"),
    scienceLevel: text(rawValue(raw, "scienceLevel", "science_level"), "待核实"),
    attention: text(raw.attention, "待核实"),
    contact: text(raw.contact, "未接触"),
    coreDirection: text(rawValue(raw, "coreDirection", "core_direction")),
    dualJudgement: text(rawValue(raw, "dualJudgement", "dual_judgement")),
    contactRecord: text(rawValue(raw, "contactRecord", "contact_record"), "暂无联系记录"),
    internalReview: text(rawValue(raw, "internalReview", "internal_review"), "待补充研判"),
    recentUpdate: text(rawValue(raw, "recentUpdate", "recent_update")),
    nextAction: text(rawValue(raw, "nextAction", "next_action"), "补充公开证据并确认团队信息"),
    source: text(raw.source, "AI4S Daily"),
    sourceUrls: (() => {
      const sourceUrls = raw.sourceUrls ?? raw.source_urls;
      return Array.isArray(sourceUrls)
        ? (sourceUrls as unknown[]).map((item) => text(item)).filter(Boolean)
        : [];
    })(),
    evidenceSummary: text(raw.evidenceSummary || raw.evidence_summary),
    reportId: text(raw.reportId || raw.report_id),
    reportTitle: text(raw.reportTitle || raw.report_title),
    updatedAt: text(raw.updatedAt || raw.updated_at),
  };
}

function mapSource(value: unknown): StrategicMapSource {
  const raw = record(value);
  return {
    provider: text(raw.provider, "AI4S Daily"),
    refreshed: Boolean(raw.refreshed),
    reportCount: typeof raw.reportCount === "number" ? raw.reportCount : undefined,
    candidateCount: typeof raw.candidateCount === "number" ? raw.candidateCount : undefined,
    updatedAt: text(raw.updatedAt || raw.updated_at),
  };
}

function extractData<T>(raw: unknown): T {
  const wrapped = record(raw) as WrappedResponse<T>;
  const code = wrapped.code;
  if (code !== undefined && String(code) !== "200") {
    const message = text(wrapped.detail || wrapped.message || wrapped.msg, `战略图谱请求失败（${code}）`);
    throw new Error(message);
  }
  return (wrapped.data ?? raw) as T;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${toolBaseUrl()}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(init?.headers || {}),
    },
  });
  const bodyText = await response.text();
  let body: unknown = {};
  try {
    body = bodyText ? JSON.parse(bodyText) : {};
  } catch {
    body = bodyText;
  }
  if (!response.ok) {
    const raw = record(body);
    throw new Error(text(raw.detail || raw.message || raw.msg, `战略图谱请求失败（${response.status}）`));
  }
  return extractData<T>(body);
}

function normalizeSnapshot(value: unknown): StrategicMapSnapshot {
  const raw = record(value);
  const domains = Array.isArray(raw.domains) ? raw.domains.map(mapDomain).filter((item) => item.id) : [];
  const teams = Array.isArray(raw.teams) ? raw.teams.map(mapTeam).filter((item) => item.id) : [];
  return {
    domains,
    teams,
    source: mapSource(raw.source),
  };
}

function payload(name: string, description: string) {
  return JSON.stringify({ name: name.trim(), description: description.trim() });
}

export async function loadStrategicMap(options?: {
  refresh?: boolean;
  domainId?: string;
}): Promise<StrategicMapSnapshot> {
  const params = new URLSearchParams();
  if (options?.refresh) params.set("refresh", "true");
  if (options?.domainId) params.set("domain_id", options.domainId);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return normalizeSnapshot(await request<unknown>(`/v1/strategic-map${suffix}`));
}

export async function loadStrategicDomainTeams(
  domainId: string,
  options?: { refresh?: boolean; subdomainId?: string },
): Promise<{ teams: StrategicTeam[]; source: StrategicMapSource; domain?: StrategicDomain }> {
  const params = new URLSearchParams();
  if (options?.refresh) params.set("refresh", "true");
  if (options?.subdomainId) params.set("subdomain_id", options.subdomainId);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const raw = record(await request<unknown>(`/v1/strategic-map/domains/${encodeURIComponent(domainId)}/teams${suffix}`));
  return {
    teams: Array.isArray(raw.teams) ? raw.teams.map(mapTeam).filter((item) => item.id) : [],
    source: mapSource(raw.source),
    domain: raw.domain ? mapDomain(raw.domain) : undefined,
  };
}

/** Persist the manually maintained status and detail fields for a candidate team. */
export type StrategicTeamUpdate = Pick<
  StrategicTeam,
  | "attention"
  | "contact"
  | "coreDirection"
  | "dualJudgement"
  | "contactRecord"
  | "internalReview"
  | "recentUpdate"
  | "nextAction"
>;

export async function updateStrategicTeam(
  id: string,
  details: StrategicTeamUpdate,
): Promise<StrategicTeam> {
  return mapTeam(await request<unknown>(`/v1/strategic-map/teams/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify({
      attention: details.attention.trim(),
      contact: details.contact.trim(),
      core_direction: details.coreDirection.trim(),
      dual_judgement: details.dualJudgement.trim(),
      contact_record: details.contactRecord.trim(),
      internal_review: details.internalReview.trim(),
      recent_update: details.recentUpdate.trim(),
      next_action: details.nextAction.trim(),
    }),
  }));
}

export async function createStrategicDomain(name: string, description: string): Promise<StrategicDomain> {
  const raw = record(await request<unknown>("/v1/strategic-map/domains", { method: "POST", body: payload(name, description) }));
  return mapDomain(raw.domain || raw);
}

export async function updateStrategicDomain(id: string, name: string, description: string): Promise<StrategicDomain> {
  return mapDomain(await request<unknown>(`/v1/strategic-map/domains/${encodeURIComponent(id)}`, { method: "PUT", body: payload(name, description) }));
}

export async function deleteStrategicDomain(id: string): Promise<void> {
  await request(`/v1/strategic-map/domains/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function createStrategicSubdomain(parentId: string, name: string, description: string): Promise<StrategicSubdomain> {
  return mapSubdomain(
    await request<unknown>(`/v1/strategic-map/domains/${encodeURIComponent(parentId)}/subdomains`, {
      method: "POST",
      body: payload(name, description),
    }),
    parentId,
  );
}

export async function updateStrategicSubdomain(id: string, name: string, description: string, parentId: string): Promise<StrategicSubdomain> {
  return mapSubdomain(
    await request<unknown>(`/v1/strategic-map/subdomains/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: payload(name, description),
    }),
    parentId,
  );
}

export async function deleteStrategicSubdomain(id: string): Promise<void> {
  await request(`/v1/strategic-map/subdomains/${encodeURIComponent(id)}`, { method: "DELETE" });
}
