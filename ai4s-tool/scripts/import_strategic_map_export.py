"""Build a validated Strategic Map SQLite database from a JSON export."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, JSON, Table


def _datetime(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _row_for_table(table: Table, raw: dict[str, Any]) -> dict[str, Any]:
    unknown = set(raw) - set(table.columns.keys())
    if unknown:
        raise ValueError(f"{table.name}: export contains unknown columns: {sorted(unknown)}")
    result: dict[str, Any] = {}
    for column in table.columns:
        if column.name not in raw:
            continue
        value = raw[column.name]
        if isinstance(column.type, DateTime):
            value = _datetime(value)
        elif isinstance(column.type, JSON):
            # The export was produced from SQLite, whose JSON columns are
            # returned as JSON-encoded text. Decode that inner layer before
            # SQLAlchemy serializes it into the new database.
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    pass
            if value is None and not column.nullable:
                value = {} if column.name in {"result", "payload", "progress"} else []
        result[column.name] = value
    return result


def _load_rows(export_dir: Path, table: Table) -> list[dict[str, Any]]:
    path = export_dir / f"{table.name}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path.name}: expected a JSON array")
    return [_row_for_table(table, item) for item in raw]


def _insert_chunks(connection, table: Table, rows: list[dict[str, Any]]) -> None:
    for offset in range(0, len(rows), 200):
        connection.execute(table.insert(), rows[offset : offset + 200])


def build_database(export_dir: Path, output: Path) -> dict[str, int]:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing database: {output}")
    summary = json.loads((export_dir / "SUMMARY.json").read_text(encoding="utf-8"))
    expected = summary.get("tables") or {}
    if not isinstance(expected, dict) or not expected:
        raise ValueError("SUMMARY.json does not contain table counts")

    output.parent.mkdir(parents=True, exist_ok=True)
    os.environ["STRATEGIC_MAP_DB_PATH"] = str(output)

    from ai4s_tool.api import domain_research as domain_research
    from ai4s_tool.api import strategic_graph
    from ai4s_tool.api import strategic_map
    from ai4s_tool.api import team_research_store

    with strategic_map._ENGINE.begin() as connection:
        strategic_map._Base.metadata.create_all(connection)
        domain_research.META.create_all(connection)
        team_research_store.HISTORY.create(connection, checkfirst=True)

    tables = {
        table.name: table
        for table in (
            strategic_map.StrategicDomainRow.__table__,
            strategic_map.StrategicTeamRow.__table__,
            strategic_map.StrategicPersonRow.__table__,
            strategic_map.StrategicSyncMetaRow.__table__,
            strategic_map.StrategicRefreshTaskRow.__table__,
            strategic_graph.StrategicGraphScanTaskRow.__table__,
            domain_research.JOBS,
            domain_research.LEASES,
            team_research_store.HISTORY,
        )
    }
    if set(tables) != set(expected):
        raise ValueError(
            f"table mismatch: schema={sorted(tables)}, export={sorted(expected)}"
        )

    actual: dict[str, int] = {}
    with strategic_map._ENGINE.begin() as connection:
        for table_name in expected:
            table = tables[table_name]
            rows = _load_rows(export_dir, table)
            _insert_chunks(connection, table, rows)
            actual[table_name] = len(rows)

    strategic_map._ENGINE.dispose()
    qualified = json.loads((export_dir / "qualified_teams.json").read_text(encoding="utf-8"))
    if len(qualified) != int(summary.get("baseline_qualified", -1)):
        raise ValueError("qualified_teams.json count does not match SUMMARY.json")

    connection = sqlite3.connect(output)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"SQLite integrity check failed: {integrity}")
        checks = {
            "domain parents": """
                SELECT COUNT(*) FROM strategic_map_domain child
                LEFT JOIN strategic_map_domain parent ON parent.id = child.parent_id
                WHERE child.parent_id IS NOT NULL AND parent.id IS NULL
            """,
            "team domains": """
                SELECT COUNT(*) FROM strategic_map_team team
                LEFT JOIN strategic_map_domain domain ON domain.id = team.domain_id
                WHERE domain.id IS NULL
            """,
            "person teams": """
                SELECT COUNT(*) FROM strategic_map_person person
                LEFT JOIN strategic_map_team team ON team.id = person.team_id
                WHERE team.id IS NULL
            """,
        }
        for label, query in checks.items():
            missing = connection.execute(query).fetchone()[0]
            if missing:
                raise ValueError(f"referential check failed for {label}: {missing}")
        for table_name, expected_count in expected.items():
            count = connection.execute(
                f'SELECT COUNT(*) FROM "{table_name}"'
            ).fetchone()[0]
            if count != int(expected_count) or actual[table_name] != int(expected_count):
                raise ValueError(
                    f"{table_name}: expected {expected_count}, imported {count}"
                )
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.commit()
    finally:
        connection.close()
    return actual


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("export_dir", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    counts = build_database(args.export_dir.resolve(), args.output.resolve())
    print(json.dumps(counts, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
