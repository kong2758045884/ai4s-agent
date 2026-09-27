"""Add optimization tables/outbox on an existing DB, with source reconciliation.

This never replaces a database or rewrites teams, people or previous runs.
Use the same script on a consistent copy before applying to the live path.
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


def reconcile(conn):
    result = {}
    for name, in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        columns = conn.execute(f'PRAGMA table_info("{name}")').fetchall()
        key = [r[1] for r in columns if r[5]] or [columns[0][1]]
        order = ",".join('"' + c + '"' for c in key)
        digest, count = hashlib.sha256(), 0
        for row in conn.execute(f'SELECT * FROM "{name}" ORDER BY {order}'):
            digest.update(json.dumps(tuple(row), ensure_ascii=False, default=str).encode()); count += 1
        result[name] = {"count": count, "sha256": digest.hexdigest()}
    return result


def migrate(conn):
    from ai4s_tool.api import strategic_changes as changes, strategic_outcomes as outcomes, strategic_investigations as investigations
    from ai4s_tool.api import strategic_assessments as assessments
    from ai4s_tool.api import assessment_updates
    before = reconcile(conn)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        changes.init(conn); outcomes.init(conn); investigations.init(conn); assessments.init(conn)
        assessment_updates.init(conn)
        if "strategic_map_research_run" in before:
            for row in conn.execute("""SELECT r.*,t.domain_id FROM strategic_map_research_run r
                LEFT JOIN strategic_map_team t ON t.id=r.team_id WHERE r.status IN
                ('verified','official_directory','manual','revoked','duplicate','out_of_scope','conflict','rollback','official_directory_conflict','deleted_preserved') ORDER BY r.created_at,r.id"""):
                payload = json.loads(row["payload"])
                if row["status"] in {"verified", "official_directory"} and not payload.get("published"):
                    continue
                stamp = datetime.fromisoformat(row["created_at"])
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                changes.record(conn, subject_type="team", subject_id=row["team_id"], domain_id=row["domain_id"],
                    kind=row["status"], source_id=row["id"], created_at=stamp.isoformat(),
                    payload={"reason": {"official_directory": "官网目录资料发布", "verified": "原文复核资料发布", "manual": "人工修改团队资料"}.get(row["status"], "资料资格或身份发生变化"),
                        "backfilled": True, "published": payload.get("published", False)})
    after = reconcile(conn)
    for table, metadata in before.items():
        if table.startswith("strategic_change_"):
            continue
        if after[table] != metadata:
            raise RuntimeError(f"迁移更改了原表：{table}")
    return {"before": before, "after": after, "newTables": sorted(after.keys() - before.keys()),
            "foreignKeyErrors": [tuple(r) for r in conn.execute("PRAGMA foreign_key_check")],
            "integrity": conn.execute("PRAGMA quick_check").fetchone()[0]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    args = p.parse_args()
    if not args.db.is_file():
        p.error("仅迁移已经存在且已备份的数据库")
    os.environ.update(STRATEGIC_MAP_DB_PATH=str(args.db.resolve()), STRATEGIC_MAP_SKIP_STARTUP_SYNC="true", STRATEGIC_MAP_SCHEDULER_ENABLED="false")
    with sqlite3.connect(args.db, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        result = migrate(conn)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"newTables": result["newTables"], "integrity": result["integrity"], "foreignKeyErrors": result["foreignKeyErrors"]}))


if __name__ == "__main__":
    main()
