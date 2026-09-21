import type { StrategicDomain, StrategicTeam } from "@/services/strategicMap";

export type StrategicMapSelection = {
  domainId: string;
  subdomainId: string;
  teamId: string;
};

type DomainWithTeams = StrategicDomain & { teams: StrategicTeam[] };

/** Resolve a requested selection only after the current snapshot has loaded. */
export function resolveStrategicMapSelection(
  domains: DomainWithTeams[],
  requested: StrategicMapSelection,
  fallbackToFirstTeam: boolean,
): StrategicMapSelection {
  const requestedDomain = domains.find((domain) => domain.id === requested.domainId);
  const teamDomain = requested.teamId
    ? domains.find((domain) => domain.teams.some((team) => team.id === requested.teamId))
    : undefined;
  const domain = requestedDomain ?? teamDomain ?? domains[0];
  if (!domain) {
    return {
      domainId: "",
      subdomainId: "",
      teamId: "",
    };
  }

  const subdomainId = requested.subdomainId
    && domain.subdomains.some((subdomain) => subdomain.id === requested.subdomainId)
    ? requested.subdomainId
    : "";
  const visibleTeams = subdomainId
    ? domain.teams.filter((team) => team.subdomainId === subdomainId)
    : domain.teams;
  const requestedTeam = visibleTeams.find((team) => team.id === requested.teamId);

  return {
    domainId: domain.id,
    subdomainId,
    teamId: requestedTeam?.id
      ?? (fallbackToFirstTeam ? visibleTeams[0]?.id ?? "" : ""),
  };
}
