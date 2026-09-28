import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import EvidenceSearch from "./EvidenceSearch";

it("shows the search action and source-aware filters without suggesting human review", () => {
  const html = renderToStaticMarkup(<EvidenceSearch domainId="life" subdomainId="protein"
    domainName="生命科学与医学" subdomainName="蛋白质" onOpenTeam={() => {}} />);
  expect(html).toContain("检索团队资料与成果原文");
  expect(html).toContain("蛋白质");
  expect(html).toContain("全部资料");
  expect(html).toContain("团队档案");
  expect(html).toContain("成果依据");
  expect(html).toContain("来源状态");
  expect(html).toContain("起始发表日期");
  expect(html).toContain("来源校验不等于专家审核");
  expect(html).toContain("bg-[#1768a2]");
});
