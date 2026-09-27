import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { AssessmentItem, Criterion } from "@/services/strategicAssessments";
import CombinationCoverage from "./CombinationCoverage";

it("combines complementary evidence without counting conditional evidence as confirmed coverage", () => {
  const criteria = ["A", "B", "C"].map(id => ({ id, text: `能力${id}`, necessity: "required", kind: "capability" })) as Criterion[];
  const teams = [{ teamName: "团队甲", criteriaMatrix: [{ criterionId: "A", status: "supported" }, { criterionId: "C", status: "conditional" }] },
    { teamName: "团队乙", criteriaMatrix: [{ criterionId: "B", status: "supported" }] }] as AssessmentItem[];
  const html = renderToStaticMarkup(<CombinationCoverage criteria={criteria} teams={teams} />);
  expect(html).toContain("覆盖 2 / 3 项必要条件");
  expect(html).toContain("条件支持，尚未计入覆盖：团队甲");
  expect(html).toContain("联合交付能力仍需确认");
});

it("keeps unknown required resources as gaps and excludes preferences from the denominator", () => {
  const criteria = [{ id: "resource", text: "可用设备", necessity: "required", kind: "constraint" },
    { id: "optional", text: "开源优先", necessity: "preferred", kind: "preference" }] as Criterion[];
  const html = renderToStaticMarkup(<CombinationCoverage criteria={criteria} teams={[]} />);
  expect(html).toContain("覆盖 0 / 1 项必要条件");
  expect(html).toContain("尚缺依据");
  expect(html).not.toContain("开源优先");
});
