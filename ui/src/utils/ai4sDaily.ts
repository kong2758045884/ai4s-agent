import { resolveTaskToolResultText } from "@/utils/chat/toolCalls";

export type Ai4sDailyHotspot = {
  title: string;
  content?: string;
  sourceUrls: string[];
};

export type Ai4sDailySection = {
  section: string;
  title?: string;
  content?: string;
  hotspots: Ai4sDailyHotspot[];
};

export type Ai4sDailyReport = {
  reportId: string;
  reportUrl?: string;
  title: string;
  date?: string;
  pushTime?: string;
  pushDate?: string;
  matched: boolean;
  sections: Ai4sDailySection[];
};

export type Ai4sDailyResult = {
  source: string;
  indexUrl?: string;
  matched: boolean;
  reports: Ai4sDailyReport[];
  nextAction?: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function asUrl(value: unknown): string | undefined {
  const text = asText(value);
  return /^https?:\/\//i.test(text) ? text : undefined;
}

function asStringArray(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map(asUrl).filter((item): item is string => Boolean(item));
  }
  const url = asUrl(value);
  return url ? [url] : [];
}

function parseJsonValue(value: unknown): unknown {
  let current = value;
  for (let depth = 0; depth < 3 && typeof current === "string"; depth += 1) {
    const text = current.trim();
    if (!text) return undefined;
    try {
      current = JSON.parse(text);
    } catch {
      return undefined;
    }
  }
  return current;
}

function unwrapResult(value: unknown): Record<string, unknown> | undefined {
  const parsed = parseJsonValue(value);
  if (!isRecord(parsed)) return undefined;

  // 兼容后端/历史回放可能包在 data 或 llmData 中的结果。
  if (isRecord(parsed.data) && (parsed.data.reports || parsed.data.tool === "ai4s_daily")) {
    return parsed.data;
  }
  if (isRecord(parsed.llmData) && (parsed.llmData.reports || parsed.llmData.tool === "ai4s_daily")) {
    return parsed.llmData;
  }
  if (isRecord(parsed.toolResult) && (parsed.toolResult.reports || parsed.toolResult.tool === "ai4s_daily")) {
    return parsed.toolResult;
  }
  return parsed;
}

function parseHotspot(value: unknown): Ai4sDailyHotspot | undefined {
  if (!isRecord(value)) return undefined;
  const title = asText(value.title) || asText(value.name);
  if (!title) return undefined;
  return {
    title,
    content: asText(value.content) || undefined,
    sourceUrls: [
      ...asStringArray(value.sourceUrls),
      ...asStringArray(value.source_urls),
      ...asStringArray(value.sourceUrl),
      ...asStringArray(value.source_url),
    ].filter((url, index, urls) => urls.indexOf(url) === index),
  };
}

function parseSection(value: unknown): Ai4sDailySection | undefined {
  if (!isRecord(value)) return undefined;
  const section = asText(value.section) || asText(value.name) || "document";
  const hotspots = Array.isArray(value.hotspots)
    ? value.hotspots.map(parseHotspot).filter((item): item is Ai4sDailyHotspot => Boolean(item))
    : [];
  return {
    section,
    title: asText(value.title) || undefined,
    content: asText(value.content) || undefined,
    hotspots,
  };
}

function parseReport(value: unknown): Ai4sDailyReport | undefined {
  if (!isRecord(value)) return undefined;
  const reportId = asText(value.reportId) || asText(value.id);
  const reportUrl = asUrl(value.reportUrl) || asUrl(value.report_url);
  const title = asText(value.title) || reportId;
  if (!reportId && !title) return undefined;

  const sections = Array.isArray(value.sections)
    ? value.sections.map(parseSection).filter((item): item is Ai4sDailySection => Boolean(item))
    : [];
  const matched =
    typeof value.matched === "boolean"
      ? value.matched
      : Number(value.matchScore) > 0 || sections.some((section) => section.hotspots.length > 0);

  return {
    reportId: reportId || title,
    reportUrl,
    title: title || reportId,
    date: asText(value.date) || undefined,
    pushTime: asText(value.pushTime) || undefined,
    pushDate: asText(value.pushDate) || undefined,
    matched,
    sections,
  };
}

/** 将 ai4s_daily 的 toolResult JSON 转为前端展示所需的最小来源模型。 */
export function parseAi4sDailyResult(value: unknown): Ai4sDailyResult | undefined {
  const root = unwrapResult(value);
  if (!root) return undefined;
  const tool = asText(root.tool).toLowerCase();
  const source = asText(root.source);
  if (tool && tool !== "ai4s_daily") {
    return undefined;
  }
  if (!tool && !/ai4s\s*daily/i.test(source)) {
    return undefined;
  }

  const reports = Array.isArray(root.reports)
    ? root.reports.map(parseReport).filter((item): item is Ai4sDailyReport => Boolean(item))
    : [];
  const matched =
    typeof root.matched === "boolean"
      ? root.matched
      : reports.some((report) => report.matched);

  return {
    source: source || "AI4S Daily",
    indexUrl: asUrl(root.indexUrl) || asUrl(root.index_url),
    matched,
    reports,
    nextAction: asText(root.nextAction) || asText(root.next_action) || undefined,
  };
}

export function resolveAi4sDailyResult(task: CHAT.Task): Ai4sDailyResult | undefined {
  return parseAi4sDailyResult(resolveTaskToolResultText(task));
}
