import {
  ROUTES,
  buildStrategicTeamDetailPath,
} from "./routes";

export type StrategicMapRoute = "home" | "workspace";

export type StrategicMapScrollState = {
  page: number;
  domains: number;
  subdomains: number;
  teams: number;
  profile: number;
};

export type StrategicMapNavigationContext = {
  route: StrategicMapRoute;
  domainId: string;
  subdomainId: string;
  teamId: string;
  scroll: StrategicMapScrollState;
  mobilePanel?: "profile";
  mobileListScroll?: number;
};

const PARAMS = {
  view: "view",
  domainId: "smDomain",
  subdomainId: "smSubdomain",
  teamId: "smTeam",
  route: "smRoute",
  pageScroll: "smPageScroll",
  domainScroll: "smDomainScroll",
  subdomainScroll: "smSubdomainScroll",
  teamScroll: "smTeamScroll",
  profileScroll: "smProfileScroll",
  mobilePanel: "smPanel",
  mobileListScroll: "smListScroll",
} as const;

const ALL_SUBDOMAINS = "all";
const MAX_SCROLL_OFFSET = 10_000_000;

export const EMPTY_STRATEGIC_MAP_SCROLL: StrategicMapScrollState = {
  page: 0,
  domains: 0,
  subdomains: 0,
  teams: 0,
  profile: 0,
};

function parseScroll(value: string | null): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return 0;
  return Math.min(Math.round(parsed), MAX_SCROLL_OFFSET);
}

function appendSelectionParams(
  params: URLSearchParams,
  context: StrategicMapNavigationContext,
) {
  if (context.domainId) params.set(PARAMS.domainId, context.domainId);
  params.set(PARAMS.subdomainId, context.subdomainId || ALL_SUBDOMAINS);
  if (context.teamId) params.set(PARAMS.teamId, context.teamId);
  if (context.mobilePanel === "profile") params.set(PARAMS.mobilePanel, "profile");
  if (context.mobileListScroll && Number.isFinite(context.mobileListScroll)) {
    params.set(PARAMS.mobileListScroll, String(parseScroll(String(context.mobileListScroll))));
  }

  const scrollParams: [keyof StrategicMapScrollState, string][] = [
    ["page", PARAMS.pageScroll],
    ["domains", PARAMS.domainScroll],
    ["subdomains", PARAMS.subdomainScroll],
    ["teams", PARAMS.teamScroll],
    ["profile", PARAMS.profileScroll],
  ];
  for (const [key, param] of scrollParams) {
    const value = Math.max(0, Math.round(context.scroll[key] || 0));
    if (value) params.set(param, String(value));
  }
}

function readSelectionParams(
  params: URLSearchParams,
  route: StrategicMapRoute,
  fallbackTeamId = "",
): StrategicMapNavigationContext {
  const subdomain = params.get(PARAMS.subdomainId);
  return {
    route,
    domainId: params.get(PARAMS.domainId)?.trim() ?? "",
    subdomainId: subdomain && subdomain !== ALL_SUBDOMAINS ? subdomain : "",
    teamId: params.get(PARAMS.teamId)?.trim() || fallbackTeamId,
    ...(params.get(PARAMS.mobilePanel) === "profile" ? { mobilePanel: "profile" as const } : {}),
    ...(parseScroll(params.get(PARAMS.mobileListScroll))
      ? { mobileListScroll: parseScroll(params.get(PARAMS.mobileListScroll)) } : {}),
    scroll: {
      page: parseScroll(params.get(PARAMS.pageScroll)),
      domains: parseScroll(params.get(PARAMS.domainScroll)),
      subdomains: parseScroll(params.get(PARAMS.subdomainScroll)),
      teams: parseScroll(params.get(PARAMS.teamScroll)),
      profile: parseScroll(params.get(PARAMS.profileScroll)),
    },
  };
}

export function strategicMapRouteForPathname(pathname: string): StrategicMapRoute {
  return pathname === ROUTES.WORKSPACE_STRATEGIC_MAP ? "workspace" : "home";
}

export function isStrategicMapHomeSearch(search: string): boolean {
  return new URLSearchParams(search).get(PARAMS.view) === "strategic-map";
}

export function readStrategicMapNavigationContext(
  pathname: string,
  search: string,
): StrategicMapNavigationContext | null {
  const route = strategicMapRouteForPathname(pathname);
  if (route === "home" && !isStrategicMapHomeSearch(search)) return null;
  return readSelectionParams(new URLSearchParams(search), route);
}

export function buildStrategicMapPath(
  context: StrategicMapNavigationContext,
): string {
  const params = new URLSearchParams();
  if (context.route === "home") params.set(PARAMS.view, "strategic-map");
  appendSelectionParams(params, context);
  const pathname = context.route === "workspace"
    ? ROUTES.WORKSPACE_STRATEGIC_MAP
    : ROUTES.HOME;
  const query = params.toString();
  return query ? `${pathname}?${query}` : pathname;
}

/**
 * Detail links only carry structured strategic-map fields. There is no raw
 * returnUrl, so an edited URL can never redirect the back action off-site.
 */
export function buildStrategicTeamDetailNavigationPath(
  teamId: string,
  context: StrategicMapNavigationContext,
): string {
  const params = new URLSearchParams();
  params.set(PARAMS.route, context.route);
  appendSelectionParams(params, {
    ...context,
    teamId,
  });
  return `${buildStrategicTeamDetailPath(teamId)}?${params.toString()}`;
}

export function readStrategicTeamDetailSource(
  search: string,
  teamId: string,
): StrategicMapNavigationContext {
  const params = new URLSearchParams(search);
  const route: StrategicMapRoute = params.get(PARAMS.route) === "workspace"
    ? "workspace"
    : "home";
  return {
    ...readSelectionParams(params, route, teamId),
    // The path identifies the detail being viewed; an edited/stale query must
    // never make Back highlight a different team.
    teamId,
  };
}

export function removeStrategicMapParams(search: string): string {
  const params = new URLSearchParams(search);
  for (const param of Object.values(PARAMS)) params.delete(param);
  const query = params.toString();
  return query ? `?${query}` : "";
}
