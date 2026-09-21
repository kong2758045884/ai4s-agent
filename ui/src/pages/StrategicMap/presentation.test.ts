import { describe, expect, it } from "vitest";
import { attentionLabel, evaluationLabel, judgementLabel, savedRosterPriority } from "./presentation";

describe("saved data presentation without approval labels", () => {
  it("uses neutral empty labels without changing saved/manual values", () => {
    expect(attentionLabel("待核实")).toBe("未标记");
    expect(attentionLabel("重点关注")).toBe("重点关注");
    expect(evaluationLabel("待核实")).toBe("暂无评价");
    expect(evaluationLabel("较高")).toBe("较高");
    expect(judgementLabel("AI 待核实｜科学 待核实")).toBe("暂无评价");
    expect(judgementLabel("用户自定义判断")).toBe("用户自定义判断");
  });
  it("puts saved teams with people ahead of empty institution entries, without filtering", () => {
    expect(savedRosterPriority({leader: {name: "负责人"}})).toBe(2);
    expect(savedRosterPriority({members: [{name: "成员"}]})).toBe(2);
    expect(savedRosterPriority({teamName: "实际团队"})).toBe(1);
    expect(savedRosterPriority({teamName: "公开资料未注明具体团队"})).toBe(0);
  });
});
