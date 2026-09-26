"""Read-only source audit, or one-way import into a *new* isolated AI4S DB.

Usage:
  python scripts/reconcile_impact_triage.py --source E:/.../triage.db --map-db strategic_map.db
  python scripts/reconcile_impact_triage.py --source E:/.../triage.db --map-db strategic_map.db --apply --target impact_triage.db

The importer never opens the source for writing, never touches strategic_map.db,
and refuses an existing target. Delete the new target to roll back the import.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai4s_tool.api.impact_store import (  # noqa: E402
    ROOT_MAP, business_today, connect, event_fingerprint, init_schema, stable_id,
)

TABLES = ("direction_tree", "entity_registry", "entity_directions", "entity_aliases",
          "events", "event_sources", "score_history", "l3_label_alias",
          "direction_tree_snapshot", "direction_pending_members")


def readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def map_roots(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    with readonly(path) as conn:
        rows = conn.execute("SELECT id,name FROM strategic_map_domain WHERE parent_id IS NULL AND deleted=0").fetchall()
    return {r["name"]: r["id"] for r in rows}


def audit(source: sqlite3.Connection, domain_ids: dict[str, str]) -> dict:
    counts = {table: source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in TABLES}
    names = {r["id"]: r["name"] for r in source.execute("SELECT id,name FROM direction_tree")}
    unmapped = [dict(r) for r in source.execute("SELECT id,name FROM direction_tree WHERE level=2")
                if ROOT_MAP.get(r["name"]) not in domain_ids]
    missing_url = source.execute("SELECT COUNT(*) FROM event_sources WHERE url IS NULL OR trim(url)='' ").fetchone()[0]
    unsafe_url = source.execute("""SELECT COUNT(*) FROM event_sources WHERE url IS NOT NULL
        AND url NOT LIKE 'http://%' AND url NOT LIKE 'https://%'""").fetchone()[0]
    orphan_sources = source.execute("SELECT COUNT(*) FROM event_sources s LEFT JOIN events e ON e.id=s.event_id WHERE e.id IS NULL").fetchone()[0]
    orphan_scores = source.execute("SELECT COUNT(*) FROM score_history s LEFT JOIN entity_registry e ON e.id=s.entity_id WHERE e.id IS NULL").fetchone()[0]
    null_entity = source.execute("SELECT COUNT(*) FROM events WHERE entity_id IS NULL").fetchone()[0]
    null_direction = source.execute("SELECT COUNT(*) FROM events WHERE direction_id IS NULL").fetchone()[0]
    by_name = {r["name"].casefold(): r["id"] for r in source.execute("SELECT id,name FROM entity_registry")}
    known_duplicate = [by_name.get("智谱".casefold()), by_name.get("z.ai")]
    known_eligibility = by_name.get("集智俱乐部".casefold())
    suspect_terms = ("俱乐部", "社区", "媒体", "新闻网", "论坛", "博客", "协会", "学会", "警察", "交警", "支队")
    eligibility_candidates = [dict(r) for r in source.execute("SELECT id,name FROM entity_registry")
                              if any(term in r["name"] for term in suspect_terms)]
    alias_conflicts = [dict(r) for r in source.execute("""SELECT a.alias,a.entity_id AS alias_target,e.id AS registered_id
        FROM entity_aliases a JOIN entity_registry e ON lower(e.name)=lower(a.alias)
        WHERE e.id!=a.entity_id""")]
    return {"source_counts": counts, "schema_version": source.execute("PRAGMA user_version").fetchone()[0],
            "direction_names": names, "unmapped_roots": unmapped, "missing_source_url": missing_url,
            "non_http_source_url": unsafe_url,
            "orphan_sources": orphan_sources, "orphan_scores": orphan_scores,
            "events_without_entity": null_entity, "events_without_direction": null_direction,
            "review_watch": {"possible_same_organization": known_duplicate,
                             "possible_nonresearch_subject": known_eligibility},
            "eligibility_candidates": eligibility_candidates, "alias_conflicts": alias_conflicts}


def import_snapshot(source: sqlite3.Connection, target_path: Path, domain_ids: dict[str, str]) -> dict:
    if target_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing target: {target_path}")
    plan = audit(source, domain_ids)
    if plan["unmapped_roots"]:
        raise ValueError(f"Source L2 directions lack reviewed AI4S roots: {plan['unmapped_roots']}")
    if plan["orphan_sources"] or plan["orphan_scores"]:
        raise ValueError("Source has broken references; repair/review before import")
    if plan["source_counts"]["direction_pending_members"]:
        raise ValueError("Source contains pending cluster members; import requires explicit review mapping")
    target = connect(target_path, write=True)
    assert target is not None
    try:
        init_schema(target)
        day = business_today()
        rows = source.execute("SELECT * FROM direction_tree ORDER BY level,id").fetchall()
        for r in rows:
            parent_id = stable_id("dir", r["parent_id"]) if r["parent_id"] is not None else None
            root = r["name"] if r["level"] == 2 else None
            if r["level"] == 3 and r["parent_id"] is not None:
                root = plan["direction_names"].get(r["parent_id"])
            target.execute("""INSERT INTO impact_direction
                (id,name,parent_id,level,status,review_status,ai4s_domain_id,source_id,created_on)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (stable_id("dir", r["id"]), r["name"], parent_id, r["level"], r["status"],
                 "source_unreviewed", domain_ids.get(ROOT_MAP.get(root or "", "")), r["id"], day))
        for r in source.execute("SELECT * FROM entity_registry ORDER BY id"):
            target.execute("""INSERT INTO impact_entity
              (id,name,kind,country,profile,follow_status,source_id) VALUES(?,?,?,?,?,?,?)""",
              (stable_id("ent", r["id"]), r["name"], "institution" if r["type"] == "机构" else "author",
               r["country"], r["profile"], r["follow_status"], r["id"]))
        for r in source.execute("SELECT * FROM entity_directions"):
            target.execute("INSERT INTO impact_entity_direction VALUES(?,?)",
                           (stable_id("ent", r["entity_id"]), stable_id("dir", r["direction_id"])))
        for r in source.execute("SELECT * FROM entity_aliases"):
            target.execute("INSERT INTO impact_entity_alias VALUES(?,?,?)",
                           (r["alias"], stable_id("ent", r["entity_id"]), r["entity_id"]))
        for r in source.execute("SELECT * FROM l3_label_alias"):
            target.execute("INSERT INTO impact_l3_label_alias VALUES(?,?,?)",
                           (r["source"], r["target"], r["created_at"]))
        for r in source.execute("SELECT * FROM direction_tree_snapshot"):
            target.execute("INSERT INTO impact_source_tree_snapshot VALUES(?,?,?,?)",
                           (r["id"], r["snapshot_date"], r["tree_json"], r["change_reason"] or ""))
        for r in source.execute("SELECT * FROM events ORDER BY id"):
            entity_id = stable_id("ent", r["entity_id"]) if r["entity_id"] is not None else None
            direction_id = stable_id("dir", r["direction_id"]) if r["direction_id"] is not None else None
            fingerprint = event_fingerprint(entity_id, r["title"], r["date"])
            # One source may contain same-title same-date distinct rows. Preserve each
            # imported row by including the immutable source ID in the fingerprint.
            fingerprint = stable_id("srcfp", f"{r['id']}:{fingerprint}")
            target.execute("""INSERT INTO impact_event
              (id,entity_id,direction_id,title,summary,event_date,imported_on,origin,is_flagship,l3_label,pending_label,source_id,fingerprint)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (stable_id("ev", r["id"]), entity_id, direction_id, r["title"], r["desc"], r["date"],
               day, "source_snapshot", r["is_flagship"], r["l3_label"], r["pending_label"], r["id"], fingerprint))
        for r in source.execute("SELECT * FROM event_sources ORDER BY id"):
            target.execute("""INSERT OR IGNORE INTO impact_event_source
               (id,event_id,title,url,publisher,source_date,source_id) VALUES(?,?,?,?,?,?,?)""",
               (stable_id("src", r["id"]), stable_id("ev", r["event_id"]), r["source_title"],
                r["url"] or "", r["source_name"], r["source_date"], r["id"]))
        for r in source.execute("SELECT * FROM score_history ORDER BY id"):
            target.execute("""INSERT INTO impact_score_period
              (id,entity_id,scan_date,raw_achievement,raw_status,raw_trend,
               eff_achievement,eff_status,eff_trend,tier,change_reason,reasons_json,
               anomaly_note,below_streak,score_version,calibration_status,source_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (stable_id("score", r["id"]), stable_id("ent", r["entity_id"]), r["scan_date"],
               r["raw_achievement"], r["raw_status"], r["raw_trend"],
               r["eff_achievement"], r["eff_status"], r["eff_trend"], r["tier"],
               r["change_reason"], r["reasons_json"] or "{}", r["anomaly_note"] or "",
               r["below_streak"], "hyperextract-v5-source", "uncalibrated", r["id"]))
        pair = plan["review_watch"]["possible_same_organization"]
        if all(pair):
            ids = [stable_id("ent", x) for x in pair]
            target.execute("INSERT INTO impact_review_case VALUES(?,?,?,?,?,?,?,?)",
                           (stable_id("case", "zhipu-zai"), "possible_duplicate", json.dumps(ids),
                            "用户指出智谱与 Z.ai 可能是同一机构；需核验法律实体和品牌关系，暂不合并分数", "pending", "", day, None))
        for candidate in plan["eligibility_candidates"]:
            subject = candidate["id"]
            target.execute("INSERT INTO impact_review_case VALUES(?,?,?,?,?,?,?,?)",
                           (stable_id("case", "eligibility:" + str(subject)), "eligibility", json.dumps([stable_id("ent", subject)]),
                            f"{candidate['name']} 名称可能指向社群或媒体；需核验主体类型和成果归属", "pending", "", day, None))
        for conflict in plan["alias_conflicts"]:
            target.execute("INSERT INTO impact_review_case VALUES(?,?,?,?,?,?,?,?)",
                           (stable_id("case", "alias:" + conflict["alias"]), "alias_conflict",
                            json.dumps([stable_id("ent", conflict["alias_target"]), stable_id("ent", conflict["registered_id"])]),
                            f"别名 {conflict['alias']} 同时是另一注册主体名；需核验身份", "pending", "", day, None))
        target.execute("INSERT INTO impact_meta VALUES('source_schema',?)", (str(plan["schema_version"]),))
        target.execute("INSERT INTO impact_meta VALUES('source_origin','Hyper-Extract read-only snapshot')")
        target.commit()
        target_counts = {table: target.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                         for table in ("impact_direction", "impact_entity", "impact_entity_direction",
                                       "impact_entity_alias", "impact_event", "impact_event_source", "impact_score_period",
                                       "impact_l3_label_alias", "impact_source_tree_snapshot")}
        expected = [plan["source_counts"][x] for x in ("direction_tree", "entity_registry", "entity_directions",
                                                         "entity_aliases", "events", "event_sources", "score_history",
                                                         "l3_label_alias", "direction_tree_snapshot")]
        if list(target_counts.values()) != expected:
            raise ValueError(f"Reconciliation mismatch: source={expected}, target={target_counts}")
        return {"source": plan, "target_counts": target_counts, "target": str(target_path)}
    except Exception:
        target.rollback()
        target.close()
        # Only remove a target created by this call, never an existing user DB.
        target_path.unlink(missing_ok=True)
        raise
    finally:
        target.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--map-db", type=Path)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    domains = map_roots(args.map_db)
    with readonly(args.source) as source:
        if args.apply:
            if args.target is None:
                parser.error("--apply requires --target")
            result = import_snapshot(source, args.target, domains)
        else:
            result = audit(source, domains)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
