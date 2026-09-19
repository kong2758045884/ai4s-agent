const DEFAULT_AI4S_DAILY_BASE_URL =
  "https://ai4s-frontier.github.io/AI4S-Daily-HTML";
const REPORTS_PATH = "/reports";
const CACHE_KEY = "ai4s.ai4s-daily.home.v3";
const BEIJING_TIME_ZONE = "Asia/Shanghai";
const MAX_REPORT_CANDIDATES = 3;
const MAX_HOTSPOTS = 3;

export type Ai4sDailyHomeHotspot = {
  id: string;
  reportId: string;
  reportTitle: string;
  reportDate: string;
  reportUrl: string;
  section: string;
  title: string;
  summary: string;
  sourceUrls: string[];
};

export type Ai4sDailyHomeData = {
  reportId: string;
  reportTitle: string;
  reportDate: string;
  reportUrl: string;
  hotspots: Ai4sDailyHomeHotspot[];
};

export type Ai4sDailyReportDocument = {
  reportId: string;
  reportTitle: string;
  reportDate: string;
  reportUrl: string;
  hotspots: Array<{
    title: string;
    summary: string;
    sourceUrls: string[];
    section: string;
  }>;
};

type CacheEnvelope = {
  // The cache is intentionally keyed by the local calendar day. Once a
  // report has been read successfully, revisiting the workspace on that day
  // must use the saved snapshot instead of re-fetching index.json and every
  // report markdown file.
  fetchedDay: string;
  cachedAt: number;
  data: Ai4sDailyHomeData;
};

const configuredBaseUrl =
  typeof import.meta.env.VITE_AI4S_DAILY_BASE_URL === "string"
    ? import.meta.env.VITE_AI4S_DAILY_BASE_URL
    : "";

export const AI4S_DAILY_HOME_BASE_URL = (
  configuredBaseUrl.trim() || DEFAULT_AI4S_DAILY_BASE_URL
).replace(/\/+$/, "");

let memoryCache: CacheEnvelope | null = null;
let inFlightRequest: Promise<Ai4sDailyHomeData> | null = null;

function currentCalendarDay(): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: BEIJING_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const values = Object.fromEntries(
    parts
      .filter(({ type }) => type !== "literal")
      .map(({ type, value }) => [type, value])
  );
  return `${values.year}-${values.month}-${values.day}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function normalizeText(value: string): string {
  return value
    .replace(/\r/g, "")
    .replace(/[ \t]+/g, " ")
    .replace(/\n{2,}/g, "\n")
    .trim();
}

function stripQuotes(value: string): string {
  const trimmed = value.trim();
  if (
    trimmed.length >= 2 &&
    ((trimmed.startsWith('"') && trimmed.endsWith('"')) ||
      (trimmed.startsWith("'") && trimmed.endsWith("'")))
  ) {
    return trimmed.slice(1, -1).trim();
  }
  return trimmed;
}

function truncate(value: string, maxLength: number): string {
  if (value.length <= maxLength) {
    return value;
  }
  return `${value.slice(0, Math.max(0, maxLength - 1)).trimEnd()}…`;
}

function parseFrontMatter(markdown: string): Record<string, string> {
  const match = markdown.match(/^---\s*\n([\s\S]*?)\n---(?:\s*\n|$)/);
  if (!match) {
    return {};
  }

  const fields: Record<string, string> = {};
  for (const line of match[1].split("\n")) {
    const separator = line.indexOf(":");
    if (separator <= 0) {
      continue;
    }
    const key = line.slice(0, separator).trim();
    const value = line.slice(separator + 1).trim();
    if (key) {
      fields[key] = stripQuotes(value);
    }
  }
  return fields;
}

function datePart(value: string): string {
  const match = value.match(/\d{4}-\d{2}-\d{2}/);
  return match?.[0] || "";
}

function reportDate(fields: Record<string, string>, fileName: string): string {
  return (
    datePart(fields.date || "") ||
    datePart(fields.pushDate || "") ||
    datePart(fields.pushTime || "") ||
    datePart(fileName) ||
    "日期未提供"
  );
}

function firstHeading(markdown: string): string {
  const match = markdown.match(/^#(?!#)\s+(.+?)\s*$/m);
  return match?.[1]?.trim() || "AI4S Daily 报告";
}

type ParsedSection = {
  name: string;
  label: string;
  body: string;
};

function parseSections(markdown: string): ParsedSection[] {
  const sections: ParsedSection[] = [];
  const sectionPattern =
    /<!--\s*SECTION:([A-Za-z0-9_-]+)\s+BEGIN\s*-->\s*\n?([\s\S]*?)<!--\s*SECTION:[A-Za-z0-9_-]+\s+END\s*-->/gi;
  let match: RegExpExecArray | null;

  while ((match = sectionPattern.exec(markdown))) {
    const before = markdown.slice(0, match.index);
    const headings = [...before.matchAll(/^##\s+(.+?)\s*$/gm)];
    const lastHeading = headings[headings.length - 1]?.[1]?.trim();
    const body = match[2].trim();
    const sectionHeading = body.match(/^##\s+(.+?)\s*$/m)?.[1]?.trim();
    sections.push({
      name: match[1],
      // Daily 的 section marker 位于该 section 标题之前；优先读取当前
      // body 内的标题，只有旧格式没有标题时才回退到前一个标题。
      label: sectionHeading || lastHeading || match[1],
      body,
    });
  }

  return sections;
}

function extractSourceUrls(markdown: string): string[] {
  const urls = new Set<string>();
  const markdownLinks = /\[[^\]]*\]\((https?:\/\/[^)\s]+)\)/g;
  const rawUrls = /https?:\/\/[^\s)\]}>,"']+/g;

  for (const match of markdown.matchAll(markdownLinks)) {
    urls.add(match[1].replace(/[.,;]+$/, ""));
  }
  for (const match of markdown.matchAll(rawUrls)) {
    urls.add(match[0].replace(/[.,;]+$/, ""));
  }
  return [...urls];
}

function markdownToPlainText(markdown: string): string {
  return normalizeText(
    markdown
      .replace(/<!--([\s\S]*?)-->/g, " ")
      .replace(/\[[^\]]*\]\((https?:\/\/[^)\s]+)\)/g, " ")
      .replace(/https?:\/\/[^\s)\]}>,"']+/g, " ")
      .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/\*\*([^*]+)\*\*/g, "$1")
      .replace(/__([^_]+)__/g, "$1")
      .replace(/[`*_>#]/g, "")
      .replace(/^\s*[-+*]\s+/gm, "")
      .replace(/^\s*\d+[.)]\s+/gm, "")
      .replace(/\s*🔗.*$/gm, "")
  );
}

function summarizeHotspot(body: string, fallback: string): string {
  const candidates = body
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("🔗") && !line.startsWith("###"))
    .map((line) => markdownToPlainText(line))
    .filter(Boolean);
  const summary = normalizeText(candidates.slice(0, 2).join(" "));
  return truncate(summary || markdownToPlainText(fallback), 220);
}

function parseHotspots(section: ParsedSection): Ai4sDailyReportDocument["hotspots"] {
  const headings = [...section.body.matchAll(/^###\s+(.+?)\s*$/gm)];
  const hotspots: Ai4sDailyReportDocument["hotspots"] = [];

  for (let index = 0; index < headings.length; index += 1) {
    const heading = headings[index];
    const start = (heading.index ?? 0) + heading[0].length;
    const end = headings[index + 1]?.index ?? section.body.length;
    const body = section.body.slice(start, end).trim();
    const title = normalizeText(heading[1]);
    if (!title || /今日无重要动态/.test(title)) {
      continue;
    }
    hotspots.push({
      title,
      summary: summarizeHotspot(body, title),
      sourceUrls: extractSourceUrls(body),
      section: section.label,
    });
  }

  return hotspots;
}

function parseHighlights(value: string | undefined): string[] {
  if (!value) {
    return [];
  }
  const matches = [...value.matchAll(/"([^"\\]*(?:\\.[^"\\]*)*)"/g)];
  if (matches.length) {
    return matches.map((match) => match[1]).filter(Boolean);
  }
  return value
    .replace(/^\[/, "")
    .replace(/\]$/, "")
    .split(",")
    .map((item) => stripQuotes(item))
    .filter(Boolean);
}

function toReportUrl(fileName: string, baseUrl: string): string {
  return `${baseUrl.replace(/\/+$/, "")}${REPORTS_PATH}/${encodeURIComponent(fileName)}`;
}

export function parseAi4sDailyReport(
  markdown: string,
  fileName: string,
  baseUrl = AI4S_DAILY_HOME_BASE_URL
): Ai4sDailyReportDocument {
  const fields = parseFrontMatter(markdown);
  const reportId = fileName.replace(/\.md$/i, "");
  const reportTitle = fields.title || firstHeading(markdown);
  const reportDate = reportDateValue(fields, fileName);
  const reportUrl = toReportUrl(fileName, baseUrl);
  const sections = parseSections(markdown);
  const preferredSection =
    sections.find((section) => section.name.toLowerCase() === "rss") ||
    sections.find((section) => /^###\s+/m.test(section.body)) ||
    sections[0];
  let hotspots = preferredSection ? parseHotspots(preferredSection) : [];

  if (!hotspots.length) {
    hotspots = parseHighlights(fields.highlights).map((title) => ({
      title,
      summary: truncate(markdownToPlainText(fields.lead || fields.excerpt || title), 220),
      sourceUrls: [],
      section: "报告亮点",
    }));
  }

  return {
    reportId,
    reportTitle,
    reportDate,
    reportUrl,
    hotspots,
  };
}

function reportDateValue(fields: Record<string, string>, fileName: string): string {
  return reportDate(fields, fileName);
}

function normalizeIndexFileName(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const fileName = value.trim();
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]*\.md$/i.test(fileName)) {
    return undefined;
  }
  return fileName;
}

export function parseAi4sDailyIndex(value: unknown): string[] {
  const candidates = Array.isArray(value)
    ? value
    : isRecord(value)
      ? value.reports || value.files || value.items
      : undefined;
  if (!Array.isArray(candidates)) {
    return [];
  }

  return candidates
    .map((item) => {
      if (typeof item === "string") {
        return normalizeIndexFileName(item);
      }
      if (isRecord(item)) {
        return normalizeIndexFileName(item.fileName || item.filename || item.name);
      }
      return undefined;
    })
    .filter((fileName): fileName is string => Boolean(fileName));
}

function isHomeData(value: unknown): value is Ai4sDailyHomeData {
  if (!isRecord(value) || !Array.isArray(value.hotspots)) {
    return false;
  }
  return (
    typeof value.reportId === "string" &&
    typeof value.reportTitle === "string" &&
    typeof value.reportDate === "string" &&
    typeof value.reportUrl === "string" &&
    value.hotspots.every(
      (hotspot) =>
        isRecord(hotspot) &&
        typeof hotspot.id === "string" &&
        typeof hotspot.title === "string" &&
        typeof hotspot.summary === "string" &&
        typeof hotspot.section === "string" &&
        Array.isArray(hotspot.sourceUrls)
    )
  );
}

function readCachedData(): CacheEnvelope | null {
  if (memoryCache?.fetchedDay === currentCalendarDay()) {
    return memoryCache;
  }
  if (typeof window === "undefined") {
    return null;
  }

  try {
    const raw =
      window.localStorage.getItem(CACHE_KEY) ||
      window.sessionStorage.getItem(CACHE_KEY);
    if (!raw) {
      return null;
    }
    const parsed: unknown = JSON.parse(raw);
    if (
      !isRecord(parsed) ||
      typeof parsed.fetchedDay !== "string" ||
      typeof parsed.cachedAt !== "number" ||
      !isHomeData(parsed.data)
    ) {
      return null;
    }
    memoryCache = {
      fetchedDay: parsed.fetchedDay,
      cachedAt: parsed.cachedAt,
      data: parsed.data,
    };
    return memoryCache;
  } catch {
    return null;
  }
}

function writeCachedData(data: Ai4sDailyHomeData): void {
  const envelope = {
    fetchedDay: currentCalendarDay(),
    cachedAt: Date.now(),
    data,
  } satisfies CacheEnvelope;
  memoryCache = envelope;
  if (typeof window === "undefined") {
    return;
  }
  try {
    // localStorage survives a tab/page reload, which is required to make a
    // once-per-day read meaningful. Keep sessionStorage as a fallback for
    // browsers that disable persistent storage.
    window.localStorage.setItem(CACHE_KEY, JSON.stringify(envelope));
  } catch {
    try {
      window.sessionStorage.setItem(CACHE_KEY, JSON.stringify(envelope));
    } catch {
      // Private browsing or a full storage quota should not block the homepage.
    }
  }
}

async function fetchJson(url: string): Promise<unknown> {
  const response = await fetch(url, {
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`AI4S Daily 请求失败（${response.status}）`);
  }
  return response.json();
}

async function fetchMarkdown(url: string): Promise<string> {
  const response = await fetch(url, {
    cache: "no-store",
    headers: { Accept: "text/markdown,text/plain" },
  });
  if (!response.ok) {
    throw new Error(`AI4S Daily 报告读取失败（${response.status}）`);
  }
  return response.text();
}

function compareReports(left: Ai4sDailyReportDocument, right: Ai4sDailyReportDocument): number {
  return right.reportDate.localeCompare(left.reportDate);
}

async function loadFreshAi4sDailyToday(
  fileNames: string[]
): Promise<Ai4sDailyHomeData> {
  if (!fileNames.length) {
    throw new Error("AI4S Daily 没有可用报告");
  }

  const documents = (
    await Promise.allSettled(
      fileNames.map(async (fileName) => {
        const markdown = await fetchMarkdown(toReportUrl(fileName, AI4S_DAILY_HOME_BASE_URL));
        return parseAi4sDailyReport(markdown, fileName);
      })
    )
  )
    .filter(
      (result): result is PromiseFulfilledResult<Ai4sDailyReportDocument> =>
        result.status === "fulfilled"
    )
    .map((result) => result.value)
    .sort(compareReports);

  const latest = documents[0];
  if (!latest) {
    throw new Error("AI4S Daily 最新报告读取失败");
  }

  const hotspots = latest.hotspots.slice(0, MAX_HOTSPOTS).map((hotspot, index) => ({
    ...hotspot,
    id: `${latest.reportId}-${index}-${hotspot.title}`,
    reportId: latest.reportId,
    reportTitle: latest.reportTitle,
    reportDate: latest.reportDate,
    reportUrl: latest.reportUrl,
  }));

  return {
    reportId: latest.reportId,
    reportTitle: latest.reportTitle,
    reportDate: latest.reportDate,
    reportUrl: latest.reportUrl,
    hotspots,
  };
}

export async function loadAi4sDailyToday(): Promise<Ai4sDailyHomeData> {
  if (inFlightRequest) {
    return inFlightRequest;
  }

  inFlightRequest = (async () => {
    const indexUrl = `${AI4S_DAILY_HOME_BASE_URL}${REPORTS_PATH}/index.json`;
    const indexPayload = await fetchJson(indexUrl);
    const today = currentCalendarDay();
    const fileNames = parseAi4sDailyIndex(indexPayload)
      // AI4S Daily uses Asia/Shanghai dates. Ignore a future report if the
      // index is updated shortly before the local calendar day changes.
      .filter((fileName) => {
        const date = datePart(fileName);
        return !date || date <= today;
      })
      .slice(0, MAX_REPORT_CANDIDATES);
    if (!fileNames.length) {
      throw new Error("AI4S Daily 没有可用报告");
    }

    const cached = readCachedData();
    const latestReportId = fileNames[0].replace(/\.md$/i, "");
    if (cached && cached.data.reportId === latestReportId) {
      // The report itself has not changed. Refresh only the cache envelope's
      // Beijing day so subsequent visits remain a local read.
      writeCachedData(cached.data);
      return cached.data;
    }

    return loadFreshAi4sDailyToday(fileNames);
  })()
    .then((data) => {
      writeCachedData(data);
      return data;
    })
    .catch((error) => {
      const stale = readCachedData();
      if (stale) {
        return stale.data;
      }
      throw error;
    })
    .finally(() => {
      inFlightRequest = null;
    });

  return inFlightRequest;
}

export function buildAi4sDailyResearchPrompt(hotspot: Ai4sDailyHomeHotspot): string {
  const sourceUrls = hotspot.sourceUrls.length
    ? hotspot.sourceUrls.map((url) => `- ${url}`).join("\n")
    : "- 当前热点未提供可解析的原始来源 URL，请通过 ai4s_daily 或外部搜索补充。";

  return `请围绕 AI4S Daily 今日热点《${hotspot.title}》开展一次专题搜索分析调研。

以下是 AI4S Daily 首页提供的研究起点信息。它们是线索，不应替代后续证据核验：
- 报告标题：${hotspot.reportTitle}
- 报告日期：${hotspot.reportDate}
- 所属栏目：${hotspot.section}
- 热点摘要：${hotspot.summary}
- AI4S Daily 报告 URL：${hotspot.reportUrl}
- 热点原始来源 URL：
${sourceUrls}

请先调用 ai4s_daily，检索该热点及其主题相关的历史 AI4S Daily 报告和热点内容；再根据证据是否充分、问题范围和时效性，决定是否继续使用 DeepSearch 或 WebSearch 补充论文、官网、GitHub 和其他一手资料。不要因为首页摘要已有内容就跳过 ai4s_daily，也不要把 AI4S Daily 当作唯一知识源。

最终请形成一份有来源依据的专题研究，至少包括：
1. 事件概览
2. 核心技术路线
3. 主要创新
4. 论文、团队和机构
5. 相关前序工作
6. 同类研究或竞争路线
7. 对 AI4S 领域的意义
8. 后续值得关注的问题
9. 来源证据（保留 AI4S Daily 报告 URL、热点原始来源 URL，以及后续搜索证据 URL）`;
}
