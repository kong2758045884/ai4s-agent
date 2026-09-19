package org.wwz.ai.domain.agent.runtime.tool.common;

import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.JSONArray;
import com.alibaba.fastjson.JSONObject;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.lang3.StringUtils;
import org.wwz.ai.domain.agent.adapter.port.RemoteHttpPort;
import org.wwz.ai.domain.agent.adapter.port.RemoteHttpRequest;
import org.wwz.ai.domain.agent.adapter.port.RemoteHttpResponse;
import org.wwz.ai.domain.agent.runtime.agent.AgentContext;
import org.wwz.ai.domain.agent.runtime.tool.BaseTool;
import org.wwz.ai.domain.agent.runtime.tool.ToolResultPayload;
import org.wwz.ai.domain.agent.ai4s.config.AI4SConfig;

import java.net.URI;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * AI4S Daily 专业知识源工具。
 *
 * <p>工具直接读取 AI4S Daily 公开静态数据契约（reports/index.json 和
 * reports/push-*.md），不访问 Vue 页面，也不依赖 ai4s-daily 源码。工具通过
 * AI4S 的 RemoteHttpPort 出站，返回带报告、栏目、热点和原始来源 URL 的结构化结果，
 * 供 Agent 决定是否继续调用 WebSearch 或 DeepSearch。</p>
 */
@Slf4j
public class Ai4sDailyTool implements BaseTool {

    public static final String TOOL_NAME = "ai4s_daily";

    private static final String REPORTS_PATH = "/reports/";
    private static final int DEFAULT_LOOKBACK_REPORTS = 6;
    private static final int MAX_LOOKBACK_REPORTS = 12;
    private static final int DEFAULT_RESULT_REPORTS = 3;
    private static final int MAX_RESULT_REPORTS = 5;
    private static final int MAX_SECTION_RESULTS = 4;
    private static final int MAX_HOTSPOT_RESULTS = 8;
    private static final int MAX_SECTION_CHARS = 2400;
    private static final int MAX_HOTSPOT_CHARS = 1800;
    private static final long CONNECT_TIMEOUT_SECONDS = 20L;
    private static final long READ_TIMEOUT_SECONDS = 45L;
    private static final long CALL_TIMEOUT_SECONDS = 60L;

    private static final Pattern SAFE_REPORT_FILENAME =
            Pattern.compile("^[A-Za-z0-9][A-Za-z0-9._-]*\\.md$");
    private static final Pattern FRONT_MATTER_KEY =
            Pattern.compile("^([A-Za-z][A-Za-z0-9_-]*):\\s*(.*?)\\s*$");
    private static final Pattern SECTION_BEGIN =
            Pattern.compile("<!--\\s*SECTION:([A-Za-z0-9_-]+)\\s+BEGIN\\s*-->");
    private static final Pattern H1 =
            Pattern.compile("(?m)^#(?!#)\\s+(.+?)\\s*$");
    private static final Pattern H2 =
            Pattern.compile("(?m)^##(?!#)\\s+(.+?)\\s*$");
    private static final Pattern H3 =
            Pattern.compile("(?m)^###\\s+(.+?)\\s*$");
    private static final Pattern ASCII_TERM =
            Pattern.compile("[A-Za-z][A-Za-z0-9_-]{2,}");
    private static final Pattern HAN_RUN =
            Pattern.compile("[\\p{IsHan}]{2,}");
    private static final Pattern URL =
            Pattern.compile("https?://[^\\s)\\]}>\"']+");

    private static final Set<String> QUERY_STOP_TERMS = Set.of(
            "最近", "最新", "进展", "有什么", "哪些", "值得", "关注", "方面", "工作", "重要",
            "代表性", "新进展", "最新进展", "研究", "相关", "领域", "介绍", "如何", "以及",
            "请问", "情况", "内容", "成果", "动态", "新闻", "热点", "ai"
    );

    private static final List<String> FRONT_MATTER_FIELDS = List.of(
            "title", "excerpt", "seotitle", "seodescription", "lead", "date", "pushTime",
            "pushDate", "profile", "sourceCount", "totalEntries", "highlights"
    );

    private AgentContext agentContext;

    public void setAgentContext(AgentContext agentContext) {
        this.agentContext = agentContext;
    }

    @Override
    public String getName() {
        return TOOL_NAME;
    }

    @Override
    public String getDescription() {
        return """
                AI4S Daily 是 AI4S 领域的优先专业知识源和研究起点。
                当用户询问 AI 在材料、化学、生命科学、蛋白质、药物、医学、物理、地球科学、
                科研智能体、自动化实验、科学计算或其他 AI4S 相关主题的近期进展、热点、代表性工作
                或研究判断时，即使用户没有提到“AI4S Daily”，也应优先调用本工具；这是语义判断，
                不要求用户提供报告 URL。不要在普通编程、通用技术问答或明显非 AI4S 问题中调用本工具。

                本工具会读取 AI4S Daily 的 reports/index.json 和具体 reports/push-*.md，解析真实报告、
                SECTION 栏目、热点正文和原始来源 URL。调用参数 query 应传入完整用户问题。
                返回结果是优先证据源，不是唯一知识源：若结果为空、相关性不足，或问题需要论文、GitHub、
                官网和更深入的外部证据，应继续使用 WebSearch 或 DeepSearch；不要因为 AI4S Daily 没有
                匹配内容就结束回答。最终回答应保留 AI4S Daily 报告 URL、热点来源 URL，以及后续搜索证据。
                """;
    }

    @Override
    public Map<String, Object> toParams() {
        Map<String, Object> query = new LinkedHashMap<>();
        query.put("type", "string");
        query.put("description", "完整的用户问题或研究任务，用于从 AI4S Daily 报告和热点中定位相关内容");

        Map<String, Object> lookbackReports = new LinkedHashMap<>();
        lookbackReports.put("type", "integer");
        lookbackReports.put("description", "最多读取最近多少篇报告，默认 6，范围 1-12；通常不需要填写");

        Map<String, Object> maxResults = new LinkedHashMap<>();
        maxResults.put("type", "integer");
        maxResults.put("description", "最多返回多少篇相关报告，默认 3，范围 1-5；通常不需要填写");

        Map<String, Object> properties = new LinkedHashMap<>();
        properties.put("query", query);
        properties.put("lookbackReports", lookbackReports);
        properties.put("maxResults", maxResults);

        Map<String, Object> parameters = new LinkedHashMap<>();
        parameters.put("type", "object");
        parameters.put("properties", properties);
        parameters.put("required", List.of("query"));
        return parameters;
    }

    @Override
    public Object execute(Object input) {
        Map<String, Object> params = coerceMap(input);
        String query = StringUtils.trimToEmpty(valueAsString(params.get("query")));
        if (query.isBlank()) {
            return ToolResultPayload.failureFrom("ai4s_daily 的 query 不能为空", Map.of(
                    "tool", TOOL_NAME,
                    "type", "tool_error"
            ));
        }

        int lookbackReports = clampInt(params.get("lookbackReports"),
                DEFAULT_LOOKBACK_REPORTS, 1, MAX_LOOKBACK_REPORTS);
        int maxResults = clampInt(params.get("maxResults"),
                DEFAULT_RESULT_REPORTS, 1, MAX_RESULT_REPORTS);

        try {
            AI4SConfig config = requireAI4SConfig();
            String baseUrl = normalizeBaseUrl(config.getAi4sDailyBaseUrl());
            String indexUrl = baseUrl + REPORTS_PATH + "index.json";
            String indexBody = fetchText(indexUrl, config);
            List<String> reportFiles = parseIndex(indexBody);
            if (reportFiles.isEmpty()) {
                return ToolResultPayload.softFailData(TOOL_NAME, Map.of(
                        "query", query,
                        "indexUrl", indexUrl,
                        "matched", false,
                        "message", "AI4S Daily reports/index.json 没有可用报告文件",
                        "nextAction", "继续使用 WebSearch 或 DeepSearch"
                ));
            }

            List<ReportDocument> documents = new ArrayList<>();
            List<String> fetchFailures = new ArrayList<>();
            int inspected = Math.min(lookbackReports, reportFiles.size());
            for (int i = 0; i < inspected; i++) {
                String fileName = reportFiles.get(i);
                try {
                    String reportUrl = buildReportUrl(baseUrl, fileName);
                    String reportBody = fetchText(reportUrl, config);
                    documents.add(parseReport(fileName, reportUrl, reportBody, query, i));
                } catch (Exception e) {
                    fetchFailures.add(fileName + ": " + StringUtils.defaultIfBlank(e.getMessage(), "读取失败"));
                    log.warn("{} AI4S Daily report fetch failed, file={}", requestId(), fileName, e);
                }
            }

            if (documents.isEmpty()) {
                return ToolResultPayload.failureFrom(
                        "AI4S Daily 报告读取失败",
                        Map.of(
                                "tool", TOOL_NAME,
                                "query", query,
                                "indexUrl", indexUrl,
                                "fetchFailures", fetchFailures
                        )
                );
            }

            documents.sort(Comparator
                    .comparingInt(ReportDocument::score).reversed()
                    .thenComparingInt(ReportDocument::indexPosition));
            List<ReportDocument> selected = documents.subList(0, Math.min(maxResults, documents.size()));
            boolean matched = selected.stream().anyMatch(document -> document.score() > 0);

            Map<String, Object> data = new LinkedHashMap<>();
            data.put("tool", TOOL_NAME);
            data.put("ok", Boolean.TRUE);
            data.put("source", "AI4S Daily");
            data.put("query", query);
            data.put("indexUrl", indexUrl);
            data.put("inspectedReports", inspected);
            data.put("matched", matched);
            data.put("reports", selected.stream().map(document -> toReportMap(document, query)).toList());
            if (!fetchFailures.isEmpty()) {
                data.put("fetchFailures", fetchFailures);
            }
            data.put("nextAction", matched
                    ? "将 AI4S Daily 结果作为优先证据；根据问题范围继续使用 WebSearch 或 DeepSearch 补充外部证据，并保留全部来源 URL。"
                    : "近期 AI4S Daily 报告未发现明显匹配；不要结束回答，继续使用 WebSearch 或 DeepSearch。"
            );
            return ToolResultPayload.fromData(data);
        } catch (Exception e) {
            log.error("{} AI4S Daily tool failed, query={}", requestId(), query, e);
            return ToolResultPayload.failureFrom(
                    "AI4S Daily 读取失败：" + StringUtils.defaultIfBlank(e.getMessage(), e.getClass().getSimpleName()),
                    Map.of("tool", TOOL_NAME, "query", query)
            );
        }
    }

    private Map<String, Object> toReportMap(ReportDocument document, String query) {
        Map<String, Object> report = new LinkedHashMap<>();
        report.put("reportId", document.reportId());
        report.put("reportUrl", document.reportUrl());
        report.put("matched", document.score() > 0);
        report.put("matchScore", document.score());

        for (String field : FRONT_MATTER_FIELDS) {
            Object value = document.frontMatter().get(field);
            if (value != null && !(value instanceof String string && string.isBlank())) {
                report.put(field, value);
            }
        }
        if (!report.containsKey("title") && StringUtils.isNotBlank(document.title())) {
            report.put("title", document.title());
        }

        List<SectionDocument> sections = new ArrayList<>(document.sections());
        if (!queryTerms(query).isEmpty()) {
            sections.sort(Comparator.comparingInt(SectionDocument::score).reversed());
        }
        List<Map<String, Object>> sectionMaps = sections.stream()
                .limit(MAX_SECTION_RESULTS)
                .map(this::toSectionMap)
                .toList();
        report.put("sections", sectionMaps);
        return report;
    }

    private Map<String, Object> toSectionMap(SectionDocument section) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("section", section.name());
        if (StringUtils.isNotBlank(section.title())) {
            result.put("title", section.title());
        }
        result.put("matchScore", section.score());
        String sectionContent = compactMarkdown(section.body());
        if (StringUtils.isNotBlank(sectionContent)) {
            result.put("content", truncate(sectionContent, MAX_SECTION_CHARS));
        }

        List<HotspotDocument> hotspots = new ArrayList<>(section.hotspots());
        hotspots.sort(Comparator.comparingInt(HotspotDocument::score).reversed());
        List<Map<String, Object>> hotspotMaps = hotspots.stream()
                .limit(MAX_HOTSPOT_RESULTS)
                .map(this::toHotspotMap)
                .toList();
        if (!hotspotMaps.isEmpty()) {
            result.put("hotspots", hotspotMaps);
        }
        return result;
    }

    private Map<String, Object> toHotspotMap(HotspotDocument hotspot) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("title", hotspot.title());
        result.put("matchScore", hotspot.score());
        String content = compactMarkdown(hotspot.body());
        if (StringUtils.isNotBlank(content)) {
            result.put("content", truncate(content, MAX_HOTSPOT_CHARS));
        }
        if (!hotspot.sourceUrls().isEmpty()) {
            result.put("sourceUrls", hotspot.sourceUrls());
        }
        return result;
    }

    private ReportDocument parseReport(String fileName,
                                       String reportUrl,
                                       String rawContent,
                                       String query,
                                       int indexPosition) {
        FrontMatterDocument frontMatterDocument = parseFrontMatter(rawContent);
        String body = frontMatterDocument.body();
        String title = firstMatch(H1, body);
        Object frontMatterTitle = frontMatterDocument.fields().get("title");
        if (frontMatterTitle != null && StringUtils.isNotBlank(String.valueOf(frontMatterTitle))) {
            title = String.valueOf(frontMatterTitle);
        }

        List<SectionDocument> sections = parseSections(body, query);
        String reportText = title + " " + stringifyValues(frontMatterDocument.fields()) + " " + body;
        int score = scoreText(query, reportText);
        if (score == 0) {
            score = sections.stream().mapToInt(SectionDocument::score).max().orElse(0);
        }

        String reportId = fileName.endsWith(".md")
                ? fileName.substring(0, fileName.length() - 3)
                : fileName;
        return new ReportDocument(
                reportId,
                reportUrl,
                title,
                frontMatterDocument.fields(),
                sections,
                score,
                indexPosition
        );
    }

    private List<SectionDocument> parseSections(String body, String query) {
        List<SectionDocument> sections = new ArrayList<>();
        Matcher beginMatcher = SECTION_BEGIN.matcher(body);
        int cursor = 0;
        while (beginMatcher.find(cursor)) {
            String name = beginMatcher.group(1);
            String endMarker = "<!-- SECTION:" + name + " END -->";
            int end = body.indexOf(endMarker, beginMatcher.end());
            if (end < 0) {
                cursor = beginMatcher.end();
                continue;
            }
            String sectionBody = body.substring(beginMatcher.end(), end).trim();
            String title = firstMatch(H2, sectionBody);
            List<HotspotDocument> hotspots = parseHotspots(sectionBody, query);
            int score = scoreText(query, name + " " + title + " " + sectionBody);
            sections.add(new SectionDocument(name, title, sectionBody, hotspots, score));
            cursor = end + endMarker.length();
        }

        if (sections.isEmpty() && StringUtils.isNotBlank(body)) {
            sections.add(new SectionDocument(
                    "document",
                    firstMatch(H2, body),
                    body,
                    parseHotspots(body, query),
                    scoreText(query, body)
            ));
        }
        return sections;
    }

    private List<HotspotDocument> parseHotspots(String sectionBody, String query) {
        List<HotspotDocument> hotspots = new ArrayList<>();
        Matcher headingMatcher = H3.matcher(sectionBody);
        List<HeadingSlice> headings = new ArrayList<>();
        while (headingMatcher.find()) {
            headings.add(new HeadingSlice(headingMatcher.group(1).trim(), headingMatcher.end()));
        }
        for (int i = 0; i < headings.size(); i++) {
            HeadingSlice heading = headings.get(i);
            int end = i + 1 < headings.size() ? headings.get(i + 1).start() : sectionBody.length();
            String hotspotBody = sectionBody.substring(heading.start(), end).trim();
            hotspots.add(new HotspotDocument(
                    heading.title(),
                    hotspotBody,
                    extractUrls(hotspotBody),
                    scoreText(query, heading.title() + " " + hotspotBody)
            ));
        }
        return hotspots;
    }

    private FrontMatterDocument parseFrontMatter(String rawContent) {
        String normalized = StringUtils.defaultString(rawContent).replace("\r\n", "\n");
        if (!normalized.startsWith("---\n")) {
            return new FrontMatterDocument(Map.of(), normalized);
        }
        int end = normalized.indexOf("\n---", 4);
        if (end < 0) {
            return new FrontMatterDocument(Map.of(), normalized);
        }

        Map<String, Object> fields = new LinkedHashMap<>();
        String header = normalized.substring(4, end);
        for (String line : header.split("\n")) {
            Matcher matcher = FRONT_MATTER_KEY.matcher(line);
            if (!matcher.matches()) {
                continue;
            }
            String key = matcher.group(1);
            String value = matcher.group(2);
            if (StringUtils.isBlank(value)) {
                continue;
            }
            fields.put(key, parseFrontMatterValue(value));
        }

        int bodyStart = end + 4;
        if (bodyStart < normalized.length() && normalized.charAt(bodyStart) == '\n') {
            bodyStart++;
        }
        return new FrontMatterDocument(fields, normalized.substring(bodyStart).trim());
    }

    private Object parseFrontMatterValue(String value) {
        String trimmed = value.trim();
        if (trimmed.startsWith("[") && trimmed.endsWith("]")) {
            try {
                return JSON.parse(trimmed);
            } catch (Exception ignored) {
                // 公开报告的 front matter 可能出现非严格 JSON 数组，保留原始值。
            }
        }
        if ((trimmed.startsWith("\"") && trimmed.endsWith("\""))
                || (trimmed.startsWith("'") && trimmed.endsWith("'"))) {
            return trimmed.substring(1, trimmed.length() - 1);
        }
        return trimmed;
    }

    private List<String> parseIndex(String indexBody) {
        Object parsed = JSON.parse(indexBody);
        List<String> files = new ArrayList<>();
        if (parsed instanceof JSONArray array) {
            for (Object item : array) {
                addSafeReportFile(files, item);
            }
            return files;
        }
        if (parsed instanceof JSONObject object) {
            for (String key : List.of("reports", "files", "items")) {
                Object value = object.get(key);
                if (value instanceof JSONArray array) {
                    for (Object item : array) {
                        addSafeReportFile(files, item);
                    }
                    if (!files.isEmpty()) {
                        return files;
                    }
                }
            }
        }
        throw new IllegalStateException("reports/index.json 必须是报告文件名数组");
    }

    private void addSafeReportFile(List<String> files, Object value) {
        String fileName = value == null ? "" : String.valueOf(value).trim();
        if (SAFE_REPORT_FILENAME.matcher(fileName).matches()) {
            files.add(fileName);
        }
    }

    private String fetchText(String url, AI4SConfig config) throws Exception {
        RemoteHttpResponse response = requireRemoteHttpPort().executeDetailed(RemoteHttpRequest.builder()
                .method("GET")
                .url(url)
                .headers(Map.of(
                        "Accept", "application/json, text/markdown, text/plain;q=0.9, */*",
                        "User-Agent", "AI4SAgentAi4sDaily/1.0"
                ))
                .connectTimeoutSeconds(CONNECT_TIMEOUT_SECONDS)
                .readTimeoutSeconds(READ_TIMEOUT_SECONDS)
                .writeTimeoutSeconds(READ_TIMEOUT_SECONDS)
                .callTimeoutSeconds(CALL_TIMEOUT_SECONDS)
                .proxy(StringUtils.trimToEmpty(config.getAi4sDailyProxy()))
                .followRedirects(true)
                .build());
        if (response.getStatusCode() < 200 || response.getStatusCode() >= 300) {
            throw new IllegalStateException("HTTP " + response.getStatusCode() + " "
                    + StringUtils.defaultString(response.getStatusText()));
        }
        String body = StringUtils.defaultString(response.getBody());
        if (body.isBlank()) {
            throw new IllegalStateException("响应正文为空");
        }
        return body;
    }

    private String buildReportUrl(String baseUrl, String fileName) {
        if (!SAFE_REPORT_FILENAME.matcher(fileName).matches()) {
            throw new IllegalArgumentException("非法 AI4S Daily 报告文件名");
        }
        return baseUrl + REPORTS_PATH + fileName;
    }

    private String normalizeBaseUrl(String configured) {
        String baseUrl = StringUtils.trimToEmpty(configured);
        if (baseUrl.isBlank()) {
            throw new IllegalStateException("未配置 autobots.autoagent.ai4s_daily.base_url");
        }
        URI uri;
        try {
            uri = URI.create(baseUrl);
        } catch (Exception e) {
            throw new IllegalArgumentException("AI4S Daily base URL 无效", e);
        }
        String scheme = StringUtils.defaultString(uri.getScheme()).toLowerCase(Locale.ROOT);
        if (!"http".equals(scheme) && !"https".equals(scheme)) {
            throw new IllegalArgumentException("AI4S Daily base URL 只支持 http/https");
        }
        if (StringUtils.isBlank(uri.getHost()) || StringUtils.isNotBlank(uri.getUserInfo())) {
            throw new IllegalArgumentException("AI4S Daily base URL 主机或凭证无效");
        }
        return baseUrl.replaceAll("/+$", "");
    }

    private int scoreText(String query, String text) {
        Set<String> terms = queryTerms(query);
        if (terms.isEmpty() || StringUtils.isBlank(text)) {
            return 0;
        }
        String normalizedText = normalizeForMatch(text);
        String normalizedQuery = normalizeForMatch(query);
        int score = 0;
        for (String term : terms) {
            if (normalizedText.contains(normalizeForMatch(term))) {
                score += term.length() >= 3 ? 3 : 2;
            }
        }
        if (normalizedQuery.length() >= 4 && normalizedText.contains(normalizedQuery)) {
            score += 8;
        }
        return score;
    }

    private Set<String> queryTerms(String query) {
        Set<String> terms = new LinkedHashSet<>();
        String normalized = StringUtils.defaultString(query).toLowerCase(Locale.ROOT);
        Matcher asciiMatcher = ASCII_TERM.matcher(normalized);
        while (asciiMatcher.find()) {
            String term = asciiMatcher.group();
            if (!QUERY_STOP_TERMS.contains(term)) {
                terms.add(term);
            }
        }

        Matcher hanMatcher = HAN_RUN.matcher(normalized);
        while (hanMatcher.find()) {
            String run = hanMatcher.group();
            if (!QUERY_STOP_TERMS.contains(run) && run.length() <= 10) {
                terms.add(run);
            }
            for (int i = 0; i + 1 < run.length(); i++) {
                String bigram = run.substring(i, i + 2);
                if (!QUERY_STOP_TERMS.contains(bigram)) {
                    terms.add(bigram);
                }
            }
        }
        return terms;
    }

    private String normalizeForMatch(String value) {
        return StringUtils.defaultString(value)
                .toLowerCase(Locale.ROOT)
                .replaceAll("[^\\p{IsHan}\\p{L}\\p{N}]", "");
    }

    private List<String> extractUrls(String text) {
        Set<String> urls = new LinkedHashSet<>();
        Matcher matcher = URL.matcher(StringUtils.defaultString(text));
        while (matcher.find() && urls.size() < 20) {
            String value = matcher.group().replaceAll("[.,;:]+$", "");
            urls.add(value);
        }
        return List.copyOf(urls);
    }

    private String compactMarkdown(String text) {
        return StringUtils.defaultString(text)
                .replaceAll("(?m)^\\s*<!--.*?-->\\s*$", "")
                .replaceAll("\\[([^]]+)]\\((https?://[^)]+)\\)", "$1 ($2)")
                .replaceAll("[ \\t\\x0B\\f\\r]+", " ")
                .replaceAll("\\n{3,}", "\\n\\n")
                .trim();
    }

    private String firstMatch(Pattern pattern, String text) {
        Matcher matcher = pattern.matcher(StringUtils.defaultString(text));
        return matcher.find() ? matcher.group(1).trim() : "";
    }

    private String truncate(String value, int maxLength) {
        if (value == null || value.length() <= maxLength) {
            return value;
        }
        return value.substring(0, maxLength) + "…";
    }

    private String stringifyValues(Map<String, Object> values) {
        StringBuilder builder = new StringBuilder();
        values.forEach((key, value) -> builder.append(key).append(' ').append(value).append(' '));
        return builder.toString();
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> coerceMap(Object input) {
        if (input instanceof Map<?, ?> map) {
            return (Map<String, Object>) map;
        }
        return Map.of();
    }

    private int clampInt(Object value, int fallback, int min, int max) {
        if (value == null) {
            return fallback;
        }
        try {
            int parsed = value instanceof Number number
                    ? number.intValue()
                    : Integer.parseInt(String.valueOf(value));
            return Math.max(min, Math.min(max, parsed));
        } catch (Exception ignored) {
            return fallback;
        }
    }

    private String valueAsString(Object value) {
        return value == null ? null : String.valueOf(value);
    }

    private String requestId() {
        return agentContext == null ? "unknown" : StringUtils.defaultString(agentContext.getRequestId(), "unknown");
    }

    private AI4SConfig requireAI4SConfig() {
        if (agentContext == null || agentContext.getRuntimeDependencies() == null) {
            throw new IllegalStateException("Ai4sDailyTool 缺少 AI4SRuntimeDependencies");
        }
        return agentContext.getRuntimeDependencies().requireAI4SConfig();
    }

    private RemoteHttpPort requireRemoteHttpPort() {
        if (agentContext == null || agentContext.getRuntimeDependencies() == null) {
            throw new IllegalStateException("Ai4sDailyTool 缺少 AI4SRuntimeDependencies");
        }
        return agentContext.getRuntimeDependencies().requireRemoteHttpPort();
    }

    private record FrontMatterDocument(Map<String, Object> fields, String body) {
    }

    private record HeadingSlice(String title, int start) {
    }

    private record HotspotDocument(String title, String body, List<String> sourceUrls, int score) {
    }

    private record SectionDocument(String name,
                                   String title,
                                   String body,
                                   List<HotspotDocument> hotspots,
                                   int score) {
    }

    private record ReportDocument(String reportId,
                                  String reportUrl,
                                  String title,
                                  Map<String, Object> frontMatter,
                                  List<SectionDocument> sections,
                                  int score,
                                  int indexPosition) {
    }
}
