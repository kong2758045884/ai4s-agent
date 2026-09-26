import type {
  StrategicGraphData,
  StrategicGraphElement,
} from "@/services/strategicMap";

const CORE_LEVELS = new Set(["领域大类", "领域方向", "子领域"]);

function rawOf(item: StrategicGraphElement): Record<string, unknown> {
  return item.data?.raw ?? {};
}

function levelOf(item: StrategicGraphElement): string {
  return String(rawOf(item).level || item.type || "节点");
}

export type AggregatedGraphData = {
  data: StrategicGraphData;
  collapsedGroups: Array<{
    key: string;
    label: string;
    count: number;
  }>;
  hiddenNodeCount: number;
};

export function aggregateGraphData(
  source: StrategicGraphData,
  expandedGroups: ReadonlySet<string>,
  threshold = 50,
): AggregatedGraphData {
  if (source.nodes.length <= threshold) {
    return {
      data: source,
      collapsedGroups: [],
      hiddenNodeCount: 0
    };
  }

  const groups = new Map<string, StrategicGraphElement[]>();
  const subdomainAnchors = new Map(
    source.nodes
      .filter((node) => levelOf(node) === "子领域" && rawOf(node).subdomainId)
      .map((node) => [String(rawOf(node).subdomainId), node] as const),
  );
  const domainAnchor =
    source.nodes.find((node) => levelOf(node) === "领域方向")?.id ||
    source.nodes.find((node) => levelOf(node) === "领域大类")?.id ||
    source.nodes[0]?.id;
  const visibleNodes: StrategicGraphElement[] = [];
  for (const node of source.nodes) {
    const level = levelOf(node);
    if (CORE_LEVELS.has(level) || node.highlighted) {
      visibleNodes.push(node);
      continue;
    }
    const subdomainId = String(rawOf(node).subdomainId || "");
    const key = subdomainId && subdomainAnchors.has(subdomainId)
      ? `level:${level}:subdomain:${subdomainId}`
      : `level:${level}`;
    const group = groups.get(key) ?? [];
    group.push(node);
    groups.set(key, group);
  }

  const collapsedGroups: AggregatedGraphData["collapsedGroups"] = [];
  const hiddenIds = new Set<string>();
  const aggregateNodes: StrategicGraphElement[] = [];
  const aggregateEdges: StrategicGraphElement[] = [];
  for (const [key, members] of [...groups.entries()].sort(
    (left, right) => right[1].length - left[1].length,
  )) {
    if (members.length < 4 || expandedGroups.has(key)) {
      visibleNodes.push(...members);
      continue;
    }
    const level = levelOf(members[0]);
    const subdomainId = String(rawOf(members[0]).subdomainId || "");
    const subdomain = subdomainAnchors.get(subdomainId);
    const anchor = subdomain?.id || domainAnchor;
    const count = members.length;
    const countLabel = count >= 100 ? "100+" : String(count);
    const id = `aggregate:${key}`;
    members.forEach((member) => hiddenIds.add(member.id));
    collapsedGroups.push({
      key,
      label: subdomain ? `${String(rawOf(subdomain).name || subdomain.label || subdomain.id)} · ${level}` : level,
      count
    });
    aggregateNodes.push({
      id,
      label: `${level} ${countLabel}`,
      type: "aggregate",
      data: {
        label: `${level} ${countLabel}`,
        raw: {
          name: `${level} ${countLabel}`,
          level: "聚合",
          aggregateKey: key,
          aggregateLevel: level,
          count,
          memberIds: members.map((member) => member.id),
        },
      },
    });
    if (anchor) {
      aggregateEdges.push({
        id: `aggregate-edge:${key}`,
        source: id,
        target: anchor,
        type: "edge",
        data: {
          label: "聚合",
          raw: {
            source: id,
            target: anchor,
            type: "聚合"
          },
        },
      });
    }
  }

  if (!collapsedGroups.length) {
    return {
      data: source,
      collapsedGroups: [],
      hiddenNodeCount: 0
    };
  }

  const visibleIds = new Set([
    ...visibleNodes.map((node) => node.id),
    ...aggregateNodes.map((node) => node.id),
  ]);
  const edges = [
    ...source.edges.filter(
      (edge) =>
        edge.source &&
        edge.target &&
        visibleIds.has(edge.source) &&
        visibleIds.has(edge.target) &&
        !hiddenIds.has(edge.source) &&
        !hiddenIds.has(edge.target),
    ),
    ...aggregateEdges,
  ];
  const nodes = [...visibleNodes, ...aggregateNodes];

  return {
    data: {
      ...source,
      nodes,
      edges,
      meta: {
        ...source.meta,
        stats: {
          ...(source.meta.stats ?? {}),
          Nodes: nodes.length,
          Edges: edges.length,
        },
      },
    },
    collapsedGroups,
    hiddenNodeCount: hiddenIds.size,
  };
}
