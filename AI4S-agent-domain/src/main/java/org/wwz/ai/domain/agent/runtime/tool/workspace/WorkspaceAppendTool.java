package org.wwz.ai.domain.agent.runtime.tool.workspace;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 在完整 HTML 骨架中的唯一占位符前插入短片段，避免长报告单次输出超限。
 * chunk_key 使模型重试幂等；不依赖会被上下文压缩清空的 workspace_read 状态。
 */
public class WorkspaceAppendTool extends AbstractWorkspacePathTool {

    public static final String APPEND_MARKER = "<!--AI4S_APPEND-->";
    private static final String CHUNK_PREFIX = "<!--AI4S_CHUNK:";
    private static final int MAX_CHUNK_CHARS = 6000;

    public WorkspaceAppendTool(WorkspaceService workspaceService, WorkspaceRuntimeOptions workspaceRuntimeOptions) {
        super(workspaceService, workspaceRuntimeOptions);
    }

    @Override
    public String getName() {
        return "workspace_append";
    }

    @Override
    public String getDescription() {
        return withWorkspaceHint(
                "向工作区中已经存在的 HTML 骨架逐段插入内容。骨架需在 </body> 前含唯一 "
                        + APPEND_MARKER + "。每次 content 不超过 6000 字符，chunk_key 如 chapter-01-a；"
                        + "相同 chunk_key 重试不会重复写。每段成功后再写下一段，最后一段设 finalize=true 移除占位符。"
                        + "不创建文件；先用 workspace_write 创建骨架。"
        );
    }

    @Override
    public Map<String, Object> toParams() {
        Map<String, Object> properties = new LinkedHashMap<>();
        properties.put("path", Map.of("type", "string", "description", "工作区已有 HTML 文件路径"));
        properties.put("chunk_key", Map.of("type", "string", "description", "此片段的稳定唯一键；重试使用同一个键"));
        properties.put("content", Map.of("type", "string", "description", "本次插入的 HTML 片段，不超过 6000 字符"));
        properties.put("finalize", Map.of("type", "boolean", "description", "最后一段设 true，移除未完成占位符；默认 false"));
        Map<String, Object> parameters = new LinkedHashMap<>();
        parameters.put("type", "object");
        parameters.put("properties", properties);
        parameters.put("required", List.of("path", "chunk_key", "content"));
        return parameters;
    }

    @Override
    public Object execute(Object input) {
        String agentPath = "";
        try {
            Map<String, Object> params = requireInputMap(input);
            Path root = requireWorkspaceRoot();
            Path filePath = requireWritablePath(params);
            agentPath = toAgentPath(filePath);
            if (!filePath.startsWith(root) || !Files.isRegularFile(filePath)) {
                return failResult("workspace_append 只支持会话工作区中已存在的文件");
            }
            String key = String.valueOf(params.getOrDefault("chunk_key", "")).trim();
            if (!key.matches("[A-Za-z0-9_-]{1,64}")) {
                return failResult("chunk_key 必须为 1–64 位字母、数字、下划线或连字符");
            }
            Object value = params.get("content");
            if (value == null) {
                return failResult("content is required");
            }
            String content = String.valueOf(value);
            if (content.isBlank() || content.length() > MAX_CHUNK_CHARS
                    || content.contains(APPEND_MARKER) || content.contains(CHUNK_PREFIX)) {
                return failResult("content 必须为非空、至多 6000 字符且不能含工作区内部标记");
            }
            boolean finalize = readBoolean(params, "finalize", false);
            String chunkMarker = CHUNK_PREFIX + key + "-->";

            synchronized (WorkspaceAppendTool.class) {
                String original = Files.readString(filePath, StandardCharsets.UTF_8);
                boolean duplicate = original.contains(chunkMarker);
                int markerIndex = original.indexOf(APPEND_MARKER);
                if (!duplicate && (markerIndex < 0 || markerIndex != original.lastIndexOf(APPEND_MARKER))) {
                    return failResult("HTML 骨架必须包含唯一的 " + APPEND_MARKER + "；已封稿的文件不能再追加");
                }
                String updated = original;
                if (!duplicate) {
                    updated = original.substring(0, markerIndex)
                            + chunkMarker + "\n" + content + "\n"
                            + original.substring(markerIndex);
                }
                if (finalize && updated.contains(APPEND_MARKER)) {
                    updated = updated.replace(APPEND_MARKER, "");
                }
                if (finalize) {
                    String qualityIssue = Ai4sHtmlQualityGate.validateFinalized(toRelativePath(root, filePath), updated);
                    if (qualityIssue != null) {
                        return failResult(qualityIssue);
                    }
                }
                if (updated.length() > workspaceRuntimeOptions.getMaxWriteChars()) {
                    return failResult("追加后文件超过最大写入字符数 " + workspaceRuntimeOptions.getMaxWriteChars());
                }
                if (!updated.equals(original)) {
                    Files.writeString(filePath, updated, StandardCharsets.UTF_8);
                    if (agentContext != null) {
                        agentContext.markWorkspaceFileRead(WorkspaceFileReadState.builder()
                                .absolutePath(filePath.toAbsolutePath().normalize().toString())
                                .mtimeMs(Files.getLastModifiedTime(filePath).toMillis())
                                .startLine(1)
                                .lineCount(Integer.MAX_VALUE)
                                .contentHash(WorkspaceReadStateStore.sha256Hex(updated))
                                .build());
                    }
                    WorkspaceFileRegistration.registerLocalFile(
                            agentContext, toRelativePath(root, filePath), filePath, "追加文档片段");
                }
                Map<String, Object> data = new LinkedHashMap<>();
                data.put("path", agentPath);
                data.put("chunkKey", key);
                data.put("duplicate", duplicate);
                data.put("finalized", !updated.contains(APPEND_MARKER));
                data.put("totalChars", updated.length());
                return okResult(data);
            }
        } catch (WorkspaceAccessException e) {
            log.warn("{} workspace_append rejected path={}: {}", requestId(), agentPath, e.getMessage());
            return failResult(e.getMessage());
        } catch (IOException e) {
            log.error("{} workspace_append io error path={}", requestId(), agentPath, e);
            return failResult("workspace_append failed to write file");
        } catch (Exception e) {
            log.error("{} workspace_append error path={}", requestId(), agentPath, e);
            return failResult("workspace_append execute failed");
        }
    }
}
