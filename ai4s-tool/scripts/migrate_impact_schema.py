"""Back up and add missing impact-triage tables on an explicitly selected DB.

Dry run is the default. This only adds schema; it never rewrites source records.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai4s_tool.api import impact_store

PROTECTED = ("impact_entity", "impact_direction", "impact_event", "impact_event_source", "impact_score_period")


def snapshot(conn):
    return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in PROTECTED}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    target = args.db.resolve(strict=True)
    if target.suffix.lower() != ".db":
        parser.error("--db must select an existing SQLite .db file")
    with closing(impact_store.connect(target)) as source:
        before = snapshot(source)
        version = source.execute("PRAGMA user_version").fetchone()[0]
        if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("source database integrity check failed")
    report = {"database": str(target), "schema_before": version, "schema_target": impact_store.SCHEMA_VERSION,
              "protected_counts": before, "apply": args.apply}
    if args.apply:
        backup = target.with_name(target.name + ".before-schema-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
        if backup.exists():
            raise FileExistsError(backup)
        with closing(impact_store.connect(target)) as source, closing(sqlite3.connect(backup)) as saved:
            source.backup(saved)
            if saved.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("backup integrity check failed")
        with closing(impact_store.connect(target, write=True)) as conn:
            impact_store.init_schema(conn)
            after = snapshot(conn)
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = len(conn.execute("PRAGMA foreign_key_check").fetchall())
        report.update({"backup": str(backup), "protected_counts_after": after,
                       "integrity": integrity, "foreign_key_errors": foreign_keys})
        if after != before or integrity != "ok" or foreign_keys:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            raise RuntimeError("post-migration reconciliation failed; backup is retained")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
