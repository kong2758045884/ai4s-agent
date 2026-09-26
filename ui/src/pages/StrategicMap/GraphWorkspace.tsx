import * as echarts from "echarts";
import {
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Layers3,
  ListFilter,
  LoaderCircle,
  MessageSquareText,
  Network,
  RotateCcw,
  ScanSearch,
  Search,
  Send,
  X,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";

import {
  chatWithStrategicGraph,
  loadLatestStrategicDomainRefresh,
  loadStrategicGraph,
  loadVerifiedStrategicGraph,
  loadStrategicGraphScan,
  loadStrategicGraphScans,
  loadStrategicGraphStatus,
  loadStrategicDomainRefresh,
  searchStrategicGraph,
  startStrategicDomainRefresh,
  startStrategicGraphScan,
  type StrategicGraphCluster,
  type StrategicGraphData,
  type StrategicGraphElement,
  type StrategicGraphScanCandidate,
  type StrategicGraphScanTask,
  type StrategicGraphScope,
  type StrategicGraphSearchResult,
  type StrategicGraphStatus,
  type StrategicRefreshTask,
} from "@/services/strategicMap";
import { aggregateGraphData } from "./graphAggregation";
import { fusionApi } from "@/services/researchFusion";

type Props = {
  integrated?: boolean;
  verifiedOnly?: boolean;
  domainId: string;
  domainName: string;
  subdomainId: string;
  subdomainName: string;
  taxonomyRevision?: number;
  onDataUpdated?: () => Promise<void>;
  onShowTeams?: () => void;
};

type ChatEntry = {
  id: number;
  role: "user" | "assistant";
  text: string;
};

type SearchSort = "relevance" | "degree" | "evidence" | "name";
type ScanSort =
  | "rank"
  | "total"
  | "achievementQuality"
  | "domainRelevance"
  | "recentActivity"
  | "evidenceReliability"
  | "graphInfluence"
  | "teamCompleteness"
  | "aiEvidenceReview";
type SidePanel = "search" | "chat" | "scan" | null;

const EMPTY_GRAPH: StrategicGraphData = {
  nodes: [],
  edges: [],
  provider: "Hyper-Extract",
  searchResults: [],
  meta: { stats: {} },
};

const NODE_COLORS: Record<string, string> = {
  领域大类: "#5b35d5",
  领域方向: "#7147eb",
  子领域: "#8a63f2",
  科研团队: "#6c42e6",
  机构: "#7b50ec",
  作者: "#9068f3",
  事件: "#8057e8",
  证据: "#9b76ef",
  聚合: "#2d2540",
};

function rawOf(element: StrategicGraphElement | null): Record<string, unknown> {
  return element?.data?.raw ?? {};
}

function labelOf(element: StrategicGraphElement): string {
  return (
    String(element.data?.raw?.name ?? "").trim() ||
    String(element.data?.label ?? element.label ?? element.id)
  );
}

function levelOf(element: StrategicGraphElement): string {
  return String(rawOf(element).level || element.type || "节点");
}

function scoreText(value: number | null) {
  if (value == null) return "—";
  return value.toFixed(1).replace(/\.0$/, "");
}

function sortSearchResults(
  rows: StrategicGraphSearchResult[],
  mode: SearchSort,
): StrategicGraphSearchResult[] {
  return [...rows].sort((left, right) => {
    if (mode === "name") return left.label.localeCompare(right.label, "zh-CN");
    if (mode === "degree") return right.degree - left.degree;
    if (mode === "evidence") return right.evidenceScore - left.evidenceScore;
    return right.relevance - left.relevance;
  });
}

function sortScanResults(
  rows: StrategicGraphScanCandidate[],
  mode: ScanSort,
): StrategicGraphScanCandidate[] {
  if (mode === "rank")
    return [...rows].sort((left, right) => left.rank - right.rank);
  if (mode === "total") {
    return [...rows].sort(
      (left, right) =>
        Number(right.total ?? Number.NEGATIVE_INFINITY) -
        Number(left.total ?? Number.NEGATIVE_INFINITY),
    );
  }
  return [...rows].sort(
    (left, right) =>
      Number(right.scoreBreakdown?.[mode] ?? Number.NEGATIVE_INFINITY) -
      Number(left.scoreBreakdown?.[mode] ?? Number.NEGATIVE_INFINITY),
  );
}

function scanTime(value: string): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
}

export default function GraphWorkspace({
  integrated = false,
  verifiedOnly = false,
  domainId,
  domainName,
  subdomainId,
  subdomainName,
  taxonomyRevision = 0,
  onDataUpdated,
  onShowTeams,
}: Props) {
  const chartNodeRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.EChartsType | null>(null);
  const graphLoadIdRef = useRef(0);
  const [scope, setScope] = useState<StrategicGraphScope>("domestic");
  const [cluster, setCluster] = useState<StrategicGraphCluster>(
    domainName === "科学通用底座" || domainName === "通用 AI" ? "高原" : "高峰",
  );
  const [viewMode, setViewMode] = useState<"nodes" | "edges">("nodes");
  const [sidePanel, setSidePanel] = useState<SidePanel>(null);
  const [data, setData] = useState<StrategicGraphData>(EMPTY_GRAPH);
  const [selected, setSelected] = useState<StrategicGraphElement | null>(null);
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState(subdomainName || domainName);
  const [searchSort, setSearchSort] = useState<SearchSort>("relevance");
  const [loading, setLoading] = useState(true);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");
  const [chatQuestion, setChatQuestion] = useState("");
  const [chatting, setChatting] = useState(false);
  const [chatEntries, setChatEntries] = useState<ChatEntry[]>([]);
  const [scanKeyword, setScanKeyword] = useState(subdomainName || domainName);
  const [scanLimit, setScanLimit] = useState(integrated ? 30 : 100);
  const [scanSort, setScanSort] = useState<ScanSort>("rank");
  const [scanTask, setScanTask] = useState<StrategicGraphScanTask | null>(null);
  const [scanHistory, setScanHistory] = useState<StrategicGraphScanTask[]>([]);
  const [expandedCandidate, setExpandedCandidate] = useState("");
  const [graphStatus, setGraphStatus] = useState<StrategicGraphStatus | null>(null);
  const [localRefreshTask, setLocalRefreshTask] = useState<StrategicRefreshTask | null>(null);
  const [localRefreshError, setLocalRefreshError] = useState("");
  const [localStarting, setLocalStarting] = useState(false);
  const [localPollFailures, setLocalPollFailures] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    void loadStrategicGraphStatus({ signal: controller.signal })
      .then(setGraphStatus)
      .catch(() => {
        if (!controller.signal.aborted) setGraphStatus(null);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!domainId || scope !== "domestic") {
      setLocalRefreshTask(null);
      return;
    }
    setLocalRefreshError("");
    setLocalPollFailures(0);
    const controller = new AbortController();
    void loadLatestStrategicDomainRefresh(domainId, {
      signal: controller.signal,
      subdomainId: subdomainId || undefined,
    })
      .then(setLocalRefreshTask)
      .catch(() => {
        if (!controller.signal.aborted) setLocalRefreshTask(null);
      });
    return () => controller.abort();
  }, [domainId, scope, subdomainId]);

  const context = useMemo(
    () => ({
      scope,
      cluster,
      domainId,
      subdomainId: subdomainId || undefined,
    }),
    [cluster, domainId, scope, subdomainId],
  );

  const refreshGraph = useCallback(
    async (signal?: AbortSignal) => {
      const requestId = ++graphLoadIdRef.current;
      setLoading(true);
      setError("");
      try {
        const next = await (integrated ? fusionApi.graph : verifiedOnly ? loadVerifiedStrategicGraph : loadStrategicGraph)(context, { signal });
        if (signal?.aborted || requestId !== graphLoadIdRef.current) return;
        setData(next);
        setSelected(null);
        setExpandedGroups(new Set());
      } catch (reason) {
        if (!signal?.aborted && requestId === graphLoadIdRef.current) {
          const message = reason instanceof Error ? reason.message : "请求失败";
          setError(message === "Not Found" ? "图谱接口暂不可用，请重试" : `图谱加载失败：${message}`);
        }
      } finally {
        if (!signal?.aborted && requestId === graphLoadIdRef.current) {
          setLoading(false);
        }
      }
    },
    [context, verifiedOnly, integrated],
  );

  useLayoutEffect(() => {
    const controller = new AbortController();
    // The previous branch must not remain visible while the new scoped graph
    // is loading, or after a taxonomy edit that does not change the URL.
    setData(EMPTY_GRAPH);
    setSelected(null);
    void refreshGraph(controller.signal);
    return () => {
      controller.abort();
      graphLoadIdRef.current += 1;
    };
  }, [refreshGraph, taxonomyRevision]);

  useEffect(() => {
    if (!localRefreshTask || localRefreshTask.terminal) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void loadStrategicDomainRefresh(localRefreshTask.taskId, {
        signal: controller.signal,
      })
        .then((next) => {
          if (controller.signal.aborted) return;
          setLocalPollFailures(0);
          setLocalRefreshTask(next);
          if (next.terminal) {
            void refreshGraph();
            void onDataUpdated?.().catch((reason) => {
              setLocalRefreshError(
                reason instanceof Error ? reason.message : "团队列表更新失败",
              );
            });
          }
        })
        .catch((reason) => {
          if (!controller.signal.aborted) {
            setLocalRefreshError(
              reason instanceof Error ? reason.message : "调查状态读取失败",
            );
            setLocalPollFailures((failures) => failures + 1);
          }
        });
    }, Math.min(10_000, 2_000 * (localPollFailures + 1)));
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [localPollFailures, localRefreshTask, onDataUpdated, refreshGraph]);

  useEffect(() => {
    const nextCluster =
      domainName === "科学通用底座" || domainName === "通用 AI"
        ? "高原"
        : "高峰";
    setCluster(nextCluster);
    setQuery(subdomainName || domainName);
    setScanKeyword(subdomainName || domainName);
  }, [domainName, subdomainName]);

  useEffect(() => {
    const controller = new AbortController();
    void loadStrategicGraphScans(
      {
        scope,
        domainId,
        subdomainId: subdomainId || undefined,
      },
      {
        signal: controller.signal,
        limit: 10,
      },
    )
      .then((tasks) => {
        setScanHistory(tasks);
        setScanTask(tasks[0] ?? null);
        setExpandedCandidate("");
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setScanHistory([]);
          setScanTask(null);
        }
      });
    return () => controller.abort();
  }, [domainId, scope, subdomainId]);

  const aggregated = useMemo(
    () => aggregateGraphData(data, expandedGroups, 50),
    [data, expandedGroups],
  );
  const displayData = aggregated.data;
  const sortedSearchResults = useMemo(
    () => sortSearchResults(data.searchResults, searchSort),
    [data.searchResults, searchSort],
  );
  const sortedScanResults = useMemo(
    () => sortScanResults((scanTask?.result?.candidates ?? []).map((item) => integrated
      ? { ...item, rank: item.legacyRank ?? item.rank, total: item.legacyScores?.total ?? null }
      : item), scanSort),
    [scanSort, scanTask?.result?.candidates, integrated],
  );

  const option = useMemo<echarts.EChartsOption>(() => {
    const nodes = displayData.nodes;
    const edges = displayData.edges;
    const categoryNames = Array.from(new Set(nodes.map(levelOf)));
    const showAllLabels = nodes.length <= 50;
    return {
      animationDuration: 560,
      animationEasingUpdate: "quinticInOut",
      backgroundColor: "#ffffff",
      tooltip: {
        trigger: "item",
        confine: true,
        backgroundColor: "rgba(255,255,255,.98)",
        borderColor: "#ded9ef",
        borderWidth: 1,
        extraCssText:
          "box-shadow:0 12px 34px rgba(52,40,89,.14);border-radius:10px;",
        textStyle: {
          color: "#282339",
          fontSize: 12,
        },
        formatter: (params: unknown) => {
          const item = params as {
            data?: { name?: string; description?: string; level?: string };
          };
          const escape = (value?: string) => String(value || "").replace(/[&<>"']/g,
            (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]!);
          return [
            `<strong>${escape(item.data?.name)}</strong>`,
            escape(item.data?.level),
            escape(item.data?.description),
          ]
            .filter(Boolean)
            .join("<br/>");
        },
      },
      series: [
        {
          type: "graph",
          layout: "force",
          roam: true,
          draggable: true,
          cursor: "pointer",
          categories: categoryNames.map((name) => ({
            name,
            itemStyle: { color: NODE_COLORS[name] || "#8057e8" },
          })),
          data: nodes.map((item) => {
            const raw = rawOf(item);
            const level = levelOf(item);
            const count = Number(raw.count || 0);
            const aggregate = level === "聚合";
            const label = labelOf(item);
            const important =
              showAllLabels ||
              aggregate ||
              Boolean(item.highlighted) ||
              ["领域大类", "领域方向", "子领域"].includes(level);
            return {
              id: item.id,
              name: label,
              description: String(raw.description || ""),
              level,
              category: Math.max(0, categoryNames.indexOf(level)),
              symbolSize: aggregate
                ? count >= 100
                  ? 88
                  : label.length >= 6
                    ? 76
                    : 64
                : level === "领域大类"
                  ? 48
                  : level === "领域方向"
                    ? 40
                    : level === "子领域"
                      ? 34
                      : 27,
              itemStyle: {
                color: NODE_COLORS[level] || "#8057e8",
                borderColor: "#ffffff",
                borderWidth: aggregate ? 4 : 2,
                shadowBlur: aggregate || item.highlighted ? 20 : 8,
                shadowColor: aggregate
                  ? "rgba(45,37,64,.28)"
                  : "rgba(109,70,222,.2)",
              },
              label: {
                show: important,
                position: aggregate ? "inside" : "right",
                color: aggregate ? "#ffffff" : "#312b43",
                fontSize: aggregate ? 12 : 11,
                fontWeight: aggregate ? 700 : 500,
                width: aggregate ? 72 : 150,
                overflow: "truncate",
              },
            };
          }),
          links: edges.map((item) => ({
            id: item.id,
            source: item.source,
            target: item.target,
            lineStyle: {
              color: item.type === "aggregate" ? "#8b75cc" : "#cbd0dd",
              width: viewMode === "edges" ? 1.6 : 1,
              opacity: viewMode === "edges" ? 0.88 : 0.64,
              curveness: 0.04,
            },
            label: {
              show: viewMode === "edges" && edges.length <= 80,
              formatter: String(item.data?.label || item.label || ""),
              color: "#7f7891",
              fontSize: 9,
            },
          })),
          edgeSymbol: ["none", "arrow"],
          edgeSymbolSize: 5,
          emphasis: {
            focus: "adjacency",
            scale: true,
          },
          force: {
            repulsion: nodes.length > 80 ? 180 : 310,
            edgeLength: [78, 170],
            gravity: 0.035,
            friction: 0.62,
          },
        },
      ],
    };
  }, [displayData.edges, displayData.nodes, viewMode]);

  const focusElement = useCallback(
    (id: string) => {
      const element =
        data.nodes.find((item) => item.id === id) ||
        data.edges.find((item) => item.id === id) ||
        null;
      setSelected(element);
      const index = displayData.nodes.findIndex((item) => item.id === id);
      if (index >= 0) {
        chartRef.current?.dispatchAction({
          type: "highlight",
          seriesIndex: 0,
          dataIndex: index,
        });
        chartRef.current?.dispatchAction({
          type: "showTip",
          seriesIndex: 0,
          dataIndex: index,
        });
      }
    },
    [data.edges, data.nodes, displayData.nodes],
  );

  useEffect(() => {
    if (!chartNodeRef.current) return;
    const chart = chartRef.current ?? echarts.init(chartNodeRef.current);
    chartRef.current = chart;
    chart.setOption(option, { notMerge: true });
    const onClick = (params: unknown) => {
      const event = params as {
        dataType?: string;
        data?: { id?: string } | null;
      };
      const id = String(event.data?.id || "");
      const element =
        event.dataType === "edge"
          ? displayData.edges.find((item) => item.id === id)
          : displayData.nodes.find((item) => item.id === id);
      const aggregateKey = String(rawOf(element ?? null).aggregateKey || "");
      if (aggregateKey) {
        setExpandedGroups((current) => new Set([...current, aggregateKey]));
        return;
      }
      setSelected(element ?? null);
    };
    chart.off("click");
    chart.on("click", onClick);
  }, [displayData.edges, displayData.nodes, option]);

  useEffect(() => {
    const node = chartNodeRef.current;
    if (!node) return;
    const observer = new ResizeObserver(() => chartRef.current?.resize());
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(
    () => () => {
      chartRef.current?.dispose();
      chartRef.current = null;
    },
    [],
  );

  useEffect(() => {
    if (!scanTask?.jobId || scanTask.status !== "running") return;
    const controller = new AbortController();
    const timer = window.setInterval(async () => {
      try {
        const next = await loadStrategicGraphScan(scanTask.jobId, {signal: controller.signal,});
        setScanTask(next);
        setScanHistory((current) => [
          next,
          ...current.filter((item) => item.jobId !== next.jobId),
        ]);
        if (next.status !== "running") window.clearInterval(timer);
      } catch (reason) {
        if (!controller.signal.aborted) {
          setScanTask((current) =>
            current
              ? {
                ...current,
                status: "error",
                error:
                    reason instanceof Error
                      ? reason.message
                      : "扫描状态读取失败",
              }
              : current,
          );
          window.clearInterval(timer);
        }
      }
    }, 1500);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [scanTask?.jobId, scanTask?.status]);

  const submitSearch = async (event: FormEvent) => {
    event.preventDefault();
    if (!query.trim()) return;
    if (verifiedOnly || integrated) {
      const phrase = query.trim().toLocaleLowerCase();
      const hits = data.nodes.filter((node) =>
        `${node.label || ""} ${JSON.stringify(rawOf(node))}`.toLocaleLowerCase().includes(phrase));
      const ids = new Set(hits.map((node) => node.id));
      setData({ ...data, nodes: data.nodes.map((node) => ({ ...node, highlighted: ids.has(node.id) })),
        searchResults: hits.map((node) => ({ id: node.id, label: node.label || node.id,
          level: String(rawOf(node).level || ""), description: String(rawOf(node).description || ""),
          relevance: 1, degree: data.edges.filter((edge) => edge.source === node.id || edge.target === node.id).length,
          evidenceScore: 1, highlighted: true })) });
      setSelected(hits[0] || null);
      setSidePanel("search");
      return;
    }
    setSearching(true);
    setError("");
    try {
      const next = await searchStrategicGraph(query, context);
      setData(next);
      setExpandedGroups(new Set());
      setSelected(next.nodes.find((item) => item.highlighted) ?? null);
      setSidePanel("search");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "搜索失败");
    } finally {
      setSearching(false);
    }
  };

  const submitChat = async (event: FormEvent) => {
    event.preventDefault();
    const question = chatQuestion.trim();
    if (!question || chatting) return;
    const id = Date.now();
    setChatEntries((current) => [
      ...current,
      {
        id,
        role: "user",
        text: question,
      },
    ]);
    setChatQuestion("");
    setChatting(true);
    try {
      const result = await chatWithStrategicGraph(question, context);
      setChatEntries((current) => [
        ...current,
        {
          id: id + 1,
          role: "assistant",
          text: result.response || "图谱中暂无可回答的信息。",
        },
      ]);
      if (result.data?.nodes.length) {
        setData(result.data);
        setExpandedGroups(new Set());
      }
    } catch (reason) {
      setChatEntries((current) => [
        ...current,
        {
          id: id + 1,
          role: "assistant",
          text: reason instanceof Error ? reason.message : "对话失败",
        },
      ]);
    } finally {
      setChatting(false);
    }
  };

  const startScan = async () => {
    if (!scanKeyword.trim() || !graphStatus?.features.scan) return;
    setExpandedCandidate("");
    try {
      const task = await startStrategicGraphScan(
        scanKeyword,
        {
          scope,
          domainId,
          subdomainId: subdomainId || undefined,
        },
        scanLimit,
      );
      setScanTask(task);
      setScanHistory((current) => [
        task,
        ...current.filter((item) => item.jobId !== task.jobId),
      ]);
      setSidePanel("scan");
    } catch (reason) {
      setScanTask({
        jobId: "",
        keyword: scanKeyword,
        scope,
        domainId,
        subdomainId: subdomainId || undefined,
        maxCandidates: scanLimit,
        status: "error",
        stage: "",
        progress: {
          done: 0,
          total: 0,
        },
        result: null,
        error: reason instanceof Error ? reason.message : "扫描启动失败",
        persistent: false,
        createdAt: "",
        updatedAt: "",
        finishedAt: "",
      });
    }
  };

  const startLocalRefresh = async () => {
    if (
      !domainId ||
      scope !== "domestic" ||
      !graphStatus ||
      localStarting ||
      (localRefreshTask && !localRefreshTask.terminal)
    ) return;
    setLocalRefreshError("");
    setLocalStarting(true);
    try {
      setLocalRefreshTask(
        await startStrategicDomainRefresh(domainId, {
          subdomainId: subdomainId || undefined,
        }),
      );
    } catch (reason) {
      setLocalRefreshError(
        reason instanceof Error ? reason.message : "定向调查启动失败",
      );
    } finally {
      setLocalStarting(false);
    }
  };

  const resetGraph = () => {
    setSidePanel(null);
    setExpandedGroups(new Set());
    setQuery(subdomainName || domainName);
    void refreshGraph();
  };

  const sourceStats = data.meta.stats ?? {};
  const sourceNodeCount = Number(sourceStats.Nodes || data.nodes.length || 0);
  const sourceEdgeCount = Number(sourceStats.Edges || data.edges.length || 0);
  const averageDegree = sourceNodeCount
    ? ((sourceEdgeCount * 2) / sourceNodeCount).toFixed(1)
    : "0";
  const selectedRaw = rawOf(selected);

  return (
    <section data-node-count={sourceNodeCount} data-edge-count={sourceEdgeCount} aria-busy={loading} className="strategic-graph-workspace relative min-h-[640px] min-w-0 flex-1 overflow-hidden bg-[#ffffff] lg:min-h-0">
      <div
        ref={chartNodeRef}
        className="absolute inset-0"
        aria-label="AI4S 知识关系图谱"
      />

      <div className="strategic-graph-stats absolute left-6 top-6 z-[2] w-[272px] rounded-[38px] border border-[#eceaf3] bg-white/95 px-7 py-6 shadow-[0_18px_48px_rgba(59,47,91,.08)] backdrop-blur">
        <div className="text-[10px] font-bold tracking-[0.28em] text-[#8993ad]">
          STATISTICS
        </div>
        <div className="mt-6 grid grid-cols-2 gap-x-7 gap-y-7">
          {[
            [sourceNodeCount, "NODES"],
            [sourceEdgeCount, "EDGES"],
            [averageDegree, "AVERAGE NODE DEGREE"],
            [aggregated.collapsedGroups.length, "AGGREGATES"],
          ].map(([value, label]) => (
            <div key={String(label)}>
              <div className="text-[28px] font-bold leading-none text-[#1f2638]">
                {value}
              </div>
              <div className="mt-3 text-[9px] font-bold leading-4 tracking-[0.22em] text-[#8993ad]">
                {label}
              </div>
            </div>
          ))}
        </div>
        {!verifiedOnly && graphStatus?.provider === "Hyper-Extract snapshot" ? (
          <p className="mt-5 border-t border-[#eeecf4] pt-3 text-[11px] leading-5 text-[#786334]">
            Hyper 图谱底图为历史快照
            {graphStatus.snapshotUpdatedAt?.[scope]
              ? ` · 更新至 ${graphStatus.snapshotUpdatedAt[scope].slice(0, 10)}`
              : " · 更新时间未知"}
            ；不代表实时扫描结果。
          </p>
        ) : null}
        {aggregated.hiddenNodeCount ? (
          <button
            type="button"
            onClick={() => setExpandedGroups(new Set())}
            className="mt-6 flex w-full items-center justify-between border-t border-[#eeecf4] pt-4 text-xs font-semibold text-[#6e54bd]"
          >
            <span>已聚合 {aggregated.hiddenNodeCount} 个节点</span>
            <Layers3 className="size-4" />
          </button>
        ) : expandedGroups.size ? (
          <button
            type="button"
            onClick={() => setExpandedGroups(new Set())}
            className="mt-6 flex w-full items-center justify-between border-t border-[#eeecf4] pt-4 text-xs font-semibold text-[#6e54bd]"
          >
            <span>收起已展开节点</span>
            <RotateCcw className="size-4" />
          </button>
        ) : null}
      </div>

      <div className="strategic-graph-mode absolute left-1/2 top-5 z-[3] flex -translate-x-1/2 items-center rounded-full border border-[#efedf5] bg-white/95 p-1 shadow-[0_12px_36px_rgba(55,45,82,.08)] backdrop-blur">
        <button
          type="button"
          onClick={() => setViewMode("nodes")}
          aria-pressed={viewMode === "nodes"}
          className={`flex h-10 items-center gap-2 rounded-full px-5 text-[11px] font-bold tracking-[0.18em] ${
            viewMode === "nodes"
              ? "bg-[#f1f5f9] text-[#1d4ed8]"
              : "text-[#8c94a8]"
          }`}
        >
          <ListFilter className="size-4" />
          NODES
        </button>
        <button
          type="button"
          onClick={() => setViewMode("edges")}
          aria-pressed={viewMode === "edges"}
          className={`flex h-10 items-center gap-2 rounded-full px-5 text-[11px] font-bold tracking-[0.18em] ${
            viewMode === "edges"
              ? "bg-[#f1f5f9] text-[#1d4ed8]"
              : "text-[#8c94a8]"
          }`}
        >
          <Network className="size-4" />
          EDGES
        </button>
      </div>

      <div className="strategic-graph-switches absolute right-5 top-5 z-[4] flex flex-col items-end gap-2">
        <div className="flex gap-2">
          {(["domestic", "international"] as const).filter((value) => !verifiedOnly || value === "domestic").map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={scope === value}
              onClick={() => setScope(value)}
              className={`h-9 rounded-full border px-4 text-xs font-semibold shadow-sm ${
                scope === value
                  ? "border-[#2563eb] bg-[#2563eb] text-white"
                  : "border-[#d8dbe6] bg-white/95 text-[#667085]"
              }`}
            >
              {value === "domestic" ? "国内" : "国外"}
            </button>
          ))}
        </div>
        {!verifiedOnly && <div className="flex gap-2">
          {(["高峰", "高原"] as const).map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={cluster === value}
              onClick={() => setCluster(value)}
              className={`h-9 rounded-full border px-4 text-xs font-semibold shadow-sm ${
                cluster === value
                  ? "border-[#273148] bg-[#273148] text-white"
                  : "border-[#d8dbe6] bg-white/95 text-[#667085]"
              }`}
            >
              {value}
            </button>
          ))}
        </div>}
        {!verifiedOnly && <button
          type="button"
          onClick={() => setSidePanel(sidePanel === "scan" ? null : "scan")}
          aria-pressed={sidePanel === "scan"}
          className={`h-9 rounded-full border px-4 text-xs font-semibold shadow-sm ${
            sidePanel === "scan"
              ? "border-[#2563eb] bg-[#2563eb] text-white"
              : "border-[#d8dbe6] bg-white/95 text-[#667085]"
          }`}
        >
          扫描
        </button>}
      </div>

      <div className="strategic-graph-rail absolute right-5 top-1/2 z-[4] flex -translate-y-1/2 flex-col items-center gap-1 rounded-[28px] border border-[#efedf5] bg-white/95 p-2 shadow-[0_16px_44px_rgba(55,45,82,.1)] backdrop-blur">
        {[
          {
            mode: "search" as const,
            icon: Search,
            label: "搜索",
          },
          {
            mode: "chat" as const,
            icon: MessageSquareText,
            label: "图谱对话",
          },
        ].filter(({ mode }) => (!verifiedOnly && !integrated) || mode === "search").map(({ mode, icon: Icon, label }) => (
          <button
            key={mode}
            type="button"
            onClick={() => setSidePanel(sidePanel === mode ? null : mode)}
            aria-label={label}
            title={label}
            aria-pressed={sidePanel === mode}
            className={`flex size-11 items-center justify-center rounded-full ${
              sidePanel === mode
                ? "bg-[#eff6ff] text-[#2563eb]"
                : "text-[#8790a5] hover:bg-[#f8fafc]"
            }`}
          >
            <Icon className="size-5" />
          </button>
        ))}
        <span className="my-1 h-px w-7 bg-[#eceaf2]" />
        <button
          type="button"
          onClick={resetGraph}
          aria-label="重置图谱"
          title="重置图谱"
          className="flex size-11 items-center justify-center rounded-full text-[#a4adbf] hover:bg-[#f8fafc] hover:text-[#2563eb]"
        >
          <RotateCcw className="size-5" />
        </button>
      </div>

      {sidePanel ? (
        <aside className="strategic-graph-panel absolute bottom-5 right-[86px] top-[88px] z-[5] flex w-[380px] flex-col overflow-hidden rounded-[28px] border border-[#ebe8f2] bg-white/98 shadow-[0_24px_64px_rgba(52,40,89,.16)] backdrop-blur">
          <header className="flex h-16 shrink-0 items-center justify-between border-b border-[#efedf4] px-5">
            <div className="flex items-center gap-2 text-sm font-semibold text-[#312b43]">
              {sidePanel === "search" ? (
                <Search className="size-4 text-[#2563eb]" />
              ) : sidePanel === "chat" ? (
                <MessageSquareText className="size-4 text-[#2563eb]" />
              ) : (
                <ScanSearch className="size-4 text-[#2563eb]" />
              )}
              {sidePanel === "search"
                ? "搜索与排序"
                : sidePanel === "chat"
                  ? "图谱对话"
                  : integrated ? "机构与作者专题扫描" : "优势扫描"}
            </div>
            <button
              type="button"
              onClick={() => setSidePanel(null)}
              aria-label="关闭面板"
              className="flex size-8 items-center justify-center rounded-full text-[#8c94a8] hover:bg-[#f1f5f9]"
            >
              <X className="size-4" />
            </button>
          </header>

          {sidePanel === "search" ? (
            <>
              <form
                onSubmit={submitSearch}
                className="flex shrink-0 gap-2 border-b border-[#efedf4] p-4"
              >
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  aria-label="搜索图谱"
                  placeholder="搜索节点、关系或方向"
                  className="h-10 min-w-0 flex-1 rounded-xl border border-[#ddd9e9] px-3 text-sm outline-none focus:border-[#2563eb]"
                />
                <button
                  type="submit"
                  disabled={searching || !query.trim()}
                  aria-label="执行搜索"
                  className="flex size-10 items-center justify-center rounded-xl bg-[#2563eb] text-white disabled:opacity-50"
                >
                  {searching ? (
                    <LoaderCircle className="size-4 animate-spin" />
                  ) : (
                    <Search className="size-4" />
                  )}
                </button>
              </form>
              <div className="flex shrink-0 items-center justify-between px-4 py-3">
                <span className="text-xs text-[#8991a3]">
                  {sortedSearchResults.length} 条结果
                </span>
                <select
                  value={searchSort}
                  onChange={(event) =>
                    setSearchSort(event.target.value as SearchSort)
                  }
                  aria-label="搜索结果排序"
                  className="h-8 rounded-lg border border-[#e0ddea] bg-white px-2 text-xs text-[#5f6678]"
                >
                  <option value="relevance">相关度</option>
                  <option value="degree">影响力</option>
                  <option value="evidence">证据评分</option>
                  <option value="name">名称</option>
                </select>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-4">
                {sortedSearchResults.length ? (
                  sortedSearchResults.map((item, index) => (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => focusElement(item.id)}
                      className="flex w-full gap-3 border-b border-[#f0eef5] px-2 py-3 text-left hover:bg-[#fafafa]"
                    >
                      <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-[#eff6ff] text-[11px] font-bold text-[#2563eb]">
                        {index + 1}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13px] font-semibold text-[#373044]">
                          {item.label}
                        </span>
                        <span className="mt-1 block text-[11px] text-[#8b91a0]">
                          {item.level} · 相关度 {item.relevance} · 影响力{" "}
                          {item.degree}
                        </span>
                      </span>
                    </button>
                  ))
                ) : (
                  <div className="px-4 py-12 text-center text-xs text-[#959cad]">
                    输入关键词开始搜索
                  </div>
                )}
              </div>
            </>
          ) : null}

          {sidePanel === "chat" ? (
            <>
              <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
                {chatEntries.length ? (
                  chatEntries.map((entry) => (
                    <div
                      key={entry.id}
                      className={`max-w-[90%] rounded-2xl px-3 py-2 text-xs leading-5 ${
                        entry.role === "user"
                          ? "ml-auto bg-[#2563eb] text-white"
                          : "bg-[#f1f5f9] text-[#514a62]"
                      }`}
                    >
                      {entry.text}
                    </div>
                  ))
                ) : (
                  <div className="py-12 text-center text-xs text-[#959cad]">
                    {subdomainName || domainName}
                  </div>
                )}
              </div>
              <form
                onSubmit={submitChat}
                className="flex shrink-0 gap-2 border-t border-[#efedf4] p-4"
              >
                <input
                  value={chatQuestion}
                  onChange={(event) => setChatQuestion(event.target.value)}
                  aria-label="图谱对话问题"
                  placeholder="向图谱提问"
                  className="h-10 min-w-0 flex-1 rounded-xl border border-[#ddd9e9] px-3 text-sm outline-none focus:border-[#2563eb]"
                />
                <button
                  type="submit"
                  disabled={chatting || !chatQuestion.trim()}
                  aria-label="发送问题"
                  className="flex size-10 items-center justify-center rounded-xl bg-[#2563eb] text-white disabled:opacity-50"
                >
                  {chatting ? (
                    <LoaderCircle className="size-4 animate-spin" />
                  ) : (
                    <Send className="size-4" />
                  )}
                </button>
              </form>
            </>
          ) : null}

          {sidePanel === "scan" ? (
            <>
              {integrated && <p className="border-b border-slate-100 px-4 py-3 text-xs leading-5 text-slate-500">扫描机构与作者的来源图谱，展示独立的三维研判，不作为团队入榜资格。点击开始后调用模型；打开页面不会自动扫描。</p>}
              {!graphStatus?.features.scan ? (
                <div className="mx-4 mt-4 rounded-xl border border-[#e9dfb9] bg-[#fffbeb] px-3 py-3 text-xs leading-5 text-[#795c26]">
                  <p>
                    {graphStatus
                      ? "Hyper-Extract 实时扫描服务尚未连接。可使用本系统的公开来源定向调查，结果写入当前领域或子领域的团队与关系节点；这不是 Hyper 实时扫描。"
                      : "正在检查实时扫描服务状态；图谱浏览和搜索不受影响。"}
                  </p>
                  {graphStatus ? (
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        onClick={() => void startLocalRefresh()}
                        disabled={
                          scope !== "domestic" ||
                          localStarting ||
                          Boolean(localRefreshTask && !localRefreshTask.terminal)
                        }
                        className="rounded-lg bg-[#2563eb] px-3 py-1.5 font-semibold text-white disabled:opacity-50"
                      >
                        {localStarting
                          ? "正在启动"
                          : localRefreshTask && !localRefreshTask.terminal
                          ? "定向调查进行中"
                          : subdomainId
                            ? "调查当前子领域"
                            : "调查当前领域"}
                      </button>
                      {scope !== "domestic" ? (
                        <span>当前本地调查仅支持国内团队。</span>
                      ) : null}
                      {localRefreshTask?.terminal && onShowTeams ? (
                        <button
                          type="button"
                          onClick={onShowTeams}
                          className="rounded-lg border border-[#d5caed] bg-white px-3 py-1.5 font-semibold text-[#2563eb]"
                        >
                          查看团队
                        </button>
                      ) : null}
                    </div>
                  ) : null}
                  {localRefreshTask ? (
                    <p className="mt-2">
                      {localRefreshTask.message || localRefreshTask.state}
                      {localRefreshTask.state === "succeeded"
                        ? " · 图谱与团队展示已更新"
                        : localRefreshTask.state === "partial" ||
                            localRefreshTask.state === "timed_out"
                          ? " · 已完成的增量结果仍可查看"
                          : ""}
                    </p>
                  ) : null}
                  {localRefreshError ? (
                    <p className="mt-2 text-[#a54838]">{localRefreshError}</p>
                  ) : null}
                </div>
              ) : null}
              {graphStatus?.features.scan ? (
              <div className="flex shrink-0 gap-2 border-b border-[#efedf4] p-4">
                <input
                  value={scanKeyword}
                  onChange={(event) => setScanKeyword(event.target.value)}
                  aria-label={integrated ? "机构与作者扫描关键词" : "优势团队扫描关键词"}
                  placeholder="领域关键词"
                  className="h-10 min-w-0 flex-1 rounded-xl border border-[#ddd9e9] px-3 text-sm outline-none focus:border-[#2563eb]"
                />
                <select
                  value={scanLimit}
                  onChange={(event) => setScanLimit(Number(event.target.value))}
                  aria-label="扫描候选数量"
                  className="h-10 rounded-xl border border-[#ddd9e9] px-2 text-xs"
                >
                  {(integrated ? [5, 10, 30] : [30, 100, 200]).map((limit) => <option key={limit} value={limit}>{limit}</option>)}
                </select>
                <button
                  type="button"
                  onClick={() => void startScan()}
                  disabled={
                    scanTask?.status === "running" || !scanKeyword.trim() || !graphStatus?.features.scan
                  }
                  aria-label="开始扫描"
                  className="flex size-10 items-center justify-center rounded-xl bg-[#2563eb] text-white disabled:opacity-50"
                >
                  {scanTask?.status === "running" ? (
                    <LoaderCircle className="size-4 animate-spin" />
                  ) : (
                    <ScanSearch className="size-4" />
                  )}
                </button>
              </div>
              ) : null}
              {graphStatus?.features.scan || scanTask || scanHistory.length ? (
                <>
              {scanHistory.length ? (
                <div className="flex shrink-0 items-center gap-2 border-b border-[#f2f0f6] px-4 py-2">
                  <span className="shrink-0 text-[10px] font-semibold text-[#8b91a0]">
                    历史扫描
                  </span>
                  <select
                    value={scanTask?.jobId || ""}
                    onChange={(event) => {
                      const selectedTask = scanHistory.find(
                        (item) => item.jobId === event.target.value,
                      );
                      if (selectedTask) {
                        setScanTask(selectedTask);
                        setExpandedCandidate("");
                      }
                    }}
                    aria-label="历史扫描任务"
                    className="h-8 min-w-0 flex-1 rounded-lg border border-[#e0ddea] bg-white px-2 text-[11px] text-[#5f6678]"
                  >
                    {scanHistory.map((task) => (
                      <option key={task.jobId} value={task.jobId}>
                        {task.keyword} · {scanTime(task.createdAt) || "处理中"}
                      </option>
                    ))}
                  </select>
                </div>
              ) : null}
              <div className="flex shrink-0 items-start justify-between gap-3 px-4 py-3">
                <span className="min-w-0 flex-1 text-xs leading-5 text-[#8991a3]">
                  {scanTask?.status === "running"
                    ? `${scanTask.stage || "排队中"} ${scanTask.progress.total ? `${scanTask.progress.done}/${scanTask.progress.total}` : ""}`
                    : (!integrated && scanTask?.result?.scoringSummary) ||
                      scanTask?.result?.summary ||
                      "等待扫描"}
                  {scanTask?.persistent && scanTask.updatedAt ? (
                    <span className="block text-[10px] text-[#a0a5b2]">
                      已持久化 · {scanTime(scanTask.updatedAt)}
                    </span>
                  ) : null}
                </span>
                <select
                  value={scanSort}
                  onChange={(event) =>
                    setScanSort(event.target.value as ScanSort)
                  }
                  aria-label="扫描结果排序"
                  className="h-8 rounded-lg border border-[#e0ddea] bg-white px-2 text-xs text-[#5f6678]"
                >
                  <option value="rank">综合排名</option>
                  <option value="total">总分</option>
                  {!integrated && <>
                  <option value="achievementQuality">成果质量</option>
                  <option value="domainRelevance">领域相关</option>
                  <option value="recentActivity">近期活跃</option>
                  <option value="evidenceReliability">证据可靠</option>
                  <option value="graphInfluence">图谱影响</option>
                  <option value="teamCompleteness">团队完整</option>
                  <option value="aiEvidenceReview">AI 证据研判</option>
                  </>}
                </select>
              </div>
              {scanTask?.error ? (
                <div className="mx-4 flex items-center gap-2 rounded-xl bg-[#fff1ef] px-3 py-2 text-xs text-[#a54838]">
                  <CircleAlert className="size-4" />
                  {scanTask.error}
                </div>
              ) : null}
              <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-4">
                {sortedScanResults.map((candidate) => {
                  const open = expandedCandidate === candidate.name;
                  if (integrated) return <article key={candidate.name} className="border-b border-slate-100 px-2 py-3">
                    <button type="button" onClick={() => setExpandedCandidate(open ? "" : candidate.name)} className="flex w-full items-start gap-2 text-left">
                      <span className="text-xs font-semibold text-blue-600">{candidate.rank}</span><span className="min-w-0 flex-1"><strong className="text-sm text-slate-800">{candidate.name}</strong><span className="mt-1 block text-xs text-slate-500">{candidate.type} · {candidate.event_count} 条图谱事件</span></span><span className="text-sm font-bold text-blue-700">{scoreText(candidate.total)}</span>
                    </button>
                    {open && <div className="mt-3 space-y-2 text-xs leading-5 text-slate-600"><p>{candidate.comment}</p><p>成就 {scoreText(candidate.legacyScores?.achievement ?? null)} · 地位 {scoreText(candidate.legacyScores?.status ?? null)} · 趋势 {scoreText(candidate.legacyScores?.future ?? null)}</p><p>{candidate.reasons?.achievement}</p><p>{candidate.reasons?.status}</p><p>{candidate.reasons?.future}</p><p className="text-slate-400">来源扫描评分 · 与团队评分独立</p></div>}
                  </article>;
                  return (
                    <div
                      key={candidate.name}
                      className="border-b border-[#f0eef5]"
                    >
                      <button
                        type="button"
                        onClick={() =>
                          setExpandedCandidate(open ? "" : candidate.name)
                        }
                        className="grid w-full grid-cols-[28px_minmax(0,1fr)_48px] items-center gap-2 px-2 py-3 text-left"
                      >
                        <span className="text-[11px] font-bold text-[#2563eb]">
                          {candidate.rank}
                        </span>
                        <span className="min-w-0">
                          <span className="block truncate text-[13px] font-semibold text-[#373044]">
                            {candidate.name}
                          </span>
                          <span
                            className={`mt-1 block text-[10px] ${
                              candidate.eligibility?.eligible
                                ? "text-[#56806b]"
                                : "text-[#a36a58]"
                            }`}
                          >
                            {candidate.eligibility?.eligible
                              ? "准入通过"
                              : "证据不足"}{" "}
                            · 成果{" "}
                            {scoreText(
                              candidate.scoreBreakdown?.achievementQuality ??
                                null,
                            )}
                            /25 · 相关{" "}
                            {scoreText(
                              candidate.scoreBreakdown?.domainRelevance ?? null,
                            )}
                            /15
                          </span>
                        </span>
                        <span className="flex items-center justify-end gap-1 text-sm font-bold text-[#2563eb]">
                          {scoreText(candidate.total)}
                          {open ? (
                            <ChevronDown className="size-3" />
                          ) : (
                            <ChevronRight className="size-3" />
                          )}
                        </span>
                      </button>
                      {open ? (
                        <div className="space-y-2 bg-[#fafafa] px-10 py-3 text-[11px] leading-5 text-[#666071]">
                          <p>{candidate.comment}</p>
                          <div className="grid grid-cols-2 gap-x-3 border-y border-[#ece9f2] py-2">
                            <span>
                              成果质量{" "}
                              {scoreText(
                                candidate.scoreBreakdown?.achievementQuality ??
                                  null,
                              )}
                              /25
                            </span>
                            <span>
                              领域相关{" "}
                              {scoreText(
                                candidate.scoreBreakdown?.domainRelevance ??
                                  null,
                              )}
                              /15
                            </span>
                            <span>
                              近期活跃{" "}
                              {scoreText(
                                candidate.scoreBreakdown?.recentActivity ??
                                  null,
                              )}
                              /15
                            </span>
                            <span>
                              证据可靠{" "}
                              {scoreText(
                                candidate.scoreBreakdown?.evidenceReliability ??
                                  null,
                              )}
                              /10
                            </span>
                            <span>
                              图谱影响{" "}
                              {scoreText(
                                candidate.scoreBreakdown?.graphInfluence ??
                                  null,
                              )}
                              /10
                            </span>
                            <span>
                              团队完整{" "}
                              {scoreText(
                                candidate.scoreBreakdown?.teamCompleteness ??
                                  null,
                              )}
                              /5
                            </span>
                            <span className="col-span-2">
                              AI 证据研判{" "}
                              {scoreText(
                                candidate.scoreBreakdown?.aiEvidenceReview ??
                                  null,
                              )}
                              /20
                            </span>
                          </div>
                          {!candidate.eligibility?.eligible &&
                          candidate.eligibilityReasons ? (
                              <div className="text-[#95604f]">
                                {Object.entries(candidate.eligibility)
                                  .filter(
                                    ([key, passed]) =>
                                      key !== "eligible" && !passed,
                                  )
                                  .map(([key]) => (
                                    <p key={key}>
                                      {candidate.eligibilityReasons?.[key]}
                                    </p>
                                  ))}
                              </div>
                            ) : null}
                          <p>
                            图谱证据 {candidate.evidenceIds?.length || 0} 条 ·
                            评分版本 {candidate.scoreVersion || "—"}
                          </p>
                          {candidate.reasons ? (
                            <>
                              <p>
                                原始图谱研判：{candidate.reasons.achievement}
                              </p>
                              <p>{candidate.reasons.status}</p>
                              <p>{candidate.reasons.future}</p>
                            </>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </div>
                </>
              ) : null}
            </>
          ) : null}
        </aside>
      ) : null}

      {selected && !sidePanel ? (
        <aside className="strategic-graph-detail absolute bottom-6 right-[86px] z-[3] w-[360px] rounded-[24px] border border-[#ebe8f2] bg-white/96 p-5 shadow-[0_18px_48px_rgba(52,40,89,.14)] backdrop-blur">
          <button
            type="button"
            onClick={() => setSelected(null)}
            aria-label="关闭节点详情"
            className="absolute right-3 top-3 flex size-8 items-center justify-center rounded-full text-[#9299aa] hover:bg-[#f1f5f9]"
          >
            <X className="size-4" />
          </button>
          <div className="pr-8 text-sm font-semibold text-[#312b43]">
            {labelOf(selected)}
          </div>
          <div className="mt-1 text-[11px] font-semibold text-[#2563eb]">
            {levelOf(selected)}
          </div>
          {selectedRaw.description ? (
            <p className="mt-3 max-h-28 overflow-y-auto text-xs leading-5 text-[#666071]">
              {String(selectedRaw.description)}
            </p>
          ) : null}
          {typeof selectedRaw.url === "string" && /^https?:\/\//i.test(selectedRaw.url) &&
            <a href={selectedRaw.url} target="_blank" rel="noreferrer" className="mt-3 inline-block text-xs font-semibold text-blue-600 hover:underline">查看原文依据 ↗</a>}
          {selectedRaw.score != null ? (
            <div className="mt-3 border-t border-[#efedf4] pt-3 text-xs text-[#7b8190]">
              证据评分
              <strong className="ml-2 text-lg text-[#2563eb]">
                {Number(selectedRaw.score).toFixed(1)}
              </strong>
            </div>
          ) : null}
        </aside>
      ) : null}

      {aggregated.collapsedGroups.length ? (
        <div className="strategic-graph-aggregate-hint absolute bottom-5 left-6 z-[2] flex max-w-[420px] flex-wrap gap-2">
          {aggregated.collapsedGroups.map((group) => (
            <button
              key={group.key}
              type="button"
              onClick={() =>
                setExpandedGroups((current) => new Set([...current, group.key]))
              }
              className="rounded-full border border-[#ddd7ef] bg-white/95 px-3 py-1.5 text-[11px] font-semibold text-[#1d4ed8] shadow-sm"
            >
              {group.label} {group.count >= 100 ? "100+" : group.count}
            </button>
          ))}
        </div>
      ) : null}

      {error ? (
        <div role="alert" className="absolute left-1/2 top-1/2 z-[8] flex w-[min(90%,400px)] -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-3 rounded-2xl border border-[#efc8c0] bg-white px-5 py-5 text-center text-sm text-[#a54838] shadow-lg">
          <CircleAlert className="size-4 shrink-0" />
          <span>{error}</span>
          <button type="button" onClick={() => void refreshGraph()} className="rounded-lg border border-slate-200 px-4 py-2 text-xs font-semibold text-blue-700 hover:bg-blue-50">重新加载图谱</button>
        </div>
      ) : null}
      {loading ? (
        <div className="absolute inset-0 z-[7] flex items-center justify-center bg-white/72 backdrop-blur-[1px]">
          <LoaderCircle className="size-8 animate-spin text-[#2563eb]" />
        </div>
      ) : null}
      {!loading && !error && !displayData.nodes.length ? (
        <div className="absolute inset-0 flex items-center justify-center text-sm text-[#969cad]">
          当前领域暂无匹配图谱节点
        </div>
      ) : null}
    </section>
  );
}
