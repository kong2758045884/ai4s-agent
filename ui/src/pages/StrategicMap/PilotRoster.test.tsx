import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import PilotRoster from "./PilotRoster";

it("shows the selected field and a clear team-list action", () => {
  const html = renderToStaticMarkup(<PilotRoster domainId="life" subdomainId="protein"
    domainName="生命科学与医学" subdomainName="蛋白质结构与设计" onOpenTeam={() => {}} />);
  expect(html).toContain("领域团队与资料覆盖");
  expect(html).toContain("查看团队清单");
  expect(html).toContain("蛋白质结构与设计");
  expect(html).not.toMatch(/PILOT ROSTER|试点验收|老师/);
});
