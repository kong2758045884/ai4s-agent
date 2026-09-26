import { describe, expect, it } from "vitest";

import {
  buildStrategicMapPath,
  canonicalHomePath,
  canonicalStrategicMapPath,
  buildStrategicTeamDetailNavigationPath,
  readStrategicMapNavigationContext,
  readStrategicTeamDetailSource,
  removeStrategicMapParams,
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
  it("restores the mobile profile and list position after team detail navigation", () => {
    const mobileContext: StrategicMapNavigationContext = {
      ...context, mobilePanel: "profile", mobileListScroll: 1362,
    };
    const detail = new URL(buildStrategicTeamDetailNavigationPath(context.teamId, mobileContext), "http://localhost");
    const returned = readStrategicTeamDetailSource(detail.search, context.teamId);
    expect(returned).toEqual(mobileContext);
    const map = new URL(buildStrategicMapPath(returned), "http://localhost");
    expect(readStrategicMapNavigationContext(map.pathname, map.search)).toEqual({...mobileContext, route: "workspace"});
    expect(removeStrategicMapParams(`${map.search}&unrelated=keep`)).toBe("?unrelated=keep");
  });

  it("ignores unknown mobile panels and invalid scroll positions", () => {
    const restored = readStrategicMapNavigationContext("/workspace/strategic-map", "?smPanel=bad&smListScroll=-20");
    expect(restored).not.toHaveProperty("mobilePanel");
    expect(restored).not.toHaveProperty("mobileListScroll");
    const bounded = readStrategicMapNavigationContext("/workspace/strategic-map", "?smPanel=profile&smListScroll=999999999");
    expect(bounded?.mobileListScroll).toBe(10_000_000);
  });

  it("round-trips all-subdomain selection and independent scroll containers", () => {
    const detailPath = buildStrategicTeamDetailNavigationPath(context.teamId, context);
    const detailUrl = new URL(detailPath, "http://localhost");
    const restored = readStrategicTeamDetailSource(detailUrl.search, context.teamId);

    expect(restored).toEqual(context);
    expect(buildStrategicMapPath(restored)).toBe(
      "/workspace/strategic-map?smDomain=life-science&smSubdomain=all&smTeam=team-02&smPageScroll=17&smDomainScroll=31&smSubdomainScroll=7&smTeamScroll=420&smProfileScroll=88",
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
    expect(buildStrategicMapPath(restored)).toMatch(/^\/workspace\/strategic-map\?/);
    expect(buildStrategicMapPath(restored)).not.toContain("evil.example");
  });

  it("redirects legacy map links with mode, selection and scroll to the canonical workspace", () => {
    const search = '?view=strategic-map&smMode=graph&smDomain=quantum&smSubdomain=all&smTeam=team-q&smTeamScroll=120';
    const canonical = '/workspace/strategic-map?smMode=graph&smDomain=quantum&smSubdomain=all&smTeam=team-q&smTeamScroll=120';
    expect(canonicalStrategicMapPath(search)).toBe(canonical);
    expect(canonicalHomePath(search)).toBe(canonical);
  });

  it("preserves other home views and conversation links when moving the root entry", () => {
    expect(canonicalHomePath('')).toBe('/app');
    expect(canonicalHomePath('?view=image-generation&sessionId=existing&appBuild=old')).toBe('/app?view=image-generation&sessionId=existing');
  });
});
