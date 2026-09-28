import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import PilotRoster from "./PilotRoster";

it("makes the pilot inventory a visible action without claiming expert approval", () => {
  const html = renderToStaticMarkup(<PilotRoster domainId="life" subdomainId="protein"
    domainName="生命科学与医学" subdomainName="蛋白质结构与设计" onOpenTeam={() => {}} />);
  expect(html).toContain("试点团队与证据缺口");
  expect(html).toContain("查看试点清单");
  expect(html).toContain("目标 20 支");
  expect(html).toContain("蛋白质结构与设计");
  expect(html).not.toContain("专家验收通过");
});
