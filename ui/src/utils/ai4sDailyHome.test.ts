import { describe, expect, it } from "vitest";

import {
  parseAi4sDailyIndex,
  parseAi4sDailyReport,
} from "./ai4sDailyHome";

describe("ai4sDailyHome", () => {
  it("uses the current section heading for hotspot labels", () => {
    const report = parseAi4sDailyReport(
      [
        "---",
        'title: "AI4S 前沿观察报告"',
        'date: "2026-09-18"',
        "---",
        "## 🧭 与当前专项工作的相关性",
        "<!-- SECTION:relevance BEGIN -->",
        "- context",
        "<!-- SECTION:relevance END -->",
        "<!-- SECTION:rss BEGIN -->",
        "## 📰 今日Top热点",
        "### 论文转化为可执行智能体",
        "- 研究团队发布了可执行框架。",
        " 🔗 [论文](https://example.com/paper)",
        "<!-- SECTION:rss END -->",
      ].join("\n"),
      "push-2026-09-18.md",
      "https://example.com/AI4S-Daily-HTML"
    );

    expect(report.hotspots[0]).toMatchObject({
      section: "📰 今日Top热点",
      title: "论文转化为可执行智能体",
      sourceUrls: ["https://example.com/paper"],
    });
  });

  it("accepts the public report index array", () => {
    expect(
      parseAi4sDailyIndex(["push-2026-09-18.md", "invalid.json", 42])
    ).toEqual(["push-2026-09-18.md"]);
  });
});
