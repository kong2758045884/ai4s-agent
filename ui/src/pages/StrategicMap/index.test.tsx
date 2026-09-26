import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import StrategicMap from ".";

describe("StrategicMap", () => {
  it("renders the strategic strength map workspace", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <StrategicMap />
      </MemoryRouter>,
    );

    expect(html).toContain("AI4S战略力量图谱");
    expect(html).toContain("国内科研团队库");
    expect(html).toContain("科学通用底座");
    expect(html).toContain("关系图谱");
    expect(html).toContain("全国扫描");
    expect(html).toContain("增量更新");
    expect(html).toContain("正在读取已保存的研判结果");
    const addSubdomainButton = html.match(/<button[^>]*aria-label="新增子领域"[^>]*>/)?.[0];
    expect(addSubdomainButton).toContain("disabled");
    expect(html).not.toContain("待核验线索");
    expect(html).not.toContain("研判中");
    expect(html).not.toContain("团队 A");
    expect(html).not.toContain("暂无团队线索");
  });

  it("keeps the team workspace when a subdomain filter is restored", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter
        initialEntries={[
          "/workspace/strategic-map?smDomain=science-foundation&smSubdomain=subdomain-models",
        ]}
      >
        <StrategicMap />
      </MemoryRouter>,
    );

    expect(html).toContain("国内科研团队库");
    expect(html).not.toContain("strategic-graph-workspace");
  });

  it("returns to the existing team view when the fusion flag is disabled", () => {
    vi.stubEnv("VITE_STRATEGIC_MAP_FUSION_ENABLED", "false");
    try {
      const html = renderToStaticMarkup(
        <MemoryRouter initialEntries={["/workspace/strategic-map"]}><StrategicMap /></MemoryRouter>,
      );
      expect(html).toContain("国内科研团队库");
      expect(html).not.toContain("输入任务，找到能承担它的国内团队");
      expect(html).not.toContain("动态情报</button>");
    } finally {
      vi.unstubAllEnvs();
    }
  });
});
