import { describe, expect, it } from "vitest";
import { teamDirectionSections } from "./directions";
import type { StrategicTeam } from "@/services/strategicMap";

const team = (changes: Partial<StrategicTeam>) => ({
  coreDirection: "",
  focus: "",
  researchDirections: [],
  description: "",
  ...changes,
} as StrategicTeam);

describe("teamDirectionSections", () => {
  it("uses headings present in the same team's description and preserves full source text once", () => {
    const direction = "生物分子智能设计 创建分析工具。 染色体精密操控 开发编辑方法。 表型数字孪生 建立多模态模型。";
    const sections = teamDirectionSections(team({
      description: "聚焦生物分子智能设计、染色体精密操控、表型数字孪生三大方向。",
      coreDirection: direction,
      researchDirections: [direction],
      focus: direction,
    }));
    expect(sections.map((item) => item.title)).toEqual(["生物分子智能设计", "染色体精密操控", "表型数字孪生"]);
    expect(sections.map((item) => `${item.title} ${item.paragraphs.join(" ")}`).join(" ")).toContain("建立多模态模型");
    expect(sections.length).toBe(3);
  });

  it("keeps distinct directions and does not infer headings from punctuation alone", () => {
    expect(teamDirectionSections(team({
      coreDirection: "方向甲 有公开描述。 方向乙 有另一项描述。",
      description: "机构介绍"
    }))).toHaveLength(1);
    expect(teamDirectionSections(team({ researchDirections: ["方向甲", "方向乙"] }))).toHaveLength(2);
  });
});
