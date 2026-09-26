"""Local, evidence-backed task recommendations for the strategic map.

Institution impact scores and team quality scores are deliberately separate from
the task match. A task submission only reads saved evidence; the optional
expansion delegates to the existing explicit graph scan endpoint.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from . import impact_store, strategic_map
from .team_research_store import history

router = APIRouter(prefix="/strategic-map", tags=["strategic_recommendations"])
MATCH_VERSION = "task-evidence-v2"
ELIGIBILITY_VERSION = os.environ.get("AI4S_TASK_ELIGIBILITY_VERSION", "verified-outcomes-v2")
_OUTCOME = re.compile(r"论文|成果|发表|项目|专利|模型|开源|系统|装置|平台|实验|Nature|Science|CVPR|ICLR", re.I)
_CJK = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[a-z0-9]{2,}")
_STOP = {"研究", "团队", "国内", "推荐", "方向", "领域", "相关", "需要", "寻找", "开展", "适合", "具备", "技术", "机构", "科研", "能力", "我们", "一个", "请找", "帮我", "选择", "可以", "进行", "帮助", "希望", "支持", "能够", "以及"}
# Controlled lexical equivalents make English official publications searchable
# from Chinese tasks. They do not confer capabilities or create evidence.
_CONCEPT_EQUIVALENTS = (
    ("quantum key distribution", "量子密钥分发"),
    ("quantum communication", "量子通信"), ("量子通讯", "量子通信"),
    ("quantum computation", "量子计算"), ("quantum computing", "量子计算"),
    ("quantum simulation", "量子模拟"), ("quantum sensing", "量子传感"),
    ("quantum metrology", "量子精密测量"), ("precision measurement", "精密测量"),
    ("quantum memory", "量子存储"), ("quantum teleportation", "量子远程传态"),
    ("entanglement", "纠缠"), ("earth system", "地球系统"),
    ("climate prediction", "气候预测"), ("预报", "预测"),
)
_CACHE_LOCK = threading.RLock()
_EVIDENCE_CACHE: tuple[str, tuple[list[dict[str, Any]], list[tuple[str, ...]], str]] | None = None
_LEAD_CACHE: tuple[str, list[dict[str, Any]]] | None = None


class TaskRequest(BaseModel):
    taskText: str = Field(min_length=2, max_length=500)
    domainId: str | None = Field(default=None, max_length=64)
    subdomainId: str | None = Field(default=None, max_length=64)
    limit: int = Field(default=10, ge=1, le=20)


def _db(*, write: bool = False) -> sqlite3.Connection:
    path = strategic_map._DB_PATH
    uri = str(path) if write else f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=not write, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone() is not None


def _schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS strategic_team_evidence_claim (
      id TEXT PRIMARY KEY, team_id TEXT NOT NULL, source_run_id TEXT NOT NULL,
      kind TEXT NOT NULL, claim_text TEXT NOT NULL, quote TEXT NOT NULL,
      url TEXT NOT NULL, published_at TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS ix_strategic_claim_team ON strategic_team_evidence_claim(team_id);
    CREATE TABLE IF NOT EXISTS strategic_claim_index_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS strategic_task_recommendation_run (
      id TEXT PRIMARY KEY, task_text TEXT NOT NULL, domain_id TEXT,
      subdomain_id TEXT, requested_limit INTEGER NOT NULL, data_version TEXT NOT NULL,
      result_json TEXT NOT NULL, expanded_job_id TEXT NOT NULL DEFAULT '',
      expanded_job_type TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_strategic_task_run_date
      ON strategic_task_recommendation_run(created_at);
    CREATE TABLE IF NOT EXISTS strategic_intelligence_daily (
      day TEXT NOT NULL, revision INTEGER NOT NULL, input_hash TEXT NOT NULL,
      payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
      PRIMARY KEY(day,revision)
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS strategic_claim_fts USING fts5(
      claim_text, quote, content='strategic_team_evidence_claim',
      content_rowid='rowid', tokenize='trigram'
    );
    CREATE TRIGGER IF NOT EXISTS strategic_claim_ai AFTER INSERT ON strategic_team_evidence_claim BEGIN
      INSERT INTO strategic_claim_fts(rowid,claim_text,quote) VALUES(new.rowid,new.claim_text,new.quote);
    END;
    CREATE TRIGGER IF NOT EXISTS strategic_claim_ad AFTER DELETE ON strategic_team_evidence_claim BEGIN
      INSERT INTO strategic_claim_fts(strategic_claim_fts,rowid,claim_text,quote)
      VALUES('delete',old.rowid,old.claim_text,old.quote);
    END;
    """)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(strategic_task_recommendation_run)")}
    if "expanded_job_type" not in columns:
        conn.execute("ALTER TABLE strategic_task_recommendation_run ADD COLUMN expanded_job_type TEXT NOT NULL DEFAULT ''")


def _valid_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _facts(reviewed: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    values: list[tuple[str, dict[str, Any]]] = []
    for kind in ("advantage", "description"):
        value = reviewed.get(kind)
        if isinstance(value, dict):
            values.append((kind, value))
    for value in reviewed.get("research_directions") or []:
        if isinstance(value, dict):
            values.append(("direction", value))
    return values


def _candidate_signature() -> str:
    """Cheap invalidation key for team, research and person edits, including other processes."""
    with closing(_db()) as conn:
        values = [str(strategic_map._DB_PATH)]
        for table in ("strategic_map_team", "strategic_map_person", "strategic_map_research_run"):
            if not _has_table(conn, table):
                values.extend(("0", "None"))
                continue
            column = "created_at" if table == "strategic_map_research_run" else "updated_at"
            count, latest = conn.execute(f"SELECT COUNT(*),MAX({column}) FROM {table}").fetchone()
            values.extend((str(count), str(latest)))
        return "|".join(values)


def _catalogue_evidence() -> tuple[list[dict[str, Any]], list[tuple[str, ...]], str]:
    """All published identity-backed teams, independent of recommendation gates."""
    global _EVIDENCE_CACHE
    from .verified_team_catalogue import project, VERSION
    from .team_research_store import HISTORY
    from sqlalchemy import inspect, select
    signature = VERSION + "|" + _candidate_signature()
    with _CACHE_LOCK:
        if _EVIDENCE_CACHE and _EVIDENCE_CACHE[0] == signature:
            return _EVIDENCE_CACHE[1]
    candidates, claims, versions = [], [], []
    with strategic_map._SESSION_FACTORY() as session:
        domains = {d.id: d.name for d in session.query(strategic_map.StrategicDomainRow).filter_by(deleted=False).all()}
        teams = session.query(strategic_map.StrategicTeamRow).filter(
            strategic_map.StrategicTeamRow.deleted.is_(False),
            strategic_map.StrategicTeamRow.domain_id.in_(list(domains)),
        ).all()
        observations = {}
        if inspect(session.connection()).has_table(HISTORY.name):
            query = select(HISTORY).where(HISTORY.c.team_id.in_([team.id for team in teams]), HISTORY.c.status.in_([
                "verified", "official_directory", "official_directory_conflict",
                "conflict", "revoked", "duplicate", "out_of_scope",
            ])).order_by(HISTORY.c.created_at.desc(), HISTORY.c.id.desc())
            for row in session.execute(query).mappings():
                observations.setdefault(row["team_id"], []).append(row)
        for team in teams:
            payload = strategic_map._team_to_dict(team, session)
            projected = project(payload, observations.get(team.id, []))
            if projected is None:
                continue
            payload, run_claims = projected
            payload["domainName"] = domains.get(team.domain_id, "")
            payload["subdomainName"] = domains.get(team.subdomain_id, "")
            candidates.append(payload)
            claims.extend(run_claims)
            versions.append(f"{team.id}:{team.updated_at}:{team.score_version}:{payload['catalogueSourceRunId']}")
    version = hashlib.sha256((VERSION + "\n" + "\n".join(sorted(versions))).encode()).hexdigest()[:16]
    result = (candidates, list({row[0]: row for row in claims}.values()), version)
    with _CACHE_LOCK:
        _EVIDENCE_CACHE = (signature, result)
    return result


def _candidate_evidence() -> tuple[list[dict[str, Any]], list[tuple[str, ...]], str]:
    """Verified domestic units with outcomes; roster completeness is not a veto."""
    teams, claims, version = _catalogue_evidence()
    outcomes = {row[1] for row in claims if row[3] == "outcome"}
    candidates = [team for team in teams if team["id"] in outcomes and
                  (ELIGIBILITY_VERSION != "staffing-v1" or team.get("candidateQualified"))]
    ids = {team["id"] for team in candidates}
    return candidates, [row for row in claims if row[1] in ids], version + ":" + ELIGIBILITY_VERSION


def _sync_claims(conn: sqlite3.Connection, claims: list[tuple[str, ...]]) -> None:
    # Rebuild only on an explicit task submission. Deletions remove revoked
    # facts from both the claim table and its FTS index in one transaction.
    conn.execute("DELETE FROM strategic_team_evidence_claim")
    conn.executemany("""INSERT INTO strategic_team_evidence_claim
      (id,team_id,source_run_id,kind,claim_text,quote,url,published_at)
      VALUES(?,?,?,?,?,?,?,?)""", claims)


def _terms(value: str) -> set[str]:
    value = value.casefold()
    for phrase, equivalent in _CONCEPT_EQUIVALENTS:
        value = value.replace(phrase, equivalent)
    terms = set(_WORD.findall(value))
    for filler in sorted(_STOP, key=len, reverse=True):
        value = value.replace(filler, " ")
    value = re.sub(r"[的和与及或]|[，、；：]", " ", value)
    for block in _CJK.findall(value):
        terms.update(block[i:i + 2] for i in range(len(block) - 1))
        if len(block) <= 3:
            terms.add(block)
    return {term for term in terms if term not in _STOP}


def _score(task_terms: set[str], team: dict[str, Any], team_claims: list[tuple[str, ...]]) -> tuple[int, list[dict[str, str]]]:
    if not task_terms:
        return 0, []
    fields = " ".join([
        team.get("domainName") or "", team.get("subdomainName") or "",
        *(team.get("researchDirections") or []),
    ])
    direction_fit = len(task_terms & _terms(fields)) / len(task_terms)
    ranked: list[tuple[float, tuple[str, ...]]] = []
    for claim in team_claims:
        matched = len(task_terms & _terms(claim[4] + " " + claim[5])) / len(task_terms)
        ranked.append((matched, claim))
    ranked.sort(key=lambda entry: (-entry[0], entry[1][0]))
    evidence_fit = ranked[0][0] if ranked else 0.0
    outcomes = [(fit, claim) for fit, claim in ranked if claim[3] == "outcome" and fit >= 0.25]
    if evidence_fit < 0.5 or not outcomes:
        return 0, []
    outcome_coverage = min(1.0, sum(1 for relevance, claim in ranked if relevance > 0 and claim[3] == "outcome") / 2)
    score = round(100 * (0.60 * evidence_fit + 0.25 * direction_fit + 0.15 * outcome_coverage))
    # Every recommendation visibly includes a relevant achieved result.
    selected = [outcomes[0][1]] + [claim for fit, claim in ranked if fit > 0 and claim[0] != outcomes[0][1][0]]
    cited = [{"kind": claim[3], "text": claim[4], "quote": claim[5], "url": claim[6]}
             for claim in selected[:4]]
    return score, cited


def _institution_links() -> dict[str, dict[str, str]]:
    conn = impact_store.connect()
    if conn is None:
        return {}
    with closing(conn):
        rows = conn.execute("""SELECT l.team_id,e.id,e.name,e.eligibility
          FROM impact_team_link l JOIN impact_entity e ON e.id=l.entity_id
          WHERE e.kind='institution' AND e.country='zn'""").fetchall()
        return {r["team_id"]: {"id": r["id"], "name": r["name"], "eligibility": r["eligibility"]} for r in rows}


def _pending_leads(task_terms: set[str], domain_id: str | None,
                   subdomain_id: str | None, formal_ids: set[str]) -> list[dict[str, Any]]:
    """Useful search leads remain visibly separate until claim-level review."""
    global _LEAD_CACHE
    signature = _candidate_signature()
    with _CACHE_LOCK:
        cached = _LEAD_CACHE[1] if _LEAD_CACHE and _LEAD_CACHE[0] == signature else None
    if cached is None:
        cached = []
        with strategic_map._SESSION_FACTORY() as session:
            for team in session.query(strategic_map.StrategicTeamRow).filter_by(deleted=False).all():
                payload = strategic_map._team_to_dict(team, session)
                if payload.get("candidateQualified") and payload.get("isDomestic"):
                    cached.append(payload)
        with _CACHE_LOCK:
            _LEAD_CACHE = (signature, cached)
    leads: list[dict[str, Any]] = []
    for payload in cached:
        if payload["id"] in formal_ids or (domain_id and payload["domainId"] != domain_id):
            continue
        if subdomain_id and payload["subdomainId"] != subdomain_id:
            continue
        urls = [url for url in (payload.get("sourceUrls") or []) if _valid_url(url)]
        if not urls:
            continue
        context = " ".join([payload.get("teamName") or "", payload.get("focus") or "",
                            payload.get("description") or "", payload.get("evidenceSummary") or ""])
        relevance = len(task_terms & _terms(context)) / max(1, len(task_terms))
        if relevance < 0.5:
            continue
        leads.append({"teamId": payload["id"], "teamName": payload.get("teamName") or payload["name"],
                      "institutionName": payload.get("institutionName") or payload["name"],
                      "matchHint": round(relevance * 100), "sourceUrl": urls[0],
                      "reason": "现有档案匹配任务，但成果与团队的逐条原文归属尚待核验"})
    return sorted(leads, key=lambda item: (-item["matchHint"], item["teamName"]))[:8]


def _validate_scope(domain_id: str | None, subdomain_id: str | None) -> None:
    with strategic_map._SESSION_FACTORY() as session:
        if domain_id:
            strategic_map._get_root_domain(session, domain_id)
        if subdomain_id:
            child = strategic_map._get_domain(session, subdomain_id)
            if not domain_id or child.parent_id != domain_id:
                raise HTTPException(400, "子领域不属于所选领域")


def _load_run(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM strategic_task_recommendation_run WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "推荐任务不存在")
    result = json.loads(row["result_json"])
    # Retain historical snapshots on disk; the public projection omits leads.
    result["pendingLeads"] = []
    if row["expanded_job_id"]:
        try:
            if "expanded_job_type" in row.keys() and row["expanded_job_type"] == "domain_refresh":
                task = strategic_map.get_domain_refresh(row["expanded_job_id"])["data"]
                result["expansion"] = {"jobId": row["expanded_job_id"], "state": task["state"],
                                       "stage": task["message"], "progress": task.get("result", {}).get("progress")}
            else:
                from .strategic_graph import get_graph_scan
                result["expansion"] = get_graph_scan(row["expanded_job_id"])["data"]
        except HTTPException:
            result["expansion"] = {"jobId": row["expanded_job_id"], "status": "unavailable"}
    return result


@router.post("/task-recommendations")
def recommend(body: TaskRequest) -> dict[str, Any]:
    _validate_scope(body.domainId, body.subdomainId)
    task = body.taskText.strip()
    if len(task) < 2:
        raise HTTPException(422, "请输入领域或任务")
    teams, claims, data_version = _candidate_evidence()
    by_team: dict[str, list[tuple[str, ...]]] = {}
    for claim in claims:
        by_team.setdefault(claim[1], []).append(claim)
    links = _institution_links()
    terms = _terms(task)
    items = []
    eligible_count = 0
    for team in teams:
        if body.domainId and team["domainId"] != body.domainId:
            continue
        if body.subdomainId and team["subdomainId"] != body.subdomainId:
            continue
        eligible_count += 1
        score, citations = _score(terms, team, by_team.get(team["id"], []))
        if score <= 0 or not citations:
            continue
        link = links.get(team["id"])
        items.append({
            "teamId": team["id"], "teamName": team.get("teamName") or team["name"],
            "institutionId": link["id"] if link else None,
            "institutionName": team.get("institutionName") or team["name"],
            "institutionImpact": "待核验关联" if not link else "来源评分待校准",
            "domainId": team["domainId"], "subdomainId": team.get("subdomainId"),
            "taskMatchScore": score, "teamScore": team.get("scoreTotal"),
            "teamScoreVersion": team.get("scoreVersion"), "matchVersion": MATCH_VERSION,
            "capability": team.get("description") or team.get("focus") or "",
            "citations": citations,
            "unknowns": ["机构影响力尚未建立审核关联"] if not link else [],
            "nextStep": "核对所引成果与任务条件，并联系团队确认当前能力",
            "updatedAt": team.get("updatedAt") or "",
        })
    items.sort(key=lambda item: (-item["taskMatchScore"], -float(item["teamScore"] or 0), item["teamId"]))
    run_id = "recommend-" + uuid.uuid4().hex
    result = {"runId": run_id, "taskText": task, "domainId": body.domainId,
              "subdomainId": body.subdomainId, "requestedLimit": body.limit,
              "eligibleTeamCount": eligible_count, "matchedTeamCount": len(items),
              "shortfall": max(0, body.limit - len(items)), "items": items[:body.limit],
              "pendingLeads": [],
              "dataVersion": data_version, "matchVersion": MATCH_VERSION,
              "eligibilityVersion": ELIGIBILITY_VERSION,
              "createdAt": datetime.now(timezone.utc).isoformat(),
              "notice": "任务匹配分、团队总分和机构影响力分别计算；只推荐有团队级原文成果证据的国内团队"}
    with closing(_db(write=True)) as conn:
        _schema(conn)
        with conn:
            indexed = conn.execute("SELECT value FROM strategic_claim_index_meta WHERE key='data_version'").fetchone()
            if not indexed or indexed[0] != data_version:
                _sync_claims(conn, claims)
                conn.execute("INSERT OR REPLACE INTO strategic_claim_index_meta(key,value) VALUES('data_version',?)", (data_version,))
            conn.execute("""INSERT INTO strategic_task_recommendation_run
              (id,task_text,domain_id,subdomain_id,requested_limit,data_version,result_json,created_at)
              VALUES(?,?,?,?,?,?,?,?)""", (run_id, task, body.domainId, body.subdomainId,
              body.limit, data_version, json.dumps(result, ensure_ascii=False), result["createdAt"]))
    return result


@router.get("/task-recommendations/{run_id}")
def recommendation(run_id: str) -> dict[str, Any]:
    with closing(_db()) as conn:
        if not _has_table(conn, "strategic_task_recommendation_run"):
            raise HTTPException(404, "推荐任务不存在")
        return _load_run(conn, run_id)


@router.post("/task-recommendations/{run_id}/expand", status_code=202)
def expand_recommendation(run_id: str) -> dict[str, Any]:
    with closing(_db()) as conn:
        if not _has_table(conn, "strategic_task_recommendation_run"):
            raise HTTPException(404, "推荐任务不存在")
        row = conn.execute("SELECT * FROM strategic_task_recommendation_run WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "推荐任务不存在")
        if row["expanded_job_id"]:
            return _load_run(conn, run_id)
        task, domain_id, subdomain_id = row["task_text"], row["domain_id"], row["subdomain_id"]
    from .strategic_graph import GraphScanRequest, _remote_scan_available, start_graph_scan
    if _remote_scan_available():
        scan = start_graph_scan(GraphScanRequest(keyword=task[:120], scope="domestic",
                                      max_candidates=100, domain_id=domain_id,
                                      subdomain_id=subdomain_id))["data"]
        job_id, job_type = scan["jobId"], "graph_scan"
    elif domain_id:
        refresh = strategic_map.start_domain_refresh(domain_id, subdomain_id=subdomain_id)["data"]
        job_id, job_type = refresh["taskId"], "domain_refresh"
    else:
        raise HTTPException(503, "全网扫描服务尚未连接；请先选择一个领域再扩展调查")
    with closing(_db(write=True)) as conn, conn:
        conn.execute("UPDATE strategic_task_recommendation_run SET expanded_job_id=?,expanded_job_type=? WHERE id=? AND expanded_job_id=''",
                     (job_id, job_type, run_id))
    return recommendation(run_id)


@router.get("/intelligence/search")
def intelligence_search(q: str = Query(min_length=2, max_length=100),
                        domain_id: str | None = None, page: int = Query(1, ge=1),
                        size: int = Query(20, ge=1, le=50), verified_only: bool = True) -> dict[str, Any]:
    _validate_scope(domain_id, None)
    query = q.strip()
    if len(query) < 2:
        raise HTTPException(422, "请输入至少两个字的检索词")
    terms = sorted(_terms(query), key=lambda value: (-len(value), value))[:8] or [query]
    if verified_only:
        # Use the current reviewed evidence set, including on the first search
        # and after withdrawal; a stale persisted index must not republish facts.
        teams, claims, version = _catalogue_evidence()
        scoped = {team["id"]: team for team in teams
                  if not domain_id or team["domainId"] == domain_id}
        results = []
        for claim in claims:
            team = scoped.get(claim[1])
            if not team:
                continue
            title = f"{team.get('institutionName', '')} · {team.get('teamName', '')}"
            haystack = f"{title} {claim[4]} {claim[5]}".casefold()
            if query.casefold() not in haystack and not all(term in haystack for term in terms):
                continue
            results.append({"type": "team_claim", "id": claim[0], "teamId": claim[1],
                "title": title, "snippet": claim[5][:260], "url": claim[6],
                "domainId": team["domainId"], "verificationStatus": "verified"})
        results.sort(key=lambda row: (row["title"], row["id"]))
        return {"items": results[(page - 1) * size:page * size], "total": len(results),
                "page": page, "size": size, "query": query, "dataVersion": version}
    results: list[dict[str, Any]] = []
    with closing(_db()) as conn:
        profile_rows = conn.execute("""SELECT t.id,t.institution_name,t.team_name,t.domain_id,
            t.core_direction,t.focus,t.description,t.source_urls
            FROM strategic_map_team t WHERE t.deleted=0 AND
            (t.institution_name LIKE ? OR t.team_name LIKE ? OR t.core_direction LIKE ?
             OR t.focus LIKE ? OR t.description LIKE ? OR EXISTS (
                 SELECT 1 FROM strategic_map_person p WHERE p.team_id=t.id AND p.deleted=0 AND p.name LIKE ?))
            ORDER BY t.institution_name,t.team_name""", [f"%{query}%"] * 6).fetchall()
        for row in profile_rows:
            if domain_id and row["domain_id"] != domain_id:
                continue
            urls = json.loads(row["source_urls"] or "[]")
            results.append({"type": "team_profile", "id": row["id"], "teamId": row["id"],
                "title": f"{row['institution_name']} · {row['team_name']}",
                "snippet": (row["core_direction"] or row["focus"] or row["description"] or "团队档案，成果待逐条核验")[:260],
                "url": next((url for url in urls if _valid_url(url)), None),
                "domainId": row["domain_id"], "reviewNotice": "团队档案线索；入榜仍须逐条核验成果"})
        if _has_table(conn, "strategic_team_evidence_claim"):
            if len(query) >= 3 and _has_table(conn, "strategic_claim_fts"):
                clean = query.replace('"', ' ').strip()
                try:
                    rows = conn.execute("""SELECT c.*,t.institution_name,t.team_name,t.domain_id
                      FROM strategic_claim_fts f JOIN strategic_team_evidence_claim c ON c.rowid=f.rowid
                      JOIN strategic_map_team t ON t.id=c.team_id WHERE strategic_claim_fts MATCH ?
                      ORDER BY c.team_id,c.id""", (f'"{clean}"',)).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            else:
                rows = []
            if not rows:
                conditions = " AND ".join("(c.claim_text LIKE ? OR c.quote LIKE ? OR t.team_name LIKE ? OR t.institution_name LIKE ?)" for _ in terms)
                patterns = [f"%{term}%" for term in terms for _ in range(4)]
                rows = conn.execute(f"""SELECT c.*,t.institution_name,t.team_name,t.domain_id
                  FROM strategic_team_evidence_claim c JOIN strategic_map_team t ON t.id=c.team_id
                  WHERE {conditions} ORDER BY c.team_id,c.id""", patterns).fetchall()
            for row in rows:
                if domain_id and row["domain_id"] != domain_id:
                    continue
                snippet = row["quote"] if query.casefold() in (row["quote"] or "").casefold() else row["claim_text"]
                results.append({"type": "team_claim", "id": row["id"], "teamId": row["team_id"],
                                "title": f"{row['institution_name']} · {row['team_name']}",
                                "snippet": snippet[:260], "url": row["url"],
                                "domainId": row["domain_id"]})
    impact_conn = impact_store.connect()
    if impact_conn is not None:
        with closing(impact_conn):
            conditions = " AND ".join("(e.title LIKE ? OR e.summary LIKE ? OR s.title LIKE ?)" for _ in terms)
            patterns = [f"%{term}%" for term in terms for _ in range(3)]
            rows = impact_conn.execute(f"""SELECT e.id,e.title,e.summary,e.event_date,d.ai4s_domain_id,
              s.url FROM impact_event e LEFT JOIN impact_direction d ON d.id=e.direction_id
              LEFT JOIN impact_event_source s ON s.id=(SELECT MIN(x.id) FROM impact_event_source x WHERE x.event_id=e.id)
              WHERE {conditions} ORDER BY e.event_date DESC,e.id""", patterns).fetchall()
            for row in rows:
                if domain_id and row["ai4s_domain_id"] != domain_id:
                    continue
                results.append({"type": "institution_event", "id": row["id"], "title": row["title"],
                                "snippet": row["summary"][:260], "url": row["url"],
                                "domainId": row["ai4s_domain_id"], "date": row["event_date"]})
    total = len(results)
    return {"items": results[(page - 1) * size:page * size], "total": total,
            "page": page, "size": size, "query": query}


@router.get("/intelligence/verified-teams")
def verified_teams() -> dict[str, Any]:
    from .verified_team_catalogue import VERSION
    teams, claims, version = _catalogue_evidence()
    return {"teamIds": [team["id"] for team in teams], "teams": teams, "dataVersion": version,
            "claimCount": len(claims), "projectionVersion": VERSION}


@router.get("/intelligence/verified-daily/{day}")
def verified_daily(day: date) -> dict[str, Any]:
    source = intelligence_daily(day)
    teams, _, version = _catalogue_evidence()
    team_ids = {team["id"] for team in teams}
    allowed_events: set[str] = set()
    connection = impact_store.connect()
    if connection is not None:
        with closing(connection):
            allowed_events = {row[0] for row in connection.execute("""SELECT v.id FROM impact_event v
              JOIN impact_entity e ON e.id=v.entity_id
              WHERE v.origin='manual_reviewed' AND e.kind='institution' AND e.eligibility='eligible'
              AND e.mainland_confirmed=1 AND e.country='zn' AND e.eligibility_basis_url!=''
              AND NOT EXISTS (SELECT 1 FROM impact_event n WHERE n.supersedes_event_id=v.id)""")}
    events = [event for event in source.get("impact", {}).get("events", []) if event["id"] in allowed_events]
    changes = [change for change in source.get("teamChanges", []) if change["teamId"] in team_ids]
    recommendations = []
    for change in source.get("recommendationChanges", []):
        before = [item for item in change["before"] if item in team_ids]
        after = [item for item in change["after"] if item in team_ids]
        if before != after:
            recommendations.append({**change, "before": before, "after": after})
    return {"date": day.isoformat(), "revision": source.get("revision", 0),
            "frozen": source.get("frozen", False), "events": events,
            "teamChanges": changes, "recommendationChanges": recommendations,
            "dataVersion": version, "projectionVersion": "reviewed-public-v1"}


@router.get("/intelligence/verified-graph")
def verified_graph(domain_id: str | None = None, subdomain_id: str | None = None) -> dict[str, Any]:
    """Build only relationships and quotes supported by reviewed team records."""
    _validate_scope(domain_id, subdomain_id)
    from .strategic_graph import _node, _edge
    teams, claims, version = _catalogue_evidence()
    selected = {team["id"]: team for team in teams
                if (not domain_id or team["domainId"] == domain_id)
                and (not subdomain_id or team.get("subdomainId") == subdomain_id)}
    nodes, edges = {}, []
    for team_id, team in selected.items():
        institution = team.get("institutionName") or team.get("name") or ""
        institution_id = "institution:" + institution
        team_node_id = "team:" + team_id
        direction_id = "domain:" + team["domainId"]
        basis = (team.get("institutionEvidence") or [{}])[0]
        nodes[institution_id] = _node(institution_id, institution,
            {"name": institution, "level": "机构", "url": basis.get("url", ""), "description": basis.get("quote", "")})
        nodes[direction_id] = _node(direction_id, team.get("domainName", ""),
            {"name": team.get("domainName", ""), "level": "领域方向"})
        nodes[team_node_id] = _node(team_node_id, team.get("teamName", ""),
            {"name": team.get("teamName", ""), "level": "科研团队", "description": team.get("description", "")})
        edges.extend([_edge(team_node_id, institution_id, "所属机构"),
                      _edge(team_node_id, direction_id, "研究方向")])
    for claim in claims:
        if claim[1] not in selected:
            continue
        node_id = "evidence:" + claim[0]
        nodes[node_id] = _node(node_id, claim[4][:35],
            {"name": claim[4], "level": "证据", "description": claim[5], "url": claim[6]})
        edges.append(_edge(node_id, "team:" + claim[1], "证据支持"))
    return {"nodes": list(nodes.values()), "edges": edges, "provider": "AI4S 原文证据",
            "searchResults": [], "dataVersion": version,
            "meta": {"stats": {"Nodes": len(nodes), "Edges": len(edges)},
                     "features": {"scan": False, "chat": False}}}


def _daily_payload(day: str) -> dict[str, Any]:
    impact_conn = impact_store.connect()
    base = impact_store.daily_report(impact_conn, day) if impact_conn is not None else {"markdown": "", "events": [], "tree_changes": [], "identity_changes": [], "score_changes": []}
    impact_by_domain: dict[str, list[str]] = {}
    input_versions: dict[str, Any] = {"schema": "ai4s-daily-v1", "impactBatchIds": [row["id"] for row in base.get("batches", [])],
        "impactEventIds": [row["id"] for row in base.get("events", [])],
        "taxonomyAuditIds": [f"{'mapping' if 'label' in row else 'direction'}:{row['id']}" for row in base.get("tree_changes", [])],
        "scoreChangeHash": hashlib.sha256(json.dumps(base.get("score_changes", []),
            ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16],
        "teamResearchRunIds": [], "recommendationRunIds": []}
    if impact_conn is not None:
        direction_domain = {row["id"]: row["ai4s_domain_id"] for row in impact_conn.execute(
            "SELECT id,ai4s_domain_id FROM impact_direction WHERE ai4s_domain_id IS NOT NULL")}
        for event in base.get("events", []):
            mapped = direction_domain.get(event["direction_id"])
            if mapped:
                impact_by_domain.setdefault(mapped, []).append(event["title"])
        sources = impact_conn.execute("""SELECT s.event_id,s.id,s.url,s.title FROM impact_event_source s
            JOIN impact_event e ON e.id=s.event_id WHERE e.imported_on=? AND e.origin!='source_snapshot'
            ORDER BY s.event_id,s.id""", (day,)).fetchall()
        input_versions["impactSourceHash"] = hashlib.sha256(json.dumps([tuple(row) for row in sources],
            ensure_ascii=False).encode()).hexdigest()[:16]
        revisions = impact_conn.execute("""SELECT id,input_hash FROM impact_score_revision
            WHERE created_on=? ORDER BY id""", (day,)).fetchall() if _has_table(impact_conn, "impact_score_revision") else []
        input_versions["scoreRevisionInputs"] = [tuple(row) for row in revisions]
        impact_conn.close()
    team_changes: list[dict[str, Any]] = []
    recommendation_changes: list[dict[str, Any]] = []
    by_domain: dict[str, dict[str, Any]] = {}
    domain_names: dict[str, str] = {}
    with closing(_db()) as conn:
        domain_names = {row["id"]: row["name"] for row in conn.execute(
            "SELECT id,name FROM strategic_map_domain WHERE parent_id IS NULL AND deleted=0 ORDER BY sort_order,id")}
        if _has_table(conn, "strategic_map_research_run"):
            rows = conn.execute("""SELECT r.id,r.team_id,r.payload,t.team_name,t.institution_name,t.domain_id
              FROM strategic_map_research_run r JOIN strategic_map_team t ON t.id=r.team_id
              WHERE r.status='verified' AND substr(r.created_at,1,10)=?
              ORDER BY t.domain_id,r.team_id,r.created_at""", (day,)).fetchall()
            for row in rows:
                payload = json.loads(row["payload"])
                if not payload.get("published"):
                    continue
                input_versions["teamResearchRunIds"].append(row["id"])
                team_changes.append({"teamId": row["team_id"], "teamName": row["team_name"],
                                     "institutionName": row["institution_name"], "domainId": row["domain_id"],
                                     "reason": "团队资料经独立审核更新", "scoreBefore": (payload.get("before") or {}).get("scoreTotal"),
                                     "scoreAfter": payload.get("after_score"),
                                     "scoreVersion": payload.get("after_score_version")})
        if _has_table(conn, "strategic_task_recommendation_run"):
            rows = conn.execute("""SELECT id,task_text,domain_id,result_json,created_at
              FROM strategic_task_recommendation_run WHERE substr(created_at,1,10)<=?
              ORDER BY task_text,domain_id,created_at,id""", (day,)).fetchall()
            previous: dict[tuple[str, str], dict[str, Any]] = {}
            for row in rows:
                key = (row["task_text"], row["domain_id"] or "")
                current = json.loads(row["result_json"])
                if row["created_at"][:10] == day and key in previous:
                    old_ids = [v["teamId"] for v in previous[key]["items"]]
                    new_ids = [v["teamId"] for v in current["items"]]
                    if old_ids != new_ids:
                        input_versions["recommendationRunIds"].append(row["id"])
                        recommendation_changes.append({"taskText": row["task_text"],
                          "domainId": row["domain_id"], "before": old_ids, "after": new_ids,
                          "reason": "已核证据或任务匹配结果发生变化"})
                previous[key] = current
        if _has_table(conn, "strategic_claim_index_meta"):
            indexed = conn.execute("SELECT value FROM strategic_claim_index_meta WHERE key='data_version'").fetchone()
            input_versions["teamEvidenceVersion"] = indexed[0] if indexed else None
    for item in team_changes:
        slot = by_domain.setdefault(item["domainId"], {"teamUpdates": 0, "recommendationChanges": 0, "followUps": []})
        slot["teamUpdates"] += 1
        slot["followUps"].append(f"核对{item['teamName']}的新增成果及人员归属")
    for item in recommendation_changes:
        if item["domainId"]:
            by_domain.setdefault(item["domainId"], {"teamUpdates": 0, "recommendationChanges": 0, "followUps": []})["recommendationChanges"] += 1
    domain_summaries = {}
    for domain_id, name in domain_names.items():
        slot = by_domain.get(domain_id, {"teamUpdates": 0, "recommendationChanges": 0, "followUps": []})
        titles = impact_by_domain.get(domain_id, [])
        domain_summaries[domain_id] = {"domainName": name, "institutionEvents": len(titles),
            "teamUpdates": slot["teamUpdates"], "recommendationChanges": slot["recommendationChanges"],
            "summary": f"新增机构事件 {len(titles)} 条，已核团队资料更新 {slot['teamUpdates']} 次，任务推荐名单变化 {slot['recommendationChanges']} 次。"
                + (" 重点事件：" + "；".join(titles[:2]) + "。" if titles else "")}
    return {"date": day, "impact": base, "teamChanges": team_changes,
            "recommendationChanges": recommendation_changes, "domains": by_domain,
            "domainSummaries": domain_summaries, "inputVersions": input_versions}


@router.get("/intelligence/daily/{day}")
def intelligence_daily(day: date) -> dict[str, Any]:
    key = day.isoformat()
    with closing(_db()) as conn:
        if _has_table(conn, "strategic_intelligence_daily"):
            row = conn.execute("""SELECT revision,payload_json,created_at FROM strategic_intelligence_daily
              WHERE day=? ORDER BY revision DESC LIMIT 1""", (key,)).fetchone()
            if row:
                return {**json.loads(row["payload_json"]), "revision": row["revision"],
                        "frozen": True, "frozenAt": row["created_at"]}
    return {**_daily_payload(key), "revision": 0, "frozen": False}


@router.post("/intelligence/daily/{day}/freeze")
def freeze_intelligence_daily(day: date) -> dict[str, Any]:
    key = day.isoformat()
    payload = _daily_payload(key)
    wire = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(wire.encode()).hexdigest()
    with closing(_db(write=True)) as conn:
        _schema(conn)
        with conn:
            previous = conn.execute("""SELECT revision,input_hash FROM strategic_intelligence_daily
              WHERE day=? ORDER BY revision DESC LIMIT 1""", (key,)).fetchone()
            if previous and previous["input_hash"] == digest:
                revision = previous["revision"]
            else:
                revision = (previous["revision"] + 1) if previous else 1
                conn.execute("""INSERT INTO strategic_intelligence_daily
                  (day,revision,input_hash,payload_json,created_at) VALUES(?,?,?,?,?)""",
                  (key, revision, digest, wire, datetime.now(timezone.utc).isoformat()))
    return {**payload, "revision": revision, "frozen": True}
