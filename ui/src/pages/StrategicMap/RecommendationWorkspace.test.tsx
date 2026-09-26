import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import RecommendationWorkspace from "./RecommendationWorkspace";
import type { RecommendationRun } from "@/services/strategicRecommendations";

describe("public recommendation evidence", () => {
  it("keeps citations and omits unreviewed leads and institution scores in old runs", () => {
    const run: RecommendationRun = {
      runId: "old-run", taskText: "量子计算", domainId: null, subdomainId: null,
      requestedLimit: 10, eligibleTeamCount: 1, matchedTeamCount: 1, shortfall: 9,
      dataVersion: "v1", matchVersion: "v1", createdAt: "2026-09-26T01:00:00Z", notice: "",
      pendingLeads: [{ teamId: "unreviewed", teamName: "不应展示的线索", institutionName: "未知机构", matchHint: 60, sourceUrl: "https://example.org/lead", reason: "待核验" }],
      items: [{ teamId: "approved", teamName: "量子研究组", institutionId: null, institutionName: "大学甲", institutionImpact: "待核验关联",
        domainId: "quantum", subdomainId: null, taskMatchScore: 80, teamScore: 90, teamScoreVersion: "team-v3", matchVersion: "v1",
        capability: "量子计算成果", citations: [{ kind: "outcome", text: "论文", quote: "原文中的量子计算成果", url: "https://example.org/paper" }],
        unknowns: ["机构关联未完成"], nextStep: "核验", updatedAt: "2026-09-26" }],
    };
    const html = renderToStaticMarkup(<RecommendationWorkspace domainId="" subdomainId="" domainName="全部领域" subdomainName="" draft="量子计算"
      onDraftChange={() => {}} run={run} onRunChange={() => {}} onOpenTeam={() => {}} />);
    expect(html).toContain("量子研究组");
    expect(html).toContain('href="https://example.org/paper"');
    expect(html).not.toContain("不应展示的线索");
    expect(html).not.toContain("待核验关联");
    expect(html).not.toContain("机构关联未完成");
    expect(html).toContain("任务匹配");
    expect(html).toContain("团队总分");
  });
});
