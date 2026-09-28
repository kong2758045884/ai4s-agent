import { expect, it } from "vitest";
import type { StrategicDomain } from "@/services/strategicMap";
import { findDomainMatches } from "./domainSearch";

const domains = [
  { id: "base", name: "科学通用底座", subdomains: [] },
  { id: "life", name: "生命科学与医学", subdomains: [{ id: "protein", name: "蛋白质结构预测" }] },
  { id: "quantum", name: "高能物理与量子科技", subdomains: [{ id: "quantum-computing", name: "量子计算" }] },
] as StrategicDomain[];

it("suggests stable domain and subdomain IDs from controlled names without selecting for the user", () => {
  expect(findDomainMatches(domains, "蛋白").map(item => [item.domainId, item.subdomainId]))
    .toContainEqual(["life", null]);
  expect(findDomainMatches(domains, "蛋白质结构预测")[0])
    .toMatchObject({ domainId: "life", subdomainId: "protein" });
  expect(findDomainMatches(domains, "HPC")[0])
    .toMatchObject({ domainId: "base", matchedBy: "同义词：hpc" });
  expect(findDomainMatches(domains, "量子计算").map(item => item.domainId))
    .toEqual(["quantum", "quantum"]);
  expect(findDomainMatches(domains, "未知术语")).toEqual([]);
});
