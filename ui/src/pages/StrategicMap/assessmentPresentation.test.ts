import { expect, it } from "vitest";
import type { AssessmentItem, AssessmentRun, Criterion } from "@/services/strategicAssessments";
import { shortageStages, suggestedRole } from "./assessmentPresentation";

it("proposes a role only when a required task ability has a team outcome citation", () => {
  const criteria = [{ id: "g1", kind: "goal", necessity: "required", text: "蛋白质结构研究" }] as Criterion[];
  const item = { citations: [{ id: "c1", kind: "outcome", text: "结构论文" }],
    criteriaMatrix: [{ criterionId: "g1", status: "supported", claimIds: ["c1"] }] } as AssessmentItem;
  expect(suggestedRole(item, criteria)).toEqual({ text: "蛋白质结构研究相关环节（待团队确认）", claimIds: ["c1"] });
  expect(suggestedRole({ ...item, citations: [{ ...item.citations[0], kind: "direction" }] }, criteria)).toBeNull();
  expect(suggestedRole({ ...item, criteriaMatrix: [{ ...item.criteriaMatrix[0], status: "insufficient" }] }, criteria)).toBeNull();
});

it("separates scope, outcome evidence and condition coverage without adding their gaps", () => {
  const run = { requestedLimit: 5, coverage: { scopeTeamCount: 4, outcomeBackedTeamCount: 2,
    conditionMatchedTeamCount: 1 } } as AssessmentRun;
  expect(shortageStages(run).map(stage => stage.title)).toEqual([
    "范围收录不足", "团队成果依据不足", "条件尚未获充分支持",
  ]);
  expect(shortageStages({ ...run, coverage: undefined })).toEqual([]);
});
