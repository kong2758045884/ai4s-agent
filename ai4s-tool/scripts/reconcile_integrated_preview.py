"""Read-only source versus integration-copy reconciliation.

Checks stable IDs and original source text after additive migrations. Never
opens either database for writes and never starts a scan.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

PROTECTED = {
    "strategic": {
        "strategic_map_team": ("id", "name", "institution_name", "team_name", "source_urls"),
        "strategic_map_person": ("id", "team_id", "name", "source_urls"),
    },
    "impact": {
        "impact_entity": ("id", "name", "kind", "country", "source_id"),
        "impact_direction": ("id", "name", "parent_id", "source_id"),
        "impact_event": ("id", "entity_id", "title", "summary", "event_date", "source_id"),
        "impact_event_source": ("id", "event_id", "title", "url", "source_id"),
        "impact_score_period": ("id", "entity_id", "scan_date", "tier", "score_version", "source_id"),
    },
}


def readonly(path: Path):
    if not path.is_file():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def reconcile(source_path: Path, preview_path: Path, kind: str) -> dict:
    result = {"source": str(source_path), "preview": str(preview_path), "tables": {}, "foreign_keys": {}, "integrity": {}}
    with closing(readonly(source_path)) as source, closing(readonly(preview_path)) as preview:
        for label, conn in (("source", source), ("preview", preview)):
            result["foreign_keys"][label] = len(conn.execute("PRAGMA foreign_key_check").fetchall())
            result["integrity"][label] = conn.execute("PRAGMA integrity_check").fetchone()[0]
        for table, columns in PROTECTED[kind].items():
            select = ",".join(columns)
            before = {row["id"]: tuple(row) for row in source.execute(f"SELECT {select} FROM {table}")}
            after = {row["id"]: tuple(row) for row in preview.execute(f"SELECT {select} FROM {table}")}
            removed = sorted(before.keys() - after.keys())
            added = sorted(after.keys() - before.keys())
            changed = sorted(key for key in before.keys() & after.keys() if before[key] != after[key])
            result["tables"][table] = {"source": len(before), "preview": len(after),
                "removed_ids": removed[:10], "added_ids": added[:10], "changed_original_rows": changed[:10],
                "source_id_unique": len({row[-1] for row in after.values() if row[-1] is not None})
                if columns[-1] == "source_id" else None}
        if kind == "impact":
            result["append_only"] = {table: preview.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("impact_score_revision", "impact_team_link", "impact_team_link_audit")
                if preview.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone()}
        else:
            result["append_only"] = {table: preview.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("strategic_team_evidence_claim", "strategic_task_recommendation_run", "strategic_intelligence_daily")
                if preview.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone()}
    result["ok"] = all(result["integrity"][side] == "ok" and result["foreign_keys"][side] == 0
                       for side in ("source", "preview")) and all(
        not row["removed_ids"] and not row["added_ids"] and not row["changed_original_rows"]
        for row in result["tables"].values())
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("strategic-source", "strategic-preview", "impact-source", "impact-preview"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    report = {"strategic": reconcile(args.strategic_source, args.strategic_preview, "strategic"),
              "impact": reconcile(args.impact_source, args.impact_preview, "impact")}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not all(part["ok"] for part in report.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
