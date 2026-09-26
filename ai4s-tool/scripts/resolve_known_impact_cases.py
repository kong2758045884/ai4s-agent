"""Review known identity and taxonomy collisions in the isolated AI4S impact DB.

Dry run by default. --apply makes a SQLite backup first, then records a reversible
identity link and an event-label mapping. Source Hyper-Extract DB is never opened.
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

IDENTITY_SOURCES = {
    "Z.ai": "https://www.zhipuai.cn/zh/about",
    "Zhipu AI": "https://z.ai/company",
}


def plan(conn: sqlite3.Connection) -> dict:
    canonical = conn.execute("SELECT id FROM impact_entity WHERE name=?", ("智谱",)).fetchone()
    aliases = [(name, conn.execute("SELECT id FROM impact_entity WHERE name=?", (name,)).fetchone(), url)
               for name, url in IDENTITY_SOURCES.items()]
    target = conn.execute("""SELECT id,parent_id FROM impact_direction WHERE name=? AND level=3""",
                          ("具身智能",)).fetchall()
    if not canonical or any(row is None for _, row, _ in aliases) or len(target) != 1:
        raise ValueError("Expected one 智谱, each known English brand, and one existing 具身智能 L3")
    pending = store._rows(conn, """SELECT id,direction_id,l3_label,pending_label FROM impact_event
        WHERE direction_id IS NULL AND pending_label=? ORDER BY id""", ("具身智能",))
    link_table_exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='impact_identity_link'").fetchone()
    alias_plan = []
    for name, row, url in aliases:
        linked = (conn.execute("SELECT canonical_entity_id FROM impact_identity_link WHERE source_entity_id=?",
                               (row["id"],)).fetchone() if link_table_exists else None)
        if linked and linked["canonical_entity_id"] != canonical["id"]:
            raise ValueError(f"{name} is already linked to a different canonical subject")
        alias_plan.append({"id": row["id"], "name": name, "evidence_url": url, "already_linked": bool(linked)})
    return {"canonical_id": canonical["id"], "aliases": alias_plan,
            "target_l3": target[0]["id"], "target_l2": target[0]["parent_id"],
            "pending_event_count": len(pending),
            "pending_events": pending}


def apply(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    # SQLite's backup API produces a consistent copy, including WAL state.
    backup = path.with_name(path.name + ".before-known-cases-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
    if backup.exists():
        raise FileExistsError(backup)
    source = sqlite3.connect(path)
    try:
        with sqlite3.connect(backup) as copy:
            source.backup(copy)
    finally:
        source.close()
    with store.connect(path, write=True) as conn:
        store.init_schema(conn)
        before = plan(conn)
        with conn:
            for alias in before["aliases"]:
                if not alias["already_linked"]:
                    conn.execute("""INSERT INTO impact_identity_link
                        (source_entity_id,canonical_entity_id,evidence_url,reviewed_on) VALUES(?,?,?,?)""",
                        (alias["id"], before["canonical_id"], alias["evidence_url"], store.business_today()))
                    conn.execute("""UPDATE impact_review_case SET status='resolved',resolution=?,resolved_on=?
                        WHERE kind='possible_duplicate' AND status IN ('pending','confirmed_unresolved')
                          AND subject_ids LIKE ? AND subject_ids LIKE ?""",
                        (f"confirmed_duplicate:官方身份依据确认 {alias['name']} 与智谱同一展示主体；原分保留待重评",
                         store.business_today(), f'%"{alias["id"]}"%', f'%"{before["canonical_id"]}"%'))
                if not conn.execute("""SELECT 1 FROM impact_identity_audit WHERE source_entity_id=?
                    AND canonical_entity_id=? AND action='linked'""",
                    (alias["id"], before["canonical_id"])).fetchone():
                    conn.execute("""INSERT INTO impact_identity_audit
                        (source_entity_id,canonical_entity_id,action,evidence_url,note,reviewed_on) VALUES(?,?,?,?,?,?)""",
                        (alias["id"], before["canonical_id"], "linked", alias["evidence_url"],
                         "官网确认品牌/英文名归属；原评分分别保留并等待联合证据重评", store.business_today()))
            if before["pending_events"]:
                conn.executemany("""UPDATE impact_event SET direction_id=?,l3_label='具身智能',pending_label=NULL WHERE id=?""",
                                 [(before["target_l2"], item["id"]) for item in before["pending_events"]])
                conn.execute("""INSERT INTO impact_event_mapping_audit
                    (label,target_direction_id,before_json,note,reviewed_on) VALUES(?,?,?,?,?)""",
                    ("具身智能", before["target_l3"], json.dumps(before["pending_events"], ensure_ascii=False),
                     "与既有 L3 同名；逐条核对来源池后归入现有节点，不新建 L2/L3", store.business_today()))
        after = plan(conn)
        return {"backup": str(backup), "identity_links": [a["name"] for a in after["aliases"] if a["already_linked"]],
                "mapped_events": before["pending_event_count"] - after["pending_event_count"],
                "remaining_pending": after["pending_event_count"],
                "source_score_periods_preserved": conn.execute("SELECT COUNT(*) FROM impact_score_period WHERE source_id IS NOT NULL").fetchone()[0]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        result = apply(args.target)
    else:
        conn = store.connect(args.target)
        if conn is None:
            raise FileNotFoundError(args.target)
        with conn:
            result = plan(conn)
            result.pop("pending_events")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
