import { describe, expect, it } from "vitest";

import type { StrategicDomain, StrategicTeam } from "@/services/strategicMap";
import { resolveStrategicMapSelection } from "./selection";

const team = (id: string, domainId: string, subdomainId: string | null): StrategicTeam => ({
  id,
  domainId,
  subdomainId,
  name: id,
  focus: "",
  aiLevel: "",
  scienceLevel: "",
  attention: "",
  contact: "",
  coreDirection: "",
  dualJudgement: "",
  contactRecord: "",
  internalReview: "",
  recentUpdate: "",
  nextAction: "",
  source: "",
  sourceUrls: [],
  evidenceSummary: "",
  reportId: "",
  reportTitle: "",
  updatedAt: "",
});

const domains: (StrategicDomain & { teams: StrategicTeam[] })[] = [
  {
    id: "life",
    name: "生命科学",
    label: "生命科学",
    description: "",
    parentId: null,
    subdomains: [{
      id: "bio",
      parentId: "life",
      name: "生物",
      label: "生物",
      description: "",
    }],
    teams: [team("shared", "life", "bio"), team("life-2", "life", null)],
  },
  {
    id: "alloy",
    name: "合金材料",
    label: "合金材料",
    description: "",
    parentId: null,
    subdomains: [{
      id: "hea",
      parentId: "alloy",
      name: "高熵合金",
      label: "高熵合金",
      description: "",
    }],
    teams: [team("shared", "alloy", "hea"), team("alloy-2", "alloy", null)],
  },
];

describe("resolveStrategicMapSelection", () => {
  it("honors the actual source domain when the same team id appears twice", () => {
    expect(resolveStrategicMapSelection(domains, {
      domainId: "alloy",
      subdomainId: "hea",
      teamId: "shared",
    }, false)).toEqual({
      domainId: "alloy",
      subdomainId: "hea",
      teamId: "shared",
    });
  });

  it("keeps a valid parent but clears removed subdomain and team", () => {
    expect(resolveStrategicMapSelection(domains, {
      domainId: "life",
      subdomainId: "removed-subdomain",
      teamId: "removed-team",
    }, false)).toEqual({
      domainId: "life",
      subdomainId: "",
      teamId: "",
    });
  });
});
