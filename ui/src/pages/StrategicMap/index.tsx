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
  CircuitBoard,
  CircleAlert,
  Clock3,
  Dna,
  Flag,
  FlaskConical,
  LoaderCircle,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Plus,
  RefreshCcw,
  Rocket,
  Save,
  Sparkles,
  Trash2,
  UsersRound,
  Zap,
  X,
} from "lucide-react";
import { useLocation, useNavigate } from "react-router-dom";

import {
  createStrategicDomain,
  createStrategicSubdomain,
  deleteStrategicDomain,
  deleteStrategicSubdomain,
  loadStrategicDomainTeams,
  loadStrategicMap,
  updateStrategicTeam,
  updateStrategicDomain,
  updateStrategicSubdomain,
  type StrategicDomain,
  type StrategicMapSource,
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
} from "@/router/strategicMapNavigation";
import {
  resolveStrategicMapSelection,
  type StrategicMapSelection,
} from "./selection";

type AttentionLevel = string;
type ContactStatus = string;

type Team = StrategicTeam;

type DemoTeam = Omit<
  Team,
  "domainId" | "subdomainId" | "source" | "sourceUrls" | "evidenceSummary" | "reportId" | "reportTitle" | "updatedAt"
>;

type Domain = StrategicDomain & { teams: Team[] };

type EditorState = {
  kind: "domain" | "subdomain";
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
  coreDirection: "当前领域暂未同步到 AI4S Daily 相关线索",
  dualJudgement: "AI 待核实｜科学 待核实",
  contactRecord: "暂无联系记录",
  internalReview: "请先同步 AI4S Daily，再补充知识库证据",
  recentUpdate: "",
  nextAction: "点击“同步 AI4S Daily”获取最新候选线索",
  source: "AI4S Daily",
  sourceUrls: [],
  evidenceSummary: "",
  reportId: "",
  reportTitle: "",
  updatedAt: "",
};

const DEMO_DOMAINS: (Omit<Domain, "subdomains" | "teams" | "name"> & { teams: DemoTeam[] })[] = [
  {
    id: "life-science",
    label: "生命科学",
    description: "药物、结构与生物计算",
    // Do not ship fabricated fallback candidates. The API is the source of
    // truth; until it returns an evidence-backed row the pool stays empty.
    teams: [],
  },
  {
    id: "alloy-materials",
    label: "合金材料",
    description: "先进材料、工艺与装备",
    teams: [],
  },
  {
    id: "integrated-circuit",
    label: "集成电路",
    description: "器件、芯片与设计自动化",
    teams: [],
  },
  {
    id: "power-solver",
    label: "电力求解器",
    description: "电网、能源与复杂系统计算",
    teams: [],
  },
  {
    id: "aerospace",
    label: "航空航天",
    description: "飞行器、推进与空间任务",
    teams: [],
  },
  {
    id: "other",
    label: "其他重点领域",
    description: "待扩展的战略观察对象",
    teams: [],
  },
]; 

const DOMAINS: Domain[] = DEMO_DOMAINS.map((domain) => ({
  ...domain,
  name: domain.label,
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
      {level}
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
    const updatedDelta = (right.updatedAt || "").localeCompare(left.updatedAt || "");
    if (updatedDelta !== 0) {
      return updatedDelta;
    }
    return teamOrganization(left).localeCompare(teamOrganization(right), "zh-CN");
  });
}

function teamOrganization(team: Team): string {
  const value = (team.organization?.trim() || team.name || "")
    .replace(/^\s*(?:\d{1,3}[.、)）:\-]?|[①②③④⑤⑥⑦⑧⑨⑩❶❷❸❹❺❻❼❽❾❿])\s*/, "")
    .trim();
  if (!value || /^(?:团队|机构|单位)\s*[A-Z0-9一二三四五六七八九十]*$/i.test(value)) {
    return "机构信息待核实";
  }
  if (/^(?:国家级?)?实验室$/.test(value)) {
    return "具体实验室名称待核实";
  }
  return value;
}

function teamDisplayName(team: Team): string {
  const value = (team.teamName?.trim() || team.focus || "")
    .replace(/^\s*(?:\d{1,3}[.、)）:\-]?|[①②③④⑤⑥⑦⑧⑨⑩❶❷❸❹❺❻❼❽❾❿])\s*/, "")
    .trim();
  if (!value || /^(?:团队|机构|单位)\s*[A-Z0-9一二三四五六七八九十]*$/i.test(value) || value === "相关团队线索") {
    return "团队信息待核实";
  }
  return value;
}

/** Keep the navigation scannable by giving each strategic area a small visual anchor. */
function DomainIcon({ label, className = "size-4" }: { label: string; className?: string }) {
  const Icon = label.includes("生命")
    ? Dna
    : label.includes("合金") || label.includes("材料")
      ? Atom
      : label.includes("集成") || label.includes("芯片")
        ? CircuitBoard
        : label.includes("智能") || label.includes("计算")
          ? BrainCircuit
          : label.includes("电力") || label.includes("能源")
            ? Zap
            : label.includes("航空") || label.includes("航天")
              ? Rocket
              : label.includes("化学") || label.includes("实验")
                ? FlaskConical
                : Sparkles;
  return <Icon className={className} aria-hidden="true" />;
}

export default function StrategicMap() {
  const navigate = useNavigate();
  const location = useLocation();
  const initialContextRef = useRef<StrategicMapNavigationContext | undefined>(undefined);
  if (initialContextRef.current === undefined) {
    initialContextRef.current = readStrategicMapNavigationContext(location.pathname, location.search)
      ?? {
        route: strategicMapRouteForPathname(location.pathname),
        domainId: "",
        subdomainId: "",
        teamId: "",
        scroll: { ...EMPTY_STRATEGIC_MAP_SCROLL },
      };
  }
  const initialContext = initialContextRef.current;
  const [domains, setDomains] = useState<Domain[]>(DOMAINS);
  const [activeDomainId, setActiveDomainId] = useState(initialContext.domainId || DOMAINS[0].id);
  const [activeSubdomainId, setActiveSubdomainId] = useState(initialContext.subdomainId);
  const [selectedTeamId, setSelectedTeamId] = useState(initialContext.teamId);
  const [source, setSource] = useState<StrategicMapSource>({ provider: "AI4S Daily" });
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [teamDetailsSaving, setTeamDetailsSaving] = useState(false);
  const [teamEditing, setTeamEditing] = useState(false);
  const [teamDetailsDraft, setTeamDetailsDraft] = useState<TeamDetailsDraft>({
    attention: "",
    contact: "",
    coreDirection: "",
    dualJudgement: "",
    contactRecord: "",
    internalReview: "",
    recentUpdate: "",
    nextAction: "",
  });
  const [error, setError] = useState("");
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [leftNavCollapsed, setLeftNavCollapsed] = useState(false);
  const selectionRef = useRef<StrategicMapSelection>({
    domainId: initialContext.domainId || DOMAINS[0].id,
    subdomainId: initialContext.subdomainId,
    teamId: initialContext.teamId,
  });
  const domainsRef = useRef(domains);
  const refreshRequestIdRef = useRef(0);
  const scrollFrameRef = useRef<number | null>(null);
  const scrollStateRef = useRef<StrategicMapScrollState>({ ...initialContext.scroll });
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
    domains.find((domain) => domain.id === activeDomainId) ?? domains[0] ?? EMPTY_DOMAIN;
  const visibleTeams = rankCandidateTeams(
    activeSubdomainId
      ? activeDomain.teams.filter((team) => team.subdomainId === activeSubdomainId)
      : activeDomain.teams,
  );
  const selectedTeam =
    visibleTeams.find((team) => team.id === selectedTeamId) ?? EMPTY_TEAM;

  useEffect(() => {
    setTeamDetailsDraft({
      attention: selectedTeam.attention,
      contact: selectedTeam.contact,
      coreDirection: selectedTeam.coreDirection,
      dualJudgement: selectedTeam.dualJudgement,
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
    selectedTeam.contactRecord,
    selectedTeam.internalReview,
    selectedTeam.recentUpdate,
    selectedTeam.nextAction,
  ]);

  const priorityCount = useMemo(
    () => visibleTeams.filter((team) => team.attention === "重点关注").length,
    [visibleTeams],
  );

  const captureScrollState = useCallback((): StrategicMapScrollState => ({
    page: pageScrollRef.current?.scrollTop ?? scrollStateRef.current.page,
    domains: domainScrollRef.current?.scrollTop ?? scrollStateRef.current.domains,
    subdomains: subdomainScrollRef.current?.scrollTop ?? scrollStateRef.current.subdomains,
    teams: teamScrollRef.current?.scrollTop ?? scrollStateRef.current.teams,
    profile: profileScrollRef.current?.scrollTop ?? scrollStateRef.current.profile,
  }), []);

  const currentNavigationContext = useCallback((teamId = selectionRef.current.teamId) => ({
    route: initialContext.route,
    ...selectionRef.current,
    teamId,
    scroll: captureScrollState(),
  }), [captureScrollState, initialContext.route]);

  const replaceCurrentMapUrl = useCallback(() => {
    if (typeof window === "undefined") return;
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
    if (scrollFrameRef.current != null) window.cancelAnimationFrame(scrollFrameRef.current);
    scrollFrameRef.current = window.requestAnimationFrame(() => {
      scrollFrameRef.current = null;
      replaceCurrentMapUrl();
    });
  }, [replaceCurrentMapUrl]);

  const recordScroll = useCallback((
    key: keyof StrategicMapScrollState,
    event: UIEvent<HTMLDivElement>,
  ) => {
    scrollStateRef.current = {
      ...scrollStateRef.current,
      [key]: event.currentTarget.scrollTop,
    };
    queueScrollUrlSync();
  }, [queueScrollUrlSync]);

  const openTeamDetail = useCallback((teamId: string) => {
    if (!teamId || teamId === EMPTY_TEAM.id) return;
    const context = currentNavigationContext(teamId);
    const returnPath = buildStrategicMapPath(context);
    if (typeof window !== "undefined") {
      // Make browser Back restore the same context too, not just the in-page back button.
      window.history.replaceState(window.history.state, "", returnPath);
    }
    navigate(buildStrategicTeamDetailNavigationPath(teamId, context));
  }, [currentNavigationContext, navigate]);

  useEffect(() => {
    if (!loading) replaceCurrentMapUrl();
  }, [activeDomainId, activeSubdomainId, loading, replaceCurrentMapUrl, selectedTeamId]);

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

  useEffect(() => () => {
    if (scrollFrameRef.current != null && typeof window !== "undefined") {
      window.cancelAnimationFrame(scrollFrameRef.current);
    }
  }, []);

  useEffect(() => {
    let disposed = false;
    loadStrategicMap()
      .then((snapshot) => {
        if (disposed) return;
        const nextDomains = mergeSnapshotDomains(snapshot.domains, snapshot.teams);
        if (nextDomains.length) {
          domainsRef.current = nextDomains;
          setDomains(nextDomains);
        }
        const requested = selectionRef.current;
        applySelection(resolveStrategicMapSelection(
          nextDomains.length ? nextDomains : domainsRef.current,
          requested,
          !requested.teamId,
        ));
        setSource(snapshot.source);
        setError("");
      })
      .catch((reason) => {
        if (!disposed) {
          const requested = selectionRef.current;
          applySelection(resolveStrategicMapSelection(
            domainsRef.current,
            requested,
            !requested.teamId,
          ));
          setError(reason instanceof Error ? reason.message : "读取战略图谱失败，当前显示本地缓存");
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
    applySelection({
      domainId: domain.id,
      subdomainId: "",
      teamId: rankCandidateTeams(domain.teams)[0]?.id ?? "",
    });
  };

  const refreshTeams = async () => {
    if (!activeDomain.id || activeDomain.id === EMPTY_DOMAIN.id) return;
    const requestId = ++refreshRequestIdRef.current;
    const refreshedDomainId = activeDomain.id;
    setSyncing(true);
    setError("");
    try {
      const result = await loadStrategicDomainTeams(refreshedDomainId, { refresh: true });
      if (requestId !== refreshRequestIdRef.current) return;
      const nextDomains = domainsRef.current.map((domain) => (
        domain.id === refreshedDomainId
          ? {
            ...domain,
            ...(result.domain ?? {}),
            teams: result.teams,
          }
          : domain
      ));
      domainsRef.current = nextDomains;
      setDomains(nextDomains);
      const requested = selectionRef.current;
      applySelection(resolveStrategicMapSelection(nextDomains, requested, !requested.teamId));
      if (selectionRef.current.domainId === refreshedDomainId) setSource(result.source);
    } catch (reason) {
      if (requestId === refreshRequestIdRef.current) {
        setError(reason instanceof Error ? reason.message : "AI4S Daily 同步失败");
      }
    } finally {
      if (requestId === refreshRequestIdRef.current) setSyncing(false);
    }
  };

  const saveTeamDetails = async () => {
    if (!selectedTeam.id || selectedTeam.id === EMPTY_TEAM.id || teamDetailsSaving) return;
    setTeamDetailsSaving(true);
    setError("");
    try {
      const next = await updateStrategicTeam(selectedTeam.id, teamDetailsDraft);
      setDomains((current) => current.map((domain) => ({
        ...domain,
        teams: domain.teams.map((team) => team.id === next.id ? next : team),
      })));
      setTeamDetailsDraft({
        attention: next.attention,
        contact: next.contact,
        coreDirection: next.coreDirection,
        dualJudgement: next.dualJudgement,
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

  const startDomainEditor = (domain?: Domain) => {
    setEditor({
      kind: "domain",
      id: domain?.id,
      name: domain?.name ?? "",
      description: domain?.description ?? "",
    });
    setError("");
  };

  const startSubdomainEditor = (subdomain?: StrategicSubdomain) => {
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
    const isNewDomain = editor.kind === "domain" && !editor.id;
    setSaving(true);
    if (isNewDomain) setSyncing(true);
    setError("");
    try {
      if (editor.kind === "domain") {
        const next = editor.id
          ? await updateStrategicDomain(editor.id, editor.name, editor.description)
          : await createStrategicDomain(editor.name, editor.description);
        const createdDomainTeams = !editor.id
          ? await loadStrategicDomainTeams(next.id)
          : undefined;
        setDomains((current) => {
          const withTeams = {
            ...next,
            ...(createdDomainTeams?.domain ?? {}),
            teams: createdDomainTeams?.teams ?? current.find((item) => item.id === next.id)?.teams ?? [],
          };
          return editor.id ? current.map((item) => item.id === next.id ? withTeams : item) : [...current, withTeams];
        });
        if (!editor.id) {
          applySelection({
            domainId: next.id,
            subdomainId: "",
            teamId: "",
          });
        }
      } else if (editor.parentId) {
        const next = editor.id
          ? await updateStrategicSubdomain(editor.id, editor.name, editor.description, editor.parentId)
          : await createStrategicSubdomain(editor.parentId, editor.name, editor.description);
        setDomains((current) => current.map((domain) => {
          if (domain.id !== editor.parentId) return domain;
          const subdomains = editor.id
            ? domain.subdomains.map((item) => item.id === next.id ? next : item)
            : [...domain.subdomains, next];
          return {
            ...domain,
            subdomains,
          };
        }));
        if (!editor.id) {
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
      if (isNewDomain) setSyncing(false);
    }
  };

  const removeDomain = async (domain: Domain) => {
    if (typeof window !== "undefined" && !window.confirm(`确定删除领域“${domain.name}”及其子领域吗？`)) return;
    try {
      await deleteStrategicDomain(domain.id);
      setDomains((current) => current.filter((item) => item.id !== domain.id));
      if (activeDomainId === domain.id) {
        const next = domains.find((item) => item.id !== domain.id);
        applySelection({
          domainId: next?.id ?? "",
          subdomainId: "",
          teamId: next ? rankCandidateTeams(next.teams)[0]?.id ?? "" : "",
        });
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除领域失败");
    }
  };

  const removeSubdomain = async (subdomain: StrategicSubdomain) => {
    if (typeof window !== "undefined" && !window.confirm(`确定删除子领域“${subdomain.name}”吗？`)) return;
    try {
      await deleteStrategicSubdomain(subdomain.id);
      setDomains((current) => current.map((domain) => domain.id === activeDomain.id
        ? {
          ...domain,
          subdomains: domain.subdomains.filter((item) => item.id !== subdomain.id),
        }
        : domain));
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
    <div className="flex h-full min-h-0 w-full min-w-0 flex-col overflow-x-hidden overflow-y-hidden bg-[#f3f6f9] text-[var(--chat-text)]">
      <header className="shrink-0 border-b border-[#1a6683] bg-[#105d79] px-4 py-3 text-white shadow-[0_2px_8px_rgba(14,77,105,0.16)] sm:px-5 sm:py-4 md:px-7">
        <div className="mx-auto flex w-full max-w-[1600px] flex-wrap items-center gap-x-8 gap-y-2 sm:gap-x-16 sm:gap-y-3">
          <h1 className="shrink-0 text-[20px] font-semibold tracking-[0.02em] sm:text-[22px] md:text-[25px]">
            AI4S战略力量图谱
          </h1>
          <p className="order-3 w-full text-[13px] font-medium tracking-[0.02em] text-white/85 sm:order-none sm:w-auto sm:min-w-[250px] sm:flex-1 sm:text-[15px] md:text-[17px]">
            自动扫描论文、项目、人才与成果
          </p>
        </div>
      </header>

      <div ref={pageScrollRef} onScroll={(event) => recordScroll("page", event)} className="mx-auto flex min-h-0 w-full max-w-[1600px] min-w-0 flex-1 flex-col gap-3 overflow-y-auto p-3 sm:gap-4 sm:p-4 md:p-5 lg:overflow-hidden lg:flex-row">
        <aside
          className={`flex min-h-0 w-full shrink-0 flex-col rounded-xl border border-[#d1dce7] bg-white shadow-[0_4px_14px_rgba(27,64,96,0.05)] transition-[width,height,padding] duration-200 lg:h-full ${
            leftNavCollapsed
              ? "h-[58px] p-2 sm:h-[58px] lg:w-[58px]"
              : "h-[260px] p-3.5 sm:h-[290px] lg:w-[276px]"
          }`}
          aria-label="战略领域导航"
        >
          <div className={`flex items-center gap-2 pb-4 ${leftNavCollapsed ? "justify-center px-0" : "justify-between px-2"}`}>
            {leftNavCollapsed ? null : (
              <div className="min-w-0">
                <div className="truncate text-[19px] font-semibold leading-7 text-[#164f70]">输入领域 / 子领域</div>
                <p className="mt-0.5 truncate text-[13px] text-[#6d7d8b]">点击切换研判对象</p>
              </div>
            )}
            {!leftNavCollapsed ? (
              <div className="flex shrink-0 items-center gap-0.5">
                <button type="button" onClick={() => startDomainEditor()} className="rounded-lg p-1.5 text-[#236ca8] hover:bg-[#edf6fb]" title="新增领域" aria-label="新增领域">
                  <Plus className="size-4" />
                </button>
                <button type="button" onClick={() => setLeftNavCollapsed(true)} className="rounded-lg p-1.5 text-[#607486] hover:bg-[#edf6fb]" title="收起领域导航" aria-label="收起领域导航">
                  <PanelLeftClose className="size-4" />
                </button>
              </div>
            ) : (
              <button type="button" onClick={() => setLeftNavCollapsed(false)} className="rounded-lg p-1.5 text-[#236ca8] hover:bg-[#edf6fb]" title="展开领域导航" aria-label="展开领域导航">
                <PanelLeftOpen className="size-4" />
              </button>
            )}
          </div>
          <div className={leftNavCollapsed ? "hidden" : "flex min-h-0 flex-1 flex-col px-1 pr-1"}>
            <div ref={domainScrollRef} onScroll={(event) => recordScroll("domains", event)} className="min-h-0 h-[72%] shrink-0 overflow-y-auto pr-1">
              {domains.map((domain) => {
                const active = domain.id === activeDomain.id;
                return (
                  <div
                    key={domain.id}
                    className={`group relative mb-3 flex min-h-[72px] w-full items-center rounded-xl border transition ${
                      active
                        ? "border-[#226ca8] bg-[#226ca8] text-white shadow-[0_6px_14px_rgba(34,108,168,0.2)]"
                        : "border-[#dce5ef] bg-[#f7f9fc] text-[#30465b] hover:border-[#8eb6d2] hover:bg-[#edf6fb]"
                    }`}
                  >
                    <button type="button" onClick={() => selectDomain(domain)} aria-pressed={active} className="flex min-h-[72px] min-w-0 flex-1 items-center gap-3 px-5 py-3 pr-20 text-left">
                      <DomainIcon label={domain.label} className="size-5 shrink-0" />
                      <span className="truncate text-[17px] font-semibold">{domain.label}</span>
                    </button>
                    <span className={`absolute right-2 flex items-center gap-0.5 ${active ? "opacity-100" : "opacity-0 group-hover:opacity-100"}`}>
                      <button type="button" onClick={() => startDomainEditor(domain)} className="rounded p-1.5 hover:bg-black/10" title="编辑领域" aria-label={`编辑${domain.label}`}><Pencil className="size-3.5" /></button>
                      <button type="button" onClick={() => void removeDomain(domain)} className="rounded p-1.5 hover:bg-red-500/20" title="删除领域" aria-label={`删除${domain.label}`}><Trash2 className="size-3.5" /></button>
                    </span>
                  </div>
                );
              })}
            </div>
            <div className="flex min-h-0 h-[28%] shrink-0 flex-col border-t border-[#e4eaf1] pt-3">
              <div className="flex shrink-0 items-center justify-between px-2 text-[13px] font-semibold text-[#5e7484]">
                <span>子领域</span>
                <button type="button" onClick={() => startSubdomainEditor()} className="rounded-lg p-1 text-[#236ca8] hover:bg-[#edf6fb]" title="新增子领域" aria-label="新增子领域"><Plus className="size-4" /></button>
              </div>
              <div ref={subdomainScrollRef} onScroll={(event) => recordScroll("subdomains", event)} className="min-h-0 flex-1 overflow-y-auto pr-1">
                <button type="button" onClick={() => applySelection({
                  domainId: activeDomain.id,
                  subdomainId: "",
                  teamId: rankCandidateTeams(activeDomain.teams)[0]?.id ?? "",
                })} className={`mt-2 flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-[13px] ${!activeSubdomainId ? "bg-[#eef6fb] font-semibold text-[#236ca8]" : "text-[#607486] hover:bg-[#f5f8fb]"}`}><DomainIcon label={activeDomain.label} className="size-3.5 shrink-0" /><span>全部子领域</span></button>
                {activeDomain.subdomains.map((subdomain) => (
                  <div key={subdomain.id} className="group/sub relative mt-1 flex items-center">
                    <button type="button" onClick={() => applySelection({
                      domainId: activeDomain.id,
                      subdomainId: subdomain.id,
                      teamId: rankCandidateTeams(activeDomain.teams.filter(
                        (team) => team.subdomainId === subdomain.id,
                      ))[0]?.id ?? "",
                    })} className={`flex min-w-0 flex-1 items-center gap-2 rounded-lg px-3 py-2 pr-16 text-left text-[13px] ${activeSubdomainId === subdomain.id ? "bg-[#eef6fb] font-semibold text-[#236ca8]" : "text-[#607486] hover:bg-[#f5f8fb]"}`}><DomainIcon label={activeDomain.label} className="size-3.5 shrink-0" /><span className="block truncate">{subdomain.name}</span></button>
                    <span className="absolute right-1 flex opacity-0 group-hover/sub:opacity-100">
                      <button type="button" onClick={() => startSubdomainEditor(subdomain)} className="rounded p-1 text-[#607486] hover:bg-[#e8f0f6]" title="编辑子领域" aria-label={`编辑${subdomain.name}`}><Pencil className="size-3" /></button>
                      <button type="button" onClick={() => void removeSubdomain(subdomain)} className="rounded p-1 text-[#a46b73] hover:bg-red-50" title="删除子领域" aria-label={`删除${subdomain.name}`}><Trash2 className="size-3" /></button>
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </aside>

        <main className="flex min-h-[420px] min-w-0 flex-none flex-col rounded-xl border border-[#d1dce7] bg-white p-3.5 shadow-[0_4px_14px_rgba(27,64,96,0.05)] sm:p-4 md:p-5 lg:min-h-0 lg:flex-1">
          <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[#e6edf4] pb-4">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-[18px] font-semibold text-[#174f70] sm:text-[20px]">国内优势团队候选池</h2>
                <span className="rounded-full border border-[#c2d9eb] bg-[#eef7fd] px-2.5 py-1 text-[11px] font-semibold text-[#2c6a98]">
                  {visibleTeams.length} 支候选 · 重点 {priorityCount}
                </span>
              </div>
              <p className="mt-1 text-[12px] text-[var(--chat-text-muted)]">
                当前研判领域：{activeDomain.label} · {source.refreshed ? "刚刚完成 AI4S Daily 研判" : "展示已保存研判结果，点击右侧按钮更新"}
              </p>
            </div>
            <button type="button" onClick={() => void refreshTeams()} disabled={loading || syncing || !activeDomain.id || activeDomain.id === EMPTY_DOMAIN.id} className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-[#cbd8e5] px-3 py-1.5 text-[12px] font-medium text-[#5f7181] transition hover:border-[#6fa1c2] hover:text-[#23648f] disabled:cursor-not-allowed disabled:opacity-60" title="从 AI4S Daily 获取当前领域最新候选">
              {syncing ? <LoaderCircle className="size-3.5 animate-spin" /> : <RefreshCcw className="size-3.5" />}
              {syncing ? "研判中…" : "重新研判"}
            </button>
          </div>

          <div ref={teamScrollRef} onScroll={(event) => recordScroll("teams", event)} className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto pr-1 pt-4">
            <div className="hidden grid-cols-[1fr_112px_178px] items-center gap-3 border-b border-[#dfe8ef] px-3 py-2 text-[11px] font-semibold tracking-[0.06em] text-[#75899a] sm:grid">
              <span>优势团队</span>
              <span>AI / 科学</span>
              <span>关注与联系</span>
            </div>
            {syncing ? (
              <div
                className="flex min-h-[180px] items-center justify-center rounded-xl border border-[#c6dceb] bg-[#f5fbff] px-6 text-center"
                aria-live="polite"
                role="status"
              >
                <div className="flex items-center gap-3 text-left">
                  <LoaderCircle className="size-6 shrink-0 animate-spin text-[#2375b3]" aria-hidden="true" />
                  <div>
                    <p className="text-[14px] font-semibold text-[#23648f]">AI 正在研判中…</p>
                    <p className="mt-1 text-[12px] leading-5 text-[#71889a]">正在检索公开报告并整理优势团队候选，请稍候。</p>
                  </div>
                </div>
              </div>
            ) : visibleTeams.length ? visibleTeams.map((team, index) => {
              const selected = team.id === selectedTeam.id;
              const rank = String(index + 1).padStart(2, "0");
              return (
                <button
                  key={team.id}
                  type="button"
                  onClick={() => applySelection({
                    domainId: activeDomain.id,
                    subdomainId: activeSubdomainId,
                    teamId: team.id,
                  })}
                  aria-pressed={selected}
                  aria-label={`${rank} ${teamOrganization(team)} · ${teamDisplayName(team)}`}
                  className={`relative grid min-h-[68px] w-full gap-3 rounded-xl border px-3.5 py-2.5 text-left transition sm:grid-cols-[1fr_112px_178px] sm:items-center sm:px-4 ${
                    selected
                      ? "border-[#7daed1] bg-[#f2f8fd] shadow-[0_4px_12px_rgba(39,104,152,0.08)]"
                      : "border-[#e0e7ef] bg-white hover:border-[#a8c7de] hover:bg-[#f8fbfd]"
                  }`}
                >
                  {selected ? <span className="absolute inset-y-2 left-0 w-1 rounded-r-full bg-[#2375b3]" /> : null}
                  <span className="flex min-w-0 items-center gap-3">
                    <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-[#edf5fb] font-mono text-[12px] font-bold tracking-[0.04em] text-[#236ca8]" aria-hidden="true">
                      {rank}
                    </span>
                    <span className="min-w-0" aria-label={`${teamOrganization(team)} · ${teamDisplayName(team)}`}>
                      <span className="block break-words text-[15px] font-semibold text-[#274158] sm:truncate">
                        {teamOrganization(team)}
                      </span>
                      <span className="mt-1 block break-words text-[12px] font-medium text-[#6a8194] sm:truncate">
                        团队：{teamDisplayName(team)}
                      </span>
                      <span className="mt-0.5 block truncate text-[11px] text-[var(--chat-text-muted)] sm:hidden">
                        {team.dualJudgement}
                      </span>
                    </span>
                  </span>
                  <span className="hidden text-[14px] font-semibold text-[#2e668e] sm:block">
                    {team.aiLevel} / {team.scienceLevel}
                  </span>
                  <span className="flex items-center justify-between gap-2 sm:block">
                    <AttentionBadge level={team.attention} />
                    <span className={`ml-2 text-[11px] ${contactStyles[team.contact] ?? contactStyles.未接触}`}>{team.contact}</span>
                  </span>
                </button>
              );
            }) : (
              <div className="flex min-h-[180px] items-center justify-center rounded-xl border border-dashed border-[#cbd8e5] bg-[#fbfdff] px-6 text-center text-[13px] leading-6 text-[#778897]">
                {loading ? "正在读取已保存的研判结果…" : "当前没有检索到可核验的国内机构或团队，请点击“重新研判”从 AI4S Daily 获取最新证据"}
              </div>
            )}
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-[#e6edf4] pt-3 text-[12px] text-[var(--chat-text-muted)]">
            <span>关注等级与联系状态分别标注</span>
            <AttentionBadge level="重点关注" />
            <AttentionBadge level="持续关注" />
            <AttentionBadge level="待核实" />
          </div>
        </main>

        <aside className="flex min-h-[380px] w-full min-w-0 shrink-0 flex-col overflow-hidden rounded-xl border border-[#d1dce7] bg-white p-3.5 shadow-[0_4px_14px_rgba(27,64,96,0.05)] sm:p-4 md:p-5 lg:min-h-0 lg:w-[430px]">
          <div className="flex shrink-0 items-start justify-between gap-3 border-b border-[#e6edf4] pb-4">
            <div>
              <h2 className="text-[21px] font-semibold text-[#174f70]">团队画像与下一步</h2>
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
              disabled={selectedTeam.id === EMPTY_TEAM.id || teamDetailsSaving}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-[#236ca8] px-3 py-1.5 text-[11px] font-semibold text-white transition hover:bg-[#1b5d94] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {teamDetailsSaving ? <LoaderCircle className="size-3.5 animate-spin" /> : teamEditing ? <Save className="size-3.5" /> : <Pencil className="size-3.5" />}
              {teamEditing ? "保存" : "编辑"}
            </button>
          </div>

          <h3 className="shrink-0 border-b border-[#e6edf4] py-4 text-[#174f70]">
            <span className="block text-[19px] font-semibold">{teamOrganization(selectedTeam)}</span>
            <span className="mt-1 block text-[14px] font-medium text-[#6a8194]">团队：{teamDisplayName(selectedTeam)}</span>
          </h3>

          <div className="shrink-0 border-b border-[#e6edf4] py-3">
            <div className="mb-2 text-[12px] font-semibold tracking-wide text-[#71899a]">团队负责人</div>
            {selectedTeam.leader ? (
              <button
                type="button"
                onClick={() => openTeamDetail(selectedTeam.id)}
                className="flex min-w-0 w-full items-center gap-3 rounded-xl border border-[#d7e7f0] bg-[#f7fbfd] p-3 text-left transition hover:border-[#8bb8d2] hover:bg-[#f1f8fc]"
              >
                <span className="relative flex size-11 shrink-0 items-center justify-center overflow-hidden rounded-full bg-[#e4f1f8] font-semibold text-[#23668f]">
                  {selectedTeam.leader.name.slice(0, 1) || "人"}
                  {selectedTeam.leader.avatarUrl ? <img src={selectedTeam.leader.avatarUrl} alt="" className="absolute inset-0 h-full w-full object-cover" onError={(event) => { event.currentTarget.style.display = "none"; }} /> : null}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block break-words text-[14px] font-semibold text-[#24455d]">{selectedTeam.leader.name}</span>
                  <span className="mt-0.5 block break-words text-[11px] text-[#6a8194]">{[selectedTeam.leader.title, selectedTeam.leader.role].filter(Boolean).join(" · ") || "负责人信息已确认"}</span>
                  {selectedTeam.leader.researchDirection ? <span className="mt-1 block break-words text-[11px] leading-4 text-[#587284]">{selectedTeam.leader.researchDirection}</span> : null}
                </span>
                <ArrowRight className="size-4 shrink-0 text-[#3c7da1]" />
              </button>
            ) : (
              <button type="button" onClick={() => openTeamDetail(selectedTeam.id)} disabled={selectedTeam.id === EMPTY_TEAM.id} className="flex w-full items-center justify-between gap-3 rounded-xl border border-dashed border-[#cbdde8] px-3 py-3 text-left text-[12px] text-[#718797] hover:bg-[#f8fbfd]">
                <span>暂无可靠负责人资料，查看团队详情</span><ArrowRight className="size-4 shrink-0 text-[#3c7da1]" />
              </button>
            )}
          </div>

          <div ref={profileScrollRef} onScroll={(event) => recordScroll("profile", event)} className="min-h-0 flex-1 overflow-y-auto pr-1">
          <div className="shrink-0 border-b border-[#e6edf4] py-3">
            {teamEditing ? (
              <div className="grid grid-cols-2 gap-3">
                <label className="block text-[12px] font-semibold text-[#546b7d]">
                  关注等级
                  <select
                    value={teamDetailsDraft.attention}
                    onChange={(event) => setTeamDetailsDraft((current) => ({ ...current, attention: event.target.value }))}
                    className="mt-1.5 w-full rounded-lg border border-[#ccd9e4] bg-white px-2.5 py-2 text-[13px] font-medium text-[#304b61] outline-none focus:border-[#4c91bd]"
                    disabled={teamDetailsSaving || selectedTeam.id === EMPTY_TEAM.id}
                  >
                    <option value="重点关注">重点关注</option>
                    <option value="持续关注">持续关注</option>
                    <option value="一般关注">一般关注</option>
                    <option value="待核实">待核实</option>
                  </select>
                </label>
                <label className="block text-[12px] font-semibold text-[#546b7d]">
                  联系状态
                  <select
                    value={teamDetailsDraft.contact}
                    onChange={(event) => setTeamDetailsDraft((current) => ({ ...current, contact: event.target.value }))}
                    className="mt-1.5 w-full rounded-lg border border-[#ccd9e4] bg-white px-2.5 py-2 text-[13px] font-medium text-[#304b61] outline-none focus:border-[#4c91bd]"
                    disabled={teamDetailsSaving || selectedTeam.id === EMPTY_TEAM.id}
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
                  <div className="mb-1 text-[12px] font-semibold text-[#546b7d]">关注等级</div>
                  <AttentionBadge level={selectedTeam.attention} />
                </div>
                <div>
                  <div className="mb-1 text-[12px] font-semibold text-[#546b7d]">联系状态</div>
                  <span className={`text-[13px] font-semibold ${contactStyles[selectedTeam.contact] ?? contactStyles.未接触}`}>{selectedTeam.contact}</span>
                </div>
              </div>
            )}
          </div>

          <div className={`space-y-4 py-4 text-[14px] ${teamEditing ? "" : "space-y-3 py-3"}`}>
            <div className="flex items-start gap-4">
              <Flag className="mt-0.5 size-4 shrink-0 text-[#6b96b5]" />
              <div className="grid min-w-0 flex-1 grid-cols-[68px_1fr] gap-3">
                <div className="font-semibold text-[#546b7d]">核心方向</div>
                {teamEditing ? (
                  <textarea value={teamDetailsDraft.coreDirection} onChange={(event) => setTeamDetailsDraft((current) => ({ ...current, coreDirection: event.target.value }))} className="min-h-[58px] w-full resize-y rounded-lg border border-[#ccd9e4] bg-white px-2.5 py-2 text-[13px] font-medium leading-5 text-[#304b61] outline-none focus:border-[#4c91bd]" maxLength={500} />
                ) : <div className="font-semibold leading-5 text-[#304b61]">{selectedTeam.coreDirection}</div>}
              </div>
            </div>
            <div className="flex items-start gap-4">
              <CircleAlert className="mt-0.5 size-4 shrink-0 text-[#6b96b5]" />
              <div className="grid min-w-0 flex-1 grid-cols-[68px_1fr] gap-3">
                <div className="font-semibold text-[#546b7d]">双高判断</div>
                {teamEditing ? (
                  <input value={teamDetailsDraft.dualJudgement} onChange={(event) => setTeamDetailsDraft((current) => ({ ...current, dualJudgement: event.target.value }))} className="w-full rounded-lg border border-[#ccd9e4] bg-white px-2.5 py-2 text-[13px] font-medium text-[#304b61] outline-none focus:border-[#4c91bd]" maxLength={120} />
                ) : <div className="font-semibold text-[#304b61]">{selectedTeam.dualJudgement}</div>}
              </div>
            </div>
            <div className="flex items-start gap-4">
              <UsersRound className="mt-0.5 size-4 shrink-0 text-[#6b96b5]" />
              <div className="grid min-w-0 flex-1 grid-cols-[68px_1fr] gap-3">
                <div className="font-semibold text-[#546b7d]">联系记录</div>
                {teamEditing ? (
                  <textarea value={teamDetailsDraft.contactRecord} onChange={(event) => setTeamDetailsDraft((current) => ({ ...current, contactRecord: event.target.value }))} className="min-h-[52px] w-full resize-y rounded-lg border border-[#ccd9e4] bg-white px-2.5 py-2 text-[13px] font-medium leading-5 text-[#304b61] outline-none focus:border-[#4c91bd]" maxLength={255} />
                ) : <div className="font-semibold text-[#304b61]">{selectedTeam.contactRecord}</div>}
              </div>
            </div>
            <div className="flex items-start gap-4">
              <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-[#6b96b5]" />
              <div className="grid min-w-0 flex-1 grid-cols-[68px_1fr] gap-3">
                <div className="font-semibold text-[#546b7d]">内部评价</div>
                {teamEditing ? (
                  <textarea value={teamDetailsDraft.internalReview} onChange={(event) => setTeamDetailsDraft((current) => ({ ...current, internalReview: event.target.value }))} className="min-h-[62px] w-full resize-y rounded-lg border border-[#ccd9e4] bg-white px-2.5 py-2 text-[13px] font-medium leading-5 text-[#304b61] outline-none focus:border-[#4c91bd]" maxLength={500} />
                ) : <div className="font-semibold leading-5 text-[#304b61]">{selectedTeam.internalReview}</div>}
              </div>
            </div>
            <div className="flex items-start gap-4">
              <CalendarDays className="mt-0.5 size-4 shrink-0 text-[#6b96b5]" />
              <div className="grid min-w-0 flex-1 grid-cols-[68px_1fr] gap-3">
                <div className="font-semibold text-[#546b7d]">最近更新</div>
                {teamEditing ? (
                  <input value={teamDetailsDraft.recentUpdate} onChange={(event) => setTeamDetailsDraft((current) => ({ ...current, recentUpdate: event.target.value }))} className="w-full rounded-lg border border-[#ccd9e4] bg-white px-2.5 py-2 text-[13px] font-medium text-[#304b61] outline-none focus:border-[#4c91bd]" maxLength={64} />
                ) : <div className="font-semibold text-[#304b61]">{selectedTeam.recentUpdate}</div>}
              </div>
            </div>
          </div>

          <div className={`mt-3 rounded-xl border border-[#bcd5e8] bg-[#eef7fd] p-4 ${teamEditing ? "shrink-0" : "shrink-0"}`}>
            <div className="flex items-center gap-2 text-[15px] font-semibold text-[#23668f]">
              <Clock3 className="size-4" />
              下一步行动
            </div>
            {teamEditing ? (
              <textarea value={teamDetailsDraft.nextAction} onChange={(event) => setTeamDetailsDraft((current) => ({ ...current, nextAction: event.target.value }))} className="mt-2 min-h-[82px] w-full resize-y rounded-lg border border-[#bcd5e8] bg-white px-2.5 py-2 text-[13px] font-medium leading-5 text-[#294b65] outline-none focus:border-[#4c91bd]" maxLength={500} />
            ) : <p className="mt-2 text-[14px] font-semibold leading-6 text-[#294b65]">{selectedTeam.nextAction}</p>}
            <p className="mt-3 text-[11px] text-[#63829a]">
              数据来源：
              {selectedTeam.sourceUrls?.[0] || selectedTeam.source === "AI4S Daily" ? (
                <a
                  href={selectedTeam.sourceUrls?.[0] || "https://ai4s-frontier.github.io/AI4S-Daily-HTML"}
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
                  {selectedTeam.sourceUrls?.[1] || selectedTeam.sourceUrls?.[0] ? (
                    <a
                      href={selectedTeam.sourceUrls?.[1] || selectedTeam.sourceUrls?.[0]}
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
        </aside>
      </div>
      {error ? <div className="absolute bottom-4 left-1/2 z-20 max-w-[min(640px,calc(100%-2rem))] -translate-x-1/2 rounded-lg border border-[#f1c6c6] bg-[#fff5f5] px-4 py-2.5 text-[12px] text-[#b44747] shadow-lg">{error}</div> : null}
      {editor ? (
        <div className="fixed inset-0 z-30 flex items-center justify-center bg-[#12324a]/25 p-4 backdrop-blur-[2px]">
          <form onSubmit={saveEditor} className="w-full max-w-[440px] rounded-2xl border border-[#d1dce7] bg-white p-5 shadow-[0_18px_60px_rgba(23,63,94,0.2)]">
            <div className="flex items-center justify-between">
              <h2 className="text-[18px] font-semibold text-[#174f70]">{editor.id ? "编辑" : "新增"}{editor.kind === "domain" ? "领域" : "子领域"}</h2>
              <button type="button" onClick={() => setEditor(null)} className="rounded-lg p-1.5 text-[#6d7d8b] hover:bg-[#f1f5f8]" aria-label="关闭"><X className="size-4" /></button>
            </div>
            <label className="mt-5 block text-[12px] font-semibold text-[#5a7182]">名称<input autoFocus value={editor.name} onChange={(event) => setEditor({ ...editor, name: event.target.value })} className="mt-1.5 w-full rounded-lg border border-[#ccd9e4] px-3 py-2.5 text-[14px] text-[#274158] outline-none focus:border-[#4c91bd] disabled:cursor-not-allowed disabled:bg-[#f5f8fb]" maxLength={120} required disabled={saving} /></label>
            <label className="mt-4 block text-[12px] font-semibold text-[#5a7182]">说明<textarea value={editor.description} onChange={(event) => setEditor({ ...editor, description: event.target.value })} className="mt-1.5 min-h-[84px] w-full resize-y rounded-lg border border-[#ccd9e4] px-3 py-2.5 text-[14px] text-[#274158] outline-none focus:border-[#4c91bd] disabled:cursor-not-allowed disabled:bg-[#f5f8fb]" maxLength={255} disabled={saving} /></label>
            {!editor.id && saving ? (
              <div className="mt-4 flex items-center gap-3 rounded-xl border border-[#c6dceb] bg-[#f5fbff] px-3.5 py-3.5" role="status" aria-live="polite">
                <LoaderCircle className="size-5 shrink-0 animate-spin text-[#2375b3]" aria-hidden="true" />
                <div>
                  <p className="text-[13px] font-semibold text-[#23648f]">AI 正在研判中…</p>
                  <p className="mt-1 text-[12px] leading-5 text-[#71889a]">正在检索公开报告并整理优势团队候选，请稍候。</p>
                </div>
              </div>
            ) : null}
            <div className="mt-5 flex justify-end gap-2"><button type="button" onClick={() => setEditor(null)} disabled={saving} className="rounded-lg border border-[#d2dee8] px-4 py-2 text-[13px] text-[#607486] hover:bg-[#f5f8fb] disabled:cursor-not-allowed disabled:opacity-60">取消</button><button type="submit" disabled={saving} className="inline-flex items-center gap-1.5 rounded-lg bg-[#236ca8] px-4 py-2 text-[13px] font-semibold text-white hover:bg-[#1b5d94] disabled:opacity-60">{saving ? <LoaderCircle className="size-3.5 animate-spin" /> : <Save className="size-3.5" />}保存</button></div>
          </form>
        </div>
      ) : null}
    </div>
  );
}
