import { describe, expect, it } from "vitest";
import type { AssessmentRun } from "@/services/strategicAssessments";
import { compareVersions } from "./assessmentVersionDiff";

function snapshot(items: { teamId: string; teamName: string; citations: { id: string; text: string; quote: string; url: string; kind: "outcome" }[] }[],
  roles: { teamId: string; role: string; rationale: string; claimIds: string[] }[] = []): AssessmentRun {
  return {
    items,
    selection: {
      comparedTeamIds: roles.map(r => r.teamId),
      combination: roles
    }
  } as AssessmentRun;
}

const claim = (id: string, quote = "成果原文") => ({
  id,
  text: quote,
  quote,
  url: `https://example.org/${id}`,
  kind: "outcome" as const
});

describe("saved assessment version comparison", () => {
  it("separates team membership, frozen evidence and unconfirmed new roles", () => {
    const before = snapshot([
      {
        teamId: "a",
        teamName: "甲组",
        citations: [claim("a1")]
      },
      {
        teamId: "b",
        teamName: "乙组",
        citations: [claim("b1")]
      },
    ], [{
      teamId: "a",
      role: "样本分析",
      rationale: "原文支持",
      claimIds: ["a1"]
    }]);
    const after = snapshot([
      {
        teamId: "a",
        teamName: "甲组",
        citations: [claim("a1", "修订后的原文"), claim("a2")]
      },
      {
        teamId: "c",
        teamName: "丙组",
        citations: [claim("c1")]
      },
    ]);
    const changes = compareVersions(before, after);
    expect(Object.fromEntries(changes.map(c => [c.teamId, c.kind]))).toEqual({
      a: "updated",
      b: "removed",
      c: "added"
    });
    expect(changes.find(c => c.teamId === "a")).toMatchObject({
      addedEvidence: ["a2"],
      changedEvidence: ["a1"],
      previousRole: { role: "样本分析" },
      currentRole: undefined,
      roleChanged: true
    });
  });

  it("does not treat the same saved evidence and role as a change", () => {
    const run = snapshot([{
      teamId: "a",
      teamName: "甲组",
      citations: [claim("a1")]
    }],
    [{
      teamId: "a",
      role: "分析",
      rationale: "a1",
      claimIds: ["a1"]
    }]);
    expect(compareVersions(run, run)).toEqual([]);
  });

  it("flags a changed role citation even when the proposed role text is unchanged", () => {
    const items = [{
      teamId: "a",
      teamName: "甲组",
      citations: [claim("a1"), claim("a2")]
    }];
    const before = snapshot(items, [{
      teamId: "a",
      role: "分析",
      rationale: "待讨论",
      claimIds: ["a1"]
    }]);
    const after = snapshot(items, [{
      teamId: "a",
      role: "分析",
      rationale: "待讨论",
      claimIds: ["a2"]
    }]);
    expect(compareVersions(before, after)).toMatchObject([{
      teamId: "a",
      roleChanged: true,
      addedEvidence: [],
      removedEvidence: []
    }]);
  });
});
