package org.wwz.ai.test.spring.ai;

import org.junit.Assert;
import org.junit.Before;
import org.junit.Test;
import org.wwz.ai.domain.agent.runtime.agent.AgentContext;
import org.wwz.ai.domain.agent.runtime.tool.ToolResultPayload;
import org.wwz.ai.domain.agent.runtime.tool.workspace.WorkspaceAppendTool;
import org.wwz.ai.domain.agent.runtime.tool.workspace.WorkspacePathGuard;
import org.wwz.ai.domain.agent.runtime.tool.workspace.WorkspaceRuntimeOptions;
import org.wwz.ai.domain.agent.runtime.tool.workspace.WorkspaceService;
import org.wwz.ai.domain.agent.runtime.tool.skill.SkillRuntimeOptions;
import org.wwz.ai.domain.agent.runtime.tool.skill.SkillVirtualPaths;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

public class WorkspaceAppendToolTest {

    private Path file;
    private WorkspaceAppendTool tool;

    @Before
    public void setUp() throws Exception {
        Path root = Files.createTempDirectory("ai4s-workspace-append");
        file = root.resolve("report.html");
        Files.writeString(file,
                "<!doctype html><html><body><main><!--AI4S_APPEND--></main></body></html>",
                StandardCharsets.UTF_8);
        WorkspaceRuntimeOptions options = WorkspaceRuntimeOptions.builder()
                .enabled(true)
                .rootTemplate(root.toString())
                .maxWriteChars(20_000)
                .build();
        WorkspaceService service = new WorkspaceService(
                options,
                new WorkspacePathGuard(),
                null,
                new SkillVirtualPaths(SkillRuntimeOptions.builder().enabled(false).build()));
        tool = new WorkspaceAppendTool(service, options);
        tool.setAgentContext(AgentContext.builder()
                .requestId("append-test")
                .sessionId("append-session")
                .workspaceRoot(root.toString())
                .build());
    }

    @Test
    public void appendsInOrderAndRetriesIdempotentlyThenFinalizes() throws Exception {
        ToolResultPayload first = (ToolResultPayload) tool.execute(Map.of(
                "path", "report.html",
                "chunk_key", "chapter-01",
                "content", "<section id='one'><h2>事件概览</h2></section>"));
        Assert.assertFalse(Boolean.TRUE.equals(first.getFailed()));
        ToolResultPayload retry = (ToolResultPayload) tool.execute(Map.of(
                "path", "report.html",
                "chunk_key", "chapter-01",
                "content", "<section id='one'><h2>事件概览</h2></section>"));
        Assert.assertEquals(Boolean.TRUE, ((Map<?, ?>) retry.getLlmData()).get("duplicate"));
        ToolResultPayload last = (ToolResultPayload) tool.execute(Map.of(
                "path", "report.html",
                "chunk_key", "chapter-02",
                "content", "<section id='two'><h2>技术路线</h2></section>",
                "finalize", true));
        Assert.assertFalse(Boolean.TRUE.equals(last.getFailed()));
        Assert.assertEquals(Boolean.TRUE, ((Map<?, ?>) last.getLlmData()).get("finalized"));
        String html = Files.readString(file, StandardCharsets.UTF_8);
        Assert.assertEquals(1, html.split("id='one'", -1).length - 1);
        Assert.assertTrue(html.indexOf("id='one'") < html.indexOf("id='two'"));
        Assert.assertFalse(html.contains(WorkspaceAppendTool.APPEND_MARKER));
        Assert.assertTrue(html.endsWith("</html>"));
    }

    @Test
    public void rejectsMissingMarkerOrOversizedChunkWithoutChangingFile() throws Exception {
        String original = Files.readString(file, StandardCharsets.UTF_8);
        ToolResultPayload tooLong = (ToolResultPayload) tool.execute(Map.of(
                "path", "report.html", "chunk_key", "long", "content", "x".repeat(6001)));
        Assert.assertTrue(Boolean.TRUE.equals(tooLong.getFailed()));
        Assert.assertEquals(original, Files.readString(file, StandardCharsets.UTF_8));

        Files.writeString(file, "<html></html>", StandardCharsets.UTF_8);
        ToolResultPayload noMarker = (ToolResultPayload) tool.execute(Map.of(
                "path", "report.html", "chunk_key", "chapter-01", "content", "<section>body</section>"));
        Assert.assertTrue(Boolean.TRUE.equals(noMarker.getFailed()));
        Assert.assertEquals("<html></html>", Files.readString(file, StandardCharsets.UTF_8));
    }

    @Test
    public void rejectsDuplicateChunkKeyAfterFinalizationWithoutReopeningFile() throws Exception {
        tool.execute(Map.of("path", "report.html", "chunk_key", "last", "content", "<p>done</p>", "finalize", true));
        String finished = Files.readString(file, StandardCharsets.UTF_8);
        ToolResultPayload retry = (ToolResultPayload) tool.execute(Map.of(
                "path", "report.html", "chunk_key", "last", "content", "<p>done</p>", "finalize", true));
        Assert.assertFalse(Boolean.TRUE.equals(retry.getFailed()));
        Assert.assertEquals(Boolean.TRUE, ((Map<?, ?>) retry.getLlmData()).get("duplicate"));
        Assert.assertEquals(finished, Files.readString(file, StandardCharsets.UTF_8));
    }

    @Test
    public void reportFinalizationRejectsExtraOrWrongChapterAndUnmatchedSources() throws Exception {
        Path report = file.getParent().resolve("report").resolve("item.html");
        Files.createDirectories(report.getParent());
        Files.writeString(report,
                "<html><body><main><h2>结论摘要</h2><!--AI4S_APPEND--></main></body></html>",
                StandardCharsets.UTF_8);

        ToolResultPayload extraHeading = appendFinalReport(reportSections("论文团队", "[S01]"));
        Assert.assertTrue(Boolean.TRUE.equals(extraHeading.getFailed()));
        Assert.assertTrue(Files.readString(report).contains(WorkspaceAppendTool.APPEND_MARKER));
        Assert.assertFalse(Files.readString(report).contains("AI4S_CHUNK"));

        Files.writeString(report,
                "<html><body><main><h3>结论摘要</h3><!--AI4S_APPEND--></main></body></html>",
                StandardCharsets.UTF_8);
        ToolResultPayload wrongChapter = appendFinalReport(reportSections("论文与团队", "[S01]"));
        Assert.assertTrue(Boolean.TRUE.equals(wrongChapter.getFailed()));
        ToolResultPayload missingSource = appendFinalReport(reportSections("论文团队", "[S02]"));
        Assert.assertTrue(Boolean.TRUE.equals(missingSource.getFailed()));

        ToolResultPayload unlinkedSource = appendFinalReport(
                reportSections("论文团队", "[S01]").replace(" href='https://example.org/s01'", ""));
        Assert.assertTrue(Boolean.TRUE.equals(unlinkedSource.getFailed()));

        ToolResultPayload invalidSourceUrl = appendFinalReport(
                reportSections("论文团队", "[S01]").replace("https://example.org/s01", "https://"));
        Assert.assertTrue(Boolean.TRUE.equals(invalidSourceUrl.getFailed()));

        ToolResultPayload unlinkedCitation = appendFinalReport(
                reportSections("论文团队", "[S01]").replace("<a href='#s01'>[S01]</a>", "[S01]"));
        Assert.assertTrue(Boolean.TRUE.equals(unlinkedCitation.getFailed()));

        ToolResultPayload sourceOutsideChapter = appendFinalReport(
                reportSections("论文团队", "[S01]").replace(
                        "<section id='ch9'><h2>来源证据</h2>",
                        "<section id='ch9'><h2>来源证据</h2></section>"));
        Assert.assertTrue(Boolean.TRUE.equals(sourceOutsideChapter.getFailed()));

        ToolResultPayload duplicateSource = appendFinalReport(
                reportSections("论文团队", "[S01]").replace(
                        "<section id='ch9'><h2>来源证据</h2>",
                        "<p id='s01'>重复锚点</p><section id='ch9'><h2>来源证据</h2>"));
        Assert.assertTrue(Boolean.TRUE.equals(duplicateSource.getFailed()));

        ToolResultPayload accepted = appendFinalReport(
                reportSections("论文团队", "[S01]").replace(">[S01]</a>", ">S01</a>"));
        Assert.assertFalse(Boolean.TRUE.equals(accepted.getFailed()));
        Assert.assertFalse(Files.readString(report).contains(WorkspaceAppendTool.APPEND_MARKER));
    }

    @Test
    public void posterFinalizationRequiresPrintStyles() throws Exception {
        Path poster = file.getParent().resolve("poster").resolve("item.html");
        Files.createDirectories(poster.getParent());
        Files.writeString(poster, "<html><body><!--AI4S_APPEND--></body></html>", StandardCharsets.UTF_8);
        ToolResultPayload missingStyle = (ToolResultPayload) tool.execute(Map.of(
                "path", "poster/item.html", "chunk_key", "poster", "content", "<h1>海报</h1>", "finalize", true));
        Assert.assertTrue(Boolean.TRUE.equals(missingStyle.getFailed()));
        Files.writeString(poster,
                "<html><head><style>@media print { body { color: black; } }</style></head><body><!--AI4S_APPEND--></body></html>",
                StandardCharsets.UTF_8);
        ToolResultPayload missingLink = (ToolResultPayload) tool.execute(Map.of(
                "path", "poster/item.html", "chunk_key", "poster", "content", "<h1>海报</h1>", "finalize", true));
        Assert.assertTrue(Boolean.TRUE.equals(missingLink.getFailed()));
        ToolResultPayload invalidLink = (ToolResultPayload) tool.execute(Map.of(
                "path", "poster/item.html", "chunk_key", "poster",
                "content", "<h1>海报</h1><a href='https://'>来源</a>", "finalize", true));
        Assert.assertTrue(Boolean.TRUE.equals(invalidLink.getFailed()));
        ToolResultPayload accepted = (ToolResultPayload) tool.execute(Map.of(
                "path", "poster/item.html", "chunk_key", "poster",
                "content", "<h1>海报</h1><a href='https://example.org/source'>来源</a>", "finalize", true));
        Assert.assertFalse(Boolean.TRUE.equals(accepted.getFailed()));
    }

    private ToolResultPayload appendFinalReport(String content) {
        return (ToolResultPayload) tool.execute(Map.of(
                "path", "report/item.html", "chunk_key", "all-chapters", "content", content, "finalize", true));
    }

    private String reportSections(String fourthTitle, String sourceId) {
        List<String> chapters = List.of("事件概览", "技术路线", "主要创新", fourthTitle,
                "前序工作", "竞争路线", "AI4S意义", "待观察问题", "来源证据");
        StringBuilder html = new StringBuilder();
        for (int i = 0; i < chapters.size(); i++) {
            html.append("<section id='ch").append(i + 1).append("'><h2>")
                    .append(chapters.get(i)).append("</h2>");
            if (i == 0) html.append("<p>有来源的正文 <a href='#s01'>[S01]</a></p>");
            if (i == 8) html.append("<p id='s").append(sourceId, 2, sourceId.length() - 1)
                    .append("'>").append(sourceId)
                    .append(" <a href='https://example.org/s01'>来源</a></p>");
            html.append("</section>");
        }
        return html.toString();
    }
}
