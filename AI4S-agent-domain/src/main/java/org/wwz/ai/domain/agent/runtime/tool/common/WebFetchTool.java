package org.wwz.ai.domain.agent.runtime.tool.common;

import lombok.Data;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.lang3.StringUtils;
import org.wwz.ai.domain.agent.adapter.port.RemoteHttpPort;
import org.wwz.ai.domain.agent.adapter.port.RemoteHttpRequest;
import org.wwz.ai.domain.agent.adapter.port.RemoteHttpResponse;
import org.wwz.ai.domain.agent.runtime.agent.AgentContext;
import org.wwz.ai.domain.agent.runtime.dto.Message;
import org.wwz.ai.domain.agent.runtime.llm.LLM;
import org.wwz.ai.domain.agent.runtime.tool.BaseTool;
import org.wwz.ai.domain.agent.runtime.tool.ToolResultPayload;
import org.wwz.ai.domain.agent.ai4s.config.AI4SConfig;

import java.net.URI;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * WebFetch：抓取 URL → HTML 转文本 → 用 prompt 经小模型提炼。
 * 不再依赖 ai4s-tool /web_fetch 与文件产物。
 */
@Slf4j
@Data
public class WebFetchTool implements BaseTool {

    public static final String TOOL_NAME = "WebFetch";

    private static final int MAX_URL_LENGTH = 2000;
    private static final int MAX_MARKDOWN_LENGTH = 100_000;
    private static final int MAX_SAME_HOST_REDIRECTS = 10;
    private static final long FETCH_TIMEOUT_SECONDS = 60L;
    private static final int EXTRACT_TIMEOUT_SECONDS = 90;
    private static final String USER_AGENT = "AI4SAgentWebFetch/1.0";
    private static final Pattern SCRIPT_STYLE = Pattern.compile(
            "(?is)<(script|style|noscript|svg|iframe)[^>]*>.*?</\\1>");
    private static final Pattern TAG = Pattern.compile("(?is)<[^>]+>");
    private static final Pattern MULTI_SPACE = Pattern.compile("[ \\t\\x0B\\f\\r]+");
    private static final Pattern MULTI_NL = Pattern.compile("\\n{3,}");
    private static final Pattern HTML_TITLE = Pattern.compile("(?is)<title[^>]*>(.*?)</title>");
    private static final Pattern META_DATE = Pattern.compile(
            "(?is)<meta[^>]+(?:name|property)=[\\\"'](?:article:published_time|date|datePublished|publication_date)[\\\"'][^>]+content=[\\\"']([^\\\"']+)[\\\"']");
    private static final int SOURCE_EXCERPT_CHARS = 6000;

    private AgentContext agentContext;

    @Override
    public String getName() {
        return TOOL_NAME;
    }

    @Override
    public String getDescription() {
        return """
                IMPORTANT: WebFetch WILL FAIL for authenticated or private URLs. Prefer specialized MCP tools for GitHub/Confluence/etc.

                Fetches content from a URL and processes it with a prompt using a secondary model.
                - Inputs: url (required), prompt (required — what to extract/analyze)
                - HTTP is upgraded to HTTPS
                - HTML is converted to plain text/markdown-like content
                - Cross-host redirects are NOT followed automatically; the tool returns redirect info for a new call
                - Results may be summarized if the page is very large
                - Read-only; does not write files
                """;
    }

    @Override
    public Map<String, Object> toParams() {
        Map<String, Object> url = new LinkedHashMap<>();
        url.put("type", "string");
        url.put("description", "The URL to fetch content from (http/https)");

        Map<String, Object> prompt = new LinkedHashMap<>();
        prompt.put("type", "string");
        prompt.put("description", "The prompt to run on the fetched content (what to extract or analyze)");

        Map<String, Object> properties = new LinkedHashMap<>();
        properties.put("url", url);
        properties.put("prompt", prompt);

        Map<String, Object> parameters = new LinkedHashMap<>();
        parameters.put("type", "object");
        parameters.put("properties", properties);
        parameters.put("required", List.of("url", "prompt"));
        return parameters;
    }

    @Override
    @SuppressWarnings("unchecked")
    public Object execute(Object input) {
        long start = System.currentTimeMillis();
        String rawUrl = "";
        String prompt = "";
        try {
            Map<String, Object> params = coerceMap(input);
            rawUrl = StringUtils.trimToEmpty(valueAsString(params.get("url")));
            prompt = StringUtils.trimToEmpty(valueAsString(params.get("prompt")));
            if (StringUtils.isBlank(rawUrl)) {
                return failure("WebFetch 失败：url 不能为空", rawUrl, prompt);
            }
            if (StringUtils.isBlank(prompt)) {
                return failure("WebFetch 失败：prompt 不能为空（需说明要从页面提取/分析什么）", rawUrl, prompt);
            }

            String upgradedUrl = upgradeToHttps(rawUrl);
            validateUrl(upgradedUrl);

            FetchResult fetch = fetchWithPermittedRedirects(upgradedUrl, 0);
            if (fetch.redirect()) {
                Map<String, Object> data = new LinkedHashMap<>();
                data.put("tool", "web_fetch");
                data.put("ok", Boolean.FALSE);
                data.put("redirect", Boolean.TRUE);
                data.put("url", fetch.originalUrl());
                data.put("redirectUrl", fetch.redirectUrl());
                data.put("status", fetch.statusCode());
                data.put("hint", "Call web_fetch again with redirectUrl and the same prompt.");
                data.put("prompt", prompt);
                return ToolResultPayload.fromData(data);
            }

            String markdown = truncateContent(fetch.content());
            ExtractOutcome extracted = applyPromptToContent(prompt, markdown);
            long durationMs = System.currentTimeMillis() - start;

            Map<String, Object> data = new LinkedHashMap<>();
            data.put("tool", "web_fetch");
            data.put("ok", Boolean.TRUE);
            data.put("url", fetch.finalUrl());
            data.put("status", fetch.statusCode());
            data.put("statusText", StringUtils.defaultString(fetch.statusText()));
            data.put("bytes", fetch.bytes());
            data.put("durationMs", durationMs);
            data.put("prompt", prompt);
            data.put("content", extracted.content());
            data.put("degraded", extracted.degraded());
            data.put("source", sourceMetadata(fetch, markdown));
            if (extracted.degraded()) {
                // 诊断信息只写日志；工具 observation 可能进入报告代理，不能把异常类名、代理地址
                // 或连接堆栈当作事实资料暴露给最终用户。
                data.put("hint", "页面抓取成功，以下内容为页面正文摘录，未完成模型提炼。");
            }
            return ToolResultPayload.fromData(data);
        } catch (FetchHttpException e) {
            long durationMs = System.currentTimeMillis() - start;
            String category = httpCategory(e.statusCode);
            log.warn("{} WebFetch failed stage=http category={} status={} durationMs={} url={}",
                    requestId(), category, e.statusCode, durationMs, e.url);
            Map<String, Object> detail = failureDetails(rawUrl, prompt, category, durationMs);
            detail.put("status", e.statusCode);
            detail.put("statusText", StringUtils.defaultString(e.statusText));
            return ToolResultPayload.failureFrom("WebFetch 未完成：目标页面返回 HTTP " + e.statusCode, detail);
        } catch (Exception e) {
            long durationMs = System.currentTimeMillis() - start;
            String category = errorCategory(e);
            log.error("{} WebFetch failed stage=fetch category={} durationMs={} url={}",
                    requestId(), category, durationMs, rawUrl, e);
            return failure("WebFetch 未完成（" + category + "）", rawUrl, prompt, category, durationMs);
        }
    }

    private FetchResult fetchWithPermittedRedirects(String url, int depth) throws Exception {
        if (depth > MAX_SAME_HOST_REDIRECTS) {
            throw new IllegalStateException("Too many redirects (exceeded " + MAX_SAME_HOST_REDIRECTS + ")");
        }

        RemoteHttpResponse response = executeFetchRequest(url);

        int code = response.getStatusCode();
        if (code == 301 || code == 302 || code == 303 || code == 307 || code == 308) {
            String location = headerIgnoreCase(response.getHeaders(), "Location");
            if (StringUtils.isBlank(location)) {
                throw new IllegalStateException("Redirect missing Location header");
            }
            String redirectUrl = URI.create(url).resolve(location.trim()).toString();
            redirectUrl = upgradeToHttps(redirectUrl);
            if (isPermittedRedirect(url, redirectUrl)) {
                return fetchWithPermittedRedirects(redirectUrl, depth + 1);
            }
            return FetchResult.redirect(url, redirectUrl, code);
        }

        if (code < 200 || code >= 300) {
            throw new FetchHttpException(
                    code,
                    url,
                    response.getStatusText(),
                    StringUtils.abbreviate(StringUtils.defaultString(response.getBody()), 2000)
            );
        }

        String contentType = StringUtils.defaultString(headerIgnoreCase(response.getHeaders(), "Content-Type")).toLowerCase(Locale.ROOT);
        String body = StringUtils.defaultString(response.getBody());
        String content;
        PageMetadata pageMetadata = PageMetadata.empty();
        if (contentType.contains("text/html") || looksLikeHtml(body)) {
            pageMetadata = extractPageMetadata(body);
            content = htmlToText(body);
        } else {
            content = body;
        }
        if (StringUtils.isBlank(content)) {
            throw new IllegalStateException("Empty content from " + url);
        }
        String finalUrl = StringUtils.defaultIfBlank(response.getFinalUrl(), url);
        return FetchResult.content(finalUrl, code, response.getStatusText(), content,
                content.getBytes(StandardCharsets.UTF_8).length,
                pageMetadata.title(), pageMetadata.sourceDate(), Instant.now().toString());
    }

    private ExtractOutcome applyPromptToContent(String prompt, String markdownContent) {
        String modelPrompt = """
                Web page content:
                ---
                %s
                ---

                %s

                Provide a concise response based only on the content above.
                - Prefer short quotes; do not dump the entire page
                - Use quotation marks for exact language from the source
                """.formatted(markdownContent, prompt);

        try {
            AI4SConfig config = requireAI4SConfig();
            String modelName = StringUtils.defaultIfBlank(config.getSummaryModelName(), config.getReactModelName());
            LLM llm = new LLM(modelName, "", agentContext.getRuntimeDependencies());
            // Prefer stream aggregation: some OpenAI-compatible gateways return empty/truncated
            // non-stream JSON bodies that Spring AI cannot deserialize as ChatCompletion.
            String answer = llm.ask(
                    agentContext,
                    Collections.singletonList(Message.userMessage(modelPrompt, null)),
                    Collections.emptyList(),
                    true,
                    false,
                    0.0
            ).get(EXTRACT_TIMEOUT_SECONDS, TimeUnit.SECONDS);
            return ExtractOutcome.success(StringUtils.defaultIfBlank(answer, "No response from model"));
        } catch (Exception e) {
            String root = rootCauseSummary(e);
            log.warn("{} WebFetch model extract failed, degrade to page text, root={}", requestId(), root, e);
            String degraded = """
                    页面已成功抓取，但未能完成二次提炼。
                    提取要求：%s

                    页面正文摘录：
                    %s
                    """.formatted(prompt, markdownContent);
            return ExtractOutcome.degraded(degraded, root);
        }
    }

    /**
     * 代理是可选优化项，不是访问前提。代理连接失败时记录诊断并立即直连重试，避免把本机
     * Clash/7890 之类的部署细节传播到工具结果或正式报告。
     */
    private RemoteHttpResponse executeFetchRequest(String url) throws IOException {
        AI4SConfig config = requireAI4SConfig();
        String proxy = StringUtils.trimToNull(config.getWebFetchProxy());
        var builder = RemoteHttpRequest.builder()
                .method("GET")
                .url(url)
                .headers(Map.of(
                        "Accept", "text/markdown, text/html, text/plain, */*",
                        "User-Agent", USER_AGENT
                ))
                .connectTimeoutSeconds(30L)
                .readTimeoutSeconds(FETCH_TIMEOUT_SECONDS)
                .writeTimeoutSeconds(FETCH_TIMEOUT_SECONDS)
                .callTimeoutSeconds(FETCH_TIMEOUT_SECONDS)
                .followRedirects(false);
        if (proxy != null) {
            try {
                return requireRemoteHttpPort().executeDetailed(builder.proxy(proxy).build());
            } catch (Exception proxyFailure) {
                log.warn("{} WebFetch transport failed stage=proxy category={} retry={} url={}",
                        requestId(), errorCategory(proxyFailure),
                        !Boolean.TRUE.equals(config.getWebFetchProxyRequired()), url);
                if (Boolean.TRUE.equals(config.getWebFetchProxyRequired())) {
                    throw new IOException("configured proxy is required but unavailable", proxyFailure);
                }
            }
        }
        return requireRemoteHttpPort().executeDetailed(builder.proxy(null).build());
    }

    private static String rootCauseSummary(Throwable error) {
        Throwable cursor = error;
        while (cursor.getCause() != null && cursor.getCause() != cursor) {
            cursor = cursor.getCause();
        }
        String message = StringUtils.defaultIfBlank(cursor.getMessage(), cursor.getClass().getSimpleName());
        return cursor.getClass().getSimpleName() + ": " + message;
    }

    private record ExtractOutcome(String content, boolean degraded, String errorSummary) {
        private static ExtractOutcome success(String content) {
            return new ExtractOutcome(content, false, null);
        }

        private static ExtractOutcome degraded(String content, String errorSummary) {
            return new ExtractOutcome(content, true, errorSummary);
        }
    }

    private static String htmlToText(String html) {
        String cleaned = SCRIPT_STYLE.matcher(html).replaceAll(" ");
        cleaned = cleaned.replaceAll("(?i)<br\\s*/?>", "\n");
        cleaned = cleaned.replaceAll("(?i)</p>", "\n\n");
        cleaned = cleaned.replaceAll("(?i)</div>", "\n");
        cleaned = cleaned.replaceAll("(?i)</h[1-6]>", "\n\n");
        cleaned = cleaned.replaceAll("(?i)</li>", "\n");
        cleaned = cleaned.replaceAll("(?i)<li[^>]*>", "- ");
        cleaned = TAG.matcher(cleaned).replaceAll(" ");
        cleaned = decodeBasicEntities(cleaned);
        cleaned = MULTI_SPACE.matcher(cleaned).replaceAll(" ");
        cleaned = MULTI_NL.matcher(cleaned).replaceAll("\n\n");
        return cleaned.trim();
    }

    private static String decodeBasicEntities(String text) {
        return text
                .replace("&nbsp;", " ")
                .replace("&amp;", "&")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
                .replace("&quot;", "\"")
                .replace("&#39;", "'")
                .replace("&apos;", "'");
    }

    private static boolean looksLikeHtml(String body) {
        String sample = body.length() > 500 ? body.substring(0, 500).toLowerCase(Locale.ROOT) : body.toLowerCase(Locale.ROOT);
        return sample.contains("<html") || sample.contains("<body") || sample.contains("<div") || sample.contains("<p");
    }

    private static String truncateContent(String content) {
        if (content == null || content.length() <= MAX_MARKDOWN_LENGTH) {
            return content;
        }
        return content.substring(0, MAX_MARKDOWN_LENGTH) + "\n\n[Content truncated due to length...]";
    }

    private static PageMetadata extractPageMetadata(String html) {
        Matcher title = HTML_TITLE.matcher(StringUtils.defaultString(html));
        Matcher date = META_DATE.matcher(StringUtils.defaultString(html));
        return new PageMetadata(
                title.find() ? htmlToText(title.group(1)) : "",
                date.find() ? date.group(1).trim() : "unknown"
        );
    }

    private Map<String, Object> sourceMetadata(FetchResult fetch, String content) {
        Map<String, Object> source = new LinkedHashMap<>();
        String url = StringUtils.defaultString(fetch.finalUrl());
        source.put("sourceId", "web-" + UUID.nameUUIDFromBytes(url.getBytes(StandardCharsets.UTF_8)));
        source.put("title", StringUtils.defaultIfBlank(fetch.title(), url));
        source.put("url", url);
        source.put("sourceType", "web_page");
        source.put("contentScope", fetch.content().length() > MAX_MARKDOWN_LENGTH ? "page_text_truncated" : "page_text");
        source.put("contentExcerpt", StringUtils.abbreviate(StringUtils.defaultString(content), SOURCE_EXCERPT_CHARS));
        source.put("contentChars", fetch.content().length());
        source.put("fetchedAt", fetch.fetchedAt());
        source.put("sourceDate", StringUtils.defaultIfBlank(fetch.sourceDate(), "unknown"));
        return source;
    }

    private static void validateUrl(String url) {
        if (url.length() > MAX_URL_LENGTH) {
            throw new IllegalArgumentException("URL too long (max " + MAX_URL_LENGTH + ")");
        }
        URI uri;
        try {
            uri = URI.create(url);
        } catch (Exception e) {
            throw new IllegalArgumentException("Invalid URL: " + url);
        }
        String scheme = StringUtils.defaultString(uri.getScheme()).toLowerCase(Locale.ROOT);
        if (!"http".equals(scheme) && !"https".equals(scheme)) {
            throw new IllegalArgumentException("Only http/https URLs are supported");
        }
        if (StringUtils.isNotBlank(uri.getUserInfo())) {
            throw new IllegalArgumentException("URL must not contain username/password");
        }
        String host = uri.getHost();
        if (StringUtils.isBlank(host)) {
            throw new IllegalArgumentException("URL hostname is required");
        }
        // 允许 localhost / 裸主机名用于内网与测试；公网域名仍应含点
        if (!"localhost".equalsIgnoreCase(host) && !host.contains(".")) {
            throw new IllegalArgumentException("URL hostname is not publicly resolvable style: " + host);
        }
    }

    private static String upgradeToHttps(String url) {
        if (url != null && url.regionMatches(true, 0, "http://", 0, 7)) {
            return "https://" + url.substring(7);
        }
        return url;
    }

    /**
     * 仅允许同主机（含 www. 增删）的 redirect；跨域返回给模型再调。
     */
    static boolean isPermittedRedirect(String originalUrl, String redirectUrl) {
        try {
            URI original = URI.create(originalUrl);
            URI redirect = URI.create(redirectUrl);
            if (!StringUtils.equalsIgnoreCase(original.getScheme(), redirect.getScheme())) {
                return false;
            }
            int originalPort = original.getPort();
            int redirectPort = redirect.getPort();
            if (originalPort != redirectPort) {
                return false;
            }
            if (StringUtils.isNotBlank(redirect.getUserInfo())) {
                return false;
            }
            String o = stripWww(StringUtils.defaultString(original.getHost()).toLowerCase(Locale.ROOT));
            String r = stripWww(StringUtils.defaultString(redirect.getHost()).toLowerCase(Locale.ROOT));
            return o.equals(r);
        } catch (Exception e) {
            return false;
        }
    }

    private static String stripWww(String hostname) {
        return hostname.startsWith("www.") ? hostname.substring(4) : hostname;
    }

    private static String headerIgnoreCase(Map<String, String> headers, String name) {
        if (headers == null || name == null) {
            return null;
        }
        for (Map.Entry<String, String> entry : headers.entrySet()) {
            if (entry.getKey() != null && entry.getKey().equalsIgnoreCase(name)) {
                return entry.getValue();
            }
        }
        return null;
    }

    private ToolResultPayload failure(String message, String url, String prompt) {
        return failure(message, url, prompt, "invalid_request", 0L);
    }

    private ToolResultPayload failure(String message,
                                      String url,
                                      String prompt,
                                      String category,
                                      long durationMs) {
        return ToolResultPayload.failureFrom(message, failureDetails(url, prompt, category, durationMs));
    }

    private Map<String, Object> failureDetails(String url,
                                               String prompt,
                                               String category,
                                               long durationMs) {
        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("type", "tool_error");
        detail.put("tool", "web_fetch");
        detail.put("stage", "fetch");
        detail.put("category", category);
        detail.put("durationMs", durationMs);
        if (StringUtils.isNotBlank(url)) {
            detail.put("url", url);
        }
        if (StringUtils.isNotBlank(prompt)) {
            detail.put("prompt", prompt);
        }
        return detail;
    }

    private static String httpCategory(int status) {
        if (status == 401 || status == 403) {
            return "access_denied";
        }
        if (status == 429) {
            return "rate_limited";
        }
        if (status >= 500) {
            return "upstream_unavailable";
        }
        return "http_error";
    }

    private static String errorCategory(Throwable error) {
        Throwable cursor = error;
        while (cursor.getCause() != null && cursor.getCause() != cursor) {
            cursor = cursor.getCause();
        }
        String name = cursor.getClass().getSimpleName().toLowerCase(Locale.ROOT);
        String message = StringUtils.defaultString(cursor.getMessage()).toLowerCase(Locale.ROOT);
        if (name.contains("timeout") || message.contains("timed out")) {
            return "temporary_timeout";
        }
        if (message.contains("proxy")) {
            return "proxy_unavailable";
        }
        if (name.contains("unknownhost") || name.contains("connect") || name.contains("socket") || name.contains("io")) {
            return "network_unavailable";
        }
        if (name.contains("illegalargument")) {
            return "invalid_request";
        }
        return "fetch_failed";
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> coerceMap(Object input) {
        if (input instanceof Map<?, ?> map) {
            return (Map<String, Object>) map;
        }
        return Map.of();
    }

    private String valueAsString(Object value) {
        return value == null ? null : String.valueOf(value);
    }

    private String requestId() {
        return agentContext == null ? "unknown" : StringUtils.defaultString(agentContext.getRequestId(), "unknown");
    }

    private AI4SConfig requireAI4SConfig() {
        if (agentContext == null || agentContext.getRuntimeDependencies() == null) {
            throw new IllegalStateException("WebFetchTool 缺少 AI4SRuntimeDependencies");
        }
        return agentContext.getRuntimeDependencies().requireAI4SConfig();
    }

    private RemoteHttpPort requireRemoteHttpPort() {
        if (agentContext == null || agentContext.getRuntimeDependencies() == null) {
            throw new IllegalStateException("WebFetchTool 缺少 AI4SRuntimeDependencies");
        }
        return agentContext.getRuntimeDependencies().requireRemoteHttpPort();
    }

    private static final class FetchHttpException extends IllegalStateException {
        private final int statusCode;
        private final String url;
        private final String statusText;
        private final String responseBody;

        private FetchHttpException(int statusCode,
                                   String url,
                                   String statusText,
                                   String responseBody) {
            super("HTTP " + statusCode);
            this.statusCode = statusCode;
            this.url = url;
            this.statusText = statusText;
            this.responseBody = responseBody;
        }
    }

    private record FetchResult(
            boolean redirect,
            String originalUrl,
            String redirectUrl,
            String finalUrl,
            int statusCode,
            String statusText,
            String content,
            int bytes,
            String title,
            String sourceDate,
            String fetchedAt
    ) {
        static FetchResult redirect(String original, String redirect, int code) {
            return new FetchResult(true, original, redirect, null, code, null, null, 0,
                    null, "unknown", Instant.now().toString());
        }

        static FetchResult content(String finalUrl, int code, String statusText, String content, int bytes,
                                   String title, String sourceDate, String fetchedAt) {
            return new FetchResult(false, null, null, finalUrl, code, statusText, content, bytes,
                    title, sourceDate, fetchedAt);
        }
    }

    private record PageMetadata(String title, String sourceDate) {
        static PageMetadata empty() {
            return new PageMetadata("", "unknown");
        }
    }
}
