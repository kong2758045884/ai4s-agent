"""Audit and backfill reviewed team citations without touching source databases.

Dry run is the default. --apply backs up the selected strategic-map SQLite file
before creating the claim index and task tables. Use an isolated copy first.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategic-db", type=Path, required=True)
    parser.add_argument("--impact-db", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    target = args.strategic_db.resolve(strict=True)
    if target.suffix.lower() != ".db":
        parser.error("strategic-db must be a SQLite .db file")
    os.environ["STRATEGIC_MAP_DB_PATH"] = str(target)
    if args.impact_db:
        os.environ["AI4S_IMPACT_DB_PATH"] = str(args.impact_db.resolve(strict=True))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ai4s_tool.api import task_recommendations as task

    teams, claims, version = task._candidate_evidence()
    print({"database": str(target), "reviewed_teams": len(teams),
           "cited_claims": len(claims), "data_version": version, "apply": args.apply})
    if not args.apply:
        return
    backup = target.with_name(target.name + ".before-task-evidence-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
    if backup.exists():
        raise FileExistsError(backup)
    source = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    destination = sqlite3.connect(backup)
    try:
        source.backup(destination)
        if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("backup failed integrity check")
    finally:
        destination.close()
        source.close()
    with closing(task._db(write=True)) as connection, connection:
        task._schema(connection)
        task._sync_claims(connection, claims)
    print({"backup": str(backup), "verified_claims": len(claims)})


if __name__ == "__main__":
    main()
