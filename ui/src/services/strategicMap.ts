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
  locked?: boolean;
  subdomains: StrategicSubdomain[];
};

export type StrategicEligibility = {
  concreteTeam: boolean;
  geography: boolean;
  domainRelevance: boolean;
  advantageEvidence: boolean;
  leaderEvidence: boolean;
  eligible: boolean;
};

export type StrategicScoreBreakdown = {
  achievementQuality: number;
  domainRelevance: number;
  recentActivity: number;
  evidenceReliability: number;
  graphInfluence: number;
  teamCompleteness: number;
  aiEvidenceReview: number;
};

export type CapabilityAssessment = {
  level: string;
  source: "ai" | "manual" | "none";
  reason: string;
  citations: { url: string; quote: string }[];
  updatedAt?: string;
};

export type StrategicTeam = {
  id: string;
  domainId: string;
  subdomainId?: string | null;
  name: string;
  /** API 明确返回的机构与团队线索；旧快照没有时回退到 name/focus。 */
  organization?: string;
  institutionName?: string;
  teamName?: string;
  description?: string;
  researchDirections?: string[];
  location?: string;
  isDomestic?: boolean;
  verificationStatus?: string;
  eligibility?: StrategicEligibility;
  scoreBreakdown?: StrategicScoreBreakdown;
  scoreTotal?: number;
  scoreEvidenceIds?: string[];
  scoreVersion?: string;
  scoredAt?: string;
  meetsBaseline?: boolean;
  candidateQualified?: boolean;
  focus: string;
  aiLevel: string;
  scienceLevel: string;
  capabilityAssessments?: { ai: CapabilityAssessment; science: CapabilityAssessment };
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
  leader?: StrategicPerson | null;
  leaders?: StrategicPerson[];
  members?: StrategicPerson[];
  leaderId?: string;
};

export type StrategicPerson = {
  verificationStatus?: string;
  id: string;
  teamId: string;
  name: string;
  title: string;
  role: string;
  researchDirection: string;
  bio: string;
  avatarUrl: string;
  profileUrl: string;
  sourceUrls: string[];
  sourceType: string;
  lastVerifiedAt: string;
  isLeader: boolean;
};

export type StrategicTeamDetail = {
  team: StrategicTeam;
  leader: StrategicPerson | null;
  leaders?: StrategicPerson[];
  members: StrategicPerson[];
  domain?: StrategicDomain;
  subdomain?: StrategicSubdomain | null;
};

export type StrategicMapSource = {
  provider: string;
  pipeline?: string;
  refreshed?: boolean;
  reportCount?: number;
  candidateCount?: number;
  teamCount?: number;
  lastError?: string;
  updatedAt?: string;
};

export type StrategicRefreshState =
  | "accepted"
  | "running"
  | "succeeded"
  | "partial"
  | "failed"
  | "cancelled"
  | "timed_out";

export type StrategicRefreshTask = {
  taskId: string;
  domainId: string;
  subdomainId?: string | null;
  state: StrategicRefreshState;
  terminal: boolean;
  message: string;
  result: Record<string, unknown>;
  createdAt: string;
  startedAt: string;
  updatedAt: string;
  finishedAt: string;
};

export type StrategicNationwideRefresh = {
  seeded: {
    total: number;
    created: number;
    retainedVerified: number;
    byDomain: Record<string, number>;
  };
  tasks: StrategicRefreshTask[];
};

export type StrategicMapSnapshot = {
  domains: StrategicDomain[];
  teams: StrategicTeam[];
  source: StrategicMapSource;
};

export type StrategicGraphScope = "domestic" | "international";
export type StrategicGraphCluster = "高峰" | "高原";

export type StrategicGraphStatus = {
  available: boolean;
  hyperAvailable: boolean;
  provider: string;
  features: Record<string, boolean>;
  snapshotUpdatedAt: Partial<Record<StrategicGraphScope, string>>;
};

export type StrategicGraphElement = {
  id: string;
  label?: string;
  type?: string;
  highlighted?: boolean;
  source?: string;
  target?: string;
  data?: {
    label?: string;
    raw?: Record<string, unknown>;
  };
};

export type StrategicGraphSearchResult = {
  id: string;
  label: string;
  level: string;
  description: string;
  relevance: number;
  degree: number;
  evidenceScore: number;
  highlighted: boolean;
};

export type StrategicGraphData = {
  nodes: StrategicGraphElement[];
  edges: StrategicGraphElement[];
  provider: string;
  searchResults: StrategicGraphSearchResult[];
  meta: {
    type?: string;
    features?: Record<string, boolean>;
    stats?: Record<string, number>;
    filter?: {
      domain?: string;
      subdomain?: string;
      originalNodes?: number;
      filteredNodes?: number;
    };
  };
};

export type StrategicGraphScanCandidate = {
  rank: number;
  name: string;
  type: string;
  achievement: number | null;
  status: number | null;
  future: number | null;
  total: number | null;
  comment: string;
  reasons: {
    achievement: string;
    status: string;
    future: string;
  } | null;
  event_count: number;
  direction_overlap?: number;
  profile?: string;
  directions?: string[];
  aliases?: string[];
  eligibility: StrategicEligibility;
  eligibilityReasons?: Record<string, string>;
  scoreBreakdown: StrategicScoreBreakdown;
  evidenceIds: string[];
  evidence?: Array<{
    id: string;
    type: string;
    title: string;
    description: string;
  }>;
  scoreVersion: string;
  scoredAt: string;
  legacyRank?: number;
  legacyScores?: {
    achievement: number | null;
    status: number | null;
    future: number | null;
    total: number | null;
  };
};

export type StrategicGraphScanResult = {
  keyword: string;
  total_found: number;
  truncated: boolean;
  ranking_fallback: boolean;
  partial: boolean;
  filtered_venues: string[];
  summary: string;
  scoringSummary?: string;
  scoreVersion?: string;
  scoredAt?: string;
  candidates: StrategicGraphScanCandidate[];
};

export type StrategicGraphScanTask = {
  jobId: string;
  hyperJobId?: string;
  keyword: string;
  scope: StrategicGraphScope;
  domainId?: string;
  subdomainId?: string;
  maxCandidates: number;
  status: "running" | "done" | "error";
  stage: string;
  progress: { done: number; total: number };
  result: StrategicGraphScanResult | null;
  error: string | null;
  persistent: boolean;
  createdAt: string;
  updatedAt: string;
  finishedAt: string;
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
  return normalizeToolBaseUrlForBrowser(
    trimTrailingSlash(AI4S_TOOL_BASE_URL || ""),
  );
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

function mapPerson(rawValueItem: unknown, teamId = ""): StrategicPerson {
  const raw = record(rawValueItem);
  const sourceUrls = raw.sourceUrls ?? raw.source_urls;
  return {
    id: text(raw.id || raw.person_id),
    teamId: text(raw.teamId || raw.team_id, teamId),
    name: text(raw.name, "未确认姓名"),
    title: text(raw.title),
    role: text(raw.role),
    researchDirection: text(raw.researchDirection || raw.research_direction),
    bio: text(raw.bio),
    avatarUrl: text(raw.avatarUrl || raw.avatar_url),
    profileUrl: text(raw.profileUrl || raw.profile_url),
    sourceUrls: Array.isArray(sourceUrls)
      ? sourceUrls.map((item) => text(item)).filter(Boolean)
      : [],
    sourceType: text(raw.sourceType || raw.source_type),
    lastVerifiedAt: text(raw.lastVerifiedAt || raw.last_verified_at),
    verificationStatus: text(raw.verificationStatus || raw.verification_status),
    isLeader: Boolean(raw.isLeader ?? raw.is_leader),
  };
}

export function reviewedTeamPeople(people: StrategicPerson[] = []): StrategicPerson[] {
  return people.filter((person) => person.verificationStatus === "verified"
    && [person.profileUrl, ...person.sourceUrls].some((url) => /^https?:\/\//i.test(url)));
}

function mapSubdomain(
  rawValueItem: unknown,
  parentId: string,
): StrategicSubdomain {
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
    ? raw.subdomains
      .map((item) => mapSubdomain(item, id))
      .filter((item) => item.id)
    : [];
  return {
    id,
    name: text(raw.name || raw.label || raw.domain_name),
    label: text(raw.label || raw.name || raw.domain_name),
    description: text(raw.description || raw.domain_desc),
    parentId:
      raw.parentId == null && raw.parent_id == null
        ? null
        : text(raw.parentId || raw.parent_id),
    locked: Boolean(raw.locked),
    subdomains: children,
  };
}

export function mapTeam(rawValueItem: unknown): StrategicTeam {
  const raw = record(rawValueItem);
  const rawDirections = raw.researchDirections ?? raw.research_directions;
  const id = text(raw.id || raw.team_id);
  const leaderValue = raw.leader;
  const membersValue = raw.members;
  const rawEligibility = record(raw.eligibility);
  const rawBreakdown = record(raw.scoreBreakdown ?? raw.score_breakdown);
  return {
    id,
    domainId: text(rawValue(raw, "domainId", "domain_id")),
    subdomainId:
      raw.subdomainId == null && raw.subdomain_id == null
        ? null
        : text(raw.subdomainId || raw.subdomain_id),
    name: text(raw.name || raw.team_name, "待命名候选"),
    organization: text(
      raw.organization || raw.organization_name || raw.name || raw.team_name,
    ),
    institutionName: text(
      raw.institutionName ||
        raw.institution_name ||
        raw.organization ||
        raw.name ||
        raw.team_name,
    ),
    teamName: text(
      raw.teamName ||
        raw.team_name_label ||
        raw.team_name ||
        raw.focus ||
        raw.team_focus,
    ),
    description: text(raw.description),
    researchDirections: Array.isArray(rawDirections)
      ? rawDirections.map((item) => text(item)).filter(Boolean)
      : [],
    location: text(raw.location),
    verificationStatus: text(raw.verificationStatus || raw.verification_status),
    eligibility: {
      concreteTeam: Boolean(rawEligibility.concreteTeam),
      geography: Boolean(rawEligibility.geography),
      domainRelevance: Boolean(rawEligibility.domainRelevance),
      advantageEvidence: Boolean(rawEligibility.advantageEvidence),
      leaderEvidence: Boolean(rawEligibility.leaderEvidence),
      eligible: Boolean(rawEligibility.eligible),
    },
    scoreBreakdown: {
      achievementQuality: Number(rawBreakdown.achievementQuality || 0),
      domainRelevance: Number(rawBreakdown.domainRelevance || 0),
      recentActivity: Number(rawBreakdown.recentActivity || 0),
      evidenceReliability: Number(rawBreakdown.evidenceReliability || 0),
      graphInfluence: Number(rawBreakdown.graphInfluence || 0),
      teamCompleteness: Number(rawBreakdown.teamCompleteness || 0),
      aiEvidenceReview: Number(rawBreakdown.aiEvidenceReview || 0),
    },
    scoreTotal: Number(raw.scoreTotal ?? raw.score_total ?? 0),
    scoreEvidenceIds: (() => {
      const evidenceIds = raw.scoreEvidenceIds ?? raw.score_evidence_ids;
      return Array.isArray(evidenceIds)
        ? evidenceIds.map((item) => text(item)).filter(Boolean)
        : [];
    })(),
    scoreVersion: text(raw.scoreVersion || raw.score_version),
    scoredAt: text(raw.scoredAt || raw.scored_at),
    meetsBaseline: Boolean(raw.meetsBaseline ?? raw.meets_baseline),
    candidateQualified: Boolean(
      raw.candidateQualified ?? raw.candidate_qualified ?? (
        (raw.meetsBaseline ?? raw.meets_baseline)
        && rawEligibility.eligible
        && Number(raw.scoreTotal ?? raw.score_total ?? 0) >= 60
      ),
    ),
    isDomestic:
      raw.isDomestic == null && raw.is_domestic == null
        ? undefined
        : Boolean(raw.isDomestic ?? raw.is_domestic),
    focus: text(raw.focus || raw.team_focus),
    aiLevel: text(rawValue(raw, "aiLevel", "ai_level"), "待核实"),
    capabilityAssessments: raw.capabilityAssessments as StrategicTeam["capabilityAssessments"],
    scienceLevel: text(
      rawValue(raw, "scienceLevel", "science_level"),
      "待核实",
    ),
    attention: text(raw.attention, "待核实"),
    contact: text(raw.contact, "未接触"),
    coreDirection: text(rawValue(raw, "coreDirection", "core_direction")),
    dualJudgement: text(rawValue(raw, "dualJudgement", "dual_judgement")),
    contactRecord: text(
      rawValue(raw, "contactRecord", "contact_record"),
      "暂无联系记录",
    ),
    internalReview: text(
      rawValue(raw, "internalReview", "internal_review"),
      "待补充研判",
    ),
    recentUpdate: text(rawValue(raw, "recentUpdate", "recent_update")),
    nextAction: text(
      rawValue(raw, "nextAction", "next_action"),
      "补充公开证据并确认团队信息",
    ),
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
    leader: leaderValue ? mapPerson(leaderValue, id) : null,
    leaders: Array.isArray(raw.leaders)
      ? raw.leaders.map((item) => mapPerson(item, id))
      : leaderValue
        ? [mapPerson(leaderValue, id)]
        : [],
    members: Array.isArray(membersValue)
      ? membersValue.map((item) => mapPerson(item, id))
      : [],
    leaderId: text(raw.leaderId || raw.leader_id),
  };
}

function mapSource(value: unknown): StrategicMapSource {
  const raw = record(value);
  return {
    provider: text(raw.provider, "AI4S Daily"),
    pipeline: text(raw.pipeline),
    refreshed: Boolean(raw.refreshed),
    reportCount:
      typeof raw.reportCount === "number" ? raw.reportCount : undefined,
    candidateCount:
      typeof raw.candidateCount === "number" ? raw.candidateCount : undefined,
    teamCount: typeof raw.teamCount === "number" ? raw.teamCount : undefined,
    lastError: text(raw.lastError || raw.last_error),
    updatedAt: text(raw.updatedAt || raw.updated_at),
  };
}

function extractData<T>(raw: unknown): T {
  const wrapped = record(raw) as WrappedResponse<T>;
  const code = wrapped.code;
  if (code !== undefined && String(code) !== "200") {
    const message = text(
      wrapped.detail || wrapped.message || wrapped.msg,
      `战略图谱请求失败（${code}）`,
    );
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
    throw new Error(
      text(
        raw.detail || raw.message || raw.msg,
        `战略图谱请求失败（${response.status}）`,
      ),
    );
  }
  return extractData<T>(body);
}

function normalizeSnapshot(value: unknown): StrategicMapSnapshot {
  const raw = record(value);
  const domains = Array.isArray(raw.domains)
    ? raw.domains.map(mapDomain).filter((item) => item.id)
    : [];
  const teams = Array.isArray(raw.teams)
    ? raw.teams.map(mapTeam).filter((item) => item.id)
    : [];
  return {
    domains,
    teams,
    source: mapSource(raw.source),
  };
}

function mapRefreshTask(value: unknown): StrategicRefreshTask {
  const raw = record(value);
  const state = text(raw.state, "failed") as StrategicRefreshState;
  return {
    taskId: text(raw.taskId || raw.task_id),
    domainId: text(raw.domainId || raw.domain_id),
    subdomainId:
      raw.subdomainId == null && raw.subdomain_id == null
        ? null
        : text(raw.subdomainId || raw.subdomain_id),
    state,
    terminal:
      typeof raw.terminal === "boolean"
        ? raw.terminal
        : ["succeeded", "partial", "failed", "cancelled", "timed_out"].includes(
          state,
        ),
    message: text(raw.message),
    result: record(raw.result),
    createdAt: text(raw.createdAt || raw.created_at),
    startedAt: text(raw.startedAt || raw.started_at),
    updatedAt: text(raw.updatedAt || raw.updated_at),
    finishedAt: text(raw.finishedAt || raw.finished_at),
  };
}

function payload(name: string, description: string) {
  return JSON.stringify({
    name: name.trim(),
    description: description.trim(),
  });
}

export async function loadStrategicMap(options?: {
  refresh?: boolean;
  domainId?: string;
  signal?: AbortSignal;
}): Promise<StrategicMapSnapshot> {
  const params = new URLSearchParams();
  if (options?.refresh) params.set("refresh", "true");
  if (options?.domainId) params.set("domain_id", options.domainId);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return normalizeSnapshot(
    await request<unknown>(`/v1/strategic-map${suffix}`, {signal: options?.signal,}),
  );
}

export async function loadStrategicDomainTeams(
  domainId: string,
  options?: { refresh?: boolean; subdomainId?: string },
): Promise<{
  teams: StrategicTeam[];
  source: StrategicMapSource;
  domain?: StrategicDomain;
}> {
  const params = new URLSearchParams();
  if (options?.refresh) params.set("refresh", "true");
  if (options?.subdomainId) params.set("subdomain_id", options.subdomainId);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const raw = record(
    await request<unknown>(
      `/v1/strategic-map/domains/${encodeURIComponent(domainId)}/teams${suffix}`,
    ),
  );
  return {
    teams: Array.isArray(raw.teams)
      ? raw.teams.map(mapTeam).filter((item) => item.id)
      : [],
    source: mapSource(raw.source),
    domain: raw.domain ? mapDomain(raw.domain) : undefined,
  };
}

export async function loadStrategicTeamDetail(
  teamId: string,
  options?: { signal?: AbortSignal },
): Promise<StrategicTeamDetail> {
  const raw = record(
    await request<unknown>(
      `/v1/strategic-map/teams/${encodeURIComponent(teamId)}`,
      { signal: options?.signal },
    ),
  );
  const team = mapTeam(raw.team || raw);
  const leader = raw.leader
    ? mapPerson(raw.leader, team.id)
    : (team.leader ?? null);
  const members = Array.isArray(raw.members)
    ? raw.members.map((item) => mapPerson(item, team.id))
    : (team.members ?? []);
  return {
    team,
    leader,
    leaders: Array.isArray(raw.leaders)
      ? raw.leaders.map((item) => mapPerson(item, team.id))
      : (team.leaders ?? (leader ? [leader] : [])),
    members,
    domain: raw.domain ? mapDomain(raw.domain) : undefined,
    subdomain: raw.subdomain
      ? (() => {
        const subdomain = record(raw.subdomain);
        return mapSubdomain(
          subdomain,
          text(subdomain.parentId || subdomain.parent_id),
        );
      })()
      : null,
  };
}

export async function startStrategicDomainRefresh(
  domainId: string,
  options?: { signal?: AbortSignal; subdomainId?: string },
): Promise<StrategicRefreshTask> {
  const params = new URLSearchParams();
  if (options?.subdomainId) params.set("subdomain_id", options.subdomainId);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return mapRefreshTask(
    await request<unknown>(
      `/v1/strategic-map/domains/${encodeURIComponent(domainId)}/refreshes${suffix}`,
      {
        method: "POST",
        signal: options?.signal,
      },
    ),
  );
}

export async function startStrategicNationwideRefresh(options?: {
  signal?: AbortSignal;
}): Promise<StrategicNationwideRefresh> {
  const raw = record(
    await request<unknown>("/v1/strategic-map/nationwide/refreshes", {
      method: "POST",
      signal: options?.signal,
    }),
  );
  const seeded = record(raw.seeded);
  return {
    seeded: {
      total: Number(seeded.total || 0),
      created: Number(seeded.created || 0),
      retainedVerified: Number(seeded.retainedVerified || 0),
      byDomain: record(seeded.byDomain) as Record<string, number>,
    },
    tasks: Array.isArray(raw.tasks) ? raw.tasks.map(mapRefreshTask) : [],
  };
}

export async function loadStrategicDomainRefresh(
  taskId: string,
  options?: { signal?: AbortSignal },
): Promise<StrategicRefreshTask> {
  return mapRefreshTask(
    await request<unknown>(
      `/v1/strategic-map/refreshes/${encodeURIComponent(taskId)}`,
      { signal: options?.signal },
    ),
  );
}

export async function loadLatestStrategicDomainRefresh(
  domainId: string,
  options?: { signal?: AbortSignal; subdomainId?: string },
): Promise<StrategicRefreshTask | null> {
  const params = new URLSearchParams();
  if (options?.subdomainId) params.set("subdomain_id", options.subdomainId);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const raw = record(
    await request<unknown>(
      `/v1/strategic-map/domains/${encodeURIComponent(domainId)}/refreshes/latest${suffix}`,
      { signal: options?.signal },
    ),
  );
  return raw.task ? mapRefreshTask(raw.task) : null;
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
> & { aiLevel?: string; scienceLevel?: string };

export async function updateStrategicTeam(
  id: string,
  details: Pick<StrategicTeamUpdate, "attention" | "contact"> & Partial<StrategicTeamUpdate>,
): Promise<StrategicTeam> {
  return mapTeam(
    await request<unknown>(
      `/v1/strategic-map/teams/${encodeURIComponent(id)}`,
      {
        method: "PUT",
        body: JSON.stringify({
          attention: details.attention.trim(),
          contact: details.contact.trim(),
          core_direction: details.coreDirection?.trim(),
          dual_judgement: details.dualJudgement?.trim(),
          ai_level: details.aiLevel,
          science_level: details.scienceLevel,
          contact_record: details.contactRecord?.trim(),
          internal_review: details.internalReview?.trim(),
          recent_update: details.recentUpdate?.trim(),
          next_action: details.nextAction?.trim(),
        }),
      },
    ),
  );
}

export async function createStrategicDomain(
  name: string,
  description: string,
): Promise<StrategicDomain> {
  const raw = record(
    await request<unknown>("/v1/strategic-map/domains", {
      method: "POST",
      body: payload(name, description),
    }),
  );
  return mapDomain(raw.domain || raw);
}

export async function updateStrategicDomain(
  id: string,
  name: string,
  description: string,
): Promise<StrategicDomain> {
  return mapDomain(
    await request<unknown>(
      `/v1/strategic-map/domains/${encodeURIComponent(id)}`,
      {
        method: "PUT",
        body: payload(name, description),
      },
    ),
  );
}

export async function deleteStrategicDomain(id: string): Promise<void> {
  await request(`/v1/strategic-map/domains/${encodeURIComponent(id)}`, {method: "DELETE",});
}

export async function createStrategicSubdomain(
  parentId: string,
  name: string,
  description: string,
): Promise<StrategicSubdomain> {
  return mapSubdomain(
    await request<unknown>(
      `/v1/strategic-map/domains/${encodeURIComponent(parentId)}/subdomains`,
      {
        method: "POST",
        body: payload(name, description),
      },
    ),
    parentId,
  );
}

export async function updateStrategicSubdomain(
  id: string,
  name: string,
  description: string,
  parentId: string,
): Promise<StrategicSubdomain> {
  return mapSubdomain(
    await request<unknown>(
      `/v1/strategic-map/subdomains/${encodeURIComponent(id)}`,
      {
        method: "PUT",
        body: payload(name, description),
      },
    ),
    parentId,
  );
}

export async function deleteStrategicSubdomain(id: string): Promise<void> {
  await request(`/v1/strategic-map/subdomains/${encodeURIComponent(id)}`, {method: "DELETE",});
}

function graphContextParams(context: {
  scope: StrategicGraphScope;
  cluster: StrategicGraphCluster;
  domainId?: string;
  subdomainId?: string;
}): URLSearchParams {
  const params = new URLSearchParams({
    scope: context.scope,
    cluster: context.cluster,
  });
  if (context.domainId) params.set("domain_id", context.domainId);
  if (context.subdomainId) params.set("subdomain_id", context.subdomainId);
  return params;
}

function mapGraphData(value: unknown): StrategicGraphData {
  const raw = record(value);
  const meta = record(raw.meta);
  const filter = record(meta.filter);
  return {
    nodes: Array.isArray(raw.nodes)
      ? (raw.nodes as StrategicGraphElement[]).filter((item) => item?.id)
      : [],
    edges: Array.isArray(raw.edges)
      ? (raw.edges as StrategicGraphElement[]).filter(
        (item) => item?.id && item?.source && item?.target,
      )
      : [],
    provider: text(raw.provider, "Hyper-Extract"),
    searchResults: Array.isArray(raw.searchResults)
      ? raw.searchResults
        .map((item) => {
          const result = record(item);
          return {
            id: text(result.id),
            label: text(result.label),
            level: text(result.level, "节点"),
            description: text(result.description),
            relevance: Number(result.relevance || 0),
            degree: Number(result.degree || 0),
            evidenceScore: Number(result.evidenceScore || 0),
            highlighted: Boolean(result.highlighted),
          };
        })
        .filter((item) => item.id)
      : [],
    meta: {
      type: text(meta.type),
      features: record(meta.features) as Record<string, boolean>,
      stats: record(meta.stats) as Record<string, number>,
      filter: Object.keys(filter).length
        ? {
          domain: text(filter.domain),
          subdomain: text(filter.subdomain),
          originalNodes: Number(filter.originalNodes || 0),
          filteredNodes: Number(filter.filteredNodes || 0),
        }
        : undefined,
    },
  };
}

export async function loadStrategicGraphStatus(options?: {
  signal?: AbortSignal;
}): Promise<StrategicGraphStatus> {
  const raw = record(
    await request<unknown>("/v1/strategic-map/graph/status", {
      signal: options?.signal,
    }),
  );
  return {
    available: Boolean(raw.available),
    hyperAvailable: Boolean(raw.hyperAvailable),
    provider: text(raw.provider),
    features: record(raw.features) as Record<string, boolean>,
    snapshotUpdatedAt: {
      domestic: text(record(raw.snapshotUpdatedAt).domestic),
      international: text(record(raw.snapshotUpdatedAt).international),
    },
  };
}

export async function loadStrategicGraph(
  context: {
    scope: StrategicGraphScope;
    cluster: StrategicGraphCluster;
    domainId?: string;
    subdomainId?: string;
  },
  options?: { signal?: AbortSignal },
): Promise<StrategicGraphData> {
  const params = graphContextParams(context);
  return mapGraphData(
    await request<unknown>(`/v1/strategic-map/graph/data?${params}`, {signal: options?.signal,}),
  );
}

export async function loadVerifiedStrategicGraph(
  context: Parameters<typeof loadStrategicGraph>[0],
  options?: { signal?: AbortSignal },
): Promise<StrategicGraphData> {
  return mapGraphData(await request<unknown>(
    `/v1/strategic-map/intelligence/verified-graph?${graphContextParams(context)}`,
    { signal: options?.signal },
  ));
}

export async function searchStrategicGraph(
  query: string,
  context: {
    scope: StrategicGraphScope;
    cluster: StrategicGraphCluster;
    domainId?: string;
    subdomainId?: string;
  },
  options?: { signal?: AbortSignal },
): Promise<StrategicGraphData> {
  return mapGraphData(
    await request<unknown>("/v1/strategic-map/graph/search", {
      method: "POST",
      signal: options?.signal,
      body: JSON.stringify({
        query: query.trim(),
        scope: context.scope,
        cluster: context.cluster,
        domain_id: context.domainId || null,
        subdomain_id: context.subdomainId || null,
      }),
    }),
  );
}

export async function chatWithStrategicGraph(
  query: string,
  context: {
    scope: StrategicGraphScope;
    cluster: StrategicGraphCluster;
    domainId?: string;
    subdomainId?: string;
  },
): Promise<{ response: string; data?: StrategicGraphData }> {
  const raw = record(
    await request<unknown>("/v1/strategic-map/graph/chat", {
      method: "POST",
      body: JSON.stringify({
        query: query.trim(),
        scope: context.scope,
        cluster: context.cluster,
        domain_id: context.domainId || null,
        subdomain_id: context.subdomainId || null,
      }),
    }),
  );
  return {
    response: text(raw.response),
    data: raw.data ? mapGraphData(raw.data) : undefined,
  };
}

function mapGraphScanTask(value: unknown): StrategicGraphScanTask {
  const raw = record(value);
  const progress = record(raw.progress);
  return {
    jobId: text(raw.jobId || raw.job_id),
    hyperJobId: text(raw.hyperJobId || raw.hyper_job_id),
    keyword: text(raw.keyword),
    scope: text(raw.scope, "domestic") as StrategicGraphScope,
    domainId: text(raw.domainId || raw.domain_id),
    subdomainId: text(raw.subdomainId || raw.subdomain_id),
    maxCandidates: Number(raw.maxCandidates || raw.max_candidates || 100),
    status: text(raw.status, "error") as StrategicGraphScanTask["status"],
    stage: text(raw.stage),
    progress: {
      done: Number(progress.done || 0),
      total: Number(progress.total || 0),
    },
    result: raw.result ? (raw.result as StrategicGraphScanResult) : null,
    error: raw.error ? text(raw.error) : null,
    persistent: Boolean(raw.persistent),
    createdAt: text(raw.createdAt || raw.created_at),
    updatedAt: text(raw.updatedAt || raw.updated_at),
    finishedAt: text(raw.finishedAt || raw.finished_at),
  };
}

export async function startStrategicGraphScan(
  keyword: string,
  context: {
    scope: StrategicGraphScope;
    domainId?: string;
    subdomainId?: string;
  },
  maxCandidates: number,
): Promise<StrategicGraphScanTask> {
  const raw = record(
    await request<unknown>("/v1/strategic-map/graph/scans", {
      method: "POST",
      body: JSON.stringify({
        keyword: keyword.trim(),
        scope: context.scope,
        max_candidates: maxCandidates,
        domain_id: context.domainId || null,
        subdomain_id: context.subdomainId || null,
      }),
    }),
  );
  return mapGraphScanTask(raw);
}

export async function loadStrategicGraphScan(
  jobId: string,
  options?: { signal?: AbortSignal },
): Promise<StrategicGraphScanTask> {
  return mapGraphScanTask(
    await request<unknown>(
      `/v1/strategic-map/graph/scans/${encodeURIComponent(jobId)}`,
      { signal: options?.signal },
    ),
  );
}

export async function loadStrategicGraphScans(
  context: {
    scope: StrategicGraphScope;
    domainId?: string;
    subdomainId?: string;
  },
  options?: { signal?: AbortSignal; limit?: number },
): Promise<StrategicGraphScanTask[]> {
  const params = new URLSearchParams({
    scope: context.scope,
    limit: String(options?.limit || 10),
  });
  if (context.domainId) params.set("domain_id", context.domainId);
  if (context.subdomainId) params.set("subdomain_id", context.subdomainId);
  const raw = await request<unknown>(
    `/v1/strategic-map/graph/scans?${params.toString()}`,
    { signal: options?.signal },
  );
  return Array.isArray(raw) ? raw.map(mapGraphScanTask) : [];
}
