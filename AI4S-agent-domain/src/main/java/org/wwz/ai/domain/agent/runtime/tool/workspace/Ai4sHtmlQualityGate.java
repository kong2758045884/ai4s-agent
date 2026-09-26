package org.wwz.ai.domain.agent.runtime.tool.workspace;

import java.net.URI;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 对 AI4S 正式 HTML 产物做不依赖模型的封稿检查。只在 report/ 与 poster/ 下的
 * HTML 文件最终移除追加标记时触发，其他工作区文件不受影响。
 */
final class Ai4sHtmlQualityGate {

    private static final List<String> CHAPTERS = List.of(
            "事件概览", "技术路线", "主要创新", "论文团队", "前序工作",
            "竞争路线", "AI4S意义", "待观察问题", "来源证据");
    private static final Pattern H2 = Pattern.compile("<h2\\b[^>]*>(.*?)</h2>", Pattern.CASE_INSENSITIVE | Pattern.DOTALL);
    private static final Pattern TAG = Pattern.compile("<[^>]*>");
    private static final Pattern SOURCE_SECTION = Pattern.compile(
            "<section\\b[^>]*\\bid\\s*=\\s*['\"](?:ch9|c9)['\"][^>]*>(.*?)</section>",
            Pattern.CASE_INSENSITIVE | Pattern.DOTALL);
    private static final Pattern SOURCE_ID = Pattern.compile("\\[S\\d{2,3}\\]");
    private static final Pattern CITATION_LINK = Pattern.compile(
            "<a\\b[^>]*\\bhref\\s*=\\s*['\"]#s(\\d{2,3})['\"][^>]*>(.*?)</a>",
            Pattern.CASE_INSENSITIVE | Pattern.DOTALL);
    private static final Pattern SOURCE_ANCHOR = Pattern.compile("\\bid\\s*=\\s*['\"]s(\\d{2,3})['\"]", Pattern.CASE_INSENSITIVE);
    private static final Pattern EXTERNAL_LINK = Pattern.compile("\\bhref\\s*=\\s*['\"](https?://[^'\"]+)['\"]", Pattern.CASE_INSENSITIVE);
    private static final Pattern HREF = Pattern.compile("\\bhref\\s*=\\s*['\"]([^'\"]+)['\"]", Pattern.CASE_INSENSITIVE);
    private static final Pattern OLD_PLACEHOLDER = Pattern.compile("<!--\\s*CH\\d+\\s*-->", Pattern.CASE_INSENSITIVE);

    private Ai4sHtmlQualityGate() {
    }

    static String validateFinalized(String relativePath, String html) {
        String path = relativePath.replace('\\', '/').toLowerCase(Locale.ROOT);
        if (!path.endsWith(".html") || (!path.startsWith("report/") && !path.startsWith("poster/"))) {
            return null;
        }
        List<String> issues = new ArrayList<>();
        if (!html.toLowerCase(Locale.ROOT).contains("</body>")
                || !html.toLowerCase(Locale.ROOT).contains("</html>")) {
            issues.add("缺少 </body> 或 </html> 结束标签");
        }
        if (html.contains(WorkspaceAppendTool.APPEND_MARKER) || OLD_PLACEHOLDER.matcher(html).find()) {
            issues.add("仍有未完成占位符");
        }
        if (path.startsWith("report/")) {
            validateReport(html, issues);
        } else {
            if (!Pattern.compile("@media\\s+print", Pattern.CASE_INSENSITIVE).matcher(html).find()) {
                issues.add("海报缺少 @media print 打印样式");
            }
            if (!posterHasSourceLink(html)) {
                issues.add("海报缺少可点击的原始来源或报告第九章链接");
            }
        }
        return issues.isEmpty() ? null : "AI4S HTML 封稿校验未通过：" + String.join("；", issues);
    }

    private static void validateReport(String html, List<String> issues) {
        Matcher matcher = H2.matcher(html);
        List<String> actual = new ArrayList<>();
        while (matcher.find()) {
            actual.add(TAG.matcher(matcher.group(1)).replaceAll("").replaceAll("\\s+", "").trim());
        }
        if (!CHAPTERS.equals(actual)) {
            issues.add("顶级 <h2> 必须恰好九章且顺序/标题为 " + String.join(" / ", CHAPTERS)
                    + "；实际为 " + String.join(" / ", actual)
                    + "。摘要等非章节标题请用 <h3>");
        }
        Matcher sourceSection = SOURCE_SECTION.matcher(html);
        if (!sourceSection.find()) {
            issues.add("第九章缺少 id=ch9 或 id=c9 的 <section>");
            return;
        }
        String body = html.substring(0, sourceSection.start());
        Set<String> cited = sourceIds(body);
        Matcher citationLinks = CITATION_LINK.matcher(body);
        while (citationLinks.find()) {
            String number = citationLinks.group(1);
            if (visibleCitation(citationLinks.group(2), number)) {
                cited.add("[S" + number + "]");
            }
        }
        Set<String> listed = anchoredSourceIds(sourceSection.group(1));
        Set<String> allAnchors = anchoredSourceIds(html);
        Map<String, Integer> anchorCounts = new HashMap<>();
        Matcher anchors = SOURCE_ANCHOR.matcher(html);
        while (anchors.find()) {
            anchorCounts.merge("[S" + anchors.group(1) + "]", 1, Integer::sum);
        }
        for (Map.Entry<String, Integer> entry : anchorCounts.entrySet()) {
            if (entry.getValue() > 1) {
                issues.add("来源锚点 " + entry.getKey() + " 重复 " + entry.getValue() + " 次");
            }
        }
        if (cited.isEmpty()) {
            issues.add("正文缺少 [Sxx] 来源引用");
        }
        Set<String> missing = new LinkedHashSet<>(cited);
        missing.removeAll(listed);
        Set<String> unused = new LinkedHashSet<>(listed);
        unused.removeAll(cited);
        if (!missing.isEmpty() || !unused.isEmpty()) {
            issues.add("正文引用与第九章来源编号不一致；未列出的引用=" + missing + "，未使用的来源=" + unused);
        }
        for (String id : cited) {
            String number = id.substring(2, id.length() - 1);
            if (!hasLinkedCitation(body, number)) {
                issues.add("正文引用 " + id + " 缺少指向 #s" + number + " 的可点击链接");
            }
        }
        Set<String> outside = new LinkedHashSet<>(allAnchors);
        outside.removeAll(listed);
        if (!outside.isEmpty()) {
            issues.add("来源条目落在第九章 <section> 之外=" + outside);
        }
        for (String id : listed) {
            if (!sourceEntryHasLink(sourceSection.group(1), id)) {
                issues.add("来源 " + id + " 缺少可点击的 http(s) 原始链接");
            }
        }
    }

    private static Set<String> sourceIds(String text) {
        Set<String> ids = new LinkedHashSet<>();
        Matcher matcher = SOURCE_ID.matcher(text);
        while (matcher.find()) {
            ids.add(matcher.group());
        }
        return ids;
    }

    private static Set<String> anchoredSourceIds(String text) {
        Set<String> ids = new LinkedHashSet<>();
        Matcher matcher = SOURCE_ANCHOR.matcher(text);
        while (matcher.find()) {
            ids.add("[S" + matcher.group(1) + "]");
        }
        return ids;
    }

    private static boolean sourceEntryHasLink(String sourceSection, String id) {
        String number = id.substring(2, id.length() - 1);
        Pattern entry = Pattern.compile(
                "<(tr|li|article|div|p|a)\\b(?=[^>]*\\bid\\s*=\\s*['\"]s"
                        + Pattern.quote(number) + "['\"])[^>]*>.*?</\\1>",
                Pattern.CASE_INSENSITIVE | Pattern.DOTALL);
        Matcher matcher = entry.matcher(sourceSection);
        if (!matcher.find()) {
            return false;
        }
        Matcher links = EXTERNAL_LINK.matcher(matcher.group());
        while (links.find()) {
            if (validHttpLink(links.group(1))) {
                return true;
            }
        }
        return false;
    }

    private static boolean posterHasSourceLink(String html) {
        Matcher links = HREF.matcher(html);
        while (links.find()) {
            String href = links.group(1);
            if (validHttpLink(href)) {
                return true;
            }
            try {
                URI relative = URI.create(href);
                if (relative.getScheme() == null
                        && relative.getPath() != null
                        && relative.getPath().toLowerCase(Locale.ROOT).endsWith(".html")
                        && relative.getFragment() != null
                        && relative.getFragment().matches("(?i)s\\d{2,3}")) {
                    return true;
                }
            } catch (IllegalArgumentException ignored) {
                // A malformed URL cannot be followed to a source.
            }
        }
        return false;
    }

    private static boolean validHttpLink(String href) {
        try {
            URI url = URI.create(href);
            return ("http".equalsIgnoreCase(url.getScheme()) || "https".equalsIgnoreCase(url.getScheme()))
                    && url.getHost() != null && !url.getHost().isBlank();
        } catch (IllegalArgumentException ignored) {
            return false;
        }
    }

    private static boolean hasLinkedCitation(String body, String number) {
        Matcher matcher = CITATION_LINK.matcher(body);
        while (matcher.find()) {
            if (number.equals(matcher.group(1)) && visibleCitation(matcher.group(2), number)) {
                return true;
            }
        }
        return false;
    }

    private static boolean visibleCitation(String html, String number) {
        String visible = TAG.matcher(html).replaceAll("");
        return Pattern.compile("\\bS" + Pattern.quote(number) + "\\b", Pattern.CASE_INSENSITIVE)
                .matcher(visible).find();
    }
}
