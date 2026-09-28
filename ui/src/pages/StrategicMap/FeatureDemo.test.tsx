import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import FeatureDemo from "./FeatureDemo";

it("presents the three modules and workflow without internal delivery language", () => {
  const html = renderToStaticMarkup(<FeatureDemo onClose={() => {}} onNavigate={() => {}} />);
  expect(html).toContain("研判工作台");
  expect(html).toContain("团队资料");
  expect(html).toContain("情报观察");
  expect(html).toContain("从科研任务到团队研判");
  expect(html).toContain("查看领域团队");
  expect(html).toContain("核验状态以团队资料页实时显示为准");
  expect(html).not.toMatch(/老师|P\d{2}|原型|实施计划|验收|PRODUCT TOUR/);
});
