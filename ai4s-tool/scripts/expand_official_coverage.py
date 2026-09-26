"""Replayable free-HTTP directory expansion, preview-only writes and guarded undo.

Default: use cached official pages and write an evidence manifest, without DB
writes. --fetch permits public HTTP cache misses. --apply needs an explicit
preview database. No search provider, embedding or language model is invoked.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TABLES = ("strategic_map_team", "strategic_map_person", "strategic_map_research_run")


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def snapshot(conn):
    return {table: {r["id"]: dict(r) for r in conn.execute(f'SELECT * FROM "{table}"')} for table in TABLES}


def changes(before, after):
    return {table: [{"id": key, "before": before[table].get(key), "after": after[table].get(key)}
                    for key in sorted(before[table].keys() | after[table].keys())
                    if before[table].get(key) != after[table].get(key)] for table in TABLES}


def active_jobs(conn):
    for table in ("strategic_map_refresh_task", "strategic_map_graph_scan_task"):
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
            if conn.execute(f"SELECT COUNT(*) FROM {table} WHERE state IN ('running','accepted','queued')").fetchone()[0]:
                raise RuntimeError("存在进行中的调查任务，请完成后重放导入")


def undo(conn, manifest):
    """Refuse to undo rows edited after the batch; preserve unrelated user data."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        active_jobs(conn)
        for table in TABLES:
            for change in manifest["changes"][table]:
                row = conn.execute(f'SELECT * FROM "{table}" WHERE id=?', (change["id"],)).fetchone()
                if (dict(row) if row else None) != change["after"]:
                    raise RuntimeError(f"回滚拒绝：{table}/{change['id']} 已有后续改动")
        for table in reversed(TABLES):
            for change in manifest["changes"][table]:
                if change["before"] is None:
                    conn.execute(f'DELETE FROM "{table}" WHERE id=?', (change["id"],))
        for table in TABLES:
            for change in manifest["changes"][table]:
                old = change["before"]
                if old is not None:
                    cols = list(old)
                    assignments = ",".join(f'"{c}"=?' for c in cols if c != "id")
                    conn.execute(f'UPDATE "{table}" SET {assignments} WHERE id=?',
                                 [old[c] for c in cols if c != "id"] + [old["id"]])
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("回滚外键检查失败")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", type=Path)
    args = parser.parse_args()
    os.environ.update(ENV="preview", STRATEGIC_MAP_SCHEDULER_ENABLED="false",
                      STRATEGIC_MAP_SKIP_STARTUP_SYNC="true", AI4S_SKIP_EMBEDDING_HEALTH="1")
    if args.apply or args.rollback:
        if not args.db or not args.db.is_file() or "preview" not in str(args.db.resolve()).lower():
            parser.error("写操作仅允许明确指定已存在的 preview 数据库副本")
        os.environ["STRATEGIC_MAP_DB_PATH"] = str(args.db.resolve())
    if args.rollback:
        manifest = json.loads(args.rollback.read_text(encoding="utf-8"))
        if str(args.db.resolve()) != manifest["database"]:
            parser.error("回滚数据库与导入清单不一致")
        with sqlite3.connect(args.db) as conn:
            conn.row_factory = sqlite3.Row
            undo(conn, manifest)
        save(args.output, {"rolledBack": str(args.rollback), "database": str(args.db.resolve())})
        return

    from ai4s_tool.api.official_research_catalogues import SOURCES, discover_source
    from ai4s_tool.api.official_team_directory import fetch_directory_page

    def fetch(url):
        path = args.cache / (hashlib.sha256(url.encode()).hexdigest()[:16] + ".json")
        if path.exists():
            page = json.loads(path.read_text(encoding="utf-8"))
            if page.get("status") == "ok":
                return page
        if not args.fetch:
            return {"url": url, "status": "cache_missing"}
        page = fetch_directory_page(url)
        save(path, page)
        return page

    records, errors = [], []
    for source in SOURCES:
        directory = fetch(source["url"])
        if directory.get("status") != "ok":
            errors.append({"url": source["url"], "reason": "directory_fetch_failed"})
            continue
        found, failed = discover_source(source, directory, fetch)
        records.extend(found)
        errors.extend(failed)
    manifest = {"createdAt": datetime.now(timezone.utc).isoformat(), "mode": "dry-run",
                "records": records, "errors": errors,
                "summary": {"units": len(records), "outcomes": sum(
                    f["kind"] == "outcome" for r in records for f in r["facts"])}}
    save(args.output, manifest)
    if args.apply:
        from ai4s_tool.api import strategic_map as sm, official_team_directory as directory
        with sqlite3.connect(args.db) as conn:
            conn.row_factory = sqlite3.Row
            active_jobs(conn)
            before = snapshot(conn)
            backup = args.output.with_suffix(".before.db")
            if backup.exists():
                raise RuntimeError("备份已存在；请使用新的输出文件名，避免覆盖")
            with sqlite3.connect(backup) as target:
                conn.backup(target)
        manifest.update(mode="applied", database=str(args.db.resolve()), backup=str(backup.resolve()), results=[])
        try:
            with sm._SESSION_FACTORY() as session:
                for domain in session.query(sm.StrategicDomainRow).filter_by(parent_id=None, deleted=False):
                    scoped = [r for r in records if r["domain"] == domain.name]
                    if scoped:
                        manifest["results"].append({"domain": domain.name, **directory.sync(session, domain, records=scoped)})
        finally:
            with sqlite3.connect(args.db) as conn:
                conn.row_factory = sqlite3.Row
                after = snapshot(conn)
                manifest["changes"] = changes(before, after)
                manifest["countsBefore"] = {t: len(v) for t, v in before.items()}
                manifest["countsAfter"] = {t: len(v) for t, v in after.items()}
                manifest["foreignKeyErrors"] = [tuple(r) for r in conn.execute("PRAGMA foreign_key_check")]
            save(args.output, manifest)
    print(json.dumps({k: v for k, v in manifest.items() if k not in {"records", "changes"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
