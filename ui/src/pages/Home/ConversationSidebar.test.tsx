import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import ConversationSidebar from "./ConversationSidebar";

describe("ConversationSidebar", () => {
  it("hides the temporarily disabled workspace navigation entries", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <ConversationSidebar
          activeView="chat"
          recentSessions={[]}
          recentSessionsLoading={false}
          onNewChat={() => {}}
          onSelectSession={() => {}}
          onChangeView={() => {}}
          onManageFeaturedConversation={() => {}}
        />
      </MemoryRouter>
    );

    expect(html).toContain("精品对话");
    expect(html).not.toContain("子 Agent");
    expect(html).not.toContain("模型");
    expect(html).not.toContain("MRAG");
    expect(html).toContain("查看当前会话的文件");
  });

  it("renders task file panel with back action", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <ConversationSidebar
          activeView="chat"
          recentSessions={[]}
          recentSessionsLoading={false}
          sidebarPanel="task-files"
          taskList={[]}
          onNewChat={() => {}}
          onSelectSession={() => {}}
          onChangeView={() => {}}
          onManageFeaturedConversation={() => {}}
          onCloseTaskFiles={() => {}}
        />
      </MemoryRouter>
    );

    expect(html).toContain("文件");
    expect(html).toContain("返回");
    expect(html).toContain("当前会话暂无文件");
  });

  it("collapses the task session list outside the chat view", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <ConversationSidebar
          activeView="strategic-map"
          recentSessions={[
            {
              sessionId: "session-hidden",
              title: "不应显示的任务",
              status: "SUCCESS",
              latestQueryText: "",
              runCount: 1,
              finishedRunCount: 1,
              failedRunCount: 0,
              startedAt: "",
              lastActiveAt: "",
            },
          ]}
          recentSessionsLoading={false}
          onNewChat={() => {}}
          onSelectSession={() => {}}
          onChangeView={() => {}}
          onManageFeaturedConversation={() => {}}
        />
      </MemoryRouter>
    );

    expect(html).not.toContain("不应显示的任务");
  });
});
