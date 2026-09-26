import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import GraphWorkspace from "./GraphWorkspace";

describe("StrategicMap GraphWorkspace", () => {
  it("keeps the Hyper search, chat, graph switches and scan controls", () => {
    const html = renderToStaticMarkup(
      <GraphWorkspace
        domainId="domain-materials"
        domainName="化学与材料"
        subdomainId="subdomain-alloy"
        subdomainName="合金与结构材料"
      />,
    );

    expect(html).toContain("国内");
    expect(html).toContain("国外");
    expect(html).toContain("高峰");
    expect(html).toContain("高原");
    expect(html).toContain("搜索");
    expect(html).toContain("重置图谱");
    expect(html).toContain("图谱对话");
    expect(html).toContain("扫描");
  });
});
