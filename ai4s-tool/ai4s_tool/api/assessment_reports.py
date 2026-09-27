"""Private daily snapshots; weekly/monthly views aggregate exact saved daily IDs."""
from __future__ import annotations

import json
import uuid
from contextlib import closing
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from . import strategic_assessments as store, assessment_updates, strategic_daily, task_recommendations as tasks
from .strategic_identity import current_visitor, problem
from .strategic_interpretation import digest

router = APIRouter(prefix="/strategic-map", tags=["assessment_reports"])


def init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS strategic_assessment_report (
      id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, kind TEXT NOT NULL, domain_id TEXT NOT NULL,
      start_day TEXT NOT NULL, end_day TEXT NOT NULL, revision INTEGER NOT NULL,
      input_hash TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
      UNIQUE(owner_id,kind,domain_id,start_day,end_day,revision))""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_private_reports ON strategic_assessment_report(owner_id,end_day)")


class ReportRequest(store.Mutation):
    day: date
    domainId: str = Field(min_length=1, max_length=100)
    kind: Literal["daily", "weekly", "monthly"] = "daily"


def _bounds(body):
    start = body.day
    if body.kind == "weekly":
        start -= timedelta(days=6)
    elif body.kind == "monthly":
        start = start.replace(day=1)
    return start.isoformat(), body.day.isoformat()


def _dates(start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    return [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]


def _counts(entries, public):
    affected = set()
    totals = {"taskUpdates": len(entries), "added": 0, "removed": 0, "updated": 0}
    for entry in entries:
        for key in ("added", "removed", "updated"):
            totals[key] += len(entry["changes"][key])
            affected.update(entry["changes"][key])
    totals["affectedTeams"] = len(affected)
    totals["publicEvents"] = len({e["id"] for p in public for e in p.get("sourceEvents", [])})
    return totals


def _daily(conn, owner, domain, day):
    entries = []
    for row in conn.execute("""SELECT u.*,a.title,b.result_json AS before_json,r.result_json AS after_json
        FROM strategic_assessment_update u JOIN strategic_assessment a ON a.id=u.assessment_id
        JOIN strategic_assessment_run b ON b.id=u.parent_run_id JOIN strategic_assessment_run r ON r.id=u.run_id
        WHERE a.owner_id=? ORDER BY u.created_at,u.id""", (owner,)):
        if strategic_daily.local_day(row["created_at"]) != day:
            continue
        before, after = json.loads(row["before_json"]), json.loads(row["after_json"])
        before["items"] = [t for t in before["items"] if t.get("domainId") == domain["id"]]
        after["items"] = [t for t in after["items"] if t.get("domainId") == domain["id"]]
        diff = store._diff(before, after)
        if not diff["sameConditions"] or not any(diff[k] for k in ("added", "removed", "updated")):
            continue
        entries.append({"updateId": row["id"], "taskId": row["assessment_id"], "title": row["title"],
            "ingestedAt": row["created_at"], "reason": row["reason"], "changes": diff,
            "before": before, "after": after, "changeIds": json.loads(row["change_ids_json"]),
            "nextStep": "对照变化前后的成果原文，确认其适用范围；人员投入和合作条件需另行沟通。"})
    public = None
    if tasks._has_table(conn, "strategic_daily_snapshot"):
        row = conn.execute("SELECT * FROM strategic_daily_snapshot WHERE day=? ORDER BY revision DESC LIMIT 1", (day,)).fetchone()
        if row:
            value = json.loads(row["payload_json"])
            public = strategic_daily.project({**value, "revision": row["revision"], "inputHash": row["input_hash"],
                                               "frozen": True, "frozenAt": row["created_at"]}, domain["id"])
            # Old public recommendation runs may include other users' task text.
            # A private report only includes its owner's assessment runs above.
            public.pop("recommendationChanges", None)
            public.pop("followUps", None)
            public["teamChanges"] = [{k: t[k] for k in ("changeId", "teamId", "teamName", "institutionName", "domainId", "kind", "ingestedAt", "sourceId") if k in t}
                                     for t in public["teamChanges"]]
            public["summary"] = f"公开事件 {len(public['sourceEvents'])} 条，团队资料更新 {len(public['teamChanges'])} 次。"
    totals = _counts(entries, [public] if public else [])
    return {"version": "private-assessment-report-v1", "kind": "daily", "timezone": "Asia/Shanghai",
        "scope": {"domainId": domain["id"], "domainName": domain["name"]}, "startDay": day, "endDay": day,
        "changes": entries, "publicDaily": public, "dailyInputs": [], "missingDays": [],
        "counts": totals, "summary": f"已保存研判发生 {totals['taskUpdates']} 次证据更新，涉及 {totals['affectedTeams']} 支团队。",
        "notice": "发生日期以原文为准；入库日期按北京时间统计。迟到资料在入库日显示，重新保存历史日报会创建修订版。"}


def _aggregate(conn, owner, domain, kind, start, end):
    rows = conn.execute("""SELECT * FROM strategic_assessment_report p WHERE owner_id=? AND domain_id=?
        AND kind='daily' AND end_day>=? AND end_day<=? AND NOT EXISTS
        (SELECT 1 FROM strategic_assessment_report n WHERE n.owner_id=p.owner_id AND n.domain_id=p.domain_id
          AND n.kind='daily' AND n.end_day=p.end_day AND n.revision>p.revision) ORDER BY end_day,id""", (owner, domain["id"], start, end)).fetchall()
    references, entries, public = [], [], []
    present = set()
    for row in rows:
        value = json.loads(row["payload_json"])
        present.add(row["end_day"])
        references.append({"reportId": row["id"], "day": row["end_day"], "revision": row["revision"], "inputHash": row["input_hash"]})
        entries.extend(value["changes"])
        if value.get("publicDaily"):
            public.append(value["publicDaily"])
    entries = list({e["updateId"]: e for e in entries}.values())
    missing = [d for d in _dates(start, end) if d not in present]
    totals = _counts(entries, public)
    return {"version": "private-assessment-report-v1", "kind": kind, "timezone": "Asia/Shanghai",
        "scope": {"domainId": domain["id"], "domainName": domain["name"]}, "startDay": start, "endDay": end,
        "changes": entries, "dailyInputs": references, "publicDailyInputs": public, "missingDays": missing,
        "counts": totals, "summary": f"汇总 {len(present)} 份已保存日报，研判更新 {totals['taskUpdates']} 次，涉及 {totals['affectedTeams']} 支团队。",
        "notice": "汇总只使用列出的日报编号、修订和输入哈希。缺失日期没有当作零变化；后续补录不会改写本版本。"}


def _value(row):
    return {"reportId": row["id"], "revision": row["revision"], "inputHash": row["input_hash"],
            "frozenAt": row["created_at"], **json.loads(row["payload_json"])}


@router.post("/assessment-reports")
def create_report(body: ReportRequest, owner: str = Depends(current_visitor)):
    domain = next((d for d in store._domains() if not d["parent_id"] and d["id"] == body.domainId), None)
    if not domain:
        problem(422, "DOMAIN_REQUIRED", "每份报告必须选择一个明确领域")
    if body.day > datetime.now(strategic_daily.BEIJING).date():
        problem(422, "FUTURE_REPORT", "不能生成未来日期报告")
    start, end = _bounds(body)
    request = body.model_dump(mode="json")
    with closing(store._connect()) as conn:
        init(conn); assessment_updates.init(conn); conn.commit()
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            replay = store._replay(conn, owner, body.requestId, "assessment-report", request)
            if replay:
                return replay
            payload = _daily(conn, owner, domain, end) if body.kind == "daily" else _aggregate(conn, owner, domain, body.kind, start, end)
            fingerprint = digest(payload)
            previous = conn.execute("""SELECT * FROM strategic_assessment_report WHERE owner_id=? AND kind=?
                AND domain_id=? AND start_day=? AND end_day=? ORDER BY revision DESC LIMIT 1""", (owner, body.kind, body.domainId, start, end)).fetchone()
            if previous and previous["input_hash"] == fingerprint:
                result = store._reply(_value(previous), body.requestId)
            else:
                report_id = "report-" + uuid.uuid4().hex
                revision = previous["revision"] + 1 if previous else 1
                conn.execute("INSERT INTO strategic_assessment_report VALUES(?,?,?,?,?,?,?,?,?,?)", (
                    report_id, owner, body.kind, body.domainId, start, end, revision, fingerprint, store._json(payload), store._now()))
                result = store._reply(_value(conn.execute("SELECT * FROM strategic_assessment_report WHERE id=?", (report_id,)).fetchone()), body.requestId)
            store._record_request(conn, owner, body.requestId, "assessment-report", request, result)
            return result


@router.get("/assessment-reports/{report_id}")
def report(report_id: str, owner: str = Depends(current_visitor)):
    with closing(store._connect()) as conn:
        init(conn); conn.commit()
        row = conn.execute("SELECT * FROM strategic_assessment_report WHERE id=? AND owner_id=?", (report_id, owner)).fetchone()
        if not row:
            problem(404, "REPORT_NOT_FOUND", "报告不存在或当前访客无权读取")
        return store._reply(_value(row))


@router.get("/assessment-reports")
def reports(domain_id: str | None = None, page: int = Query(1, ge=1), owner: str = Depends(current_visitor)):
    with closing(store._connect()) as conn:
        init(conn); conn.commit()
        where = "owner_id=?" + (" AND domain_id=?" if domain_id else "")
        args = [owner] + ([domain_id] if domain_id else [])
        total = conn.execute(f"SELECT COUNT(*) FROM strategic_assessment_report WHERE {where}", args).fetchone()[0]
        rows = conn.execute(f"SELECT * FROM strategic_assessment_report WHERE {where} ORDER BY created_at DESC,id DESC LIMIT 20 OFFSET ?", [*args, (page-1)*20])
        return store._reply({"items": [{"reportId": r["id"], "kind": r["kind"], "domainId": r["domain_id"],
            "startDay": r["start_day"], "endDay": r["end_day"], "revision": r["revision"], "frozenAt": r["created_at"]} for r in rows], "page": page, "total": total})
