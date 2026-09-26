import {
  attentionLabel,
  savedRosterPriority,
} from "./presentation";
import "./mobile.css";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type UIEvent,
} from "react";
import {
  ArrowRight,
  Atom,
  BrainCircuit,
  CalendarDays,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Database,
  Dna,
  Flag,
  FlaskConical,
  Globe2,
  LoaderCircle,
  Network,
  Orbit,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Plus,
  RefreshCcw,
  Save,
  ScanSearch,
  Trash2,
  UsersRound,
  X,
} from "lucide-react";
import { useLocation, useNavigate, useNavigationType } from "react-router-dom";

import {
  createStrategicSubdomain,
  deleteStrategicSubdomain,
  loadStrategicDomainRefresh,
  loadStrategicMap,
  loadStrategicTeamDetail,
  mapTeam,
  reviewedTeamPeople,
  startStrategicDomainRefresh,
  startStrategicNationwideRefresh,
  updateStrategicTeam,
  updateStrategicSubdomain,
  type StrategicDomain,
  type StrategicMapSource,
  type StrategicRefreshTask,
  type StrategicSubdomain,
  type StrategicTeam,
  type StrategicTeamUpdate,
} from "@/services/strategicMap";
import {
  buildStrategicMapPath,
  buildStrategicTeamDetailNavigationPath,
  EMPTY_STRATEGIC_MAP_SCROLL,
  readStrategicMapNavigationContext,
  strategicMapRouteForPathname,
  type StrategicMapNavigationContext,
  type StrategicMapScrollState,
  type StrategicMapWorkspaceMode,
} from "@/router/strategicMapNavigation";
import {
  resolveStrategicMapSelection,
  type StrategicMapSelection,
} from "./selection";
import FusionGraphWorkspace from "./FusionGraphWorkspace";
import RecommendationWorkspace from "./RecommendationWorkspace";
import TeamJudgementEditor, { capabilityLevelLabel, capabilitySourceLabel } from "./TeamJudgementEditor";
import ImpactTriage from "@/pages/ImpactTriage";
import { recommendationApi, type RecommendationRun } from "@/services/strategicRecommendations";

type AttentionLevel = string;
type ContactStatus = string;

type Team = StrategicTeam;

type DemoTeam = Omit<
  Team,
  | "domainId"
  | "subdomainId"
  | "source"
  | "sourceUrls"
  | "evidenceSummary"
  | "reportId"
  | "reportTitle"
  | "updatedAt"
>;

type Domain = StrategicDomain & { teams: Team[] };

type EditorState = {
  kind: "subdomain";
  id?: string;
  parentId?: string;
  name: string;
  description: string;
};

type TeamDetailsDraft = StrategicTeamUpdate;

const EMPTY_TEAM: Team = {
  id: "empty-team",
  domainId: "",
  subdomainId: null,
  name: "暂无候选团队",
  organization: "暂无候选团队",
  teamName: "暂无团队线索",
  focus: "",
  aiLevel: "待核实",
  scienceLevel: "待核实",
  attention: "待核实",
  contact: "未接触",
  coreDirection: "当前领域暂未形成可核验团队线索",
  dualJudgement: "AI 待核实｜科学 待核实",
  contactRecord: "暂无联系记录",
  internalReview: "请运行多源研判并补充公开证据",
  recentUpdate: "",
  nextAction: "点击“重新研判”获取最新候选线索",
  source: "多源公开证据",
  sourceUrls: [],
  evidenceSummary: "",
  reportId: "",
  reportTitle: "",
  updatedAt: "",
};

const DEMO_DOMAINS: (Omit<Domain, "subdomains" | "teams" | "name"> & {
  teams: DemoTeam[];
})[] = [
  {
    id: "science-foundation",
    label: "科学通用底座",
    description: "数据、计算、模型与自主科研基础设施",
    // Do not ship fabricated fallback candidates. The API is the source of
    // truth; until it returns an evidence-backed row the pool stays empty.
    teams: [],
  },
  {
    id: "general-ai",
    label: "通用 AI",
    description: "基础模型、智能体、系统与可信治理",
    teams: [],
  },
  {
    id: "high-energy-quantum",
    label: "高能物理与量子科技",
    description: "粒子、核物理、量子信息与精密测量",
    teams: [],
  },
  {
    id: "chemistry-materials",
    label: "化学与材料",
    description: "计算化学、材料发现与先进材料",
    teams: [],
  },
  {
    id: "life-medicine",
    label: "生命科学与医学",
    description: "生命机制、药物研发与临床医学",
    teams: [],
  },
  {
    id: "earth-science",
    label: "地球科学",
    description: "天气气候、地理空间、海洋与地质",
    teams: [],
  },
];

const DOMAINS: Domain[] = DEMO_DOMAINS.map((domain) => ({
  ...domain,
  name: domain.label,
  locked: true,
  subdomains: [],
  teams: domain.teams.map((team) => ({
    ...team,
    domainId: domain.id,
    subdomainId: null,
    source: "演示数据",
    sourceUrls: [],
    evidenceSummary: "",
    reportId: "",
    reportTitle: "",
    updatedAt: "",
  })),
}));

const attentionStyles: Record<AttentionLevel, string> = {
  重点关注: "border-[#e45d5d]/70 bg-[#fff2f2] text-[#d94747]",
  持续关注: "border-[#4f86bd]/70 bg-[#eff6ff] text-[#2f679f]",
  待核实: "border-[#e1a649]/70 bg-[#fff9eb] text-[#b87916]",
  一般关注: "border-[#b8bec8] bg-[#f5f6f8] text-[#6b7280]",
};

const contactStyles: Record<ContactStatus, string> = {
  已联系: "text-[#6f7885]",
  待拜访: "text-[#8b6a2c]",
  已交流: "text-[#6f7885]",
  未接触: "text-[#8b929d]",
  侧面了解: "text-[#6f7885]",
};

function AttentionBadge({ level }: { level: AttentionLevel }) {
  return (
    <span
      className={`inline-flex min-w-[104px] items-center justify-center rounded-full border px-3 py-1 text-[12px] font-semibold ${attentionStyles[level] ?? attentionStyles["待核实"]}`}
    >
      {attentionLabel(level)}
    </span>
  );
}

const EMPTY_DOMAIN: Domain = {
  id: "empty-domain",
  name: "暂无领域",
  label: "暂无领域",
  description: "请先新增一个研判领域",
  parentId: null,
  subdomains: [],
  teams: [],
};

function mergeSnapshotDomains(
  snapshotDomains: StrategicDomain[],
  snapshotTeams: StrategicTeam[],
): Domain[] {
  const teamsByDomain = new Map<string, Team[]>();
  for (const team of snapshotTeams) {
    const items = teamsByDomain.get(team.domainId) ?? [];
    items.push(team);
    teamsByDomain.set(team.domainId, items);
  }
  return snapshotDomains.map((domain) => ({
    ...domain,
    teams: teamsByDomain.get(domain.id) ?? [],
  }));
}

function abortableDelay(
  milliseconds: number,
  signal: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    let timer = 0;
    const abort = () => {
      window.clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    timer = window.setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", abort, { once: true });
  });
}

function isAbortError(reason: unknown): boolean {
  return reason instanceof DOMException && reason.name === "AbortError";
}

const candidateAttentionOrder: Record<string, number> = {
  重点关注: 0,
  持续关注: 1,
  待核实: 2,
  一般关注: 3,
};

function rankCandidateTeams(teams: Team[]): Team[] {
  return [...teams].sort((left, right) => {
    const attentionDelta =
      (candidateAttentionOrder[left.attention] ?? 99) -
      (candidateAttentionOrder[right.attention] ?? 99);
    if (attentionDelta !== 0) {
      return attentionDelta;
    }
    const scoreDelta = (right.scoreTotal ?? 0) - (left.scoreTotal ?? 0);
    if (scoreDelta !== 0) return scoreDelta;
    const rosterDelta = savedRosterPriority(right) - savedRosterPriority(left);
    if (rosterDelta !== 0) return rosterDelta;
    const updatedDelta = (right.updatedAt || "").localeCompare(
      left.updatedAt || "",
    );
    if (updatedDelta !== 0) {
      return updatedDelta;
    }
    return teamOrganization(left).localeCompare(
      teamOrganization(right),
      "zh-CN",
    );
  });
}

function teamOrganization(team: Team): string {
  const value = (team.organization?.trim() || team.name || "")
    .replace(/^\s*(?:\d{1,3}[.、)）:-]?|[①②③④⑤⑥⑦⑧⑨⑩❶❷❸❹❺❻❼❽❾❿])\s*/, "")
    .trim();
  if (
    !value ||
    /^(?:团队|机构|单位)\s*[A-Z0-9一二三四五六七八九十]*$/i.test(value)
  ) {
    return "机构名称未填写";
  }
  if (/^(?:国家级?)?实验室$/.test(value)) {
    return "实验室名称未填写";
  }
  return value;
}

function teamDisplayName(team: Team): string {
  const value = (team.teamName?.trim() || team.focus || "")
    .replace(/^\s*(?:\d{1,3}[.、)）:-]?|[①②③④⑤⑥⑦⑧⑨⑩❶❷❸❹❺❻❼❽❾❿])\s*/, "")
    .trim();
  if (
    !value ||
    /^(?:团队|机构|单位)\s*[A-Z0-9一二三四五六七八九十]*$/i.test(value) ||
    value === "相关团队线索"
  ) {
    return "团队名称未填写";
  }
  return value;
}

/** Keep the navigation scannable by giving each strategic area a small visual anchor. */
function DomainIcon({
  label,
  className = "size-4",
}: {
  label: string;
  className?: string;
}) {
  const Icon = label.includes("生命")
    ? Dna
    : label.includes("化学") || label.includes("材料")
      ? Atom
      : label.includes("通用 AI")
        ? BrainCircuit
        : label.includes("量子") || label.includes("高能")
          ? Orbit
          : label.includes("地球")
            ? Globe2
            : label.includes("实验")
              ? FlaskConical
              : Database;
  return <Icon className={className} aria-hidden="true" />;
}

async function loadVerifiedSnapshot(options?: { signal?: AbortSignal }) {
  const [snapshot, catalogue] = await Promise.all([
    loadStrategicMap(options), recommendationApi.verifiedTeams(options?.signal),
  ]);
  const verified = new Set(catalogue.teamIds);
  const published = catalogue.teams?.map(mapTeam) ?? snapshot.teams.filter((team) => verified.has(team.id));
  return { ...snapshot, teams: published.map((team) => ({
    ...team, leaders: reviewedTeamPeople(team.leaders), members: reviewedTeamPeople(team.members),
    leader: reviewedTeamPeople(team.leader ? [team.leader] : [])[0] || null,
  })) };
}

export default function StrategicMap() {
  const FUSION_ENABLED = import.meta.env.VITE_STRATEGIC_MAP_FUSION_ENABLED !== "false";
  const navigate = useNavigate();
  const location = useLocation();
  const initialContextRef = useRef<StrategicMapNavigationContext | undefined>(
    undefined,
  );
  if (initialContextRef.current === undefined) {
    initialContextRef.current = readStrategicMapNavigationContext(
      location.pathname,
      location.search,
    ) ?? {
      route: strategicMapRouteForPathname(location.pathname),
      domainId: "",
      subdomainId: "",
      teamId: "",
      scroll: { ...EMPTY_STRATEGIC_MAP_SCROLL },
    };
  }
  const initialContext = initialContextRef.current;
  const [domains, setDomains] = useState<Domain[]>(DOMAINS);
  const [activeDomainId, setActiveDomainId] = useState(
    initialContext.domainId || DOMAINS[0].id,
  );
  const [activeSubdomainId, setActiveSubdomainId] = useState(
    initialContext.subdomainId,
  );
  const [selectedTeamId, setSelectedTeamId] = useState(initialContext.teamId);
  const [source, setSource] = useState<StrategicMapSource>({
    provider: "多源公开证据",
  });
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [nationwideSyncing, setNationwideSyncing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [teamDetailsSaving, setTeamDetailsSaving] = useState(false);
  const [teamEditing, setTeamEditing] = useState(false);
  const [teamDetailsDraft, setTeamDetailsDraft] = useState<TeamDetailsDraft>({
    attention: "",
    contact: "",
    coreDirection: "",
    dualJudgement: "",
    aiLevel: "待核实",
    scienceLevel: "待核实",
    contactRecord: "",
    internalReview: "",
    recentUpdate: "",
    nextAction: "",
  });
  const [error, setError] = useState("");
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [leftNavCollapsed, setLeftNavCollapsed] = useState(false);
  // A domain/subdomain is a shared filter for both workspaces. Restoring a
  // filtered URL must not silently turn a team view into a graph view; only
  // the explicit header tabs are allowed to change the workspace mode.
  const [workspaceMode, setWorkspaceMode] = useState<StrategicMapWorkspaceMode>(
    (initialContext.mode && (FUSION_ENABLED || ["teams", "graph"].includes(initialContext.mode))
      ? initialContext.mode : undefined)
      || (FUSION_ENABLED && initialContext.route === "workspace" && !initialContext.domainId && !initialContext.subdomainId
        ? "recommend" : "teams"),
  );
  const navigationType = useNavigationType();
  const [taskDraft, setTaskDraft] = useState("");
  const [recommendationRun, setRecommendationRun] = useState<RecommendationRun | null>(null);
  const [recommendAcrossDomains, setRecommendAcrossDomains] = useState(
    initialContext.recommendAcrossDomains || !initialContext.domainId,
  );
  useEffect(() => {
    // URL sync uses replace navigation. Only browser Back/Forward may restore
    // a mode from the URL; a delayed replace must not undo a tab click.
    if (navigationType !== "POP") return;
    const requested = readStrategicMapNavigationContext(location.pathname, location.search)?.mode;
    if (requested && (FUSION_ENABLED || requested === "teams" || requested === "graph")) setWorkspaceMode(requested);
  }, [location.pathname, location.search, navigationType]);
  const [taxonomyRevision, setTaxonomyRevision] = useState(0);
  const [mobilePanel, setMobilePanel] = useState<"teams" | "profile">(
    initialContext.mobilePanel ?? "teams",
  );
  const [mobileManageOpen, setMobileManageOpen] = useState(false);
  const mobileListScrollRef = useRef(initialContext.mobileListScroll ?? 0);
  const pendingPanelScrollRef = useRef<number | null>(null);
  const selectionRef = useRef<StrategicMapSelection>({
    domainId: initialContext.domainId || DOMAINS[0].id,
    subdomainId: initialContext.subdomainId,
    teamId: initialContext.teamId,
  });
  const domainsRef = useRef(domains);
  const refreshRequestIdRef = useRef(0);
  const refreshAbortRef = useRef<AbortController | null>(null);
  const scrollFrameRef = useRef<number | null>(null);
  const leavingMapRef = useRef(false);
  const scrollStateRef = useRef<StrategicMapScrollState>({
    ...initialContext.scroll,
  });
  const pendingScrollRestoreRef = useRef<StrategicMapScrollState | null>(
    readStrategicMapNavigationContext(location.pathname, location.search)
      ? { ...initialContext.scroll }
      : null,
  );
  const pageScrollRef = useRef<HTMLDivElement>(null);
  const domainScrollRef = useRef<HTMLDivElement>(null);
  const subdomainScrollRef = useRef<HTMLDivElement>(null);
  const teamScrollRef = useRef<HTMLDivElement>(null);
  const profileScrollRef = useRef<HTMLDivElement>(null);

  const applySelection = useCallback((selection: StrategicMapSelection) => {
    selectionRef.current = selection;
    setActiveDomainId(selection.domainId);
    setActiveSubdomainId(selection.subdomainId);
    setSelectedTeamId(selection.teamId);
  }, []);

  useEffect(() => {
    domainsRef.current = domains;
  }, [domains]);

  const activeDomain =
    domains.find((domain) => domain.id === activeDomainId) ??
    domains[0] ??
    EMPTY_DOMAIN;
  const scopedTeams = rankCandidateTeams(
    activeSubdomainId
      ? activeDomain.teams.filter(
          (team) => team.subdomainId === activeSubdomainId,
        )
      : activeDomain.teams,
  );
  const visibleTeams = scopedTeams;
  const selectedTeam =
    visibleTeams.find((team) => team.id === selectedTeamId) ??
    visibleTeams[0] ?? EMPTY_TEAM;
  const activeSubdomain = activeDomain.subdomains.find(
    (subdomain) => subdomain.id === activeSubdomainId,
  );

  useEffect(() => {
    setTeamDetailsDraft({
      attention: selectedTeam.attention,
      contact: selectedTeam.contact,
      coreDirection: selectedTeam.coreDirection,
      dualJudgement: selectedTeam.dualJudgement,
      aiLevel: selectedTeam.aiLevel,
      scienceLevel: selectedTeam.scienceLevel,
      contactRecord: selectedTeam.contactRecord,
      internalReview: selectedTeam.internalReview,
      recentUpdate: selectedTeam.recentUpdate,
      nextAction: selectedTeam.nextAction,
    });
    setTeamEditing(false);
  }, [
    selectedTeam.id,
    selectedTeam.attention,
    selectedTeam.contact,
    selectedTeam.coreDirection,
    selectedTeam.dualJudgement,
    selectedTeam.aiLevel,
    selectedTeam.scienceLevel,
    selectedTeam.contactRecord,
    selectedTeam.internalReview,
    selectedTeam.recentUpdate,
    selectedTeam.nextAction,
  ]);

  const priorityCount = useMemo(
    () => visibleTeams.filter((team) => team.attention === "重点关注").length,
    [visibleTeams],
  );

  const captureScrollState = useCallback(
    (): StrategicMapScrollState => ({
      page: pageScrollRef.current?.scrollTop ?? scrollStateRef.current.page,
      domains:
        domainScrollRef.current?.scrollTop ?? scrollStateRef.current.domains,
      subdomains:
        subdomainScrollRef.current?.scrollTop ??
        scrollStateRef.current.subdomains,
      teams: teamScrollRef.current?.scrollTop ?? scrollStateRef.current.teams,
      profile:
        profileScrollRef.current?.scrollTop ?? scrollStateRef.current.profile,
    }),
    [],
  );

  const currentNavigationContext = useCallback(
    (teamId = selectionRef.current.teamId) => ({
      route: initialContext.route,
      mode: workspaceMode,
      recommendAcrossDomains: (workspaceMode === "recommend" || workspaceMode === "intelligence") && recommendAcrossDomains,
      ...selectionRef.current,
      teamId,
      scroll: captureScrollState(),
      ...(mobilePanel === "profile" ? { mobilePanel: "profile" as const } : {}),
      ...(mobileListScrollRef.current
        ? { mobileListScroll: mobileListScrollRef.current }
        : {}),
    }),
    [captureScrollState, initialContext.route, mobilePanel, workspaceMode, recommendAcrossDomains],
  );

  const switchMobilePanel = (panel: "teams" | "profile", reset = false) => {
    if (scrollFrameRef.current != null) {
      window.cancelAnimationFrame(scrollFrameRef.current);
      scrollFrameRef.current = null;
    }
    const page = pageScrollRef.current;
    if (page && page.clientWidth <= 1100) {
      if (mobilePanel === "teams") mobileListScrollRef.current = page.scrollTop;
      if (reset) mobileListScrollRef.current = 0;
      const offset = panel === "teams" ? mobileListScrollRef.current : 0;
      if (panel === mobilePanel) page.scrollTop = offset;
      else pendingPanelScrollRef.current = offset;
    }
    setMobilePanel(panel);
  };

  useLayoutEffect(() => {
    if (
      !loading &&
      pendingPanelScrollRef.current !== null &&
      pageScrollRef.current
    ) {
      pageScrollRef.current.scrollTop = pendingPanelScrollRef.current;
      pendingPanelScrollRef.current = null;
      pendingScrollRestoreRef.current = null;
    }
  }, [loading, mobilePanel]);

  const replaceCurrentMapUrl = useCallback(() => {
    if (typeof window === "undefined" || leavingMapRef.current) return;
    const path = buildStrategicMapPath(currentNavigationContext());
    const currentPath = `${window.location.pathname}${window.location.search}`;
    if (path !== currentPath) {
      navigate(path, {
        replace: true,
        preventScrollReset: true,
      });
    }
  }, [currentNavigationContext, navigate]);

  const queueScrollUrlSync = useCallback(() => {
    if (typeof window === "undefined") return;
    if (scrollFrameRef.current != null)
      window.cancelAnimationFrame(scrollFrameRef.current);
    scrollFrameRef.current = window.requestAnimationFrame(() => {
      scrollFrameRef.current = null;
      replaceCurrentMapUrl();
    });
  }, [replaceCurrentMapUrl]);

  const recordScroll = useCallback(
    (key: keyof StrategicMapScrollState, event: UIEvent<HTMLDivElement>) => {
      scrollStateRef.current = {
        ...scrollStateRef.current,
        [key]: event.currentTarget.scrollTop,
      };
      queueScrollUrlSync();
    },
    [queueScrollUrlSync],
  );

  const openTeamDetail = useCallback(
    (teamId: string) => {
      if (!teamId || teamId === EMPTY_TEAM.id) return;
      // A touch scroll can queue a URL update immediately before this click.
      // Never let that pending update replace the detail route we are opening.
      leavingMapRef.current = true;
      if (scrollFrameRef.current != null) {
        window.cancelAnimationFrame(scrollFrameRef.current);
        scrollFrameRef.current = null;
      }
      const context = currentNavigationContext(teamId);
      const returnPath = buildStrategicMapPath(context);
      if (typeof window !== "undefined") {
        // Make browser Back restore the same context too, not just the in-page back button.
        window.history.replaceState(window.history.state, "", returnPath);
      }
      navigate(buildStrategicTeamDetailNavigationPath(teamId, context));
    },
    [currentNavigationContext, navigate],
  );

  useEffect(() => {
    if (!loading) replaceCurrentMapUrl();
  }, [
    activeDomainId,
    activeSubdomainId,
    loading,
    replaceCurrentMapUrl,
    selectedTeamId,
  ]);

  useLayoutEffect(() => {
    if (loading || !pendingScrollRestoreRef.current) return;
    const scroll = pendingScrollRestoreRef.current;
    pendingScrollRestoreRef.current = null;
    const entries: [HTMLDivElement | null, number][] = [
      [pageScrollRef.current, scroll.page],
      [domainScrollRef.current, scroll.domains],
      [subdomainScrollRef.current, scroll.subdomains],
      [teamScrollRef.current, scroll.teams],
      [profileScrollRef.current, scroll.profile],
    ];
    for (const [element, offset] of entries) {
      if (element) element.scrollTop = offset;
    }
    scrollStateRef.current = captureScrollState();
  }, [activeDomainId, activeSubdomainId, captureScrollState, domains, loading]);

  useEffect(
    () => () => {
      if (scrollFrameRef.current != null && typeof window !== "undefined") {
        window.cancelAnimationFrame(scrollFrameRef.current);
      }
      refreshAbortRef.current?.abort();
    },
    [],
  );

  useEffect(() => {
    let disposed = false;
    loadVerifiedSnapshot()
      .then((snapshot) => {
        if (disposed) return;
        const nextDomains = mergeSnapshotDomains(
          snapshot.domains,
          snapshot.teams,
        );
        if (nextDomains.length) {
          domainsRef.current = nextDomains;
          setDomains(nextDomains);
        }
        const requested = selectionRef.current;
        applySelection(
          resolveStrategicMapSelection(
            nextDomains.length ? nextDomains : domainsRef.current,
            requested,
            !requested.teamId,
          ),
        );
        setSource(snapshot.source);
        setError("");
      })
      .catch((reason) => {
        if (!disposed) {
          const requested = selectionRef.current;
          applySelection(
            resolveStrategicMapSelection(
              domainsRef.current,
              requested,
              !requested.teamId,
            ),
          );
          setError(
            reason instanceof Error
              ? reason.message
              : "读取战略图谱失败，当前显示本地缓存",
          );
        }
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [applySelection]);

  const selectDomain = (domain: Domain) => {
    setRecommendAcrossDomains(false);
    switchMobilePanel("teams", true);
    const poolTeams = domain.teams;
    applySelection({
      domainId: domain.id,
      subdomainId: "",
      teamId: rankCandidateTeams(poolTeams)[0]?.id ?? "",
    });
  };

  const selectSubdomain = (subdomainId: string) => {
    setRecommendAcrossDomains(false);
    switchMobilePanel("teams", true);
    applySelection({
      domainId: activeDomain.id,
      subdomainId,
      teamId:
        rankCandidateTeams(
          activeDomain.teams.filter(
            (team) =>
              (!subdomainId || team.subdomainId === subdomainId),
          ),
        )[0]?.id ?? "",
    });
  };

  const reloadRefreshResult = useCallback(
    async (
      refreshedDomainId: string,
      requestId: number,
      signal: AbortSignal,
    ) => {
      const requested = selectionRef.current;
      const snapshot = await loadVerifiedSnapshot({ signal });
      if (requestId !== refreshRequestIdRef.current || signal.aborted) return;
      let nextDomains = mergeSnapshotDomains(snapshot.domains, snapshot.teams);

      // The selected profile endpoint is read independently so the list, leader
      // and member detail all advance to the same persisted revision.
      if (
        requested.teamId &&
        snapshot.teams.some((team) => team.id === requested.teamId)
      ) {
        const detail = await loadStrategicTeamDetail(requested.teamId, {
          signal,
        });
        if (requestId !== refreshRequestIdRef.current || signal.aborted) return;
        nextDomains = nextDomains.map((domain) => ({
          ...domain,
          teams: domain.teams.map((team) =>
            team.id === detail.team.id
              ? {
                  ...team,
                  leader: reviewedTeamPeople(detail.leader ? [detail.leader] : [])[0] || null,
                  leaders: reviewedTeamPeople(detail.leaders),
                  members: reviewedTeamPeople(detail.members),
                }
              : team,
          ),
        }));
      }

      if (requestId !== refreshRequestIdRef.current || signal.aborted) return;
      domainsRef.current = nextDomains;
      setDomains(nextDomains);
      applySelection(
        resolveStrategicMapSelection(nextDomains, requested, !requested.teamId),
      );
      if (selectionRef.current.domainId === refreshedDomainId) {
        setSource({
          ...snapshot.source,
          refreshed: true,
        });
      }
    },
    [applySelection],
  );

  const refreshMapAfterGraph = useCallback(async () => {
    const snapshot = await loadVerifiedSnapshot();
    const nextDomains = mergeSnapshotDomains(snapshot.domains, snapshot.teams);
    domainsRef.current = nextDomains;
    setDomains(nextDomains);
    const requested = selectionRef.current;
    applySelection(
      resolveStrategicMapSelection(nextDomains, requested, !requested.teamId),
    );
    setSource(snapshot.source);
  }, [applySelection]);

  const trackRefreshTask = useCallback(
    async (
      initialTask: StrategicRefreshTask,
      requestId: number,
      signal: AbortSignal,
    ) => {
      let task = initialTask;
      let connectionFailures = 0;
      setSyncing(!task.terminal);
      try {
        while (!task.terminal) {
          await abortableDelay(
            task.state === "accepted" ? 1_000 : 2_000,
            signal,
          );
          try {
            task = await loadStrategicDomainRefresh(task.taskId, { signal });
            connectionFailures = 0;
            if (requestId === refreshRequestIdRef.current) setError("");
          } catch (reason) {
            if (isAbortError(reason) || signal.aborted) throw reason;
            connectionFailures += 1;
            if (requestId === refreshRequestIdRef.current) {
              setError(
                `与服务的连接中断，正在恢复任务状态（第 ${connectionFailures} 次重试）`,
              );
            }
            await abortableDelay(
              Math.min(10_000, 1_000 * connectionFailures),
              signal,
            );
          }
        }
        if (requestId !== refreshRequestIdRef.current || signal.aborted) return;
        try {
          await reloadRefreshResult(task.domainId, requestId, signal);
        } catch (reason) {
          if (isAbortError(reason) || signal.aborted) throw reason;
          setError(
            `任务已结束，但读取最新结果失败：${reason instanceof Error ? reason.message : "未知错误"}`,
          );
          return;
        }
        if (task.state === "succeeded") {
          setError("");
        } else {
          setError(
            task.message ||
              (
                {
                  partial: "本轮仅部分完成，已显示成功保存的结果",
                  failed: "本轮调查失败，已保留此前结果",
                  cancelled: "本轮调查已取消",
                  timed_out: "本轮调查超时，已显示成功保存的结果",
                } as Record<string, string>
              )[task.state] ||
              "本轮调查未完整完成",
          );
        }
      } finally {
        if (requestId === refreshRequestIdRef.current) setSyncing(false);
      }
    },
    [reloadRefreshResult],
  );

  const refreshTeams = async () => {
    if (!activeDomain.id || activeDomain.id === EMPTY_DOMAIN.id || syncing)
      return;
    refreshAbortRef.current?.abort();
    const controller = new AbortController();
    refreshAbortRef.current = controller;
    const requestId = ++refreshRequestIdRef.current;
    const refreshedDomainId = activeDomain.id;
    setSyncing(true);
    setError("");
    try {
      const task = await startStrategicDomainRefresh(refreshedDomainId, {
        signal: controller.signal,
        subdomainId: activeSubdomainId || undefined,
      });
      await trackRefreshTask(task, requestId, controller.signal);
    } catch (reason) {
      if (!isAbortError(reason) && requestId === refreshRequestIdRef.current) {
        setSyncing(false);
        setError(
          reason instanceof Error ? reason.message : "重新研判任务提交失败",
        );
      }
    }
  };

  const refreshNationwideTeams = async () => {
    if (nationwideSyncing || syncing) return;
    setNationwideSyncing(true);
    setError("");
    try {
      const result = await startStrategicNationwideRefresh();
      const snapshot = await loadVerifiedSnapshot();
      const nextDomains = mergeSnapshotDomains(
        snapshot.domains,
        snapshot.teams,
      );
      domainsRef.current = nextDomains;
      setDomains(nextDomains);
      applySelection(
        resolveStrategicMapSelection(
          nextDomains,
          selectionRef.current,
          !selectionRef.current.teamId,
        ),
      );
      setSource({
        ...snapshot.source,
        provider: `全国图谱扫描：已载入 ${result.seeded.total} 支官方团队候选，六大领域后台核验已启动`,
      });
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "全国团队扫描启动失败",
      );
    } finally {
      setNationwideSyncing(false);
    }
  };

  const saveTeamDetails = async () => {
    if (
      !selectedTeam.id ||
      selectedTeam.id === EMPTY_TEAM.id ||
      teamDetailsSaving
    )
      return;
    setTeamDetailsSaving(true);
    setError("");
    try {
      // Only submit edited text: the public catalogue may show a concise
      // evidence projection instead of the original manually saved field.
      const changes: Pick<StrategicTeamUpdate, "attention" | "contact"> & Partial<StrategicTeamUpdate> = {
        attention: teamDetailsDraft.attention,
        contact: teamDetailsDraft.contact,
      };
      for (const field of ["coreDirection", "contactRecord", "internalReview", "recentUpdate", "nextAction"] as const) {
        if (teamDetailsDraft[field] !== selectedTeam[field]) changes[field] = teamDetailsDraft[field];
      }
      for (const field of ["aiLevel", "scienceLevel"] as const) {
        if (teamDetailsDraft[field] !== undefined && teamDetailsDraft[field] !== selectedTeam[field]) {
          changes[field] = teamDetailsDraft[field];
        }
      }
      const next = await updateStrategicTeam(selectedTeam.id, changes);
      setDomains((current) =>
        current.map((domain) => ({
          ...domain,
          teams: domain.teams.map((team) =>
            team.id === next.id ? {
              ...team,
              attention: next.attention, contact: next.contact,
              aiLevel: next.aiLevel, scienceLevel: next.scienceLevel,
              capabilityAssessments: next.capabilityAssessments,
              dualJudgement: next.dualJudgement,
              coreDirection: changes.coreDirection === undefined ? team.coreDirection : next.coreDirection,
              contactRecord: next.contactRecord, internalReview: next.internalReview,
              recentUpdate: next.recentUpdate, nextAction: next.nextAction, updatedAt: next.updatedAt,
            } : team,
          ),
        })),
      );
      setTeamDetailsDraft({
        attention: next.attention,
        contact: next.contact,
        coreDirection: next.coreDirection,
        dualJudgement: next.dualJudgement,
        aiLevel: next.aiLevel,
        scienceLevel: next.scienceLevel,
        contactRecord: next.contactRecord,
        internalReview: next.internalReview,
        recentUpdate: next.recentUpdate,
        nextAction: next.nextAction,
      });
      setTeamEditing(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存团队详情失败");
    } finally {
      setTeamDetailsSaving(false);
    }
  };

  const startSubdomainEditor = (subdomain?: StrategicSubdomain) => {
    if (loading || activeDomain.id === EMPTY_DOMAIN.id) return;
    setEditor({
      kind: "subdomain",
      id: subdomain?.id,
      parentId: activeDomain.id,
      name: subdomain?.name ?? "",
      description: subdomain?.description ?? "",
    });
    setError("");
  };

  const saveEditor = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!editor || !editor.name.trim() || saving) return;
    setSaving(true);
    setError("");
    try {
      if (editor.parentId) {
        const isNewSubdomain = !editor.id;
        const next = editor.id
          ? await updateStrategicSubdomain(
              editor.id,
              editor.name,
              editor.description,
              editor.parentId,
            )
          : await createStrategicSubdomain(
              editor.parentId,
              editor.name,
              editor.description,
            );
        setDomains((current) =>
          current.map((domain) => {
            if (domain.id !== editor.parentId) return domain;
            const subdomains = editor.id
              ? domain.subdomains.map((item) =>
                  item.id === next.id ? next : item,
                )
              : [...domain.subdomains, next];
            return {
              ...domain,
              subdomains,
            };
          }),
        );
        setTaxonomyRevision((revision) => revision + 1);
        if (isNewSubdomain) {
          applySelection({
            domainId: editor.parentId,
            subdomainId: next.id,
            teamId: "",
          });
        }
      }
      setEditor(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const removeSubdomain = async (subdomain: StrategicSubdomain) => {
    if (
      typeof window !== "undefined" &&
      !window.confirm(
        `确定删除子领域“${subdomain.name}”吗？该子领域下的团队会保留在所属大领域，图谱中的子领域节点会移除。`,
      )
    )
      return;
    try {
      await deleteStrategicSubdomain(subdomain.id);
      setDomains((current) =>
        current.map((domain) =>
          domain.id === activeDomain.id
            ? {
                ...domain,
                subdomains: domain.subdomains.filter(
                  (item) => item.id !== subdomain.id,
                ),
              }
            : domain,
        ),
      );
      setTaxonomyRevision((revision) => revision + 1);
      if (activeSubdomainId === subdomain.id) {
        applySelection({
          domainId: activeDomain.id,
          subdomainId: "",
          teamId: selectedTeamId,
        });
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除子领域失败");
    }
  };

  return (
    <div className="strategic-map flex h-full min-h-0 w-full min-w-0 flex-col overflow-x-hidden overflow-y-hidden bg-[#fafafa] text-slate-900">
      <header className="strategic-map-header shrink-0 border-b border-slate-200 bg-white px-4 py-3 text-slate-900 sm:px-5 sm:py-4 md:px-7">
        <div className="mx-auto flex w-full max-w-[1600px] flex-wrap items-center gap-x-8 gap-y-2 sm:gap-x-16 sm:gap-y-3">
          <h1 className="shrink-0 text-[20px] font-semibold tracking-[0.02em] sm:text-[22px] md:text-[25px]">
            AI4S战略力量图谱
          </h1>
          <p className="order-3 w-full text-[13px] font-medium tracking-[0.02em] text-slate-400 sm:order-none sm:w-auto sm:min-w-[250px] sm:flex-1 sm:text-[15px] md:text-[17px]">
            {loading ? "以成果为依据，发现国内科研力量" : `${domains.reduce((total, domain) => total + domain.teams.length, 0)} 支已核实团队 · ${domains.length} 个研究领域`}
          </p>
          <div className="strategic-map-view-tabs flex max-w-full shrink-0 items-center overflow-x-auto rounded-xl border border-slate-200 bg-slate-50 p-1">
            {FUSION_ENABLED && <button type="button" aria-pressed={workspaceMode === "recommend"} onClick={() => setWorkspaceMode("recommend")}
              className={`inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg px-3 text-xs font-semibold ${workspaceMode === "recommend" ? "bg-white text-blue-700 shadow-sm" : "text-slate-500"}`}>
              <ScanSearch className="size-4" />任务推荐
            </button>}
            <button
              type="button"
              aria-pressed={workspaceMode === "teams"}
              onClick={() => setWorkspaceMode("teams")}
              className={`inline-flex h-8 shrink-0 items-center gap-1.5 rounded px-3 text-xs font-semibold ${
                workspaceMode === "teams"
                  ? "bg-white text-blue-700 shadow-sm"
                  : "text-slate-500"
              }`}
            >
              <UsersRound className="size-4" />
              团队
            </button>
            <button
              type="button"
              aria-pressed={workspaceMode === "graph"}
              onClick={() => setWorkspaceMode("graph")}
              className={`inline-flex h-8 shrink-0 items-center gap-1.5 rounded px-3 text-xs font-semibold ${
                workspaceMode === "graph"
                  ? "bg-white text-blue-700 shadow-sm"
                  : "text-slate-500"
              }`}
            >
              <Network className="size-4" />
              关系图谱
            </button>
            {FUSION_ENABLED && <button type="button" aria-pressed={workspaceMode === "intelligence"} onClick={() => setWorkspaceMode("intelligence")}
              className={`inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg px-3 text-xs font-semibold ${workspaceMode === "intelligence" ? "bg-white text-blue-700 shadow-sm" : "text-slate-500"}`}>
              <CalendarDays className="size-4" />动态情报
            </button>}
          </div>
        </div>
      </header>

      <div
        ref={pageScrollRef}
        onScroll={(event) => recordScroll("page", event)}
        className="strategic-map-page mx-auto flex min-h-0 w-full max-w-[1600px] min-w-0 flex-1 flex-col gap-3 overflow-y-auto p-3 sm:gap-4 sm:p-4 md:p-5 lg:overflow-hidden lg:flex-row"
      >
        <section
          className="strategic-map-mobile-navigation"
          aria-label="领域筛选"
        >
          <div className="flex items-center justify-between gap-2 text-sm font-semibold text-[#0f172a]">
            <span>领域筛选</span>
            <button
              type="button"
              onClick={() => setMobileManageOpen((open) => !open)}
              aria-expanded={mobileManageOpen}
              aria-controls="mobile-domain-management"
              className="px-2 text-xs text-[#236ca8]"
            >
              {mobileManageOpen ? "收起管理" : "管理领域"}
            </button>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <label className="min-w-0 text-xs text-[#607486]">
              领域
              <select
                aria-label="选择领域"
                value={(workspaceMode === "recommend" || workspaceMode === "intelligence") && recommendAcrossDomains ? "" : activeDomain.id}
                onChange={(event) => {
                  if (event.target.value === "" && (workspaceMode === "recommend" || workspaceMode === "intelligence")) { setRecommendAcrossDomains(true); return; }
                  const domain = domains.find(
                    (item) => item.id === event.target.value,
                  );
                  if (domain) selectDomain(domain);
                }}
                className="mt-1 w-full min-w-0 rounded-lg border border-[#ccd9e4] bg-white px-2 text-base text-[#274158]"
              >
                {(workspaceMode === "recommend" || workspaceMode === "intelligence") && <option value="">全部六大领域</option>}
                {domains.map((domain) => (
                  <option key={domain.id} value={domain.id}>
                    {domain.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="min-w-0 text-xs text-[#607486]">
              子领域
              <select
                aria-label="选择子领域"
                value={activeSubdomainId}
                disabled={(workspaceMode === "recommend" || workspaceMode === "intelligence") && recommendAcrossDomains}
                onChange={(event) => selectSubdomain(event.target.value)}
                className="mt-1 w-full min-w-0 rounded-lg border border-[#ccd9e4] bg-white px-2 text-base text-[#274158]"
              >
                <option value="">全部子领域</option>
                {activeDomain.subdomains.map((subdomain) => (
                  <option key={subdomain.id} value={subdomain.id}>
                    {subdomain.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {mobileManageOpen ? (
            <div
              id="mobile-domain-management"
              className="mt-3 border-t border-[#e6edf4] pt-2 text-xs text-[#236ca8]"
            >
              <div className="flex flex-wrap gap-x-4">
                <span className="text-[#607486]">六大根领域已锁定</span>
              </div>
              <div className="flex flex-wrap gap-x-4">
                <button
                  type="button"
                  onClick={() => startSubdomainEditor()}
                  disabled={loading || activeDomain.id === EMPTY_DOMAIN.id}
                >
                  新增子领域
                </button>
                {activeDomain.subdomains
                  .filter((item) => item.id === activeSubdomainId)
                  .map((subdomain) => (
                    <span key={subdomain.id} className="flex gap-4">
                      <button
                        type="button"
                        onClick={() => startSubdomainEditor(subdomain)}
                      >
                        编辑子领域
                      </button>
                      <button
                        type="button"
                        onClick={() => void removeSubdomain(subdomain)}
                        className="text-red-600"
                      >
                        删除子领域
                      </button>
                    </span>
                  ))}
              </div>
            </div>
          ) : null}
        </section>
        {workspaceMode === "teams" ? (
          <nav className="strategic-map-mobile-tabs" aria-label="团队视图切换">
            <button
              type="button"
              aria-pressed={mobilePanel === "teams"}
              aria-controls="strategic-team-list"
              onClick={() => switchMobilePanel("teams")}
            >
              团队列表（{visibleTeams.length}）
            </button>
            <button
              type="button"
              aria-pressed={mobilePanel === "profile"}
              aria-controls="strategic-team-profile"
              disabled={selectedTeam.id === EMPTY_TEAM.id}
              onClick={() => switchMobilePanel("profile")}
            >
              团队画像
            </button>
          </nav>
        ) : null}
        <aside
          data-collapsed={leftNavCollapsed}
          className={`strategic-map-domain-nav flex min-h-0 w-full shrink-0 flex-col rounded-xl border border-[#e2e8f0] bg-white shadow-[0_4px_14px_rgba(27,64,96,0.05)] transition-[width,height,padding] duration-200 lg:h-full ${
            leftNavCollapsed
              ? "h-[58px] p-2 sm:h-[58px] lg:w-[58px]"
              : "h-[260px] p-3.5 sm:h-[290px] lg:w-[276px]"
          }`}
          aria-label="战略领域导航"
        >
          <div
            className={`flex items-center gap-2 pb-4 ${leftNavCollapsed ? "justify-center px-0" : "justify-between px-2"}`}
          >
            {leftNavCollapsed ? null : (
              <div className="min-w-0">
                <div className="truncate text-[19px] font-semibold leading-7 text-[#164f70]">
                  输入领域 / 子领域
                </div>
                <p className="mt-0.5 truncate text-[13px] text-[#6d7d8b]">
                  点击切换研判对象
                </p>
              </div>
            )}
            {!leftNavCollapsed ? (
              <div className="flex shrink-0 items-center gap-0.5">
                <button
                  type="button"
                  onClick={() => setLeftNavCollapsed(true)}
                  className="rounded-lg p-1.5 text-[#607486] hover:bg-[#edf6fb]"
                  title="收起领域导航"
                  aria-label="收起领域导航"
                >
                  <PanelLeftClose className="size-4" />
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setLeftNavCollapsed(false)}
                className="rounded-lg p-1.5 text-[#236ca8] hover:bg-[#edf6fb]"
                title="展开领域导航"
                aria-label="展开领域导航"
              >
                <PanelLeftOpen className="size-4" />
              </button>
            )}
          </div>
          <div
            className={
              leftNavCollapsed
                ? "hidden"
                : "flex min-h-0 flex-1 flex-col px-1 pr-1"
            }
          >
          <div
            ref={domainScrollRef}
              onScroll={(event) => recordScroll("domains", event)}
              className="min-h-0 h-[72%] shrink-0 overflow-y-auto pr-1"
          >
              {(workspaceMode === "recommend" || workspaceMode === "intelligence") && <button type="button" onClick={() => setRecommendAcrossDomains(true)}
                aria-pressed={recommendAcrossDomains}
                className={`mb-3 w-full rounded-xl border px-5 py-3 text-left text-sm font-semibold ${recommendAcrossDomains ? "border-blue-200 bg-blue-50 text-blue-700" : "border-slate-200 bg-white text-slate-600"}`}>
                全部六大领域
              </button>}
              {domains.map((domain) => {
                const active = domain.id === activeDomain.id && !((workspaceMode === "recommend" || workspaceMode === "intelligence") && recommendAcrossDomains);
                return (
                  <div
                    key={domain.id}
                    className={`group relative mb-3 flex min-h-[72px] w-full items-center rounded-xl border transition ${
                      active
                        ? "border-blue-200 bg-blue-50 text-blue-700"
                        : "border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:bg-slate-50"
                    }`}
                  >
                    <button
                      type="button"
                      onClick={() => selectDomain(domain)}
                      aria-pressed={active}
                      className="flex min-h-[72px] min-w-0 flex-1 items-center gap-3 px-5 py-3 text-left"
                    >
                      <DomainIcon
                        label={domain.label}
                        className="size-5 shrink-0"
                      />
                      <span className="whitespace-normal text-[16px] font-semibold leading-6">
                        {domain.label}
                      </span>
                    </button>
                  </div>
                );
              })}
            </div>
            <div className="flex min-h-0 h-[28%] shrink-0 flex-col border-t border-[#e4eaf1] pt-3">
              <div className="flex shrink-0 items-center justify-between px-2 text-[13px] font-semibold text-[#5e7484]">
                <span>子领域</span>
                <button
                  type="button"
                  onClick={() => startSubdomainEditor()}
                  disabled={loading || activeDomain.id === EMPTY_DOMAIN.id}
                  className="rounded-lg p-1 text-[#236ca8] hover:bg-[#edf6fb]"
                  title="新增子领域"
                  aria-label="新增子领域"
                >
                  <Plus className="size-4" />
                </button>
              </div>
              <div
                ref={subdomainScrollRef}
                onScroll={(event) => recordScroll("subdomains", event)}
                className="min-h-0 flex-1 overflow-y-auto pr-1"
              >
                <button
                  type="button"
                  onClick={() => selectSubdomain("")}
                  className={`mt-2 flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-[13px] ${!activeSubdomainId ? "bg-[#eef6fb] font-semibold text-[#236ca8]" : "text-[#607486] hover:bg-[#f5f8fb]"}`}
                >
                  <DomainIcon
                    label={activeDomain.label}
                    className="size-3.5 shrink-0"
                  />
                  <span>全部子领域</span>
                </button>
                {activeDomain.subdomains.map((subdomain) => (
                  <div
                    key={subdomain.id}
                    className="group/sub relative mt-1 flex items-center"
                  >
                    <button
                      type="button"
                      onClick={() => selectSubdomain(subdomain.id)}
                      className={`flex min-w-0 flex-1 items-center gap-2 rounded-lg px-3 py-2 pr-16 text-left text-[13px] ${activeSubdomainId === subdomain.id ? "bg-[#eef6fb] font-semibold text-[#236ca8]" : "text-[#607486] hover:bg-[#f5f8fb]"}`}
                    >
                      <DomainIcon
                        label={activeDomain.label}
                        className="size-3.5 shrink-0"
                      />
                      <span className="block truncate">{subdomain.name}</span>
                    </button>
                    <span className="absolute right-1 flex opacity-0 group-hover/sub:opacity-100">
                      <button
                        type="button"
                        onClick={() => startSubdomainEditor(subdomain)}
                        className="rounded p-1 text-[#607486] hover:bg-[#e8f0f6]"
                        title="编辑子领域"
                        aria-label={`编辑${subdomain.name}`}
                      >
                        <Pencil className="size-3" />
                      </button>
                      <button
                        type="button"
                        onClick={() => void removeSubdomain(subdomain)}
                        className="rounded p-1 text-[#a46b73] hover:bg-red-50"
                        title="删除子领域"
                        aria-label={`删除${subdomain.name}`}
                      >
                        <Trash2 className="size-3" />
                      </button>
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </aside>

        {workspaceMode === "recommend" ? (
          <RecommendationWorkspace
            domainId={recommendAcrossDomains || activeDomain.id === EMPTY_DOMAIN.id ? "" : activeDomain.id}
            subdomainId={recommendAcrossDomains ? "" : activeSubdomainId}
            domainName={recommendAcrossDomains ? "全部六大领域" : activeDomain.name}
            subdomainName={recommendAcrossDomains ? "" : activeSubdomain?.name || ""}
            draft={taskDraft}
            onDraftChange={setTaskDraft}
            run={recommendationRun}
            onRunChange={setRecommendationRun}
            onOpenTeam={openTeamDetail}
          />
        ) : workspaceMode === "intelligence" ? (
          <ImpactTriage embedded verifiedOnly domainId={recommendAcrossDomains || activeDomain.id === EMPTY_DOMAIN.id ? "" : activeDomain.id} subdomainId={recommendAcrossDomains ? "" : activeSubdomainId} />
        ) : workspaceMode === "graph" ? (
          loading ? (
            <div className="flex min-h-[420px] min-w-0 flex-1 items-center justify-center rounded-xl border border-[#e2e8f0] bg-white">
              <LoaderCircle className="size-7 animate-spin text-[#197b7a]" />
            </div>
          ) : (
            <FusionGraphWorkspace
              domainId={activeDomain.id}
              domainName={activeDomain.name}
              subdomainId={activeSubdomainId}
              subdomainName={activeSubdomain?.name ?? ""}
              taxonomyRevision={taxonomyRevision}
              onDataUpdated={refreshMapAfterGraph}
              onShowTeams={() => setWorkspaceMode("teams")}
            />
          )
        ) : (
          <>
            <main
              id="strategic-team-list"
              data-mobile-visible={mobilePanel === "teams"}
              className="strategic-map-teams flex min-h-[420px] min-w-0 flex-none flex-col rounded-xl border border-[#e2e8f0] bg-white p-3.5 shadow-[0_4px_14px_rgba(27,64,96,0.05)] sm:p-4 md:p-5 lg:min-h-0 lg:flex-1"
            >
              <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[#e6edf4] pb-4">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="text-[18px] font-semibold text-[#0f172a] sm:text-[20px]">
                      国内科研团队库
                    </h2>
                    <span className="rounded-full border border-[#c2d9eb] bg-[#eef7fd] px-2.5 py-1 text-[11px] font-semibold text-[#2c6a98]">
                      {visibleTeams.length} 支 · 重点 {priorityCount}
                    </span>
                  </div>
                  <p className="mt-1 text-[12px] text-[var(--chat-text-muted)]">
                    当前研判范围：{activeDomain.label}
                    {activeSubdomain ? ` / ${activeSubdomain.name}` : ""} ·{" "}
                    展示已核实的团队归属与公开资料 ·{" "}
                    {source.refreshed
                      ? "刚刚完成多源公开证据研判"
                      : "展示已保存研判结果，点击右侧按钮更新"}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void refreshNationwideTeams()}
                    disabled={loading || syncing || nationwideSyncing}
                    className="inline-flex items-center gap-1.5 rounded-full bg-[#176f78] px-3 py-1.5 text-[12px] font-semibold text-white transition hover:bg-[#125f67] disabled:cursor-not-allowed disabled:opacity-60"
                    title="载入六大领域官方团队候选并启动全国公开网络核验"
                  >
                    {nationwideSyncing ? (
                      <LoaderCircle className="size-3.5 animate-spin" />
                    ) : (
                      <ScanSearch className="size-3.5" />
                    )}
                    {nationwideSyncing ? "启动中…" : "全国扫描"}
                  </button>
                  <button
                    type="button"
                    onClick={() => void refreshTeams()}
                    disabled={
                      loading ||
                      syncing ||
                      nationwideSyncing ||
                      !activeDomain.id ||
                      activeDomain.id === EMPTY_DOMAIN.id
                    }
                    className="inline-flex items-center gap-1.5 rounded-full border border-[#cbd8e5] px-3 py-1.5 text-[12px] font-medium text-[#5f7181] transition hover:border-[#6fa1c2] hover:text-[#23648f] disabled:cursor-not-allowed disabled:opacity-60"
                    title="从 AI4S Daily 与公开网络获取当前领域最新候选"
                  >
                    {syncing ? (
                      <LoaderCircle className="size-3.5 animate-spin" />
                    ) : (
                      <RefreshCcw className="size-3.5" />
                    )}
                    {syncing ? "扫描中…" : "增量更新"}
                  </button>
                </div>
              </div>

              <div
                ref={teamScrollRef}
                onScroll={(event) => recordScroll("teams", event)}
                className="strategic-map-team-scroll flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto pr-1 pt-4"
              >
                {syncing && visibleTeams.length ? (
                  <div
                    className="mb-1 flex items-center gap-2 rounded-lg border border-[#c6dceb] bg-[#f5fbff] px-3 py-2 text-[12px] text-[#23648f]"
                    aria-live="polite"
                    role="status"
                  >
                    <LoaderCircle
                      className="size-4 shrink-0 animate-spin"
                      aria-hidden="true"
                    />
                    后台正在核验公开证据，已保存候选保持可浏览
                  </div>
                ) : null}
                <div className="strategic-map-table-head hidden grid-cols-[1fr_112px_178px] items-center gap-3 border-b border-[#dfe8ef] px-3 py-2 text-[11px] font-semibold tracking-[0.06em] text-[#75899a] sm:grid">
                  <span>团队</span>
                  <span>AI / 科学</span>
                  <span>关注与联系</span>
                </div>
                {syncing && !visibleTeams.length ? (
                  <div
                    className="flex min-h-[180px] items-center justify-center rounded-xl border border-[#c6dceb] bg-[#f5fbff] px-6 text-center"
                    aria-live="polite"
                    role="status"
                  >
                    <div className="flex items-center gap-3 text-left">
                      <LoaderCircle
                        className="size-6 shrink-0 animate-spin text-[#2375b3]"
                        aria-hidden="true"
                      />
                      <div>
                        <p className="text-[14px] font-semibold text-[#23648f]">
                          正在构建子领域图谱…
                        </p>
                        <p className="mt-1 text-[12px] leading-5 text-[#71889a]">
                          正在执行语义召回、邻域扩展与公开网络补充。
                        </p>
                      </div>
                    </div>
                  </div>
                ) : visibleTeams.length ? (
                  visibleTeams.map((team, index) => {
                    const selected = team.id === selectedTeam.id;
                    const rank = String(index + 1).padStart(2, "0");
                    return (
                      <button
                        key={team.id}
                        type="button"
                        onClick={() => {
                          applySelection({
                            domainId: activeDomain.id,
                            subdomainId: activeSubdomainId,
                            teamId: team.id,
                          });
                          switchMobilePanel("profile");
                        }}
                        aria-pressed={selected}
                        aria-label={`${rank} ${teamOrganization(team)} · ${teamDisplayName(team)}`}
                        data-team-id={team.id}
                        className={`strategic-map-team-row relative grid min-h-[68px] w-full shrink-0 gap-3 rounded-xl border px-3.5 py-2.5 text-left transition sm:grid-cols-[1fr_112px_178px] sm:items-center sm:px-4 ${
                          selected
                            ? "border-[#7daed1] bg-[#f2f8fd] shadow-[0_4px_12px_rgba(39,104,152,0.08)]"
                            : "border-[#e0e7ef] bg-white hover:border-[#a8c7de] hover:bg-[#f8fbfd]"
                        }`}
                      >
                        {selected ? (
                          <span className="absolute inset-y-2 left-0 w-1 rounded-r-full bg-[#2375b3]" />
                        ) : null}
                        <span className="flex min-w-0 items-center gap-3">
                          <span
                            className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-[#edf5fb] font-mono text-[12px] font-bold tracking-[0.04em] text-[#236ca8]"
                            aria-hidden="true"
                          >
                            {rank}
                          </span>
                          <span
                            className="strategic-map-team-name min-w-0"
                            aria-label={`${teamOrganization(team)} · ${teamDisplayName(team)}`}
                          >
                            <span className="block break-words text-[15px] font-semibold text-[#274158] sm:truncate">
                              {teamDisplayName(team)}
                            </span>
                            <span className="mt-1 block break-words text-[12px] font-medium text-[#6a8194] sm:truncate">
                              {teamOrganization(team)}
                            </span>
                          </span>
                        </span>
                        <span className="strategic-map-team-evaluation grid grid-cols-2 gap-2 text-[13px] font-semibold text-[#2e668e]">
                          {([ ["ai", "AI", team.aiLevel], ["science", "科学", team.scienceLevel] ] as const).map(([key, label, level]) => <span key={key} title={team.capabilityAssessments?.[key]?.reason}>
                            <span className="mb-0.5 block text-[10px] font-normal text-slate-500 sm:hidden">{label}</span>
                            <span data-capability={key}>{capabilityLevelLabel(level)}</span>
                            <span className="mt-1 block text-[10px] font-normal text-slate-500">{capabilitySourceLabel(team.capabilityAssessments?.[key]?.source)}</span>
                          </span>)}
                        </span>
                        <span className="strategic-map-team-status flex items-center justify-between gap-2 sm:block">
                          <AttentionBadge level={team.attention} />
                          <span
                            className={`ml-2 text-[11px] ${contactStyles[team.contact] ?? contactStyles.未接触}`}
                          >
                            {team.contact}
                          </span>
                          <span className="strategic-map-mobile-hint items-center gap-1">
                            查看画像
                            <ArrowRight className="size-3.5" />
                          </span>
                        </span>
                      </button>
                    );
                  })
                ) : (
                  <div className="flex min-h-[180px] items-center justify-center rounded-xl border border-dashed border-[#cbd8e5] bg-[#fbfdff] px-6 text-center text-[13px] leading-6 text-[#778897]">
                    {loading
                      ? "正在读取已保存的研判结果…"
                      : "当前范围暂无完成成果审核的团队，可调整领域或更新资料。"}
                  </div>
                )}
              </div>

              <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-[#e6edf4] pt-3 text-[12px] text-[var(--chat-text-muted)]">
                <span>关注等级与联系状态分别标注</span>
                <AttentionBadge level="重点关注" />
                <AttentionBadge level="持续关注" />
              </div>
            </main>

            {selectedTeam.id !== EMPTY_TEAM.id && <aside
              id="strategic-team-profile"
              data-mobile-visible={mobilePanel === "profile"}
              className="strategic-map-profile flex min-h-[380px] w-full min-w-0 shrink-0 flex-col overflow-hidden rounded-xl border border-[#e2e8f0] bg-white p-3.5 shadow-[0_4px_14px_rgba(27,64,96,0.05)] sm:p-4 md:p-5 lg:min-h-0 lg:w-[430px]"
            >
              <div className="flex shrink-0 items-start justify-between gap-3 border-b border-[#e6edf4] pb-4">
                <div>
                  <h2 className="text-[21px] font-semibold text-[#0f172a]">
                    团队画像与下一步
                  </h2>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    if (teamEditing) {
                      void saveTeamDetails();
                    } else {
                      setTeamEditing(true);
                    }
                  }}
                  disabled={
                    selectedTeam.id === EMPTY_TEAM.id || teamDetailsSaving
                  }
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-[#236ca8] px-3 py-1.5 text-[11px] font-semibold text-white transition hover:bg-[#1b5d94] disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {teamDetailsSaving ? (
                    <LoaderCircle className="size-3.5 animate-spin" />
                  ) : teamEditing ? (
                    <Save className="size-3.5" />
                  ) : (
                    <Pencil className="size-3.5" />
                  )}
                  {teamEditing ? "保存" : "编辑"}
                </button>
              </div>

              <h3 className="shrink-0 border-b border-[#e6edf4] py-4 text-[#0f172a]">
                <span className="block text-[19px] font-semibold">
                  {teamDisplayName(selectedTeam)}
                </span>
                <span className="mt-1 block text-[14px] font-medium text-[#6a8194]">
                  所属机构：{teamOrganization(selectedTeam)}
                </span>
              </h3>

              <div className="shrink-0 border-b border-[#e6edf4] py-3">
                <div className="flex items-end justify-between gap-3">
                  <div className="text-[12px] font-semibold tracking-wide text-[#71899a]">
                    证据评分
                  </div>
                  <div className="text-[#0f172a]">
                    <strong className="text-[24px] leading-none">
                      {(selectedTeam.scoreTotal ?? 0).toFixed(1)}
                    </strong>
                    <span className="ml-1 text-[11px] text-[#71899a]">
                      / 100
                    </span>
                  </div>
                </div>
                {(selectedTeam.scoreTotal ?? 0) > 0 ? (
                  <div className="mt-2 grid grid-cols-4 gap-1 text-center text-[10px] text-[#5d7480]">
                    <span>
                      成果{" "}
                      {selectedTeam.scoreBreakdown?.achievementQuality ?? 0}
                    </span>
                    <span>
                      相关 {selectedTeam.scoreBreakdown?.domainRelevance ?? 0}
                    </span>
                    <span>
                      活跃 {selectedTeam.scoreBreakdown?.recentActivity ?? 0}
                    </span>
                    <span>
                      AI证据{" "}
                      {selectedTeam.scoreBreakdown?.aiEvidenceReview ?? 0}
                    </span>
                  </div>
                ) : (
                  <div className="mt-2 text-[11px] text-[#a45a45]">
                    当前图谱证据不足，系统评分为 0 分
                  </div>
                )}
              </div>

                  <div className="flex shrink-0 items-start gap-3 border-b border-[#e6edf4] py-3">
                    <CircleAlert className="mt-0.5 size-4 shrink-0 text-[#6b96b5]" />
                        <TeamJudgementEditor
                          aiLevel={teamEditing ? teamDetailsDraft.aiLevel ?? selectedTeam.aiLevel : selectedTeam.aiLevel}
                          scienceLevel={teamEditing ? teamDetailsDraft.scienceLevel ?? selectedTeam.scienceLevel : selectedTeam.scienceLevel}
                          assessments={selectedTeam.capabilityAssessments}
                          editing={teamEditing}
                          disabled={teamDetailsSaving}
                          onChange={(field, level) =>
                            setTeamDetailsDraft((current) => ({
                              ...current,
                              [field]: level,
                            }))
                          }
                        />
                  </div>

              <div className="shrink-0 border-b border-[#e6edf4] py-3">
                <div className="mb-2 flex items-center justify-between text-[12px] font-semibold tracking-wide text-[#71899a]">
                  团队负责人
                  <button type="button" className="text-[#2e7096] hover:underline" onClick={() => openTeamDetail(selectedTeam.id)} disabled={selectedTeam.id === EMPTY_TEAM.id}>查看完整详情</button>
                </div>
                {selectedTeam.leader ? (
                  <button
                    type="button"
                    onClick={() => openTeamDetail(selectedTeam.id)}
                    className="flex min-w-0 w-full items-center gap-3 rounded-xl border border-[#d7e7f0] bg-[#f7fbfd] p-3 text-left transition hover:border-[#8bb8d2] hover:bg-[#f1f8fc]"
                  >
                    <span className="relative flex size-11 shrink-0 items-center justify-center overflow-hidden rounded-full bg-[#e4f1f8] font-semibold text-[#23668f]">
                      {selectedTeam.leader.name.slice(0, 1) || "人"}
                      {selectedTeam.leader.avatarUrl ? (
                        <img
                          src={selectedTeam.leader.avatarUrl}
                          alt=""
                          className="absolute inset-0 h-full w-full object-cover"
                          onError={(event) => {
                            event.currentTarget.style.display = "none";
                          }}
                        />
                      ) : null}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block break-words text-[14px] font-semibold text-[#24455d]">
                        {selectedTeam.leader.name}
                      </span>
                      <span className="mt-0.5 block break-words text-[11px] text-[#6a8194]">
                        {[selectedTeam.leader.title, selectedTeam.leader.role]
                          .filter(Boolean)
                          .join(" · ") || "团队负责人"}
                      </span>
                      {(selectedTeam.leaders?.length ?? 0) > 1 ? (
                        <span className="mt-1 block text-[11px] text-[#6a8194]">
                          其他负责人：
                          {selectedTeam
                            .leaders!.slice(1)
                            .map((person) => person.name)
                            .join("、")}
                        </span>
                      ) : null}
                      {selectedTeam.leader.researchDirection ? (
                        <span className="mt-1 block break-words text-[11px] leading-4 text-[#587284]">
                          {selectedTeam.leader.researchDirection}
                        </span>
                      ) : null}
                    </span>
                    <ArrowRight className="size-4 shrink-0 text-[#3c7da1]" />
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => openTeamDetail(selectedTeam.id)}
                    disabled={selectedTeam.id === EMPTY_TEAM.id}
                    className="flex w-full items-center justify-between gap-3 rounded-xl border border-dashed border-[#cbdde8] px-3 py-3 text-left text-[12px] text-[#718797] hover:bg-[#f8fbfd]"
                  >
                    <span className="min-w-0">
                      <span className="block font-medium text-[#526d80]">
                        暂无公开来源可核验的负责人
                      </span>
                      <span className="mt-0.5 block text-[11px] text-[#8295a3]">
                        查看现有团队与人员证据
                      </span>
                    </span>
                    <ArrowRight className="size-4 shrink-0 text-[#3c7da1]" />
                  </button>
                )}
              </div>

              <div
                ref={profileScrollRef}
                onScroll={(event) => recordScroll("profile", event)}
                className="strategic-map-profile-scroll min-h-0 flex-1 overflow-y-auto pr-1"
              >
                  <div className="flex items-start gap-3 border-b border-[#e6edf4] py-3 text-[14px]">
                    <Flag className="mt-0.5 size-4 shrink-0 text-[#6b96b5]" />
                    <div className="grid min-w-0 flex-1 grid-cols-[68px_1fr] gap-3">
                      <div className="font-semibold text-[#546b7d]">
                        核心方向
                      </div>
                      {teamEditing ? (
                        <textarea
                          value={teamDetailsDraft.coreDirection}
                          onChange={(event) =>
                            setTeamDetailsDraft((current) => ({
                              ...current,
                              coreDirection: event.target.value,
                            }))
                          }
                          className="min-h-[58px] w-full resize-y rounded-lg border border-[#ccd9e4] bg-white px-2.5 py-2 text-[13px] font-medium leading-5 text-[#304b61] outline-none focus:border-[#4c91bd]"
                          maxLength={500}
                        />
                      ) : (
                        <div className="strategic-map-core-direction min-w-0 line-clamp-3 break-words font-semibold leading-5 text-[#304b61]">
                          {selectedTeam.coreDirection ||
                            selectedTeam.researchDirections?.join("、") ||
                            selectedTeam.focus ||
                            "暂无公开研究方向"}
                        </div>
                      )}
                    </div>
                  </div>
                <section
                  aria-label="团队公开资料"
                  className="space-y-4 border-b border-[#e6edf4] py-4 text-[13px] leading-6 text-[#304b61]"
                >
                  {selectedTeam.description ? (
                    <div>
                      <h4 className="mb-1 text-[12px] font-semibold text-[#71899a]">
                        团队简介
                      </h4>
                      <p
                        className="max-h-24 overflow-hidden whitespace-pre-line break-words leading-6"
                        style={{
                          display: "-webkit-box",
                          WebkitBoxOrient: "vertical",
                          WebkitLineClamp: 4,
                        }}
                      >
                        {selectedTeam.description}
                      </p>
                    </div>
                  ) : null}
                  {(selectedTeam.researchDirections?.length ?? 0) > 0 ? (
                    <div>
                      <h4 className="mb-1 text-[12px] font-semibold text-[#71899a]">
                        研究方向
                      </h4>
                      <ul className="list-disc space-y-1 pl-4">
                        {selectedTeam.researchDirections!.map((direction) => (
                          <li key={direction} className="break-words">
                            {direction}
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                  <div>
                    <h4 className="mb-2 text-[12px] font-semibold text-[#71899a]">
                      团队成员 · {selectedTeam.members?.length ?? 0} 人
                    </h4>
                    {selectedTeam.members?.length ? (
                      <ul className="divide-y divide-[#edf2f6]">
                        {selectedTeam.members.map((member) => (
                          <li key={member.id} className="py-2 first:pt-0">
                            <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                              <span className="font-semibold text-[#24455d]">
                                {member.name}
                              </span>
                              <span className="text-[11px] text-[#6a8194]">
                                {[member.title, member.role]
                                  .filter(Boolean)
                                  .join(" · ")}
                              </span>
                            </div>
                            {member.researchDirection ? (
                              <p className="break-words text-[12px]">
                                {member.researchDirection}
                              </p>
                            ) : null}
                            {member.bio ? (
                              <p className="mt-1 break-words text-[12px] leading-5 text-[#6a8194]">
                                {member.bio}
                              </p>
                            ) : null}
                            {(
                              member.profileUrl || member.sourceUrls?.[0]
                            )?.match(/^https?:\/\//i) ? (
                              <a
                                href={member.profileUrl || member.sourceUrls[0]}
                                target="_blank"
                                rel="noreferrer"
                                className="text-[11px] text-[#236ca8] hover:underline"
                              >
                                查看人员来源
                              </a>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-[12px] text-[#8295a3]">
                        尚未取得可核验的公开成员名册
                      </p>
                    )}
                  </div>
                  {selectedTeam.sourceUrls.some((url) =>
                    /^https?:\/\//i.test(url),
                  ) ? (
                    <div>
                      <h4 className="mb-1 text-[12px] font-semibold text-[#71899a]">
                        资料来源
                      </h4>
                      <ol className="list-decimal space-y-1 pl-4 text-[12px]">
                        {selectedTeam.sourceUrls
                          .filter((url) => /^https?:\/\//i.test(url))
                          .map((url) => (
                            <li key={url}>
                              <a
                                href={url}
                                target="_blank"
                                rel="noreferrer"
                                className="break-all text-[#236ca8] hover:underline"
                              >
                                {url}
                              </a>
                            </li>
                          ))}
                      </ol>
                    </div>
                  ) : null}
                </section>
                <div className="shrink-0 border-b border-[#e6edf4] py-3">
                  {teamEditing ? (
                    <div className="grid grid-cols-2 gap-3">
                      <label className="block text-[12px] font-semibold text-[#546b7d]">
                        关注等级
                        <select
                          value={teamDetailsDraft.attention}
                          onChange={(event) =>
                            setTeamDetailsDraft((current) => ({
                              ...current,
                              attention: event.target.value,
                            }))
                          }
                          className="mt-1.5 w-full rounded-lg border border-[#ccd9e4] bg-white px-2.5 py-2 text-[13px] font-medium text-[#304b61] outline-none focus:border-[#4c91bd]"
                          disabled={
                            teamDetailsSaving ||
                            selectedTeam.id === EMPTY_TEAM.id
                          }
                        >
                          <option value="重点关注">重点关注</option>
                          <option value="持续关注">持续关注</option>
                          <option value="一般关注">一般关注</option>
                          <option value="待核实">未标记</option>
                        </select>
                      </label>
                      <label className="block text-[12px] font-semibold text-[#546b7d]">
                        联系状态
                        <select
                          value={teamDetailsDraft.contact}
                          onChange={(event) =>
                            setTeamDetailsDraft((current) => ({
                              ...current,
                              contact: event.target.value,
                            }))
                          }
                          className="mt-1.5 w-full rounded-lg border border-[#ccd9e4] bg-white px-2.5 py-2 text-[13px] font-medium text-[#304b61] outline-none focus:border-[#4c91bd]"
                          disabled={
                            teamDetailsSaving ||
                            selectedTeam.id === EMPTY_TEAM.id
                          }
                        >
                          <option value="未接触">未接触</option>
                          <option value="侧面了解">侧面了解</option>
                          <option value="待拜访">待拜访</option>
                          <option value="已交流">已交流</option>
                          <option value="已联系">已联系</option>
                        </select>
                      </label>
                    </div>
                  ) : (
                    <div className="flex flex-wrap items-center gap-3">
                      <div>
                        <div className="mb-1 text-[12px] font-semibold text-[#546b7d]">
                          关注等级
                        </div>
                        <AttentionBadge level={selectedTeam.attention} />
                      </div>
                      <div>
                        <div className="mb-1 text-[12px] font-semibold text-[#546b7d]">
                          联系状态
                        </div>
                        <span
                          className={`text-[13px] font-semibold ${contactStyles[selectedTeam.contact] ?? contactStyles.未接触}`}
                        >
                          {selectedTeam.contact}
                        </span>
                      </div>
                    </div>
                  )}
                </div>

                <div
                  className={`space-y-4 py-4 text-[14px] ${teamEditing ? "" : "space-y-3 py-3"}`}
                >
                  <div className="flex items-start gap-4">
                    <UsersRound className="mt-0.5 size-4 shrink-0 text-[#6b96b5]" />
                    <div className="grid min-w-0 flex-1 grid-cols-[68px_1fr] gap-3">
                      <div className="font-semibold text-[#546b7d]">
                        联系记录
                      </div>
                      {teamEditing ? (
                        <textarea
                          value={teamDetailsDraft.contactRecord}
                          onChange={(event) =>
                            setTeamDetailsDraft((current) => ({
                              ...current,
                              contactRecord: event.target.value,
                            }))
                          }
                          className="min-h-[52px] w-full resize-y rounded-lg border border-[#ccd9e4] bg-white px-2.5 py-2 text-[13px] font-medium leading-5 text-[#304b61] outline-none focus:border-[#4c91bd]"
                          maxLength={255}
                        />
                      ) : (
                        <div className="font-semibold text-[#304b61]">
                          {selectedTeam.contactRecord}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="flex items-start gap-4">
                    <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-[#6b96b5]" />
                    <div className="grid min-w-0 flex-1 grid-cols-[68px_1fr] gap-3">
                      <div className="font-semibold text-[#546b7d]">
                        内部评价
                      </div>
                      {teamEditing ? (
                        <textarea
                          value={teamDetailsDraft.internalReview}
                          onChange={(event) =>
                            setTeamDetailsDraft((current) => ({
                              ...current,
                              internalReview: event.target.value,
                            }))
                          }
                          className="min-h-[62px] w-full resize-y rounded-lg border border-[#ccd9e4] bg-white px-2.5 py-2 text-[13px] font-medium leading-5 text-[#304b61] outline-none focus:border-[#4c91bd]"
                          maxLength={500}
                        />
                      ) : (
                        <div className="font-semibold leading-5 text-[#304b61]">
                          {selectedTeam.internalReview}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="flex items-start gap-4">
                    <CalendarDays className="mt-0.5 size-4 shrink-0 text-[#6b96b5]" />
                    <div className="grid min-w-0 flex-1 grid-cols-[68px_1fr] gap-3">
                      <div className="font-semibold text-[#546b7d]">
                        最近更新
                      </div>
                      {teamEditing ? (
                        <input
                          value={teamDetailsDraft.recentUpdate}
                          onChange={(event) =>
                            setTeamDetailsDraft((current) => ({
                              ...current,
                              recentUpdate: event.target.value,
                            }))
                          }
                          className="w-full rounded-lg border border-[#ccd9e4] bg-white px-2.5 py-2 text-[13px] font-medium text-[#304b61] outline-none focus:border-[#4c91bd]"
                          maxLength={64}
                        />
                      ) : (
                        <div className="font-semibold text-[#304b61]">
                          {selectedTeam.recentUpdate}
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                <div
                  className={`mt-3 rounded-xl border border-[#bcd5e8] bg-[#eef7fd] p-4 ${teamEditing ? "shrink-0" : "shrink-0"}`}
                >
                  <div className="flex items-center gap-2 text-[15px] font-semibold text-[#23668f]">
                    <Clock3 className="size-4" />
                    下一步行动
                  </div>
                  {teamEditing ? (
                    <textarea
                      value={teamDetailsDraft.nextAction}
                      onChange={(event) =>
                        setTeamDetailsDraft((current) => ({
                          ...current,
                          nextAction: event.target.value,
                        }))
                      }
                      className="mt-2 min-h-[82px] w-full resize-y rounded-lg border border-[#bcd5e8] bg-white px-2.5 py-2 text-[13px] font-medium leading-5 text-[#294b65] outline-none focus:border-[#4c91bd]"
                      maxLength={500}
                    />
                  ) : (
                    <p className="mt-2 text-[14px] font-semibold leading-6 text-[#294b65]">
                      {selectedTeam.nextAction}
                    </p>
                  )}
                  <p className="mt-3 text-[11px] text-[#63829a]">
                    数据来源：
                    {selectedTeam.sourceUrls?.[0] ||
                    selectedTeam.source === "AI4S Daily" ? (
                      <a
                        href={
                          selectedTeam.sourceUrls?.[0] ||
                          "https://ai4s-frontier.github.io/AI4S-Daily-HTML"
                        }
                        target="_blank"
                        rel="noreferrer"
                        className="transition hover:text-[#23668f]"
                      >
                        {selectedTeam.source}
                      </a>
                    ) : (
                      selectedTeam.source
                    )}
                    {selectedTeam.reportTitle ? (
                      <>
                        {" · "}
                        {selectedTeam.sourceUrls?.[1] ||
                        selectedTeam.sourceUrls?.[0] ? (
                          <a
                            href={
                              selectedTeam.sourceUrls?.[1] ||
                              selectedTeam.sourceUrls?.[0]
                            }
                            target="_blank"
                            rel="noreferrer"
                            className="transition hover:text-[#23668f]"
                          >
                            {selectedTeam.reportTitle}
                          </a>
                        ) : (
                          selectedTeam.reportTitle
                        )}
                      </>
                    ) : null}
                  </p>
                </div>
              </div>
            </aside>}
          </>
        )}
      </div>
      {error ? (
        <div className="absolute bottom-4 left-1/2 z-20 max-w-[min(640px,calc(100%-2rem))] -translate-x-1/2 rounded-lg border border-[#f1c6c6] bg-[#fff5f5] px-4 py-2.5 text-[12px] text-[#b44747] shadow-lg">
          {error}
        </div>
      ) : null}
      {editor ? (
        <div className="fixed inset-0 z-30 flex items-center justify-center bg-[#12324a]/25 p-4 backdrop-blur-[2px]">
          <form
            onSubmit={saveEditor}
            className="strategic-map-editor w-full max-w-[440px] rounded-2xl border border-[#e2e8f0] bg-white p-5 shadow-[0_18px_60px_rgba(23,63,94,0.2)]"
          >
            <div className="flex items-center justify-between">
              <h2 className="text-[18px] font-semibold text-[#0f172a]">
                {editor.id ? "编辑" : "新增"}
                子领域
              </h2>
              <button
                type="button"
                onClick={() => setEditor(null)}
                className="rounded-lg p-1.5 text-[#6d7d8b] hover:bg-[#f1f5f8]"
                aria-label="关闭"
              >
                <X className="size-4" />
              </button>
            </div>
            <label className="mt-5 block text-[12px] font-semibold text-[#5a7182]">
              名称
              <input
                autoFocus
                value={editor.name}
                onChange={(event) =>
                  setEditor({
                    ...editor,
                    name: event.target.value,
                  })
                }
                className="mt-1.5 w-full rounded-lg border border-[#ccd9e4] px-3 py-2.5 text-[14px] text-[#274158] outline-none focus:border-[#4c91bd] disabled:cursor-not-allowed disabled:bg-[#f5f8fb]"
                maxLength={120}
                required
                disabled={saving}
              />
            </label>
            <label className="mt-4 block text-[12px] font-semibold text-[#5a7182]">
              说明
              <textarea
                value={editor.description}
                onChange={(event) =>
                  setEditor({
                    ...editor,
                    description: event.target.value,
                  })
                }
                className="mt-1.5 min-h-[84px] w-full resize-y rounded-lg border border-[#ccd9e4] px-3 py-2.5 text-[14px] text-[#274158] outline-none focus:border-[#4c91bd] disabled:cursor-not-allowed disabled:bg-[#f5f8fb]"
                maxLength={255}
                disabled={saving}
              />
            </label>
            {!editor.id && saving ? (
              <div
                className="mt-4 flex items-center gap-3 rounded-xl border border-[#c6dceb] bg-[#f5fbff] px-3.5 py-3.5"
                role="status"
                aria-live="polite"
              >
                <LoaderCircle
                  className="size-5 shrink-0 animate-spin text-[#2375b3]"
                  aria-hidden="true"
                />
                <div>
                  <p className="text-[13px] font-semibold text-[#23648f]">
                    正在保存子领域…
                  </p>
                </div>
              </div>
            ) : null}
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setEditor(null)}
                disabled={saving}
                className="rounded-lg border border-[#d2dee8] px-4 py-2 text-[13px] text-[#607486] hover:bg-[#f5f8fb] disabled:cursor-not-allowed disabled:opacity-60"
              >
                取消
              </button>
              <button
                type="submit"
                disabled={saving}
                className="inline-flex items-center gap-1.5 rounded-lg bg-[#236ca8] px-4 py-2 text-[13px] font-semibold text-white hover:bg-[#1b5d94] disabled:opacity-60"
              >
                {saving ? (
                  <LoaderCircle className="size-3.5 animate-spin" />
                ) : (
                  <Save className="size-3.5" />
                )}
                保存
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </div>
  );
}
