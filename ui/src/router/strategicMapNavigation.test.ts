import { describe, expect, it } from "vitest";

import {
  buildStrategicMapPath,
  buildStrategicTeamDetailNavigationPath,
  readStrategicMapNavigationContext,
  readStrategicTeamDetailSource,
  type StrategicMapNavigationContext,
} from "./strategicMapNavigation";

const context: StrategicMapNavigationContext = {
  route: "home",
  domainId: "life-science",
  subdomainId: "",
  teamId: "team-02",
  scroll: {
    page: 17,
    domains: 31,
    subdomains: 7,
    teams: 420,
    profile: 88,
  },
};

describe("strategic map navigation context", () => {
  it("round-trips all-subdomain selection and independent scroll containers", () => {
    const detailPath = buildStrategicTeamDetailNavigationPath(context.teamId, context);
    const detailUrl = new URL(detailPath, "http://localhost");
    const restored = readStrategicTeamDetailSource(detailUrl.search, context.teamId);

    expect(restored).toEqual(context);
    expect(buildStrategicMapPath(restored)).toBe(
      "/?view=strategic-map&smDomain=life-science&smSubdomain=all&smTeam=team-02&smPageScroll=17&smDomainScroll=31&smSubdomainScroll=7&smTeamScroll=420&smProfileScroll=88",
    );
  });

  it("keeps a concrete subdomain and workspace route", () => {
    const workspaceContext: StrategicMapNavigationContext = {
      ...context,
      route: "workspace",
      domainId: "alloy-materials",
      subdomainId: "high-entropy-alloys",
      teamId: "team-alloy-07",
    };
    const path = buildStrategicMapPath(workspaceContext);
    const url = new URL(path, "http://localhost");

    expect(path).toContain("/workspace/strategic-map?");
    expect(readStrategicMapNavigationContext(url.pathname, url.search)).toEqual(workspaceContext);
  });

  it("never accepts an arbitrary return route from an edited detail URL", () => {
    const restored = readStrategicTeamDetailSource(
      "?smRoute=https%3A%2F%2Fevil.example&returnUrl=https%3A%2F%2Fevil.example&smDomain=life-science&smTeam=wrong-team",
      "team-safe",
    );

    expect(restored.route).toBe("home");
    expect(restored.teamId).toBe("team-safe");
    expect(buildStrategicMapPath(restored)).toMatch(/^\/?\?view=strategic-map/);
    expect(buildStrategicMapPath(restored)).not.toContain("evil.example");
  });
});
