import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { describe, expect, it } from "vitest";
import { resolveToolRendererKind } from "./toolRegistry";
import { editDiffStats, formatEditDiffChip } from "./toolDiff";
import { resolveTaskToolOutput } from "./toolTaskAdapter";
import { GenericToolCall } from "./GenericToolCall";
import { EditToolCall } from "./EditToolCall";

function toolTask(
  partial: Partial<CHAT.Task> & { resultMap?: Record<string, unknown> }
): CHAT.Task {
  return {
    id: "t1",
    messageId: "t1",
    messageType: "tool_result",
    messageTime: "1",
    requestId: "r1",
    finish: true,
    isFinal: true,
    resultMap: {
      status: "success",
      isFinal: true,
      ...(partial.resultMap || {}),
    },
    ...partial,
  } as CHAT.Task;
}

describe("toolRegistry", () => {
  it("routes edit/write/multi_edit to edit renderer", () => {
    expect(
      resolveToolRendererKind(
        toolTask({
          resultMap: { toolName: "Edit", input: { path: "a.ts" } },
        })
      )
    ).toBe("edit");
    expect(
      resolveToolRendererKind(
        toolTask({
          resultMap: { toolName: "Write", input: { path: "b.ts" } },
        })
      )
    ).toBe("edit");
    expect(
      resolveToolRendererKind(
        toolTask({
          resultMap: { toolName: "MultiEdit", input: { path: "c.ts" } },
        })
      )
    ).toBe("edit");
  });

  it("routes Agent / task aliases to agent renderer", () => {
    expect(
      resolveToolRendererKind(
        toolTask({
          resultMap: {
            toolName: "Agent",
            input: { prompt: "x", subagent_type: "Explore" },
          },
        })
      )
    ).toBe("agent");
    expect(
      resolveToolRendererKind(
        toolTask({
          toolResult: {
            toolName: "Agent",
            toolResult: "status=completed\n\nok",
            toolParam: { query: "x" },
          },
        })
      )
    ).toBe("agent");
  });

  it("routes ask_user_question to askuser renderer", () => {
    expect(
      resolveToolRendererKind(
        toolTask({
          messageType: "ask_user_question",
          resultMap: { toolName: "AskUserQuestion" },
        })
      )
    ).toBe("askuser");
  });

  it("defaults to generic", () => {
    expect(
      resolveToolRendererKind(
        toolTask({
          resultMap: { toolName: "Bash", input: { command: "ls" } },
        })
      )
    ).toBe("generic");
  });

  it("reads nested tool output for a child tool result", () => {
    const task = toolTask({
      resultMap: {
        toolResult: {
          toolName: "workspace_grep",
          toolResult: "found Controller",
        },
      },
    });

    expect(resolveTaskToolOutput(task)).toContain("found Controller");
  });

  it("keeps generic tool results expandable inline instead of opening workspace", () => {
    const html = renderToStaticMarkup(
      createElement(GenericToolCall, {
        tool: toolTask({
          resultMap: {
            toolName: "workspace_grep",
            input: { pattern: "Controller" },
            toolResult: "found Controller",
          },
        }),
        chat: {} as CHAT.ChatItem,
        changeActiveChat: () => undefined,
      })
    );

    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain("参数");
    expect(html).toContain("found Controller");
  });

  it("renders canvas_publish as a previewable tool card", () => {
    const html = renderToStaticMarkup(
      createElement(GenericToolCall, {
        tool: toolTask({
          messageType: "tool_call",
          resultMap: {
            toolName: "canvas_publish",
            status: "success",
            input: { html_path: "pages/index.html" },
            fileInfo: [
              {
                fileName: "index.html",
                relativePath: "pages/index.html",
                domainUrl: "http://x/preview/pages/index.html",
                ossUrl: "http://x/download/pages/index.html",
                fileSize: 12,
              },
            ],
          },
        }),
        chat: {} as CHAT.ChatItem,
        changeActiveChat: () => undefined,
      })
    );

    expect(html).toContain("发布画布");
    expect(html).toContain("pages/index.html");
  });

  it("keeps edit input and output inline", () => {
    const html = renderToStaticMarkup(
      createElement(EditToolCall, {
        tool: toolTask({
          resultMap: {
            toolName: "Edit",
            input: { path: "a.ts", old_string: "old", new_string: "new" },
            toolResult: "updated a.ts",
          },
        }),
        chat: {} as CHAT.ChatItem,
        changeActiveChat: () => undefined,
      })
    );

    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain("a.ts");
    expect(html).toContain("updated a.ts");
  });

  it("pretty prints JSON input and output inside the tool row", () => {
    const html = renderToStaticMarkup(
      createElement(GenericToolCall, {
        tool: toolTask({
          resultMap: {
            toolName: "Bash",
            input: {
              command: "ls",
              options: { hidden: false },
            },
            toolResult: JSON.stringify({
              ok: true,
              files: ["a.ts", "b.ts"],
            }),
          },
        }),
        chat: {} as CHAT.ChatItem,
        changeActiveChat: () => undefined,
      })
    );

    expect(html.match(/data-testid="tool-json-block"/g)).toHaveLength(2);
    expect(html).toContain("files");
    expect(html).toContain("a.ts");
  });

  it("renders AI4S Daily provenance and follow-up deep search status", () => {
    const ai4sTask = toolTask({
      id: "ai4s-1",
      resultMap: { toolName: "ai4s_daily" },
      toolResult: {
        toolName: "ai4s_daily",
        toolResult: JSON.stringify({
          tool: "ai4s_daily",
          source: "AI4S Daily",
          matched: true,
          reports: [
            {
              reportId: "push-demo",
              title: "AI4S 前沿观察报告",
              date: "2026-09-18",
              reportUrl: "https://example.com/reports/push-demo.md",
              sections: [
                {
                  section: "rss",
                  hotspots: [
                    {
                      title: "材料发现新进展",
                      sourceUrls: ["https://paper.example.org/work"],
                    },
                  ],
                },
              ],
            },
          ],
        }),
      },
    });
    const deepSearchTask = toolTask({
      id: "deep-1",
      messageType: "deep_search",
      resultMap: { status: "success" },
    });

    const html = renderToStaticMarkup(
      createElement(GenericToolCall, {
        tool: ai4sTask,
        chat: { tasks: [[ai4sTask, deepSearchTask]] } as CHAT.ChatItem,
        changeActiveChat: () => undefined,
      })
    );

    expect(html).toContain("AI4S Daily");
    expect(html).toContain("AI4S 前沿观察报告");
    expect(html).toContain("push-demo.md");
    expect(html).toContain("paper.example.org/work");
    expect(html).toContain("已基于 AI4S Daily 线索继续进行外部补充 / 核验");
  });
});

describe("editDiffStats chip", () => {
  it("computes +A −B from old_string/new_string", () => {
    const arg = JSON.stringify({
      path: "a.ts",
      old_string: "a\nb\nc",
      new_string: "a\nx\nc\nd",
    });
    const stats = editDiffStats("Edit", arg);
    expect(stats).toEqual({ added: 2, removed: 1 });
    expect(formatEditDiffChip("Edit", arg)).toBe("+2 −1");
  });

  it("counts write content as added lines", () => {
    const arg = JSON.stringify({
      path: "new.ts",
      content: "one\ntwo\nthree",
    });
    expect(formatEditDiffChip("Write", arg)).toBe("+3 −0");
  });
});
