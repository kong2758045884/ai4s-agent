"""Explicit web investigations. Collection and independent publication are separate.

No timers call this module's start(). Restarted work is marked interrupted and
requires an explicit retry, so a process restart never starts paid work.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone

from fastapi import HTTPException

_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="team-web")
_LOCK = threading.Lock()
_ACTIVE: set[str] = set()
STAGES = ["检索来源", "抓取原文", "核验归属", "发布证据", "更新推荐"]


def now():
    return datetime.now(timezone.utc).isoformat()


def init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS strategic_web_investigation (
      id TEXT PRIMARY KEY, run_id TEXT NOT NULL, state TEXT NOT NULL, stage TEXT NOT NULL,
      detail_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      error TEXT NOT NULL DEFAULT '', attempt INTEGER NOT NULL DEFAULT 1)""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_web_active ON strategic_web_investigation(run_id) WHERE state IN ('queued','running')")
    conn.execute("""CREATE TABLE IF NOT EXISTS strategic_web_observation (
      job_id TEXT NOT NULL, team_id TEXT NOT NULL, state TEXT NOT NULL, result_json TEXT NOT NULL,
      updated_at TEXT NOT NULL, PRIMARY KEY(job_id,team_id))""")


def get(job_id):
    from .task_recommendations import _db, _has_table
    with closing(_db()) as conn:
        row = conn.execute("SELECT * FROM strategic_web_investigation WHERE id=?", (job_id,)).fetchone() if _has_table(conn, "strategic_web_investigation") else None
    if row is None:
        raise HTTPException(404, "联网调查不存在")
    return {"jobId": row["id"], "state": row["state"], "stage": row["stage"], "stages": STAGES,
            "error": row["error"], "attempt": row["attempt"], "updatedAt": row["updated_at"], **json.loads(row["detail_json"])}


def _save(job_id, *, state="running", stage, error="", **detail):
    from .task_recommendations import _db
    with closing(_db(write=True)) as conn, conn:
        row = conn.execute("SELECT detail_json FROM strategic_web_investigation WHERE id=?", (job_id,)).fetchone()
        payload = {**json.loads(row[0]), **detail}
        conn.execute("UPDATE strategic_web_investigation SET state=?,stage=?,detail_json=?,error=?,updated_at=? WHERE id=?",
                     (state, stage, json.dumps(payload, ensure_ascii=False), error, now(), job_id))


def start(run_id, *, retry=False):
    from . import task_recommendations as tasks, strategic_map as sm
    with closing(tasks._db(write=True)) as conn:
        init(conn)
        conn.commit()
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = tasks._load_run(conn, run_id)
            running = conn.execute("SELECT id FROM strategic_web_investigation WHERE run_id=? AND state IN ('queued','running')", (run_id,)).fetchone()
            if running:
                return running[0]
            old = conn.execute("SELECT * FROM strategic_web_investigation WHERE run_id=? ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchone()
            if old and (not retry or old["state"] not in {"failed", "interrupted", "partial"}):
                return old["id"]
            if not sm._llm_config():
                raise HTTPException(503, "联网调查的模型配置未完成；本地结果仍可使用")
            job_id = "web-" + uuid.uuid4().hex
            detail = {"progress": {"done": 0, "total": 0}, "publishedTeams": [], "failures": [],
                      "calls": {"search": 0, "fetch": 0, "llm": 0}, "costCny": None,
                      "costNotice": "费用未知；提供方未返回完整费用", "dailyBudgetCny": None,
                      "concurrencyLimit": 4, "retryLimit": 3, "previousJobId": old["id"] if old else None}
            if old and retry:
                old_detail = json.loads(old['detail_json'])
                detail['publishedTeams'] = old_detail.get('publishedTeams', [])
                detail['batchTeamIds'] = old_detail.get('batchTeamIds', [])
                detail['previousCalls'] = old_detail.get('calls', {})
            conn.execute("INSERT INTO strategic_web_investigation VALUES(?,?,?,?,?,?,?,?,?)",
                (job_id, run_id, "queued", STAGES[0], json.dumps(detail, ensure_ascii=False), now(), now(), "", old["attempt"] + 1 if old else 1))
            if old and retry:
                conn.execute("""INSERT INTO strategic_web_observation
                    SELECT ?,team_id,state,result_json,updated_at FROM strategic_web_observation
                    WHERE job_id=? AND state='published'""", (job_id, old['id']))
            conn.execute("UPDATE strategic_task_recommendation_run SET expanded_job_id=?,expanded_job_type='web_investigation' WHERE id=?", (job_id, run_id))
    with _LOCK:
        _ACTIVE.add(job_id)
    _POOL.submit(_execute, job_id, previous)
    return job_id


def _search(query, on_attempt=None):
    from .team_research import search_links
    for attempt in range(4):
        try:
            if on_attempt:
                on_attempt()
            return asyncio.run(search_links(query))
        except Exception as exc:
            code = getattr(exc, "status_code", None)
            if attempt == 3 or not (code == 429 or isinstance(code, int) and 500 <= code < 600):
                raise
            time.sleep(min(2 ** attempt, 8))


def _research_one(job_id, team, task):
    from . import strategic_map as sm, team_research as research, domain_research
    from .team_research_store import append_history, apply_reviewed_run
    from .task_recommendations import _db
    with sm._SESSION_FACTORY() as session:
        row = session.get(sm.StrategicTeamRow, team["id"])
        existing = sm._research_existing(session, row)
    domain = team["domainName"]
    query = f"{team['institutionName']} {team['teamName']} {' '.join(task['parsedTask']['goals'])} 论文 科研成果"
    _save(job_id, stage=STAGES[0], currentTeam=team["teamName"])
    # Persist attempts before calling the provider, including failed attempts.
    def search_attempt():
        state = get(job_id)
        _save(job_id, stage=STAGES[0], searchAttempts=state.get('searchAttempts', 0) + 1)
    hits = _search(query, on_attempt=search_attempt)
    _save(job_id, stage=STAGES[1])
    context = research.public_context(existing)
    context["known_sources"] = [{"url": u, "label": "已登记团队来源"} for u in team.get("sourceUrls", [])[:3]] + hits[:4]
    context["task_requirements"] = task["parsedTask"]
    def checkpoint(value):
        with closing(_db(write=True)) as conn, conn:
            conn.execute("INSERT OR REPLACE INTO strategic_web_observation VALUES(?,?,?,?,?)",
                (job_id, team['id'], 'collecting', json.dumps(value, ensure_ascii=False), now()))
    collected = research.Research(sm._shared_agent_llm_text, checkpoint=checkpoint).run(context, domain)
    # Preserve source bodies even when collection/review fails; never publish collected fields.
    with closing(_db(write=True)) as conn, conn:
        conn.execute("INSERT OR REPLACE INTO strategic_web_observation VALUES(?,?,?,?,?)",
            (job_id, team["id"], "collected", json.dumps(collected, ensure_ascii=False), now()))
    _save(job_id, stage=STAGES[2])
    blocked = lambda value: any(e.get('kind') == 'provider_unavailable' for e in value.get('errors', []) if isinstance(e, dict))
    if blocked(collected):
        reviewed = {'status': 'provider_unavailable', 'errors': [], 'counts': {}}
    else:
        reviewed = research.review_cached_result(collected, existing, domain, sm._shared_agent_llm_text)
        if not blocked(reviewed):
            reviewed = domain_research.review_qualification(reviewed, domain, sm._shared_agent_llm_text, existing=existing)
    published = False
    _save(job_id, stage=STAGES[3])
    with sm._SESSION_FACTORY() as session, session.begin():
        if domain_research.qualified(reviewed):
            published = apply_reviewed_run(session, team["id"], reviewed, allowed_domains={domain})
        else:
            append_history(session, team["id"], "investigation_unpublished", {"run": reviewed, "published": False, "jobId": job_id})
    counts = {"search": 1, "fetch": 0, "llm": 0, "llm_total_tokens": 0, "llm_retries": 0, 'llm_usage_unknown': 0}
    known_cost = 0.0
    for part in (collected, reviewed, reviewed.get("qualification_review") or {}):
        for key in counts:
            counts[key] += (part.get("counts") or {}).get(key, 0)
        known_cost += (part.get('counts') or {}).get('llm_estimated_cost_cny') or 0
    with closing(_db(write=True)) as conn, conn:
        conn.execute("UPDATE strategic_web_observation SET state=?,result_json=?,updated_at=? WHERE job_id=? AND team_id=?",
            ("published" if published else "unpublished", json.dumps({"collection": collected, "review": reviewed}, ensure_ascii=False), now(), job_id, team["id"]))
    provider_failed = any(error.get('kind') == 'provider_unavailable' or error.get('kind') == 'ProviderUnavailable'
                          for part in (collected, reviewed, reviewed.get('qualification_review') or {})
                          for error in part.get('errors', []) if isinstance(error, dict))
    return {"published": bool(published), "calls": counts, 'knownCostCny': known_cost,
            "providerFailed": provider_failed,
            "reason": "原文复核及成果归属未满足发布条件" if not published else ""}


def _execute(job_id, task):
    from . import task_recommendations as tasks, strategic_text
    started = time.monotonic()
    try:
        task.setdefault("parsedTask", strategic_text.parse_task(task["taskText"]))
        teams = [t for t in tasks.verified_teams()["teams"] if (not task.get("domainId") or t["domainId"] == task["domainId"])
                 and (not task.get("subdomainId") or t.get("subdomainId") == task["subdomainId"])]
        # Deterministic bounded batch; results never imply that the whole web has been searched.
        terms = task["parsedTask"]["goals"]
        teams.sort(key=lambda t: (-sum(term in strategic_text.normalize(json.dumps(t, ensure_ascii=False)) for term in terms), t["id"]))
        prior = get(job_id)
        batch_ids = prior.get('batchTeamIds')
        batch = [t for t in teams if t['id'] in batch_ids] if batch_ids else teams[:12]
        _save(job_id, stage=STAGES[0], progress={"done": 0, "total": len(batch)},
              batchTeamIds=[t['id'] for t in batch],
              scopeNotice=f"本批补充 {len(batch)} 个已有科研单元；未覆盖 {max(0,len(teams)-len(batch))} 个单元")
        if not batch:
            raise ValueError("当前范围没有可核对的具体团队，请先导入官方团队目录")
        published, failures, calls = list(prior.get('publishedTeams', [])), [], {"search": 0, "fetch": 0, "llm": 0, "llm_total_tokens": 0, "llm_retries": 0}
        known_cost = 0.0
        consecutive = 0
        for index, team in enumerate(batch):
            try:
                if team['id'] in published:
                    continue
                result = _research_one(job_id, team, task)
                if result["published"]:
                    published.append(team["id"])
                else:
                    failures.append({"teamId": team["id"], "reason": result["reason"]})
                for key, value in result["calls"].items():
                    calls[key] = calls.get(key, 0) + value
                known_cost += result.get('knownCostCny', 0)
                if result.get('providerFailed'):
                    raise RuntimeError("提供方暂不可用，已停止后续调用")
                consecutive = 0
            except Exception as exc:
                consecutive += 1
                failures.append({"teamId": team["id"], "reason": type(exc).__name__})
                if isinstance(exc, RuntimeError) or type(exc).__name__ in {"ProviderUnavailable", "AuthenticationError", "PermissionDeniedError"} or consecutive >= 3:
                    raise RuntimeError("提供方异常，调查已停止；保留已发布结果，可检查配置后重试") from exc
            finally:
                calls['search'] = max(calls['search'], get(job_id).get('searchAttempts', 0))
                _save(job_id, stage=STAGES[3], publishedTeams=published, failures=failures, calls=calls,
                      knownCostCny=round(known_cost, 6),
                      progress={"done": index + 1, "total": len(batch)}, seconds=round(time.monotonic()-started, 2))
        _save(job_id, stage=STAGES[4])
        result = tasks._recommend(tasks.TaskRequest(taskText=task["taskText"], domainId=task.get("domainId"),
            subdomainId=task.get("subdomainId"), limit=task["requestedLimit"]), parent_run_id=task["runId"])
        _save(job_id, state="partial" if failures else "completed", stage="调查完成", updatedRunId=result["runId"],
              seconds=round(time.monotonic()-started, 2))
    except Exception as exc:
        safe_error = str(exc) if type(exc) in {ValueError, RuntimeError} and not str(exc).isascii() else type(exc).__name__
        _save(job_id, state="failed", stage="调查未完成", error=safe_error, seconds=round(time.monotonic()-started, 2))
    finally:
        with _LOCK:
            _ACTIVE.discard(job_id)


def recover_interrupted():
    from .task_recommendations import _db
    with closing(_db(write=True)) as conn, conn:
        init(conn)
        conn.execute("UPDATE strategic_web_investigation SET state='interrupted',error='服务重启导致中断，请点击重试；已有推荐及已发布证据保留',updated_at=? WHERE state IN ('queued','running')", (now(),))
