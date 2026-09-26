"""Isolated, reversible impact-triage storage. No strategic-map tables are changed.

Reads never create a database. All imported Hyper-Extract rows carry their source
IDs and remain explicitly uncalibrated until AI4S evidence and thresholds are
reviewed. This module makes no model or search calls.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DB_PATH = Path(os.getenv("AI4S_IMPACT_DB_PATH") or Path(__file__).resolve().parents[2] / "impact_triage.db")
SCHEMA_VERSION = 10
ROOT_MAP = {
    "科学通用底座": "科学通用底座",
    "通用AI": "通用 AI",
    "高能物理与量子科技": "高能物理与量子科技",
    "化学与材料": "化学与材料",
    "生命科学与医学": "生命科学与医学",
    "地球科学": "地球科学",
}

DDL = """
CREATE TABLE IF NOT EXISTS impact_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS impact_direction (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, parent_id TEXT REFERENCES impact_direction(id),
 level INTEGER NOT NULL CHECK(level BETWEEN 1 AND 3), status TEXT NOT NULL,
 review_status TEXT NOT NULL DEFAULT 'source', ai4s_domain_id TEXT,
 ai4s_subdomain_id TEXT, source_id INTEGER UNIQUE, created_on TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS impact_entity (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('institution','author')),
 country TEXT NOT NULL CHECK(country IN ('zn','gw')), profile TEXT NOT NULL DEFAULT '',
 follow_status TEXT NOT NULL DEFAULT 'unfollowed', eligibility TEXT NOT NULL DEFAULT 'unreviewed',
 eligibility_note TEXT NOT NULL DEFAULT '', source_id INTEGER UNIQUE
);
CREATE TABLE IF NOT EXISTS impact_entity_direction (
 entity_id TEXT NOT NULL REFERENCES impact_entity(id), direction_id TEXT NOT NULL REFERENCES impact_direction(id),
 PRIMARY KEY(entity_id,direction_id)
);
CREATE TABLE IF NOT EXISTS impact_entity_alias (
 alias TEXT PRIMARY KEY, entity_id TEXT NOT NULL REFERENCES impact_entity(id), source_entity_id INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS impact_l3_label_alias (
 source TEXT PRIMARY KEY, target TEXT NOT NULL, source_created_at TEXT
);
CREATE TABLE IF NOT EXISTS impact_source_tree_snapshot (
 source_id INTEGER PRIMARY KEY, snapshot_date TEXT NOT NULL, tree_json TEXT NOT NULL,
 change_reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS impact_team_link (
 entity_id TEXT NOT NULL REFERENCES impact_entity(id), team_id TEXT NOT NULL,
 relation TEXT NOT NULL CHECK(relation IN ('member','affiliated','same_organization')),
 evidence_url TEXT NOT NULL, reviewed_on TEXT NOT NULL, PRIMARY KEY(entity_id,team_id)
);
CREATE TABLE IF NOT EXISTS impact_team_link_audit (
 id INTEGER PRIMARY KEY, entity_id TEXT NOT NULL, team_id TEXT NOT NULL,
 action TEXT NOT NULL CHECK(action IN ('linked','rolled_back')),
 relation TEXT NOT NULL, evidence_url TEXT NOT NULL, note TEXT NOT NULL,
 reviewed_on TEXT NOT NULL, source_audit_id INTEGER
);
CREATE TABLE IF NOT EXISTS impact_event (
 id TEXT PRIMARY KEY, entity_id TEXT REFERENCES impact_entity(id), direction_id TEXT REFERENCES impact_direction(id),
 title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', event_date TEXT,
 imported_on TEXT NOT NULL, origin TEXT NOT NULL, is_flagship INTEGER NOT NULL DEFAULT 0,
 l3_label TEXT, pending_label TEXT, source_id INTEGER UNIQUE, fingerprint TEXT NOT NULL UNIQUE,
 supersedes_event_id TEXT REFERENCES impact_event(id)
);
CREATE INDEX IF NOT EXISTS idx_impact_event_entity_date ON impact_event(entity_id,event_date);
CREATE INDEX IF NOT EXISTS idx_impact_event_direction ON impact_event(direction_id);
CREATE TABLE IF NOT EXISTS impact_event_source (
 id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES impact_event(id),
 title TEXT, url TEXT NOT NULL, publisher TEXT, source_date TEXT, source_id INTEGER UNIQUE,
 canonical_url TEXT NOT NULL DEFAULT '', content_sha256 TEXT NOT NULL DEFAULT '', excerpt TEXT NOT NULL DEFAULT '',
 UNIQUE(event_id,url)
);
CREATE TABLE IF NOT EXISTS impact_score_period (
 id TEXT PRIMARY KEY, entity_id TEXT NOT NULL REFERENCES impact_entity(id), scan_date TEXT NOT NULL,
 raw_achievement REAL, raw_status REAL, raw_trend REAL,
 eff_achievement REAL, eff_status REAL, eff_trend REAL,
 tier TEXT NOT NULL CHECK(tier IN ('A','B','C')), change_reason TEXT NOT NULL DEFAULT '',
 reasons_json TEXT NOT NULL DEFAULT '{}', anomaly_note TEXT NOT NULL DEFAULT '',
 below_streak INTEGER NOT NULL DEFAULT 0, score_version TEXT NOT NULL,
 calibration_status TEXT NOT NULL DEFAULT 'uncalibrated', source_id INTEGER UNIQUE,
 UNIQUE(entity_id,scan_date)
);
CREATE INDEX IF NOT EXISTS idx_impact_score_date ON impact_score_period(scan_date);
CREATE TABLE IF NOT EXISTS impact_review_case (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, subject_ids TEXT NOT NULL, reason TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', resolution TEXT NOT NULL DEFAULT '',
 created_on TEXT NOT NULL, resolved_on TEXT
);
CREATE TABLE IF NOT EXISTS impact_taxonomy_audit (
 id INTEGER PRIMARY KEY, direction_id TEXT NOT NULL, action TEXT NOT NULL,
 before_json TEXT NOT NULL, after_json TEXT NOT NULL, reviewed_on TEXT NOT NULL,
 reverted_on TEXT
);
CREATE TABLE IF NOT EXISTS impact_ingest_batch (
 id TEXT PRIMARY KEY, batch_date TEXT NOT NULL, origin TEXT NOT NULL,
 event_count INTEGER NOT NULL, source_count INTEGER NOT NULL, created_on TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS impact_identity_link (
 source_entity_id TEXT PRIMARY KEY REFERENCES impact_entity(id),
 canonical_entity_id TEXT NOT NULL REFERENCES impact_entity(id),
 evidence_url TEXT NOT NULL, reviewed_on TEXT NOT NULL,
 CHECK(source_entity_id != canonical_entity_id)
);
CREATE TABLE IF NOT EXISTS impact_event_mapping_audit (
 id INTEGER PRIMARY KEY, label TEXT NOT NULL, target_direction_id TEXT NOT NULL,
 before_json TEXT NOT NULL, note TEXT NOT NULL, reviewed_on TEXT NOT NULL,
 reverted_on TEXT
);
CREATE TABLE IF NOT EXISTS impact_identity_audit (
 id INTEGER PRIMARY KEY, source_entity_id TEXT NOT NULL, canonical_entity_id TEXT NOT NULL,
 action TEXT NOT NULL, evidence_url TEXT NOT NULL, note TEXT NOT NULL, reviewed_on TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS impact_score_revision (
 id TEXT PRIMARY KEY, entity_id TEXT NOT NULL REFERENCES impact_entity(id),
 scan_date TEXT NOT NULL, score_version TEXT NOT NULL, calibration_status TEXT NOT NULL,
 raw_achievement REAL, raw_status REAL, raw_trend REAL,
 eff_achievement REAL, eff_status REAL, eff_trend REAL,
 tier TEXT CHECK(tier IN ('A','B','C') OR tier IS NULL),
 change_reason TEXT NOT NULL DEFAULT '', reasons_json TEXT NOT NULL DEFAULT '{}',
 source_period_ids_json TEXT NOT NULL DEFAULT '[]', input_event_ids_json TEXT NOT NULL DEFAULT '[]',
 input_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0,
 created_on TEXT NOT NULL, UNIQUE(entity_id,scan_date,score_version,input_hash)
);
CREATE INDEX IF NOT EXISTS idx_impact_revision_entity_date ON impact_score_revision(entity_id,scan_date);
CREATE UNIQUE INDEX IF NOT EXISTS idx_impact_revision_active
 ON impact_score_revision(entity_id,scan_date) WHERE active=1;
"""


def business_today() -> str:
    """All daily buckets use the project's Asia/Shanghai business date."""
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def connect(path: Path | None = None, *, write: bool = False) -> sqlite3.Connection | None:
    path = Path(path or DB_PATH)
    if not write and not path.exists():
        return None
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
    else:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    if write:
        conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise ValueError(f"impact schema {version} is newer than supported {SCHEMA_VERSION}")
    conn.executescript(DDL)
    if "pending_label" not in {r[1] for r in conn.execute("PRAGMA table_info(impact_event)")}:
        conn.execute("ALTER TABLE impact_event ADD COLUMN pending_label TEXT")
    if "supersedes_event_id" not in {r[1] for r in conn.execute("PRAGMA table_info(impact_event)")}:
        conn.execute("ALTER TABLE impact_event ADD COLUMN supersedes_event_id TEXT REFERENCES impact_event(id)")
    source_columns = {r[1] for r in conn.execute("PRAGMA table_info(impact_event_source)")}
    for column in ("canonical_url", "content_sha256", "excerpt"):
        if column not in source_columns:
            conn.execute(f"ALTER TABLE impact_event_source ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
    entity_columns = {r[1] for r in conn.execute("PRAGMA table_info(impact_entity)")}
    if "eligibility_basis_url" not in entity_columns:
        conn.execute("ALTER TABLE impact_entity ADD COLUMN eligibility_basis_url TEXT NOT NULL DEFAULT ''")
    if "research_type" not in entity_columns:
        conn.execute("ALTER TABLE impact_entity ADD COLUMN research_type TEXT NOT NULL DEFAULT 'unverified'")
    if "mainland_confirmed" not in entity_columns:
        conn.execute("ALTER TABLE impact_entity ADD COLUMN mainland_confirmed INTEGER NOT NULL DEFAULT 0")
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    conn.commit()


def stable_id(prefix: str, value: Any) -> str:
    return prefix + "_" + hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:20]


def event_fingerprint(entity_id: str | None, title: str, event_date: str | None) -> str:
    return hashlib.sha256(json.dumps([entity_id, title.strip().casefold(), event_date], ensure_ascii=False).encode()).hexdigest()


def canonical_source_url(url: str) -> str:
    """Remove tracking query keys while preserving content-addressing parameters."""
    parsed = urlsplit(url)
    query = urlencode(sorted((key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}))
    return urlunsplit((parsed.scheme.lower(), (parsed.hostname or "").lower()
        + (f":{parsed.port}" if parsed.port else ""), parsed.path or "/", query, ""))


def source_version_fingerprint(entity_id: str, canonical_url: str, content_sha256: str) -> str:
    return hashlib.sha256(json.dumps([entity_id, canonical_url, content_sha256], ensure_ascii=False).encode()).hexdigest()


def normalize_l3_label(conn: sqlite3.Connection, label: str | None) -> str | None:
    if not label:
        return None
    aliases = {r[0]: r[1] for r in conn.execute("SELECT source,target FROM impact_l3_label_alias")}
    current = label.strip()
    seen = {current}
    while current in aliases and aliases[current] not in seen:
        current = aliases[current]
        seen.add(current)
    return current


def _rows(conn: sqlite3.Connection, sql: str, args=()) -> list[dict]:
    return [dict(row) for row in conn.execute(sql, args)]


def list_directions(conn: sqlite3.Connection) -> list[dict]:
    return _rows(conn, """SELECT d.*, CASE d.level
        WHEN 1 THEN (SELECT COUNT(*) FROM impact_event e JOIN impact_direction child ON child.id=e.direction_id WHERE child.parent_id=d.id AND NOT EXISTS (SELECT 1 FROM impact_event n WHERE n.supersedes_event_id=e.id))
        WHEN 3 THEN (SELECT COUNT(*) FROM impact_event e WHERE e.direction_id=d.parent_id AND e.l3_label=d.name AND NOT EXISTS (SELECT 1 FROM impact_event n WHERE n.supersedes_event_id=e.id))
        ELSE (SELECT COUNT(*) FROM impact_event e WHERE e.direction_id=d.id AND NOT EXISTS (SELECT 1 FROM impact_event n WHERE n.supersedes_event_id=e.id))
        END event_count FROM impact_direction d ORDER BY level,name""")


def list_ranking(conn: sqlite3.Connection, direction_id: str | None = None,
                 country: str | None = None, tier: str | None = None,
                 followed: bool | None = None, include_pending: bool = False,
                 view: str = "official", search: str | None = None) -> list[dict]:
    sql = """SELECT e.*, s.scan_date,s.raw_achievement,s.raw_status,s.raw_trend,
      s.eff_achievement,s.eff_status,s.eff_trend,s.tier,s.change_reason,
      s.reasons_json,s.anomaly_note,s.score_version,s.calibration_status,
      (SELECT COUNT(*) FROM impact_event v WHERE v.entity_id=e.id) event_count,
      (SELECT COUNT(*) FROM impact_event v WHERE v.entity_id=e.id AND v.is_flagship=1) flagship_count
      FROM impact_entity e JOIN impact_score_period s ON s.entity_id=e.id
       AND s.scan_date=(SELECT MAX(x.scan_date) FROM impact_score_period x WHERE x.entity_id=e.id)
      WHERE NOT EXISTS (SELECT 1 FROM impact_identity_link il WHERE il.source_entity_id=e.id)"""
    args: list[Any] = []
    if view == "official":
        sql += " AND e.kind='institution' AND e.country='zn' AND e.eligibility='eligible' AND e.mainland_confirmed=1 AND e.eligibility_basis_url!='' AND e.research_type IN ('university','public_research','corporate_research')"
    else:
        sql += " AND e.eligibility!='excluded'"
    if search:
        sql += """ AND (e.name LIKE ? OR EXISTS (SELECT 1 FROM impact_entity_alias a WHERE
                (a.entity_id=e.id OR a.entity_id IN (SELECT source_entity_id FROM impact_identity_link WHERE canonical_entity_id=e.id))
                AND a.alias LIKE ?)
            OR EXISTS (SELECT 1 FROM impact_identity_link il JOIN impact_entity alias ON alias.id=il.source_entity_id
                       WHERE il.canonical_entity_id=e.id AND alias.name LIKE ?)
            OR EXISTS (SELECT 1 FROM impact_event v WHERE
                (v.entity_id=e.id OR v.entity_id IN
                  (SELECT il.source_entity_id FROM impact_identity_link il WHERE il.canonical_entity_id=e.id))
                AND (v.title LIKE ? OR v.summary LIKE ?)))"""
        args.extend([f"%{search}%"] * 5)
    if direction_id:
        selected = conn.execute("SELECT level,parent_id,name FROM impact_direction WHERE id=?", (direction_id,)).fetchone()
        if selected and selected["level"] == 3:
            sql += """ AND EXISTS (SELECT 1 FROM impact_event v WHERE
                (v.entity_id=e.id OR v.entity_id IN (SELECT source_entity_id FROM impact_identity_link WHERE canonical_entity_id=e.id))
                AND v.direction_id=? AND v.l3_label=?)"""
            args.extend([selected["parent_id"], selected["name"]])
        elif selected and selected["level"] == 1:
            sql += """ AND EXISTS (SELECT 1 FROM impact_entity_direction ed JOIN impact_direction d ON d.id=ed.direction_id
                WHERE (ed.entity_id=e.id OR ed.entity_id IN (SELECT source_entity_id FROM impact_identity_link WHERE canonical_entity_id=e.id))
                AND d.parent_id=?)"""
            args.append(direction_id)
        else:
            sql += """ AND EXISTS (SELECT 1 FROM impact_entity_direction ed WHERE
                (ed.entity_id=e.id OR ed.entity_id IN (SELECT source_entity_id FROM impact_identity_link WHERE canonical_entity_id=e.id))
                AND ed.direction_id=?)"""
            args.append(direction_id)
    if country:
        sql += " AND e.country=?"
        args.append(country)
    if followed is not None:
        sql += " AND e.follow_status" + ("!='unfollowed'" if followed else "='unfollowed'")
    results = _rows(conn, sql, args)
    for row in results:
        row["reasons"] = json.loads(row.pop("reasons_json") or "{}")
        row["score_source"] = "source"
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='impact_score_revision'").fetchone():
            active = conn.execute("""SELECT * FROM impact_score_revision WHERE entity_id=?
                AND active=1 AND calibration_status='calibrated' AND tier IS NOT NULL
                ORDER BY scan_date DESC,id DESC LIMIT 1""", (row["id"],)).fetchone()
            if active:
                for field in ("scan_date", "raw_achievement", "raw_status", "raw_trend",
                              "eff_achievement", "eff_status", "eff_trend", "tier",
                              "change_reason", "score_version", "calibration_status"):
                    row[field] = active[field]
                row["reasons"] = json.loads(active["reasons_json"] or "{}")
                row["score_source"] = "calibrated_revision"
        row["direction_ids"] = [r[0] for r in conn.execute("""SELECT DISTINCT direction_id FROM impact_entity_direction
            WHERE entity_id=? OR entity_id IN (SELECT source_entity_id FROM impact_identity_link WHERE canonical_entity_id=?)
            ORDER BY direction_id""", (row["id"], row["id"]))]
        row["total"] = round(.4 * (row["eff_achievement"] or 0) + .3 * (row["eff_status"] or 0) + .3 * (row["eff_trend"] or 0), 1)
        row["identity_aliases"] = _rows(conn, """SELECT alias.name,il.source_entity_id FROM impact_identity_link il
            JOIN impact_entity alias ON alias.id=il.source_entity_id WHERE il.canonical_entity_id=?""", (row["id"],))
        row["identity_recheck_required"] = bool(row["identity_aliases"])
        members = [row["id"], *[item["source_entity_id"] for item in row["identity_aliases"]]]
        marks = ",".join("?" for _ in members)
        row["event_count"], row["flagship_count"] = conn.execute(
            f"""SELECT COUNT(*),COALESCE(SUM(v.is_flagship),0) FROM impact_event v
            WHERE v.entity_id IN ({marks}) AND NOT EXISTS
            (SELECT 1 FROM impact_event n WHERE n.supersedes_event_id=v.id)""", members).fetchone()
        if search:
            term = search.casefold()
            names = [row["name"], *[item["name"] for item in row["identity_aliases"]]]
            if any(term in name.casefold() for name in names):
                row["match_reason"] = "机构或品牌名称"
                row["match_snippet"] = next(name for name in names if term in name.casefold())
                row["match_rank"] = 0
            else:
                alias = conn.execute(f"SELECT alias FROM impact_entity_alias WHERE entity_id IN ({marks}) AND alias LIKE ? LIMIT 1",
                                     [*members, f"%{search}%"]).fetchone()
                if alias:
                    row["match_reason"], row["match_snippet"], row["match_rank"] = "登记别名", alias["alias"], 1
                else:
                    event = conn.execute(f"""SELECT title FROM impact_event WHERE entity_id IN ({marks})
                        AND (title LIKE ? OR summary LIKE ?) ORDER BY event_date DESC LIMIT 1""",
                        [*members, f"%{search}%", f"%{search}%"]).fetchone()
                    row["match_reason"], row["match_snippet"], row["match_rank"] = "事件证据", event["title"] if event else "", 2
        row["review_cases"] = _rows(conn, """SELECT id,kind,reason FROM impact_review_case
            WHERE status IN ('pending','confirmed_unresolved') AND subject_ids LIKE ?""", (f'%"{row["id"]}"%',))
    if not include_pending:
        results = [row for row in results if not row["review_cases"]]
    if tier:
        results = [row for row in results if row["tier"] == tier]
    if view == "reference":
        return sorted(results, key=lambda r: (r.get("match_rank", 0), r["name"], r["id"]))
    return sorted(results, key=lambda r: (
        0 if r["score_source"] == "calibrated_revision" else 1,
        "ABC".index(r["tier"]) if r["score_source"] == "calibrated_revision" else 3,
        -r["total"] if r["score_source"] == "calibrated_revision" else 0,
        r["name"], r["id"]))


def entity_detail(conn: sqlite3.Connection, entity_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM impact_entity WHERE id=?", (entity_id,)).fetchone()
    if not row:
        return None
    value = dict(row)
    value["directions"] = _rows(conn, "SELECT d.* FROM impact_direction d JOIN impact_entity_direction ed ON ed.direction_id=d.id WHERE ed.entity_id=?", (entity_id,))
    value["aliases"] = _rows(conn, "SELECT alias FROM impact_entity_alias WHERE entity_id=? ORDER BY alias", (entity_id,))
    value["history"] = _rows(conn, "SELECT * FROM impact_score_period WHERE entity_id=? ORDER BY scan_date", (entity_id,))
    value["events"] = _rows(conn, "SELECT * FROM impact_event WHERE entity_id=? ORDER BY event_date DESC,id", (entity_id,))
    value["identity_aliases"] = _rows(conn, """SELECT e.id,e.name,il.evidence_url FROM impact_identity_link il
        JOIN impact_entity e ON e.id=il.source_entity_id WHERE il.canonical_entity_id=?""", (entity_id,))
    if value["identity_aliases"]:
        alias_ids = [r["id"] for r in value["identity_aliases"]]
        for alias_id in alias_ids:
            value["events"].extend(_rows(conn, "SELECT * FROM impact_event WHERE entity_id=?", (alias_id,)))
            value["history"].extend(_rows(conn, "SELECT * FROM impact_score_period WHERE entity_id=?", (alias_id,)))
        value["events"].sort(key=lambda e: (e["event_date"] or "", e["id"]), reverse=True)
        value["history"].sort(key=lambda s: (s["scan_date"], s["entity_id"]))
    member_ids = [entity_id, *[item["id"] for item in value["identity_aliases"]]]
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='impact_score_revision'").fetchone():
        placeholders = ",".join("?" for _ in member_ids)
        revisions = _rows(conn, f"""SELECT * FROM impact_score_revision
          WHERE entity_id IN ({placeholders}) ORDER BY scan_date,created_on,id""", member_ids)
        for revision in revisions:
            revision["revision"] = True
            revision["source_period_ids"] = json.loads(revision.pop("source_period_ids_json"))
            revision["input_event_ids"] = json.loads(revision.pop("input_event_ids_json"))
        value["history"].extend(revisions)
        value["history"].sort(key=lambda s: (s["scan_date"], s["entity_id"], s.get("created_on") or ""))
    for score in value["history"]:
        score["reasons"] = json.loads(score.pop("reasons_json") or "{}")
    for event in value["events"]:
        event["sources"] = _rows(conn, "SELECT * FROM impact_event_source WHERE event_id=? ORDER BY id", (event["id"],))
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='impact_team_link_audit'").fetchone():
        value["team_links"] = _rows(conn, """SELECT l.*,(SELECT a.id FROM impact_team_link_audit a
            WHERE a.entity_id=l.entity_id AND a.team_id=l.team_id AND a.action='linked'
            ORDER BY a.id DESC LIMIT 1) AS audit_id
            FROM impact_team_link l WHERE l.entity_id=?""", (entity_id,))
    else:
        value["team_links"] = _rows(conn, "SELECT * FROM impact_team_link WHERE entity_id=?", (entity_id,))
    return value


def reconcile_identity_history(conn: sqlite3.Connection, canonical_id: str) -> dict[str, Any]:
    """Append date-correct evidence unions; never reuse legacy grades as calibrated scores."""
    canonical = conn.execute("SELECT id FROM impact_entity WHERE id=?", (canonical_id,)).fetchone()
    if canonical is None:
        raise ValueError("canonical entity not found")
    aliases = [r[0] for r in conn.execute(
        "SELECT source_entity_id FROM impact_identity_link WHERE canonical_entity_id=? ORDER BY source_entity_id",
        (canonical_id,))]
    members = [canonical_id, *aliases]
    slots = ",".join("?" for _ in members)
    periods = _rows(conn, f"SELECT id,entity_id,scan_date FROM impact_score_period WHERE entity_id IN ({slots}) ORDER BY scan_date,id", members)
    dates = sorted({r["scan_date"] for r in periods})
    created = 0
    for day in dates:
        # Undated legacy events cannot be silently asserted to have existed on
        # an earlier date. They remain visible in detail but outside this union.
        event_ids = [r[0] for r in conn.execute(f"""SELECT e.id FROM impact_event e WHERE e.entity_id IN ({slots})
          AND e.event_date IS NOT NULL AND e.event_date<=?
          AND (e.origin='source_snapshot' OR e.imported_on<=?)
          AND NOT EXISTS (SELECT 1 FROM impact_event n WHERE n.supersedes_event_id=e.id
              AND n.event_date<=? AND n.imported_on<=?) ORDER BY e.id""", [*members, day, day, day, day])]
        source_period_ids = [r["id"] for r in periods if r["scan_date"] <= day]
        fingerprint = hashlib.sha256(json.dumps([members, day, event_ids, source_period_ids],
                                         ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        revision_id = stable_id("reconcile", f"{canonical_id}:{day}:{fingerprint}")
        reason = {"event_count": len(event_ids), "source_period_count": len(source_period_ids),
                  "alias_count": len(aliases), "undated_events_excluded": conn.execute(
                      f"SELECT COUNT(*) FROM impact_event WHERE entity_id IN ({slots}) AND event_date IS NULL", members).fetchone()[0]}
        result = conn.execute("""INSERT OR IGNORE INTO impact_score_revision
          (id,entity_id,scan_date,score_version,calibration_status,change_reason,reasons_json,
           source_period_ids_json,input_event_ids_json,input_hash,active,created_on)
          VALUES(?,?,?,'identity-evidence-v1','pending_calibration',?,?,?,?,?,0,?)""",
          (revision_id, canonical_id, day, "身份归一后按事件日期重建证据；分数待校准",
           json.dumps(reason, ensure_ascii=False), json.dumps(source_period_ids),
           json.dumps(event_ids), fingerprint, business_today()))
        created += result.rowcount
    return {"entity_id": canonical_id, "aliases": aliases, "period_dates": dates,
            "created": created, "source_periods_preserved": len(periods), "calibration_status": "pending_calibration"}


def candidate_labels(conn: sqlite3.Connection, min_events: int = 3) -> list[dict]:
    """Show threshold candidates; never auto promote them into the live map."""
    rows = _rows(conn, """SELECT e.direction_id AS parent_id,e.l3_label AS name,COUNT(DISTINCT e.id) AS event_count,
      COUNT(DISTINCT e.event_date) AS active_dates,
      COUNT(DISTINCT s.publisher) AS independent_publishers
      FROM impact_event e JOIN impact_direction d ON d.id=e.direction_id
      LEFT JOIN impact_event_source s ON s.event_id=e.id
      WHERE d.level=2 AND e.l3_label IS NOT NULL AND trim(e.l3_label)!=''
      AND NOT EXISTS (SELECT 1 FROM impact_event n WHERE n.supersedes_event_id=e.id)
      AND NOT EXISTS (SELECT 1 FROM impact_direction child WHERE child.parent_id=e.direction_id AND child.name=e.l3_label)
      GROUP BY e.direction_id,e.l3_label HAVING COUNT(DISTINCT e.id)>=? ORDER BY event_count DESC,name""", (min_events,))
    for row in rows:
        row["multi_source_ready"] = row["independent_publishers"] >= 2 and row["active_dates"] >= 2
    return rows


def unclassified_labels(conn: sqlite3.Connection) -> list[dict]:
    """Exact labels are review hints; semantic L2 clustering needs calibration."""
    return _rows(conn, """SELECT e.pending_label AS name,COUNT(*) AS event_count,
        EXISTS(SELECT 1 FROM impact_direction d WHERE d.name=e.pending_label) AS name_collision
        FROM impact_event e WHERE e.direction_id IS NULL AND e.pending_label IS NOT NULL
        AND trim(e.pending_label)!='' GROUP BY e.pending_label ORDER BY event_count DESC,name""")


def unclassified_events(conn: sqlite3.Connection, label: str | None = None) -> list[dict]:
    sql = """SELECT v.id,v.title,v.summary,v.event_date,v.is_flagship,v.pending_label,
        e.id entity_id,e.name entity_name,e.country
        FROM impact_event v LEFT JOIN impact_entity e ON e.id=v.entity_id
        WHERE v.direction_id IS NULL"""
    args: list[Any] = []
    if label:
        sql += " AND v.pending_label=?"
        args.append(label)
    sql += " ORDER BY v.event_date DESC,v.id LIMIT 100"
    events = _rows(conn, sql, args)
    for event in events:
        event["sources"] = _rows(conn, "SELECT title,url,publisher FROM impact_event_source WHERE event_id=? ORDER BY id", (event["id"],))
    return events


def direction_events(conn: sqlite3.Connection, direction_id: str, l3_label: str | None = None) -> list[dict]:
    direction = conn.execute("SELECT * FROM impact_direction WHERE id=?", (direction_id,)).fetchone()
    if direction is None:
        return []
    sql = """SELECT v.id,v.title,v.summary,v.event_date,v.is_flagship,v.l3_label,
        e.id entity_id,e.name entity_name,e.country
        FROM impact_event v LEFT JOIN impact_entity e ON e.id=v.entity_id WHERE """
    args: list[Any]
    if direction["level"] == 1:
        sql += "v.direction_id IN (SELECT id FROM impact_direction WHERE parent_id=?)"
        args = [direction_id]
    elif direction["level"] == 3:
        sql += "v.direction_id=? AND v.l3_label=?"
        args = [direction["parent_id"], direction["name"]]
    else:
        sql += "v.direction_id=?"
        args = [direction_id]
        if l3_label:
            sql += " AND v.l3_label=?"
            args.append(l3_label)
    sql += " ORDER BY v.event_date DESC,v.id LIMIT 100"
    events = _rows(conn, sql, args)
    for event in events:
        event["sources"] = _rows(conn, "SELECT title,url,publisher FROM impact_event_source WHERE event_id=? ORDER BY id", (event["id"],))
    return events


def daily_report(conn: sqlite3.Connection, day: str) -> dict:
    date.fromisoformat(day)
    batches = _rows(conn, "SELECT * FROM impact_ingest_batch WHERE batch_date=? ORDER BY id", (day,))
    events = _rows(conn, "SELECT id,title,entity_id,direction_id,supersedes_event_id FROM impact_event WHERE imported_on=? AND origin!='source_snapshot' ORDER BY id", (day,))
    reviews = _rows(conn, "SELECT * FROM impact_taxonomy_audit WHERE reviewed_on=? ORDER BY id", (day,))
    mappings = _rows(conn, """SELECT a.id,a.label,a.target_direction_id,a.before_json,a.reviewed_on,a.reverted_on,
        d.name target_name,p.name parent_name FROM impact_event_mapping_audit a
        JOIN impact_direction d ON d.id=a.target_direction_id
        LEFT JOIN impact_direction p ON p.id=d.parent_id WHERE a.reviewed_on=? ORDER BY a.id""", (day,))
    for mapping in mappings:
        mapping["event_count"] = len(json.loads(mapping.pop("before_json")))
    identity = _rows(conn, """SELECT a.action,a.source_entity_id,a.canonical_entity_id,a.note,
        source.name source_name,target.name canonical_name FROM impact_identity_audit a
        JOIN impact_entity source ON source.id=a.source_entity_id JOIN impact_entity target ON target.id=a.canonical_entity_id
        WHERE a.reviewed_on=? ORDER BY a.id""", (day,))
    scores = _rows(conn, """SELECT s.entity_id,e.name,s.tier,s.change_reason,s.calibration_status
        FROM impact_score_period s JOIN impact_entity e ON e.id=s.entity_id
        WHERE s.scan_date=? AND s.score_version!='hyperextract-v5-source'
        ORDER BY e.name,e.id""", (day,))
    revisions = _rows(conn, """SELECT r.entity_id,e.name,r.scan_date,r.score_version,r.calibration_status
      FROM impact_score_revision r JOIN impact_entity e ON e.id=r.entity_id
      WHERE r.created_on=? ORDER BY e.name,r.scan_date,r.id""", (day,)) if conn.execute(
          "SELECT 1 FROM sqlite_master WHERE name='impact_score_revision'").fetchone() else []
    lines = [f"# 主体影响力日报 · {day}", "", "## 一、数据流入", ""]
    lines += [f"- {b['origin']}：{b['event_count']} 条事件，{b['source_count']} 个来源" for b in batches] or ["- 无新增批次"]
    lines += ["", "## 二、类目审核", ""]
    lines += [f"- {r['action']}：{r['direction_id']}{'（已回滚）' if r['reverted_on'] else ''}" for r in reviews]
    lines += [f"- 归一标签 {r['label']}：{r['event_count']} 条事件归入已有“{r['parent_name']} / {r['target_name']}”节点；未创建新类目{'（已回滚）' if r['reverted_on'] else ''}" for r in mappings]
    if not reviews and not mappings:
        lines += ["- 无类目审核"]
    lines += ["", "## 三、身份归一与重评", ""]
    lines += [f"- {r['source_name']} → {r['canonical_name']}：{'已归一身份' if r['action'] == 'linked' else '已撤销归一'}；{r['note']}" for r in identity] or ["- 无身份归一变更"]
    lines += [f"- {r['name']}：重建截至 {r['scan_date']} 的合并证据期次（{r['score_version']}；{r['calibration_status']}），保留原评分" for r in revisions]
    lines += ["", "## 四、排名变动", ""]
    lines += [f"- {s['name']}：{s['tier']}（{s['change_reason'] or '本期无等级变动'}；{s['calibration_status']}）" for s in scores] or ["- 无本期评分"]
    lines += ["", f"> 本日新增事件版本 {len(events)} 条（其中修订 {sum(bool(e['supersedes_event_id']) for e in events)} 条）；源系统历史快照不计作本日流入。"]
    return {"date": day, "batches": batches, "events": events, "tree_changes": reviews + mappings,
            "identity_changes": identity,
            "score_changes": scores, "score_revisions": revisions,
            "markdown": "\n".join(lines) + "\n"}
