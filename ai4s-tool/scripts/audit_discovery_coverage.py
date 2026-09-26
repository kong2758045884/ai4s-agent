"""Read-only source/AI4S coverage reconciliation. Never scans or calls models."""
import argparse
from collections import Counter
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--strategic-db", type=Path, required=True)
    parser.add_argument("--impact-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.environ["STRATEGIC_MAP_DB_PATH"] = str(args.strategic_db.resolve(strict=True))
    os.environ["AI4S_IMPACT_DB_PATH"] = str(args.impact_db.resolve(strict=True))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ai4s_tool.api import task_recommendations as task
    from ai4s_tool.api.triage_discovery import institution_seeds

    def entries(folder, pattern):
        files = sorted(folder.glob(pattern))
        result = []
        for path in files:
            data = json.loads(path.read_text("utf-8"))
            result.extend(data if isinstance(data, list) else data.get("entries", []))
        return files, result

    dates = args.source / "dates"
    files, raw = entries(dates, "fetch-*.json")
    _, domestic = entries(dates / "zn", "*.json")
    _, overseas = entries(dates / "gw", "*.json")
    source = {"fetchFiles": len(files), "rawEntries": len(raw),
              "uniqueUrls": len({row.get('link') for row in raw}),
              "sourceLabels": len({row.get('source') for row in raw}),
              "firstFile": files[0].name, "lastFile": files[-1].name,
              "splitDomesticEntries": len(domestic), "splitOverseasEntries": len(overseas)}
    with closing(sqlite3.connect((args.source / "triage/triage.db").resolve().as_uri() + "?mode=ro", uri=True)) as c:
        source["tables"] = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                            for t in ("entity_registry", "events", "event_sources", "score_history", "direction_tree")}
        source["entityTypes"] = dict(c.execute("SELECT type,COUNT(*) FROM entity_registry GROUP BY type"))
        source["eventRange"] = c.execute("SELECT MIN(date),MAX(date) FROM events").fetchone()
    catalogue, claims, version = task._catalogue_evidence()
    candidates, candidate_claims, _ = task._candidate_evidence()
    with task.strategic_map._SESSION_FACTORY() as s:
        rows = s.query(task.strategic_map.StrategicTeamRow).filter_by(deleted=False).all()
        payloads = [task.strategic_map._team_to_dict(row, s) for row in rows]
        root_domains = s.query(task.strategic_map.StrategicDomainRow).filter_by(deleted=False, parent_id=None).all()
        by_domain = [{"id": d.id, "name": d.name,
                      "saved": sum(t["domainId"] == d.id for t in payloads),
                      "published": sum(t["domainId"] == d.id for t in catalogue),
                      "recommendationEligible": sum(t["domainId"] == d.id for t in candidates),
                      "triageDiscoverySeeds": institution_seeds(d.id)} for d in root_domains]
    with closing(task._db()) as c:
        histories = dict(c.execute("SELECT status,COUNT(*) FROM strategic_map_research_run GROUP BY status"))
        jobs = dict(c.execute("SELECT state,COUNT(*) FROM strategic_map_research_job GROUP BY state"))
    report = {"source": source, "ai4s": {
        "savedActiveTeams": len(payloads), "staffingAndScoreGate": sum(bool(t.get("candidateQualified")) for t in payloads),
        "publishedTeams": len(catalogue), "citedFacts": len(claims),
        "recommendationEligible": len(candidates), "recommendationFacts": len(candidate_claims),
        "dataVersion": version, "byDomain": by_domain, "historyRowsByStatus": histories,
        "jobStates": jobs, "publishedByBasis": dict(Counter(t["catalogueBasis"] for t in catalogue)),
        "published": [{"teamId": t["id"], "team": t["teamName"], "institution": t["institutionName"],
                       "basis": t["catalogueBasis"], "runId": t["catalogueSourceRunId"],
                       "urls": t["sourceUrls"]} for t in catalogue]},
        "notice": "Counts describe saved data and cited observations, not a new live verification or national coverage census."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({"output": str(args.output), "source": source,
                      "ai4s": {k: v for k, v in report["ai4s"].items() if k != "published"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
