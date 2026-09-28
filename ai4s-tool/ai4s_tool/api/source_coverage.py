"""Public coverage from the same catalogue snapshot used by search/recommendation."""
from urllib.parse import urlsplit


# Research-direction leads checked against the institute's own team pages.
# They remain outside the published subdomain until a reviewer records a decision.
_PROTEIN_PILOT_LEADS = {
    "team_0bd6a5a348c66005e5e077f6": (
        "https://www.ibp.cas.cn/rc/zxz/202411/t20241107_7435201.html",
        "冷冻电镜解析蛋白质复合物的结构与功能",
    ),
    "team_6923d2ee28492e6286a6025c": (
        "https://www.ibp.cas.cn/rc/rzh/202411/t20241107_7435312.html",
        "研究重要蛋白质与病毒的三维结构",
    ),
    "team_41706ec34ff71b592d378742": (
        "https://www.ibp.cas.cn/rc/lm/202411/t20241108_7436129.html",
        "研究光合作用膜蛋白及复合物的结构与功能",
    ),
    "team_551667fa4f56da0e1622b189": (
        "https://www.ibp.cas.cn/rc/shihg/202608/t20260820_8262708.html",
        "以结构研究指导分子胶设计和蛋白质降解研究",
    ),
}
_PROTEIN_PILOT_SCOPE = ("domain_22924cc35a604e09be55c917feb51db7",
                        "subdomain_9320b214075f454f82e1d9558b110be0")


def summarize(teams, claims, version):
    by_id = {team["id"]: team for team in teams}
    claims = list({c[0]: c for c in claims if c[1] in by_id}.values())
    outcomes = {c[1] for c in claims if c[3] == "outcome"}
    sources, fetched, reviewed = {}, [], set()
    for claim in claims:
        team = by_id[claim[1]]
        metadata = team.get("claimProvenance", {}).get(claim[0], {}) or {}
        if metadata.get("fetchedAt"):
            fetched.append(metadata["fetchedAt"])
        if metadata.get("humanReview", {}).get("status") == "reviewed":
            reviewed.add(claim[0])
        try:
            url = urlsplit(claim[6])
            if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
                continue
        except ValueError:
            continue
        host = url.hostname.lower()
        source = sources.setdefault(host, {"host": host, "url": claim[6], "teamIds": set(),
                                          "claimIds": set(), "outcomeIds": set(), "fetched": []})
        source["teamIds"].add(claim[1]); source["claimIds"].add(claim[0])
        if claim[3] == "outcome":
            source["outcomeIds"].add(claim[0])
        if metadata.get("fetchedAt"):
            source["fetched"].append(metadata["fetchedAt"])
    return {
        "dataVersion": version, "totalUnits": len(by_id), "outcomeBackedUnits": len(outcomes),
        "identityOnlyUnits": len(by_id) - len(outcomes), "claimCount": len(claims),
        "humanReviewedClaims": len(reviewed),
        "officialDirectoryUnits": sum(t.get("catalogueBasis") == "official_directory" for t in by_id.values()),
        "modelReviewedUnits": sum(t.get("catalogueBasis") == "verified" for t in by_id.values()),
        "latestFetchedAt": max(fetched, default=None),
        "sources": [{"host": s["host"], "url": s["url"], "unitCount": len(s["teamIds"]),
                     "claimCount": len(s["claimIds"]), "outcomeCount": len(s["outcomeIds"]),
                     "latestFetchedAt": max(s["fetched"], default=None)}
                    for s in sorted(sources.values(), key=lambda s: (-len(s["teamIds"]), s["host"]))],
        "notice": "科研单元包含实验室、课题组和研究中心；有身份来源不等于具备任务成果。来源规则检查、AI复核和人工审核分别记录。",
    }


def pilot_roster(teams, claims, version, *, target=20):
    """A reviewable roster from published identities and currently valid claims."""
    by_id = {team["id"]: team for team in teams}
    by_team = {team_id: {} for team_id in by_id}
    for claim in claims:
        if claim[1] in by_team:
            by_team[claim[1]][claim[0]] = claim
    items = []
    for team in by_id.values():
        evidence = list(by_team[team["id"]].values())
        outcomes = [claim for claim in evidence if claim[3] == "outcome"]
        provenance = team.get("claimProvenance") or {}
        human_reviewed = sum(
            (provenance.get(claim[0], {}).get("humanReview") or {}).get("status") == "reviewed"
            for claim in evidence
        )
        source_url = next((claim[6] for claim in evidence
                           if claim[3] != "outcome" and claim[6].startswith(("https://", "http://"))), None)
        items.append({
            "teamId": team["id"], "teamName": team.get("teamName") or "",
            "institutionName": team.get("institutionName") or "",
            "teamAliases": team.get("teamAliases") or [],
            "institutionAliases": team.get("institutionAliases") or [],
            "domainId": team.get("domainId"), "subdomainId": team.get("subdomainId"),
            "subdomainName": team.get("subdomainName") or "",
            "identitySourceUrl": source_url,
            "classificationSources": [{"claimId": claim[0], "title": claim[4], "url": claim[6]}
                                      for claim in evidence if claim[3] != "outcome"],
            "claimCount": len(evidence), "outcomeCount": len(outcomes),
            "humanReviewedClaimCount": human_reviewed,
        })
    items.sort(key=lambda item: (item["institutionName"], item["teamName"], item["teamId"]))
    return {"dataVersion": version, "target": target, "totalUnits": len(items),
            "outcomeBackedUnits": sum(item["outcomeCount"] > 0 for item in items),
            "unclassifiedUnits": sum(not item["subdomainId"] for item in items),
            "shortfall": max(0, target - len(items)), "items": items}


def pilot_candidate_leads(teams, claims, domain_id, subdomain_id):
    """Only suggest existing, still-unclassified units with the saved official source."""
    if (domain_id, subdomain_id) != _PROTEIN_PILOT_SCOPE:
        return []
    evidence_by_team = {}
    for claim in claims:
        if claim[3] != "outcome":
            evidence_by_team.setdefault(claim[1], set()).add(claim[6].rstrip("/"))
    eligible = []
    for team in teams:
        lead = _PROTEIN_PILOT_LEADS.get(team["id"])
        if (not lead or team.get("domainId") != domain_id or team.get("subdomainId")
                or lead[0].rstrip("/") not in evidence_by_team.get(team["id"], set())):
            continue
        eligible.append((team, lead))
    roster = pilot_roster([team for team, _ in eligible], claims, "candidate-projection")
    details = {team["id"]: lead for team, lead in eligible}
    for item in roster["items"]:
        source_url, reason = details[item["teamId"]]
        item.update(candidateReason=reason, candidateSourceUrl=source_url, candidateStatus="awaiting_review")
    return roster["items"]
