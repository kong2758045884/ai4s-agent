import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import FeatureDemo from "./FeatureDemo";

it("organizes the guide by the teacher's three primary modules and the P01–P10 workflow", () => {
  const html = renderToStaticMarkup(<FeatureDemo onClose={() => {}} onNavigate={() => {}} />);
  expect(html).toContain("研判工作台");
  expect(html).toContain("团队资料");
  expect(html).toContain("情报观察");
  expect(html).toContain("P01");
  expect(html).toContain("P10");
  expect(html).toContain("资料边界");
  expect(html).toContain("候选不能算作专家验收");
  expect(html).not.toContain("PRODUCT TOUR");
});
