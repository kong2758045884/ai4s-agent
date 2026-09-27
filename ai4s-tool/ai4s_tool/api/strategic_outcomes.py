"""Versioned, source-bound team outcomes. Only explicit reviewed imports write."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

DDL = """CREATE TABLE IF NOT EXISTS strategic_team_outcome (
 id TEXT PRIMARY KEY, team_id TEXT NOT NULL, title TEXT NOT NULL, kind TEXT NOT NULL,
 quote TEXT NOT NULL, url TEXT NOT NULL, source_text TEXT NOT NULL, content_hash TEXT NOT NULL,
 ownership_json TEXT NOT NULL, published_at TEXT NOT NULL, fetched_at TEXT NOT NULL,
 batch_id TEXT NOT NULL, review_method TEXT NOT NULL, status TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_strategic_outcome_team ON strategic_team_outcome(team_id,status);"""


def init(conn):
    for sql in DDL.split(";"):
        if sql.strip(): conn.execute(sql)


def compact(value):
    return re.sub(r"\s+", "", str(value))


def valid_source(row):
    """Validate stored provenance without making any network requests."""
    try:
        ownership = json.loads(row["ownership_json"])
        return (hashlib.sha256(row["source_text"].encode()).hexdigest() == row["content_hash"]
                and compact(row["quote"]) in compact(row["source_text"])
                and bool(ownership.get("teamId")) and bool(ownership.get("quote"))
                and compact(ownership["quote"]) in compact(ownership.get("sourceText", ""))
                and ownership["teamId"] == row["team_id"])
    except (KeyError, TypeError, ValueError):
        return False


def publish(conn, team, page, outcomes, *, batch_id, ownership):
    from . import strategic_changes
    init(conn)
    if page.get("status") != "ok" or not page.get("text"):
        raise ValueError("missing original body")
    url = page["url"]
    hosts = {urlsplit(u).hostname for u in team.get("sourceUrls", [])}
    if urlsplit(url).scheme not in {"https", "http"} or urlsplit(url).hostname not in hosts:
        raise ValueError("outcome must belong to a registered team source host")
    if ownership.get("teamId") != team["id"] or not ownership.get("quote") or compact(ownership["quote"]) not in compact(ownership.get("sourceText", "")):
        raise ValueError("missing exact-team ownership evidence")
    if ownership.get("url") not in team.get("sourceUrls", []):
        raise ValueError("ownership source is not a reviewed team source")
    name = compact(team.get("teamName", ""))
    identity = compact(ownership["quote"])
    exact = bool(name and name in identity)
    pi_group = (name.endswith("研究组") and name[:-3] in identity and "研究组长" in identity
                and "/rc/" in urlsplit(ownership["url"]).path)
    if not (exact or pi_group):
        raise ValueError("ownership quote does not identify this specific research unit")
    if url != ownership["url"] and url not in ownership.get("linkedUrls", []):
        raise ValueError("outcome page is not linked from the exact team source")
    now = datetime.now(timezone.utc).isoformat()
    digest = hashlib.sha256(page["text"].encode()).hexdigest()
    added, duplicates = [], []
    for outcome in outcomes:
        quote = str(outcome.get("quote") or "").strip()
        if len(quote) < 20 or compact(quote) not in compact(page["text"]):
            raise ValueError("outcome quote is not verbatim original evidence")
        if pi_group and not re.search(r"我们|(?:本|我)?课题组|研究组|团队", quote):
            raise ValueError("personal publication list is not proof of a team outcome")
        key = hashlib.sha256(f"{team['id']}|{url}|{quote}".encode()).hexdigest()
        existing = conn.execute("SELECT status FROM strategic_team_outcome WHERE id=?", (key,)).fetchone()
        if existing:
            duplicates.append(key)
            continue
        conn.execute("INSERT INTO strategic_team_outcome VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, team["id"], outcome.get("title") or quote[:100], outcome.get("kind", "publication"), quote,
             url, page["text"], digest, json.dumps(ownership, ensure_ascii=False), outcome.get("publishedAt", ""),
             page.get("fetched_at", now), batch_id, "official-scoped-outcome-v1", "approved", now, now))
        strategic_changes.record(conn, subject_type="team", subject_id=team["id"], domain_id=team["domainId"],
            kind="outcome_published", source_id=key, payload={"reason": "新增团队原文成果", "outcomeId": key, "title": outcome.get("title", quote[:100]), "url": url})
        added.append(key)
    return {"added": added, "duplicates": duplicates}


def revoke_batch(conn, batch_id):
    from . import strategic_changes
    rows = conn.execute("SELECT * FROM strategic_team_outcome WHERE batch_id=? AND status='approved'", (batch_id,)).fetchall()
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        if row["updated_at"] != row["created_at"]:
            raise ValueError("outcome changed after import; manual reconciliation required")
        conn.execute("UPDATE strategic_team_outcome SET status='revoked',updated_at=? WHERE id=?", (now, row["id"]))
        team = conn.execute("SELECT domain_id FROM strategic_map_team WHERE id=?", (row["team_id"],)).fetchone()
        strategic_changes.record(conn, subject_type="team", subject_id=row["team_id"], domain_id=team[0] if team else None,
            kind="outcome_revoked", source_id=row["id"], payload={"reason": "撤回成果导入批次", "batchId": batch_id})
    return len(rows)
