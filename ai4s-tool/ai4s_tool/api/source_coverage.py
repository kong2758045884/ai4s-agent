"""Public coverage from the same catalogue snapshot used by search/recommendation."""
from urllib.parse import urlsplit


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
            "domainId": team.get("domainId"), "subdomainId": team.get("subdomainId"),
            "subdomainName": team.get("subdomainName") or "",
            "identitySourceUrl": source_url,
            "claimCount": len(evidence), "outcomeCount": len(outcomes),
            "humanReviewedClaimCount": human_reviewed,
        })
    items.sort(key=lambda item: (item["institutionName"], item["teamName"], item["teamId"]))
    return {"dataVersion": version, "target": target, "totalUnits": len(items),
            "outcomeBackedUnits": sum(item["outcomeCount"] > 0 for item in items),
            "unclassifiedUnits": sum(not item["subdomainId"] for item in items),
            "shortfall": max(0, target - len(items)), "items": items}
