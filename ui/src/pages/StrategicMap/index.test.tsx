import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import StrategicMap from ".";

describe("StrategicMap", () => {
  it("renders the strategic strength map workspace", () => {
    const html = renderToStaticMarkup(<StrategicMap />);

    expect(html).toContain("AI4S战略力量图谱");
    expect(html).toContain("国内优势团队候选池");
    expect(html).toContain("生命科学");
    expect(html).toContain("正在读取已保存的研判结果");
    expect(html).not.toContain("团队 A");
    expect(html).toContain("下一步行动");
  });
});
