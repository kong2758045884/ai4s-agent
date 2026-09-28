import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { AssessmentRun } from "@/services/strategicAssessments";
import AssessmentInvestigation from "./AssessmentInvestigation";

describe("P09 investigation without a connected collection provider", () => {
  it("shows task-prefilled internal evidence search and no remote collection action", () => {
    const run = {
      taskId: "task-1",
      runId: "run-1",
      taskText: "蛋白质结构任务",
      scope: {
        mode: "selected",
        domainIds: ["life"],
        domainNames: ["生命科学"],
        domesticOnly: true
      },
      criteria: [{
        id: "criterion-1",
        text: "蛋白质设计",
        kind: "capability",
        necessity: "required"
      }],
      items: [{
        teamId: "team-1",
        criteriaMatrix: [{
          criterionId: "criterion-1",
          status: "insufficient"
        }]
      }],
    } as AssessmentRun;
    const html = renderToStaticMarkup(<AssessmentInvestigation run={run} historical={false} onOpenRun={() => {}} onOpenTeam={() => {}} />);
    expect(html).toContain("收起库内检索");
    expect(html).toContain('value="蛋白质设计"');
    expect(html).toContain("成果依据");
    expect(html).toContain("起始发表日期");
    expect(html).toContain("检索结果仅供核对");
    expect(html).not.toContain("明确启动联网补证");
  });

  it("reads an older saved run without a criterion matrix", () => {
    const run = {
      taskId: "old-task",
      runId: "old-run",
      taskText: "蛋白质结构",
      scope: {
        mode: "auto",
        domainIds: [],
        domesticOnly: true
      },
      criteria: [{
        id: "old-criterion",
        text: "结构分析",
        necessity: "required"
      }],
      items: [{ teamId: "old-team" }]
    } as unknown as AssessmentRun;
    const html = renderToStaticMarkup(<AssessmentInvestigation run={run} historical onOpenRun={() => {}} onOpenTeam={() => {}} />);
    expect(html).toContain('value="蛋白质结构"');
  });
});
