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
