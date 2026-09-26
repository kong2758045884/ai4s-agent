import { describe, expect, it } from "vitest";
import { reviewedTeamPeople, type StrategicPerson } from "./strategicMap";

describe("reviewed people in public team profiles", () => {
  it("requires an explicit verified status and a source, preserving the original records", () => {
    const base: StrategicPerson = { id: "verified", teamId: "team", name: "研究员", title: "", role: "成员", researchDirection: "",
      bio: "", avatarUrl: "", profileUrl: "https://example.org/person", sourceUrls: [], sourceType: "official",
      lastVerifiedAt: "2026-09-26", isLeader: false, verificationStatus: "verified" };
    const people = [base, { ...base, id: "pending", verificationStatus: "pending" },
      { ...base, id: "no-source", profileUrl: "" }, { ...base, id: "old-record", verificationStatus: undefined }];
    expect(reviewedTeamPeople(people).map((person) => person.id)).toEqual(["verified"]);
    expect(people).toHaveLength(4);
  });
});
