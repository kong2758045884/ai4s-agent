"""Seed a small, source-backed domestic research institution review sample.

Dry run by default. --apply backs up only the isolated impact DB. No score is
recomputed and no strategic-map team is linked by this script.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai4s_tool.api import impact_store as store  # noqa: E402

VERIFIED = {
    "中国科学院": ("public_research", "https://www.cas.cn/xxgkml/zgkxyyb/jgsz/jbqk/"),
    "清华大学": ("university", "https://www.tsinghua.edu.cn/xxgk.htm"),
    "复旦大学": ("university", "https://www.fudan.edu.cn/18/list.htm"),
    "浙江大学": ("university", "https://www.zju.edu.cn/xxgk/17944/list.htm"),
}


def plan(conn: sqlite3.Connection) -> list[dict]:
    rows = []
    for name, (research_type, url) in VERIFIED.items():
        entity = conn.execute("SELECT id,kind,country,eligibility FROM impact_entity WHERE name=?", (name,)).fetchone()
        if not entity or entity["kind"] != "institution" or entity["country"] != "zn":
            raise ValueError(f"Source subject is not a domestic institution: {name}")
        rows.append({"id": entity["id"], "name": name, "prior_eligibility": entity["eligibility"],
                     "research_type": research_type, "evidence_url": url})
    return rows


def apply(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    backup = path.with_name(path.name + ".before-verified-seed-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
    if backup.exists():
        raise FileExistsError(backup)
    with sqlite3.connect(path) as original, sqlite3.connect(backup) as copy:
        original.backup(copy)
    with store.connect(path, write=True) as conn:
        store.init_schema(conn)
        rows = plan(conn)
        if any(row["prior_eligibility"] == "excluded" for row in rows):
            raise ValueError("Refusing to override a prior exclusion")
        with conn:
            for row in rows:
                conn.execute("""UPDATE impact_entity SET eligibility='eligible',research_type=?,
                    eligibility_basis_url=?,eligibility_note=?,mainland_confirmed=1 WHERE id=?""",
                    (row["research_type"], row["evidence_url"],
                     "官方网站确认机构身份与科研职责；仅核准入榜资格，来源评分仍待校准", row["id"]))
        return {"backup": str(backup), "verified_names": [row["name"] for row in rows],
                "official_rows": len(store.list_ranking(conn))}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    conn = store.connect(args.target)
    if conn is None:
        raise FileNotFoundError(args.target)
    with conn:
        preview = plan(conn)
    print(json.dumps(apply(args.target) if args.apply else preview, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
