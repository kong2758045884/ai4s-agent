"""Transactional outbox for local recommendation refreshes (no paid work)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone

SCHEMA = """CREATE TABLE IF NOT EXISTS strategic_change_event (
 id TEXT PRIMARY KEY, subject_type TEXT NOT NULL, subject_id TEXT NOT NULL,
 domain_id TEXT, kind TEXT NOT NULL, source_id TEXT NOT NULL, payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 error TEXT NOT NULL DEFAULT '', processed_at TEXT);
CREATE INDEX IF NOT EXISTS ix_strategic_change_pending ON strategic_change_event(state,created_at);
CREATE TABLE IF NOT EXISTS strategic_recommendation_revision (
 parent_run_id TEXT NOT NULL, run_id TEXT NOT NULL, data_version TEXT NOT NULL,
 task_key TEXT NOT NULL, change_ids TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(parent_run_id,data_version));
CREATE TABLE IF NOT EXISTS strategic_daily_snapshot (
 day TEXT NOT NULL, revision INTEGER NOT NULL, input_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(day,revision));"""


def init(conn):
    # execute statements individually to preserve the caller's transaction.
    for statement in SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def record(conn, *, subject_type, subject_id, kind, source_id, domain_id=None, payload=None, created_at=None):
    init(conn)
    key = hashlib.sha256(f"{subject_type}|{subject_id}|{kind}|{source_id}".encode()).hexdigest()
    conn.execute("""INSERT OR IGNORE INTO strategic_change_event
      (id,subject_type,subject_id,domain_id,kind,source_id,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?)""",
      (key, subject_type, subject_id, domain_id, kind, source_id,
       json.dumps(payload or {}, ensure_ascii=False), created_at or datetime.now(timezone.utc).isoformat()))
    return key


def record_history(session, run_id, team_id, status, payload, created_at):
    relevant = {"verified", "official_directory", "manual", "revoked", "duplicate", "out_of_scope",
                "official_directory_conflict", "conflict", "deleted_preserved", "rollback"}
    if status not in relevant:
        return
    if status in {"verified", "official_directory"} and not payload.get("published"):
        return
    # Use the DBAPI connection already owned by this SQLAlchemy transaction.
    conn = session.connection().connection.driver_connection
    row = conn.execute("SELECT domain_id FROM strategic_map_team WHERE id=?", (team_id,)).fetchone()
    record(conn, subject_type="team", subject_id=team_id, domain_id=row[0] if row else None,
           kind=status, source_id=run_id, created_at=(created_at.replace(tzinfo=timezone.utc) if created_at.tzinfo is None else created_at.astimezone(timezone.utc)).isoformat(),
           payload={"fields": payload.get("fields", []), "published": payload.get("published", False),
                    "reason": {"official_directory": "官网目录资料发布", "verified": "原文复核资料发布", "manual": "人工修改团队资料"}.get(status, "资料资格或身份发生变化")})


def task_key(result):
    from .strategic_text import parse_task
    parsed = result.get("parsedTask") or parse_task(result["taskText"])
    definition = {k: sorted(parsed.get(k, [])) for k in ("goals", "required", "excluded", "unresolved")}
    definition["mode"] = parsed.get("mode", "all")
    return hashlib.sha256(json.dumps({"task": definition,
        "domain": result.get("domainId"), "subdomain": result.get("subdomainId"),
        "limit": result["requestedLimit"], "match": result["matchVersion"]},
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()


_LOCK = threading.Lock()
_STARTED = False


def bridge_impact():
    """Copy committed impact outbox entries idempotently; each database owns its transaction."""
    from contextlib import closing
    from . import task_recommendations as tasks, impact_store
    source = impact_store.connect()
    if source is None:
        return
    with closing(source):
        if not tasks._has_table(source, "strategic_change_event"):
            return
        rows = source.execute("SELECT * FROM strategic_change_event ORDER BY created_at,id").fetchall()
    if not rows:
        return
    with closing(tasks._db(write=True)) as target, target:
        init(target)
        for row in rows:
            record(target, subject_type=row["subject_type"], subject_id=row["subject_id"], kind=row["kind"],
                source_id=row["source_id"], domain_id=row["domain_id"], payload=json.loads(row["payload_json"]), created_at=row["created_at"])


def start_local_worker():
    """Only local change processing; automated network/model collection remains off."""
    global _STARTED
    import os
    import time
    if _STARTED or os.getenv("AI4S_CHANGE_WORKER_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        return
    _STARTED = True
    from .strategic_investigations import recover_interrupted
    recover_interrupted()
    def loop():
        while True:
            try:
                bridge_impact()
                process_pending()
            except Exception:
                # Durable events remain pending; avoid logging provider/user payloads.
                pass
            time.sleep(10)
    threading.Thread(target=loop, name="strategic-local-changes", daemon=True).start()


def process_pending(limit=100):
    """Retryable and idempotent; updates the latest run for each task definition."""
    from contextlib import closing
    from . import task_recommendations as tasks
    if not _LOCK.acquire(blocking=False):
        return {"busy": True}
    try:
        with closing(tasks._db(write=True)) as conn:
            init(conn)
            events = conn.execute("SELECT * FROM strategic_change_event WHERE state='pending' AND attempts<3 ORDER BY created_at,id LIMIT ?", (limit,)).fetchall()
            conn.commit()
            if not events:
                return {"processed": 0}
            from . import assessment_updates
            private_result = assessment_updates.process(events)
            domains = {r["domain_id"] for r in events}
            entity_change = any(r["subject_type"] != "team" for r in events)
            latest = {}
            public_rows = conn.execute("""SELECT * FROM strategic_task_recommendation_run r WHERE NOT EXISTS
                (SELECT 1 FROM strategic_recommendation_revision v WHERE v.parent_run_id=r.id) ORDER BY created_at,id""") if tasks._has_table(conn, "strategic_task_recommendation_run") else []
            for row in public_rows:
                value = json.loads(row["result_json"])
                latest[task_key(value)] = value
            version = tasks._recommendation_version(tasks._candidate_evidence()[2], tasks._institution_links())
            for previous in latest.values():
                if not entity_change and previous.get("domainId") and previous["domainId"] not in domains:
                    continue
                if previous["dataVersion"] == version and previous["matchVersion"] == tasks.MATCH_VERSION:
                    continue
                with conn:
                    found = conn.execute("SELECT run_id FROM strategic_recommendation_revision WHERE parent_run_id=? AND data_version=?", (previous["runId"], version)).fetchone()
                if found:
                    continue
                # recommend + revision marker commit together; a crash cannot create an orphan replacement.
                tasks._recommend(tasks.TaskRequest(taskText=previous["taskText"], domainId=previous.get("domainId"),
                    subdomainId=previous.get("subdomainId"), limit=previous["requestedLimit"]),
                    parent_run_id=previous["runId"], change_ids=[r["id"] for r in events])
            with conn:
                conn.executemany("UPDATE strategic_change_event SET state='processed',processed_at=?,error='' WHERE id=?", [(datetime.now(timezone.utc).isoformat(), r["id"]) for r in events])
            return {"processed": len(events), "privateAssessments": private_result}
    except Exception as exc:
        # Preserve the outbox for retry, without storing provider bodies or credentials.
        if "conn" in locals() and "events" in locals():
            with tasks._db(write=True) as retry:
                retry.executemany("UPDATE strategic_change_event SET attempts=attempts+1,error=? WHERE id=?", [(type(exc).__name__, r["id"]) for r in events])
        return {"error": type(exc).__name__}
    finally:
        _LOCK.release()
