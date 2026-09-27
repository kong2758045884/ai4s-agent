"""Reconcile explicitly scoped official research units. Default is a free-HTTP dry run."""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--cache", type=Path)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--rollback", action="store_true")
    a = p.parse_args()
    if not a.db.is_file():
        p.error("数据库不存在")
    os.environ.update(STRATEGIC_MAP_DB_PATH=str(a.db.resolve()), STRATEGIC_MAP_SKIP_STARTUP_SYNC="true", STRATEGIC_MAP_SCHEDULER_ENABLED="false")
    from ai4s_tool.api import official_team_directory as directory, official_research_catalogues as catalogues, strategic_map as sm
    from scripts.expand_official_coverage import snapshot, changes, undo
    if a.rollback:
        manifest = json.loads(a.manifest.with_suffix(".applied.json").read_text(encoding="utf-8"))
        if manifest['database'] != str(a.db.resolve()):
            p.error('回滚数据库与原导入清单不一致')
        with sqlite3.connect(a.db) as conn:
            conn.row_factory = sqlite3.Row
            undo(conn, manifest, commit=False)
            from ai4s_tool.api.strategic_changes import record
            for change in manifest["changes"]["strategic_map_team"]:
                record(conn, subject_type="team", subject_id=change["id"], kind="rollback", source_id=manifest["batchId"], payload={"reason": "撤销官方目录导入批次"})
        return
    if not a.apply:
        if not a.cache:
            p.error("抓取需要 --cache")
        a.cache.mkdir(parents=True, exist_ok=True)
        pages = {}
        def fetch(url):
            path = a.cache / (hashlib.sha256(url.encode()).hexdigest()[:16] + ".json")
            page = json.loads(path.read_text(encoding="utf-8")) if path.exists() else directory.fetch_directory_page(url)
            path.write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")
            pages[url] = page
            return page
        records, errors = [], []
        for source in catalogues.SOURCES:
            if source["adapter"] != "scoped_unit":
                continue
            found, failed = catalogues.discover_source(source, fetch(source["url"]), fetch)
            records.extend(found); errors.extend(failed)
        manifest = {"batchId": "scoped-units-20260926", "records": records, "errors": errors, "pages": pages}
        a.manifest.parent.mkdir(parents=True, exist_ok=True)
        a.manifest.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"units": len(records), "errors": errors}, ensure_ascii=False))
        return
    manifest = json.loads(a.manifest.read_text(encoding="utf-8"))
    for item in manifest["records"]:
        for cite in item["citations"]:
            directory.citation(manifest["pages"][cite["url"]], cite["quote"])
    with sqlite3.connect(a.db) as conn:
        conn.row_factory = sqlite3.Row
        before = snapshot(conn)
    journal = a.manifest.with_suffix('.applied.json')
    previous = json.loads(journal.read_text(encoding='utf-8')) if journal.exists() else None
    if previous:
        if previous['database'] != str(a.db.resolve()):
            p.error('请为不同数据库复制一份独立导入清单，以保留原回滚记录')
        for table, records in previous['changes'].items():
            for record in records:
                if before[table].get(record['id']) != record['after']:
                    p.error('批次后已有修改，请先核对；不会覆盖原回滚记录')
    results = []
    try:
        with sm._SESSION_FACTORY() as session:
            for domain in session.query(sm.StrategicDomainRow).filter_by(parent_id=None, deleted=False):
                scoped = [r for r in manifest["records"] if r["domain"] == domain.name]
                if scoped:
                    results.append({"domain": domain.name, **directory.sync(session, domain, records=scoped)})
    finally:
        with sqlite3.connect(a.db) as conn:
            conn.row_factory = sqlite3.Row
            diff = changes(before, snapshot(conn))
        if previous:
            # Repeated imports must keep the first before/after rollback journal.
            assert not any(diff.values()), '重复导入产生变更，需要人工对账'
        else:
            journal.write_text(json.dumps({"batchId": manifest["batchId"], "database": str(a.db.resolve()), "results": results, "changes": diff}, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
