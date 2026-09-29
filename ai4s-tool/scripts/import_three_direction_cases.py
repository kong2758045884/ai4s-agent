"""Import four reviewed, source-bound teams for the three-direction pilot.

The existing protein direction is reused. Dry-run validates every original
page and database identity before --apply writes anything. Production applies
must fetch live university pages; --offline-cache is for isolated preview DBs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _compact(value):
    from ai4s_tool.api.official_team_directory import compact
    return compact(value)


def _page(item, cache):
    from ai4s_tool.api.official_team_directory import fetch_directory_page

    if cache:
        matches = [json.loads(path.read_text(encoding="utf-8")) for path in cache.glob("*.json")]
        page = next((page for page in matches if page.get("url") == item["source"]), None)
        if page is None:
            raise ValueError(f"离线原文缺失: {item['source']}")
    else:
        page = fetch_directory_page(item["source"])
    if page.get("status") != "ok" or len(page.get("text", "")) < 200:
        raise ValueError(f"原文抓取失败或正文过短: {item['source']}")
    for field in ("identityQuote", "leaderEvidence", "outcomeQuote"):
        if _compact(item[field]) not in _compact(page["text"]):
            raise ValueError(f"原文未找到 {field}: {item['source']}")
    for outcome in item.get("additionalOutcomes", []):
        if _compact(outcome["quote"]) not in _compact(page["text"]):
            raise ValueError(f"原文未找到附加成果: {item['source']}")
    if _compact(item["team"]) not in _compact(item["identityQuote"]):
        raise ValueError(f"团队身份未被原文明确命名: {item['team']}")
    for related in item["relatedPeople"]:
        if (_compact(related["evidenceQuote"]) not in _compact(page["text"])
                or _compact(related["name"]) not in _compact(related["evidenceQuote"])):
            raise ValueError(f"成员关系缺少原文: {related['name']}")
    return page


def _record(case, item, page):
    from ai4s_tool.api.official_team_directory import citation, person

    return {
        "domain": case["parent"],
        "institution_name": item["institution"],
        "team_name": item["team"],
        "description": f"公开研究方向：{item['directionEvidence']}。{item['scopeNote']}",
        "directions": [item["directionEvidence"]],
        "leader": person(page, item["leader"], item["leaderRole"], item["leaderEvidence"]),
        "members": [person(page, related["name"], related["role"], related["evidenceQuote"])
                    for related in item["relatedPeople"]],
        "citations": [citation(page, item["identityQuote"]),
                      citation(page, item["outcomeQuote"])],
        "source_urls": [item["source"]],
        "source_page": {"url": page["url"], "title": page.get("title", ""),
                        "text": page["text"], "fetched_at": page.get("fetched_at", ""),
                        "published_at": item["publishedAt"]},
        "identity_basis": "official_named_team_research_report",
        "source_label": "高校公开科研报道",
        "report_title": page.get("title") or item["outcomeTitle"],
        "observed_at": page.get("fetched_at", ""),
    }


def _subdomain_id(parent_id, name):
    return "subdomain_" + hashlib.sha256(f"{parent_id}|{name}".encode()).hexdigest()[:24]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).resolve().parents[1] / "ai4s_tool/data/three_direction_cases.json")
    parser.add_argument("--offline-cache", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error("数据库不存在")
    if args.offline_cache and not args.offline_cache.is_dir():
        parser.error("离线原文目录不存在")
    if args.apply and args.offline_cache and "runtime" not in args.db.resolve().parts:
        parser.error("正式数据库必须重新抓取原文，离线缓存仅用于隔离预览库")
    os.environ.update(STRATEGIC_MAP_DB_PATH=str(args.db.resolve()),
                      STRATEGIC_MAP_SKIP_STARTUP_SYNC="true", STRATEGIC_MAP_SCHEDULER_ENABLED="false")
    from ai4s_tool.api import strategic_map as sm, official_team_directory as directory
    from ai4s_tool.api import strategic_outcomes as outcomes

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    prepared = []
    for case in manifest["cases"]:
        for item in case["teams"]:
            page = _page(item, args.offline_cache)
            prepared.append((case, item, page, _record(case, item, page)))

    result = {"batchId": manifest["batchId"], "database": str(args.db.resolve()),
              "mode": "apply" if args.apply else "dry-run", "checkedAt": datetime.now(timezone.utc).isoformat(),
              "directions": [], "teams": []}
    with sm._SESSION_FACTORY() as session:
        roots = {row.name: row for row in session.query(sm.StrategicDomainRow).filter_by(parent_id=None, deleted=False)}
        for case in manifest["cases"]:
            root = roots.get(case["parent"])
            if root is None:
                raise ValueError(f"根领域不存在: {case['parent']}")
            same_name = session.query(sm.StrategicDomainRow).filter_by(name=case["direction"], deleted=False).all()
            if any(row.parent_id != root.id for row in same_name):
                raise ValueError(f"同名方向已位于其他领域: {case['direction']}")
            child = next(iter(same_name), None)
            target_id = child.id if child else _subdomain_id(root.id, case["direction"])
            result["directions"].append({"name": case["direction"], "domainId": root.id,
                                         "subdomainId": target_id, "existing": bool(child)})
        for case, item, page, _ in prepared:
            root = roots[case["parent"]]
            key = directory._directory_key(item["institution"], item["team"])
            team_id = sm._candidate_id(root.id, key)
            existing = session.query(sm.StrategicTeamRow).filter_by(id=team_id).first()
            target = next(entry["subdomainId"] for entry in result["directions"] if entry["name"] == case["direction"])
            if existing and (existing.deleted or existing.subdomain_id not in (None, target)):
                raise ValueError(f"已有团队归属冲突，需人工复核: {team_id}")
            result["teams"].append({"teamId": team_id, "team": item["team"], "direction": case["direction"],
                                    "source": item["source"], "sourceHash": hashlib.sha256(page["text"].encode()).hexdigest(),
                                    "fetchedAt": page.get("fetched_at"), "existing": bool(existing)})

    if not args.apply:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    for case in manifest["cases"]:
        entry = next(value for value in result["directions"] if value["name"] == case["direction"])
        with sm._SESSION_FACTORY() as session:
            child = session.get(sm.StrategicDomainRow, entry["subdomainId"])
            if child is None:
                siblings = session.query(sm.StrategicDomainRow).filter_by(parent_id=entry["domainId"], deleted=False).count()
                child = sm.StrategicDomainRow(id=entry["subdomainId"], name=case["direction"],
                                              description=case["description"], parent_id=entry["domainId"],
                                              sort_order=siblings)
                session.add(child)
                session.commit()
            root = session.get(sm.StrategicDomainRow, entry["domainId"])
            for _, item, page, record in (prepared_item for prepared_item in prepared if prepared_item[0] is case):
                directory.sync(session, root, records=[record])
                key = directory._directory_key(item["institution"], item["team"])
                row = session.get(sm.StrategicTeamRow, sm._candidate_id(root.id, key))
                if row is None or row.deleted:
                    raise ValueError(f"团队入库失败: {item['team']}")
                if row.subdomain_id != child.id:
                    row.subdomain_id = child.id
                    row.updated_at = sm._now()
                    session.commit()

    with sm._SESSION_FACTORY() as session, sqlite3.connect(args.db) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        for case, item, page, _ in prepared:
            root = roots[case["parent"]]
            team_id = sm._candidate_id(root.id, directory._directory_key(item["institution"], item["team"]))
            row = session.get(sm.StrategicTeamRow, team_id)
            team = sm._team_to_dict(row, session)
            ownership = {"teamId": team_id, "quote": item["identityQuote"], "url": item["source"],
                         "sourceText": page["text"], "sourceTitle": page.get("title", ""), "linkedUrls": []}
            selected_outcomes = [{"title": item["outcomeTitle"], "quote": item["outcomeQuote"],
                                  "kind": "research_result", "publishedAt": item["publishedAt"]}]
            selected_outcomes.extend({"title": value["title"], "quote": value["quote"],
                                      "kind": "research_result", "publishedAt": item["publishedAt"]}
                                     for value in item.get("additionalOutcomes", []))
            added = outcomes.publish(conn, team, page, selected_outcomes,
                                     batch_id=manifest["batchId"], ownership=ownership)
            next(entry for entry in result["teams"] if entry["teamId"] == team_id)["outcomes"] = added
        conn.commit()
        result["quickCheck"] = conn.execute("PRAGMA quick_check").fetchone()[0]
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
