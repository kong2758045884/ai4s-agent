"""Immutable complete daily inputs, with explicit revisions and Beijing day bounds."""
from __future__ import annotations

import hashlib
import json
from contextlib import closing
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException

BEIJING = timezone(timedelta(hours=8))


def local_day(value):
    if not value:
        return ""
    if len(str(value)) == 10:
        return str(value)
    moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(BEIJING).date().isoformat()


def capture(day):
    from . import task_recommendations as tasks, impact_store as impact, strategic_changes as changes
    teams, claims, version = tasks._catalogue_evidence()
    team_map = {t["id"]: t for t in teams}
    team_changes, recommendation_changes, change_inputs, run_inputs = [], [], [], []
    with closing(tasks._db()) as conn:
        conn.execute("BEGIN")
        if tasks._has_table(conn, "strategic_change_event"):
            for row in conn.execute("SELECT * FROM strategic_change_event ORDER BY created_at,id"):
                if local_day(row["created_at"]) != day:
                    continue
                change_inputs.append({k: row[k] for k in row.keys() if k not in {"state", "attempts", "error", "processed_at"}})
                if row["subject_type"] != "team":
                    continue
                # A withdrawal remains a change even after the public team disappears.
                team = team_map.get(row["subject_id"])
                if team is None:
                    raw = conn.execute("SELECT id,team_name,institution_name,domain_id,subdomain_id FROM strategic_map_team WHERE id=?", (row["subject_id"],)).fetchone()
                    if raw:
                        team = {"id": raw["id"], "teamName": raw["team_name"], "institutionName": raw["institution_name"], "domainId": raw["domain_id"], "subdomainId": raw["subdomain_id"]}
                if team:
                    payload = json.loads(row["payload_json"])
                    team_changes.append({"changeId": row["id"], "teamId": team["id"], "teamName": team["teamName"],
                        "institutionName": team["institutionName"], "domainId": team["domainId"], "subdomainId": team.get("subdomainId"),
                        "reason": payload.get("reason", row["kind"]), "kind": row["kind"],
                        "evidence": payload, "ingestedAt": row["created_at"], "sourceId": row["source_id"]})
        if tasks._has_table(conn, "strategic_recommendation_revision"):
            for row in conn.execute("""SELECT v.*,r.result_json,p.result_json AS previous_json FROM strategic_recommendation_revision v
                JOIN strategic_task_recommendation_run r ON r.id=v.run_id JOIN strategic_task_recommendation_run p ON p.id=v.parent_run_id ORDER BY v.created_at,v.run_id"""):
                if local_day(row["created_at"]) != day:
                    continue
                current, previous = json.loads(row["result_json"]), json.loads(row["previous_json"])
                run_inputs.append({"current": current, "previous": previous, "changeIds": json.loads(row["change_ids"])})
                if changes.task_key(current) != changes.task_key(previous):
                    continue  # An algorithm/filter/N change is not a scientific development.
                old, new = [v["teamId"] for v in previous["items"]], [v["teamId"] for v in current["items"]]
                if previous["items"] != current["items"]:
                    recommendation_changes.append({"taskText": current["taskText"], "domainId": current.get("domainId"),
                        "subdomainId": current.get("subdomainId"), "before": old, "after": new,
                        "runId": current["runId"], "previousRunId": previous["runId"],
                        "reason": "证据更新引起名单或推荐依据变化", "changeIds": json.loads(row["change_ids"])})
    source_events, score_changes, tree_changes, directions, impact_inputs = [], [], [], [], {}
    conn = impact.connect()
    if conn is not None:
        with closing(conn):
            conn.execute("BEGIN")
            directions = impact.list_directions(conn)
            eligible = {r["id"] for r in impact.list_ranking(conn, view="official")}
            impact_inputs["eligibleEntityIds"] = sorted(eligible)
            impact_inputs["directions"] = directions
            impact_inputs["scores"] = [dict(r) for r in conn.execute("SELECT * FROM impact_score_period ORDER BY id")]
            for row in conn.execute("""SELECT * FROM impact_event e WHERE NOT EXISTS
                (SELECT 1 FROM impact_event n WHERE n.supersedes_event_id=e.id) ORDER BY e.id"""):
                if row["entity_id"] not in eligible:
                    continue
                occurred, ingested = row["event_date"], row["imported_on"]
                if occurred != day and not (ingested == day and row["origin"] != "source_snapshot"):
                    continue
                item = dict(row)
                item["sources"] = [dict(r) for r in conn.execute("SELECT title,url,content_sha256,excerpt FROM impact_event_source WHERE event_id=? ORDER BY id", (row["id"],))]
                item["lateArrival"] = bool(occurred < day and ingested == day)
                source_events.append(item)
            score_changes = [r for r in impact_inputs["scores"] if r["entity_id"] in eligible and r["scan_date"] == day]
            entity_names = {r["id"]: r["name"] for r in conn.execute("SELECT id,name FROM impact_entity")}
            entity_directions = {}
            for row in conn.execute("SELECT entity_id,direction_id FROM impact_entity_direction"):
                entity_directions.setdefault(row["entity_id"], []).append(row["direction_id"])
            for row in score_changes:
                row.update(name=entity_names.get(row["entity_id"], ""), directionIds=entity_directions.get(row["entity_id"], []))
            tree_changes = impact.daily_report(conn, day)["tree_changes"]
    return {"date": day, "timezone": "Asia/Shanghai", "sourceEvents": source_events, "scoreChanges": score_changes,
        "treeChanges": tree_changes, "teamChanges": team_changes, "recommendationChanges": recommendation_changes,
        "inputs": {"version": "complete-daily-v2", "teamVersion": version, "teams": teams, "claims": claims,
            "changes": change_inputs, "recommendations": run_inputs, "impact": impact_inputs, "directions": directions}}


def project(payload, domain_id=None, subdomain_id=None):
    inputs = payload["inputs"]
    directions = inputs["directions"]
    selected = {d["id"] for d in directions if (d.get("ai4s_subdomain_id") == subdomain_id if subdomain_id else (not domain_id or d.get("ai4s_domain_id") == domain_id))}
    while True:
        children = {d["id"] for d in directions if d.get("parent_id") in selected}
        if children <= selected:
            break
        selected |= children
    labels = {(d["parent_id"], d["name"]) for d in directions if d["id"] in selected and d["level"] == 3}
    team_ids = {t["id"] for t in inputs["teams"] if (not domain_id or t["domainId"] == domain_id)
                and (not subdomain_id or t.get("subdomainId") == subdomain_id)}
    in_scope = lambda r: (not domain_id or r.get("domainId") == domain_id) and (not subdomain_id or r.get("subdomainId") == subdomain_id)
    result = {k: v for k, v in payload.items() if k != "inputs"}
    result["sourceEvents"] = [r for r in payload["sourceEvents"] if r["direction_id"] in selected or (r["direction_id"], r["l3_label"]) in labels]
    result["scoreChanges"] = [r for r in payload["scoreChanges"] if set(r["directionIds"]) & selected]
    result["treeChanges"] = [r for r in payload["treeChanges"] if (r.get("direction_id") or r.get("target_direction_id")) in selected]
    result["teamChanges"] = [r for r in payload["teamChanges"] if in_scope(r)]
    result["recommendationChanges"] = [r for r in payload["recommendationChanges"] if in_scope(r) or (r.get("domainId") is None and bool((set(r["before"]) | set(r["after"])) & team_ids))]
    if domain_id or subdomain_id:
        result["recommendationChanges"] = [{**r, "before": [t for t in r["before"] if t in team_ids], "after": [t for t in r["after"] if t in team_ids]} for r in result["recommendationChanges"]]
    result["scope"] = {"domainId": domain_id, "subdomainId": subdomain_id}
    result["inputVersion"] = inputs["version"]
    result["summary"] = f"流入或补录事件 {len(result['sourceEvents'])} 条，团队更新 {len(result['teamChanges'])} 次，推荐更新 {len(result['recommendationChanges'])} 次。"
    result["followUps"] = [{"teamId": r["teamId"], "text": f"查看{r['teamName']}的{r['reason']}，确认合作接口与任务适用条件", "evidence": r["evidence"]} for r in result["teamChanges"]]
    return result


def report(day, domain_id=None, subdomain_id=None, revision=None):
    from . import task_recommendations as tasks
    key = day.isoformat() if isinstance(day, date) else day
    with closing(tasks._db()) as conn:
        if tasks._has_table(conn, "strategic_daily_snapshot"):
            row = conn.execute("SELECT * FROM strategic_daily_snapshot WHERE day=?" + (" AND revision=?" if revision else "") + " ORDER BY revision DESC LIMIT 1", (key, revision) if revision else (key,)).fetchone()
            if row:
                return project({**json.loads(row["payload_json"]), "revision": row["revision"], "inputHash": row["input_hash"], "frozen": True, "frozenAt": row["created_at"]}, domain_id, subdomain_id)
    if revision:
        raise HTTPException(404, "该日报修订版本不存在")
    payload = capture(key)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return project({**payload, "revision": 0, "inputHash": digest, "frozen": False}, domain_id, subdomain_id)


def freeze(day, domain_id=None, subdomain_id=None):
    from . import task_recommendations as tasks, strategic_changes as changes
    key = day.isoformat() if isinstance(day, date) else day
    payload = capture(key)
    # Processing status is operational metadata, not a change to report evidence.
    for event in payload["inputs"]["changes"]:
        for field in ("state", "attempts", "error", "processed_at"):
            event.pop(field, None)
    wire = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(wire.encode()).hexdigest()
    with closing(tasks._db(write=True)) as conn:
        changes.init(conn)
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute("SELECT * FROM strategic_daily_snapshot WHERE day=? ORDER BY revision DESC LIMIT 1", (key,)).fetchone()
            if not previous or previous["input_hash"] != digest:
                number = previous["revision"] + 1 if previous else 1
                conn.execute("INSERT INTO strategic_daily_snapshot VALUES(?,?,?,?,?)", (key, number, digest, wire, datetime.now(timezone.utc).isoformat()))
    return report(key, domain_id, subdomain_id)
