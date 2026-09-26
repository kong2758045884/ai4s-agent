"""Evidence-first impact ranking, taxonomy review and deterministic daily API."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import date
from urllib.parse import urlsplit
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, HttpUrl

from . import impact_store as store

router = APIRouter(prefix="/impact-triage", tags=["impact_triage"])


@contextmanager
def _read():
    conn = store.connect()
    if conn is None:
        raise HTTPException(503, "影响力快照尚未导入；请先运行只读对账")
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def _write():
    conn = store.connect(write=True)
    assert conn is not None
    try:
        store.init_schema(conn)
        with conn:
            yield conn
    finally:
        conn.close()


@router.get("/status")
def status():
    conn = store.connect()
    if conn is None:
        return {"ready": False, "source": None, "entities": 0, "events": 0, "scores": 0}
    with closing(conn):
        return {"ready": True,
                "source": conn.execute("SELECT value FROM impact_meta WHERE key='source_origin'").fetchone()[0],
                "entities": conn.execute("SELECT count(*) FROM impact_entity").fetchone()[0],
                "events": conn.execute("SELECT count(*) FROM impact_event").fetchone()[0],
                "scores": conn.execute("SELECT count(*) FROM impact_score_period").fetchone()[0]}


@router.get("/directions")
def directions():
    with _read() as conn:
        return {"items": store.list_directions(conn), "candidates": store.candidate_labels(conn),
                "l2_candidates": store.unclassified_labels(conn),
                "unclassified_events": conn.execute("SELECT COUNT(*) FROM impact_event WHERE direction_id IS NULL").fetchone()[0]}


@router.get("/unclassified/events")
def unclassified_events(label: str | None = None):
    with _read() as conn:
        return {"items": store.unclassified_events(conn, label), "limit": 100}


@router.get("/directions/{direction_id}/events")
def events_by_direction(direction_id: str, l3_label: str | None = None):
    with _read() as conn:
        direction = conn.execute("SELECT 1 FROM impact_direction WHERE id=?", (direction_id,)).fetchone()
        if direction is None:
            raise HTTPException(404, "方向不存在")
        return {"items": store.direction_events(conn, direction_id, l3_label), "limit": 100}


@router.get("/ranking")
def ranking(direction_id: str | None = None, country: Literal["zn", "gw"] | None = None,
            tier: Literal["A", "B", "C"] | None = None, followed: bool | None = None,
            include_pending: bool = False, view: Literal["official", "reference"] = "official",
            q: str = ""):
    with _read() as conn:
        if direction_id and not conn.execute("SELECT 1 FROM impact_direction WHERE id=?", (direction_id,)).fetchone():
            raise HTTPException(404, "方向不存在")
        return {"items": store.list_ranking(conn, direction_id, country, tier, followed, include_pending, view, q[:100].strip()),
                "view": view, "score_notice": "只有已核验的国内科研机构可进入正式榜；源 A/B/C 与 0.4/0.3/0.3 权重尚未校准，不等于团队评分"}


@router.get("/entities/{entity_id}")
def entity(entity_id: str):
    with _read() as conn:
        value = store.entity_detail(conn, entity_id)
        if not value:
            raise HTTPException(404, "主体不存在")
        return value


@router.get("/entities/{entity_id}/team-candidates")
def team_candidates(entity_id: str, q: str = ""):
    """Search AI4S teams without asserting an institution relationship."""
    with _read() as conn:
        if conn.execute("SELECT 1 FROM impact_entity WHERE id=?", (entity_id,)).fetchone() is None:
            raise HTTPException(404, "主体不存在")
    from . import strategic_map
    path = strategic_map._DB_PATH
    if not path.is_file():
        return {"items": []}
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as team_db:
        team_db.row_factory = sqlite3.Row
        term = q.strip()[:80]
        if len(term) < 2:
            return {"items": []}
        rows = team_db.execute("""SELECT id,institution_name,team_name,domain_id,source_urls
             FROM strategic_map_team WHERE deleted=0 AND is_domestic=1
             AND (institution_name LIKE ? OR team_name LIKE ?) ORDER BY institution_name,team_name LIMIT 20""",
             (f"%{term}%", f"%{term}%")).fetchall()
        return {"items": [dict(row) for row in rows]}


class TeamLinkReview(BaseModel):
    team_id: str = Field(min_length=1, max_length=64)
    relation: Literal["member", "affiliated", "same_organization"]
    evidence_url: HttpUrl
    note: str = Field(min_length=3, max_length=500)


@router.post("/entities/{entity_id}/team-links")
def review_team_link(entity_id: str, body: TeamLinkReview):
    """Link two stable IDs after a human checks an official affiliation source."""
    if body.evidence_url.scheme != "https":
        raise HTTPException(400, "机构—团队关联必须提供 HTTPS 官方原文")
    from . import strategic_map
    path = strategic_map._DB_PATH
    if not path.is_file():
        raise HTTPException(503, "团队库不可用")
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as team_db:
        team_db.row_factory = sqlite3.Row
        team = team_db.execute("""SELECT id,institution_name,team_name,is_domestic,deleted,
            source_urls,evidence_urls FROM strategic_map_team WHERE id=?""", (body.team_id,)).fetchone()
        if not team or team["deleted"] or not team["is_domestic"]:
            raise HTTPException(400, "团队不存在或不是国内有效团队")
        import json as _json
        reviewed_urls = set((_json.loads(team["source_urls"] or "[]") or [])
                            + (_json.loads(team["evidence_urls"] or "[]") or []))
    with _write() as conn:
        entity = conn.execute("""SELECT kind,country,eligibility,eligibility_basis_url,mainland_confirmed
            FROM impact_entity WHERE id=?""", (entity_id,)).fetchone()
        if not entity:
            raise HTTPException(404, "主体不存在")
        if (entity["kind"] != "institution" or entity["country"] != "zn"
                or entity["eligibility"] != "eligible" or not entity["mainland_confirmed"]):
            raise HTTPException(400, "仅已核验的国内科研机构可关联团队")
        url = str(body.evidence_url)
        allowed_hosts = {urlsplit(item).hostname for item in (*reviewed_urls, entity["eligibility_basis_url"])
                         if item and urlsplit(item).scheme == "https"}
        if urlsplit(url).hostname not in allowed_hosts:
            raise HTTPException(400, "关联依据须来自已审核的机构或团队来源站点")
        if conn.execute("SELECT 1 FROM impact_team_link WHERE entity_id=? AND team_id=?",
                        (entity_id, body.team_id)).fetchone():
            raise HTTPException(409, "该机构—团队关系已审核")
        conn.execute("""INSERT INTO impact_team_link
            (entity_id,team_id,relation,evidence_url,reviewed_on) VALUES(?,?,?,?,?)""",
            (entity_id, body.team_id, body.relation, url, store.business_today()))
        audit_id = conn.execute("""INSERT INTO impact_team_link_audit
            (entity_id,team_id,action,relation,evidence_url,note,reviewed_on)
            VALUES(?,?,'linked',?,?,?,?)""",
            (entity_id, body.team_id, body.relation, url, body.note, store.business_today())).lastrowid
        return {"audit_id": audit_id, "entity_id": entity_id, "team_id": body.team_id,
                "relation": body.relation, "team_events_inherited": False}


@router.post("/team-links/{audit_id}/rollback")
def rollback_team_link(audit_id: int):
    with _write() as conn:
        audit = conn.execute("SELECT * FROM impact_team_link_audit WHERE id=? AND action='linked'",
                             (audit_id,)).fetchone()
        if not audit:
            raise HTTPException(404, "关联审核记录不存在")
        if conn.execute("SELECT 1 FROM impact_team_link_audit WHERE source_audit_id=?",
                        (audit_id,)).fetchone():
            raise HTTPException(409, "关联已回滚")
        current = conn.execute("SELECT * FROM impact_team_link WHERE entity_id=? AND team_id=?",
                               (audit["entity_id"], audit["team_id"])).fetchone()
        if not current or current["relation"] != audit["relation"] or current["evidence_url"] != audit["evidence_url"]:
            raise HTTPException(409, "关联已变化，不能按旧审核记录回滚")
        conn.execute("DELETE FROM impact_team_link WHERE entity_id=? AND team_id=?",
                     (audit["entity_id"], audit["team_id"]))
        conn.execute("""INSERT INTO impact_team_link_audit
            (entity_id,team_id,action,relation,evidence_url,note,reviewed_on,source_audit_id)
            VALUES(?,?,'rolled_back',?,?,?,?,?)""",
            (audit["entity_id"], audit["team_id"], audit["relation"], audit["evidence_url"],
             "按关联审核记录回滚；两侧主体和原事件均未改动", store.business_today(), audit_id))
        return {"rolled_back": True, "audit_id": audit_id, "team_events_preserved": True}


@router.post("/entities/{entity_id}/reconcile-history")
def reconcile_history(entity_id: str):
    """Append a versioned evidence timeline for a reviewed canonical identity."""
    with _write() as conn:
        if conn.execute("SELECT 1 FROM impact_identity_link WHERE canonical_entity_id=?", (entity_id,)).fetchone() is None:
            raise HTTPException(400, "主体尚无审核确认的身份归一关系")
        return store.reconcile_identity_history(conn, entity_id)


@router.get("/reviews")
def reviews():
    with _read() as conn:
        return {"items": store._rows(conn, "SELECT * FROM impact_review_case ORDER BY status,kind,id")}


@router.get("/audits")
def audits():
    with _read() as conn:
        return {"items": store._rows(conn, "SELECT id,direction_id,action,reviewed_on,reverted_on FROM impact_taxonomy_audit ORDER BY id DESC LIMIT 100"),
                "event_mappings": store._rows(conn, "SELECT id,label,target_direction_id,reviewed_on,reverted_on FROM impact_event_mapping_audit ORDER BY id DESC LIMIT 100")}


@router.get("/daily/{day}")
def daily(day: date):
    with _read() as conn:
        return store.daily_report(conn, day.isoformat())


class DirectionReview(BaseModel):
    decision: Literal["approve", "reject"]
    note: str = Field(min_length=3, max_length=500)


def _audit(conn: sqlite3.Connection, direction_id: str, action: str, before: dict | None, after: dict | None) -> int:
    cur = conn.execute("""INSERT INTO impact_taxonomy_audit
      (direction_id,action,before_json,after_json,reviewed_on) VALUES(?,?,?,?,?)""",
      (direction_id, action, json.dumps(before, ensure_ascii=False),
       json.dumps(after, ensure_ascii=False), store.business_today()))
    return cur.lastrowid


@router.post("/directions/{direction_id}/review")
def review_direction(direction_id: str, body: DirectionReview):
    with _write() as conn:
        row = conn.execute("SELECT * FROM impact_direction WHERE id=?", (direction_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "方向不存在")
        if row["level"] == 1:
            raise HTTPException(400, "根领域不可审核")
        if row["level"] == 2 and body.decision == "reject":
            raise HTTPException(400, "已有事件的 L2 拒绝需要方向重映射，不能仅改变状态")
        before = dict(row)
        after = {**before, "review_status": "approved" if body.decision == "approve" else "rejected",
                 "status": "formal" if body.decision == "approve" else "rejected"}
        conn.execute("UPDATE impact_direction SET review_status=?,status=? WHERE id=?",
                     (after["review_status"], after["status"], direction_id))
        audit_id = _audit(conn, direction_id, body.decision + ":" + body.note, before, after)
        return {"audit_id": audit_id, "direction": after}


class CandidateReview(DirectionReview):
    parent_id: str
    name: str = Field(min_length=2, max_length=80)


@router.post("/candidates/review")
def review_candidate(body: CandidateReview):
    with _write() as conn:
        candidate = next((c for c in store.candidate_labels(conn)
                          if c["parent_id"] == body.parent_id and c["name"] == body.name), None)
        if candidate is None:
            raise HTTPException(409, "候选已变化或不足最低样本数，请刷新")
        parent = conn.execute("SELECT * FROM impact_direction WHERE id=?", (body.parent_id,)).fetchone()
        if parent is None or parent["level"] != 2 or parent["status"] != "formal":
            raise HTTPException(400, "L3 候选必须挂在 L2 下")
        direction_id = store.stable_id("candidate", body.parent_id + ":" + body.name)
        after = {"id": direction_id, "name": body.name, "parent_id": body.parent_id, "level": 3,
                 "status": "formal" if body.decision == "approve" else "rejected",
                 "review_status": "approved" if body.decision == "approve" else "rejected",
                 "ai4s_domain_id": parent["ai4s_domain_id"], "ai4s_subdomain_id": None,
                 "source_id": None, "created_on": store.business_today()}
        try:
            conn.execute("""INSERT INTO impact_direction
              (id,name,parent_id,level,status,review_status,ai4s_domain_id,ai4s_subdomain_id,source_id,created_on)
              VALUES(:id,:name,:parent_id,:level,:status,:review_status,:ai4s_domain_id,:ai4s_subdomain_id,:source_id,:created_on)""", after)
        except sqlite3.IntegrityError:
            raise HTTPException(409, "候选已审核") from None
        audit_id = _audit(conn, direction_id, body.decision + ":" + body.note, None, after)
        return {"audit_id": audit_id, "direction": after}


@router.post("/audits/{audit_id}/rollback")
def rollback_audit(audit_id: int):
    with _write() as conn:
        audit = conn.execute("SELECT * FROM impact_taxonomy_audit WHERE id=?", (audit_id,)).fetchone()
        if not audit:
            raise HTTPException(404, "审核记录不存在")
        if audit["reverted_on"]:
            raise HTTPException(409, "审核已回滚")
        after = json.loads(audit["after_json"])
        before = json.loads(audit["before_json"])
        current = conn.execute("SELECT * FROM impact_direction WHERE id=?", (audit["direction_id"],)).fetchone()
        if (dict(current) if current else None) != after:
            raise HTTPException(409, "方向之后又发生变化，不能按旧快照回滚")
        if before is None:
            conn.execute("DELETE FROM impact_direction WHERE id=?", (audit["direction_id"],))
        else:
            conn.execute("UPDATE impact_direction SET status=?,review_status=? WHERE id=?",
                         (before["status"], before["review_status"], audit["direction_id"]))
        conn.execute("UPDATE impact_taxonomy_audit SET reverted_on=? WHERE id=?", (store.business_today(), audit_id))
        return {"rolled_back": True, "audit_id": audit_id}


class EligibilityReview(BaseModel):
    decision: Literal["eligible", "excluded", "unreviewed"]
    note: str = Field(min_length=3, max_length=500)
    research_type: Literal["university", "public_research", "corporate_research"] | None = None
    evidence_url: HttpUrl | None = None
    mainland_confirmed: bool = False


@router.post("/entities/{entity_id}/eligibility")
def review_eligibility(entity_id: str, body: EligibilityReview):
    with _write() as conn:
        entity = conn.execute("SELECT kind,country FROM impact_entity WHERE id=?", (entity_id,)).fetchone()
        if not entity:
            raise HTTPException(404, "主体不存在")
        if body.decision == "eligible" and (entity["kind"] != "institution" or entity["country"] != "zn"
                                           or not body.mainland_confirmed
                                           or body.research_type is None or body.evidence_url is None
                                           or body.evidence_url.scheme != "https"):
            raise HTTPException(400, "正式榜仅收录有 HTTPS 一手依据的中国内地科研机构；须确认内地归属和机构类型")
        conn.execute("""UPDATE impact_entity SET eligibility=?,eligibility_note=?,research_type=?,eligibility_basis_url=?,mainland_confirmed=?
            WHERE id=?""", (body.decision, body.note, body.research_type or "unverified",
                            str(body.evidence_url) if body.evidence_url else "", int(body.decision == "eligible" and body.mainland_confirmed), entity_id))
        if body.decision in ("eligible", "excluded"):
            conn.execute("""UPDATE impact_review_case SET status='resolved',resolution=?,resolved_on=?
                WHERE kind='eligibility' AND status='pending' AND subject_ids=?""",
                (body.decision + ":" + body.note, store.business_today(), json.dumps([entity_id])))
        return {"entity_id": entity_id, "eligibility": body.decision}


class FollowUpdate(BaseModel):
    followed: bool


@router.post("/entities/{entity_id}/follow")
def set_follow(entity_id: str, body: FollowUpdate):
    with _write() as conn:
        if not conn.execute("SELECT 1 FROM impact_entity WHERE id=?", (entity_id,)).fetchone():
            raise HTTPException(404, "主体不存在")
        status = "followed" if body.followed else "unfollowed"
        conn.execute("UPDATE impact_entity SET follow_status=? WHERE id=?", (status, entity_id))
        return {"entity_id": entity_id, "follow_status": status}


class IdentityReview(BaseModel):
    decision: Literal["false_positive", "confirmed_duplicate"]
    note: str = Field(min_length=3, max_length=500)
    canonical_id: str | None = None
    evidence_url: HttpUrl | None = None


@router.post("/reviews/{case_id}/identity")
def review_identity(case_id: str, body: IdentityReview):
    """Record identity decision without silently merging scores or evidence."""
    with _write() as conn:
        case = conn.execute("SELECT * FROM impact_review_case WHERE id=? AND kind='possible_duplicate'", (case_id,)).fetchone()
        if not case:
            raise HTTPException(404, "身份冲突记录不存在")
        if case["status"] not in ("pending", "confirmed_unresolved"):
            raise HTTPException(409, "该记录已完成审核")
        status = "dismissed" if body.decision == "false_positive" else "resolved"
        if body.decision == "confirmed_duplicate":
            subjects = json.loads(case["subject_ids"])
            if (body.canonical_id not in subjects or body.evidence_url is None
                    or body.evidence_url.scheme != "https" or len(subjects) != 2):
                raise HTTPException(400, "合并显示须指定规范主体和 HTTPS 官方身份依据")
            duplicate_id = next(value for value in subjects if value != body.canonical_id)
            conn.execute("""INSERT INTO impact_identity_link
                (source_entity_id,canonical_entity_id,evidence_url,reviewed_on) VALUES(?,?,?,?)""",
                (duplicate_id, body.canonical_id, str(body.evidence_url), store.business_today()))
            conn.execute("""INSERT INTO impact_identity_audit
                (source_entity_id,canonical_entity_id,action,evidence_url,note,reviewed_on) VALUES(?,?,?,?,?,?)""",
                (duplicate_id, body.canonical_id, "linked", str(body.evidence_url), body.note, store.business_today()))
        conn.execute("UPDATE impact_review_case SET status=?,resolution=?,resolved_on=? WHERE id=?",
                     (status, body.decision + ":" + body.note, store.business_today(), case_id))
        return {"case_id": case_id, "status": status, "scores_merged": False,
                "history_recheck_required": body.decision == "confirmed_duplicate"}


@router.post("/identity-links/{source_entity_id}/rollback")
def rollback_identity_link(source_entity_id: str):
    """Separate records again; source event and score rows have never been moved."""
    with _write() as conn:
        link = conn.execute("SELECT * FROM impact_identity_link WHERE source_entity_id=?", (source_entity_id,)).fetchone()
        if not link:
            raise HTTPException(404, "身份归一记录不存在")
        conn.execute("DELETE FROM impact_identity_link WHERE source_entity_id=?", (source_entity_id,))
        conn.execute("""INSERT INTO impact_identity_audit
            (source_entity_id,canonical_entity_id,action,evidence_url,note,reviewed_on) VALUES(?,?,?,?,?,?)""",
            (source_entity_id, link["canonical_entity_id"], "rolled_back", link["evidence_url"],
             "恢复两个来源主体分别展示；原始事件和历史评分保持不变", store.business_today()))
        cases = store._rows(conn, """SELECT id,subject_ids FROM impact_review_case
            WHERE kind='possible_duplicate' AND status='resolved'""")
        for case in cases:
            if {source_entity_id, link["canonical_entity_id"]}.issubset(set(json.loads(case["subject_ids"]))):
                conn.execute("UPDATE impact_review_case SET status='pending',resolved_on=NULL WHERE id=?", (case["id"],))
        return {"rolled_back": True, "source_entity_id": source_entity_id,
                "source_scores_preserved": True}


class ExistingLabelResolution(BaseModel):
    label: str = Field(min_length=2, max_length=80)
    direction_id: str
    note: str = Field(min_length=3, max_length=500)


@router.post("/unclassified/resolve-existing")
def resolve_existing_label(body: ExistingLabelResolution):
    """Map an exact collision to an existing L3, preserving every prior event row for rollback."""
    with _write() as conn:
        direction = conn.execute("SELECT * FROM impact_direction WHERE id=?", (body.direction_id,)).fetchone()
        if not direction or direction["level"] != 3 or direction["name"] != body.label:
            raise HTTPException(400, "仅可归一到同名的已有 L3 节点")
        before = store._rows(conn, """SELECT id,direction_id,l3_label,pending_label FROM impact_event
            WHERE direction_id IS NULL AND pending_label=? ORDER BY id""", (body.label,))
        if not before:
            raise HTTPException(409, "待归类标签已变化")
        conn.executemany("UPDATE impact_event SET direction_id=?,l3_label=?,pending_label=NULL WHERE id=?",
                         [(direction["parent_id"], direction["name"], event["id"]) for event in before])
        audit_id = conn.execute("""INSERT INTO impact_event_mapping_audit
            (label,target_direction_id,before_json,note,reviewed_on) VALUES(?,?,?,?,?)""",
            (body.label, body.direction_id, json.dumps(before, ensure_ascii=False), body.note,
             store.business_today())).lastrowid
        return {"audit_id": audit_id, "mapped_events": len(before), "new_node": False}


@router.post("/event-mappings/{audit_id}/rollback")
def rollback_event_mapping(audit_id: int):
    with _write() as conn:
        audit = conn.execute("SELECT * FROM impact_event_mapping_audit WHERE id=?", (audit_id,)).fetchone()
        if not audit:
            raise HTTPException(404, "归一记录不存在")
        if audit["reverted_on"]:
            raise HTTPException(409, "已回滚")
        target = conn.execute("SELECT parent_id,name FROM impact_direction WHERE id=?", (audit["target_direction_id"],)).fetchone()
        before = json.loads(audit["before_json"])
        if not target or any(not (current := conn.execute(
                "SELECT direction_id,l3_label,pending_label FROM impact_event WHERE id=?", (item["id"],)).fetchone())
                or current["direction_id"] != target["parent_id"] or current["l3_label"] != target["name"]
                or current["pending_label"] is not None for item in before):
            raise HTTPException(409, "归一后的事件发生变化，不能按旧快照回滚")
        conn.executemany("UPDATE impact_event SET direction_id=?,l3_label=?,pending_label=? WHERE id=?",
                         [(item["direction_id"], item["l3_label"], item["pending_label"], item["id"]) for item in before])
        conn.execute("UPDATE impact_event_mapping_audit SET reverted_on=? WHERE id=?", (store.business_today(), audit_id))
        return {"rolled_back": True, "events": len(before)}


class IngestEvent(BaseModel):
    external_id: str = Field(min_length=1, max_length=180)
    title: str = Field(min_length=3, max_length=500)
    summary: str = Field(default="", max_length=3000)
    event_date: date
    entity_id: str
    direction_id: str
    l3_label: str | None = Field(default=None, max_length=80)
    source_url: HttpUrl
    source_content_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    source_excerpt: str = Field(min_length=10, max_length=3000)
    source_title: str = Field(default="", max_length=500)
    publisher: str = Field(default="", max_length=180)


class IngestBatch(BaseModel):
    batch_id: str = Field(min_length=1, max_length=180)
    events: list[IngestEvent] = Field(min_length=1, max_length=50)


@router.post("/ingest")
def ingest(body: IngestBatch):
    """Explicit zero-model ingestion of already reviewed facts; no scan is triggered."""
    if os.getenv("AI4S_IMPACT_MANUAL_INGEST_ENABLED", "0") != "1":
        raise HTTPException(403, "人工事件入库尚未启用")
    with _write() as conn:
        seen = conn.execute("SELECT * FROM impact_ingest_batch WHERE id=?", (body.batch_id,)).fetchone()
        if seen:
            return {"batch_id": body.batch_id, "created": 0, "duplicate_batch": True}
        created = 0
        revised = 0
        for item in body.events:
            ent = conn.execute("SELECT 1 FROM impact_entity WHERE id=? AND eligibility='eligible'", (item.entity_id,)).fetchone()
            direction = conn.execute("SELECT * FROM impact_direction WHERE id=?", (item.direction_id,)).fetchone()
            if not ent or not direction or direction["level"] != 2 or direction["status"] != "formal" or direction["review_status"] != "approved":
                raise HTTPException(400, "主体资格或 L2 映射尚未审核")
            if conn.execute("""SELECT 1 FROM impact_review_case WHERE status IN ('pending','confirmed_unresolved')
                    AND subject_ids LIKE ?""", (f'%"{item.entity_id}"%',)).fetchone():
                raise HTTPException(400, "主体仍有身份或资格冲突待审核")
            event_id = store.stable_id("manual", item.external_id)
            canonical_url = store.canonical_source_url(str(item.source_url))
            content_hash = item.source_content_sha256.lower()
            fingerprint = store.source_version_fingerprint(item.entity_id, canonical_url, content_hash)
            prior = conn.execute("""SELECT e.id FROM impact_event e JOIN impact_event_source s ON s.event_id=e.id
                WHERE e.entity_id=? AND e.origin='manual_reviewed' AND s.canonical_url=?
                ORDER BY e.rowid DESC LIMIT 1""", (item.entity_id, canonical_url)).fetchone()
            try:
                conn.execute("""INSERT INTO impact_event
                  (id,entity_id,direction_id,title,summary,event_date,imported_on,origin,is_flagship,l3_label,fingerprint,supersedes_event_id)
                  VALUES(?,?,?,?,?,?,?,?,0,?,?,?)""",
                  (event_id, item.entity_id, item.direction_id, item.title, item.summary,
                   item.event_date.isoformat(), store.business_today(), "manual_reviewed",
                   store.normalize_l3_label(conn, item.l3_label), fingerprint, prior["id"] if prior else None))
            except sqlite3.IntegrityError:
                continue
            conn.execute("""INSERT INTO impact_event_source
                (id,event_id,title,url,publisher,source_date,canonical_url,content_sha256,excerpt)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (store.stable_id("manualsrc", item.external_id), event_id, item.source_title,
                 str(item.source_url), item.publisher, item.event_date.isoformat(),
                 canonical_url, content_hash, item.source_excerpt))
            conn.execute("INSERT OR IGNORE INTO impact_entity_direction VALUES(?,?)", (item.entity_id, item.direction_id))
            created += 1
            if prior:
                revised += 1
        conn.execute("INSERT INTO impact_ingest_batch VALUES(?,?,?,?,?,?)",
                     (body.batch_id, store.business_today(), "manual_reviewed", created, created, store.business_today()))
        return {"batch_id": body.batch_id, "created": created, "revised": revised,
                "duplicate_batch": False, "paid_calls": 0, "scored": 0}
