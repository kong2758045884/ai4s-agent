import { describe, expect, it } from "vitest";
import { parseAi4sDailyResult } from "./ai4sDaily";

describe("parseAi4sDailyResult", () => {
  it("extracts report, hotspot, and both source URL layers", () => {
    const result = parseAi4sDailyResult(
      JSON.stringify({
        tool: "ai4s_daily",
        source: "AI4S Daily",
        indexUrl: "https://example.com/reports/index.json",
        matched: true,
        reports: [
          {
            reportId: "push-2026-09-18",
            title: "AI4S 前沿观察报告",
            date: "2026-09-18",
            reportUrl: "https://example.com/reports/push-2026-09-18.md",
            sections: [
              {
                section: "rss",
                hotspots: [
                  {
                    title: "材料发现新进展",
                    content: "摘要",
                    sourceUrls: ["https://paper.example.org/work"],
                  },
                ],
              },
            ],
          },
        ],
      })
    );

    expect(result?.matched).toBe(true);
    expect(result?.indexUrl).toContain("reports/index.json");
    expect(result?.reports[0].reportUrl).toContain("push-2026-09-18.md");
    expect(result?.reports[0].sections[0].hotspots[0].sourceUrls).toEqual([
      "https://paper.example.org/work",
    ]);
  });

  it("supports an unmatched result without inventing URLs", () => {
    const result = parseAi4sDailyResult({
      tool: "ai4s_daily",
      source: "AI4S Daily",
      matched: false,
      reports: [],
    });

    expect(result?.matched).toBe(false);
    expect(result?.reports).toEqual([]);
    expect(result?.indexUrl).toBeUndefined();
  });

  it("ignores results from another tool", () => {
    expect(
      parseAi4sDailyResult({
        tool: "deep_search",
        source: "AI4S Daily",
        reports: [],
      })
    ).toBeUndefined();
  });
});

