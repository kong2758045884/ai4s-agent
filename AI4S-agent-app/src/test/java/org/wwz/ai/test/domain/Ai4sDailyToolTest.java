package org.wwz.ai.test.domain;

import org.junit.Assert;
import org.junit.Test;
import org.wwz.ai.domain.agent.adapter.port.RemoteHttpPort;
import org.wwz.ai.domain.agent.adapter.port.RemoteHttpRequest;
import org.wwz.ai.domain.agent.adapter.port.RemoteHttpResponse;
import org.wwz.ai.domain.agent.runtime.agent.AgentContext;
import org.wwz.ai.domain.agent.runtime.tool.ToolResultPayload;
import org.wwz.ai.domain.agent.runtime.tool.common.Ai4sDailyTool;
import org.wwz.ai.domain.agent.ai4s.config.AI4SConfig;
import org.wwz.ai.test.domain.support.AI4SRuntimeTestSupport;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * AI4S Daily 静态报告读取、解析和溯源回归测试。
 */
public class Ai4sDailyToolTest {

    private static final String BASE_URL = "https://daily.example/AI4S-Daily-HTML";
    private static final String INDEX_URL = BASE_URL + "/reports/index.json";
    private static final String REPORT_URL = BASE_URL + "/reports/push-2026-09-18-08-01-39.md";
    private static final String REPORT = """
            ---
            title: "AI4S前沿观察报告（9.18）"
            excerpt: "材料发现与科研智能体最新进展"
            date: "2026-09-18"
            pushTime: "2026-09-18T08:00:01+08:00"
            highlights: ["材料迁移学习突破冷启动"]
            ---

            # AI4S前沿观察报告（9.18）

            <!-- SECTION:insights BEGIN -->
            ## 📋 总览

            - 材料发现正在使用迁移学习减少昂贵的计算。
            <!-- SECTION:insights END -->

            <!-- SECTION:rss BEGIN -->
            ## 📰 今日Top热点

            ### 1️⃣ 材料发现迁移学习突破冷启动

            * **内容摘要**：研究团队利用迁移学习修正合金尺寸因子计算，提升未探索材料性质预测精度。
            * **后续调研**：需要继续核验论文和实验条件。
            🔗 [论文原文](https://example.org/materials/paper)
            <!-- SECTION:rss END -->
            """;

    @Test
    public void shouldReadIndexAndExtractReportSectionsAndSources() {
        FakeHttpPort http = new FakeHttpPort(Map.of(
                INDEX_URL, "[\"push-2026-09-18-08-01-39.md\"]",
                REPORT_URL, REPORT
        ));
        Ai4sDailyTool tool = tool(http);

        ToolResultPayload payload = (ToolResultPayload) tool.execute(Map.of(
                "query", "最近 AI 在材料发现方面有什么新进展？"
        ));

        Assert.assertFalse(Boolean.TRUE.equals(payload.getFailed()));
        Map<?, ?> data = (Map<?, ?>) payload.getLlmData();
        Assert.assertEquals("ai4s_daily", data.get("tool"));
        Assert.assertEquals(Boolean.TRUE, data.get("matched"));
        Assert.assertEquals(INDEX_URL, data.get("indexUrl"));

        List<?> reports = (List<?>) data.get("reports");
        Assert.assertEquals(1, reports.size());
        Map<?, ?> report = (Map<?, ?>) reports.get(0);
        Assert.assertEquals("push-2026-09-18-08-01-39", report.get("reportId"));
        Assert.assertEquals(REPORT_URL, report.get("reportUrl"));
        Assert.assertEquals("2026-09-18", report.get("date"));

        List<?> sections = (List<?>) report.get("sections");
        Assert.assertEquals(2, sections.size());
        Map<?, ?> rss = (Map<?, ?>) sections.get(1);
        Assert.assertEquals("rss", rss.get("section"));
        List<?> hotspots = (List<?>) rss.get("hotspots");
        Assert.assertEquals(1, hotspots.size());
        Map<?, ?> hotspot = (Map<?, ?>) hotspots.get(0);
        Assert.assertTrue(String.valueOf(hotspot.get("title")).contains("材料发现"));
        Assert.assertTrue(String.valueOf(hotspot.get("content")).contains("迁移学习"));
        Assert.assertEquals(List.of("https://example.org/materials/paper"), hotspot.get("sourceUrls"));
    }

    @Test
    public void shouldKeepGoingWhenNoRecentReportMatches() {
        FakeHttpPort http = new FakeHttpPort(Map.of(
                INDEX_URL, "[\"push-2026-09-18-08-01-39.md\"]",
                REPORT_URL, REPORT
        ));
        Ai4sDailyTool tool = tool(http);

        ToolResultPayload payload = (ToolResultPayload) tool.execute(Map.of(
                "query", "用 Java 写一个快速排序"
        ));

        Assert.assertFalse(Boolean.TRUE.equals(payload.getFailed()));
        Map<?, ?> data = (Map<?, ?>) payload.getLlmData();
        Assert.assertEquals(Boolean.FALSE, data.get("matched"));
        Assert.assertTrue(String.valueOf(data.get("nextAction")).contains("WebSearch"));
    }

    @Test
    public void shouldIgnoreUnsafeReportFilenamesFromIndex() {
        FakeHttpPort http = new FakeHttpPort(Map.of(
                INDEX_URL, "[\"../secret.md\", \"https://evil.example/x.md\"]"
        ));
        Ai4sDailyTool tool = tool(http);

        ToolResultPayload payload = (ToolResultPayload) tool.execute(Map.of(
                "query", "材料发现"
        ));

        Assert.assertFalse(Boolean.TRUE.equals(payload.getFailed()));
        Map<?, ?> data = (Map<?, ?>) payload.getLlmData();
        Assert.assertEquals(Boolean.FALSE, data.get("matched"));
        Assert.assertTrue(String.valueOf(data.get("message")).contains("没有可用报告"));
        Assert.assertFalse(http.requestedUrls.contains("https://daily.example/secret.md"));
    }

    @Test
    public void shouldExposeSemanticToolInstructionsAndParameters() {
        Ai4sDailyTool tool = new Ai4sDailyTool();

        Assert.assertEquals("ai4s_daily", tool.getName());
        Assert.assertTrue(tool.getDescription().contains("没有提到“AI4S Daily”"));
        Assert.assertTrue(tool.getDescription().contains("不要在普通编程"));
        Assert.assertTrue(tool.toParams().containsKey("properties"));
        Assert.assertTrue(String.valueOf(tool.toParams().get("required")).contains("query"));
    }

    private Ai4sDailyTool tool(RemoteHttpPort httpPort) {
        AI4SConfig config = new AI4SConfig();
        config.setAi4sDailyBaseUrl(BASE_URL);
        AgentContext context = AgentContext.builder()
                .requestId("req-ai4s-daily-test")
                .sessionId("session-ai4s-daily-test")
                .runtimeDependencies(AI4SRuntimeTestSupport.runtimeDependencies(config, httpPort))
                .build();
        Ai4sDailyTool tool = new Ai4sDailyTool();
        tool.setAgentContext(context);
        return tool;
    }

    private static final class FakeHttpPort implements RemoteHttpPort {
        private final Map<String, String> responses;
        private final List<String> requestedUrls = new java.util.ArrayList<>();

        private FakeHttpPort(Map<String, String> responses) {
            this.responses = new HashMap<>(responses);
        }

        @Override
        public String execute(RemoteHttpRequest request) {
            return responses.getOrDefault(request.getUrl(), "");
        }

        @Override
        public RemoteHttpResponse executeDetailed(RemoteHttpRequest request) {
            requestedUrls.add(request.getUrl());
            String body = responses.get(request.getUrl());
            if (body == null) {
                return RemoteHttpResponse.builder()
                        .statusCode(404)
                        .statusText("Not Found")
                        .headers(Map.of())
                        .body("missing")
                        .finalUrl(request.getUrl())
                        .build();
            }
            return RemoteHttpResponse.builder()
                    .statusCode(200)
                    .statusText("OK")
                    .headers(Map.of("Content-Type", "text/plain"))
                    .body(body)
                    .finalUrl(request.getUrl())
                    .build();
        }
    }
}
