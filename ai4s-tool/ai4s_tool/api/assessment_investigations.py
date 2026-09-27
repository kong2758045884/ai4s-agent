"""Owner checked entry points for explicit web collection against frozen criteria."""
from contextlib import closing
from datetime import date
import json
import os

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from . import strategic_assessments as store, strategic_investigations as jobs, task_recommendations as tasks
from .strategic_identity import current_visitor, problem
from . import strategic_access as access

router = APIRouter(prefix="/strategic-map", tags=["assessment_investigations"])


class InvestigationOptions(BaseModel):
    teamIds: list[str] = Field(min_length=1, max_length=12)
    criterionIds: list[str] = Field(min_length=1, max_length=40)
    publishedAfter: date | None = None


class StartInvestigation(store.Mutation):
    options: InvestigationOptions


def _run(conn, task_id, run_id, owner):
    record = store._owned(conn, task_id, owner)
    row = conn.execute("SELECT result_json FROM strategic_assessment_run WHERE id=? AND assessment_id=?", (run_id, task_id)).fetchone()
    if not row:
        problem(404, "RUN_NOT_FOUND", "研判结果不存在")
    return record, json.loads(row[0])


def _owned_job(task_id, job_id, owner):
    with closing(store._connect()) as conn:
        store._owned(conn, task_id, owner)
        jobs.init(conn); conn.commit()
        row = conn.execute("""SELECT j.run_id FROM strategic_web_investigation j
          JOIN strategic_assessment_run r ON r.id=j.run_id WHERE j.id=? AND r.assessment_id=?""", (job_id, task_id)).fetchone()
        if not row:
            problem(404, "INVESTIGATION_NOT_FOUND", "联网调查不存在或当前访客无权读取")
        return _run(conn, task_id, row[0], owner)


def _require_active(record, run):
    if json.loads(record["state_json"]).get("activeRunId") != run["runId"]:
        problem(409, "HISTORICAL_RUN", "历史结果只读，请回到当前研判发起调查")
    if run.get("mode") == "domain":
        problem(422, "TASK_REQUIRED", "请先把领域观察转成具体任务并确认条件")


def _teams(run):
    scope = run["scope"]
    return [t for t in tasks.verified_teams()["teams"]
            if (not scope["domainIds"] or t["domainId"] in scope["domainIds"])
            and (not scope.get("subdomainId") or t.get("subdomainId") == scope["subdomainId"])]


@router.get("/assessments/{task_id}/runs/{run_id}/investigations")
def investigations(task_id: str, run_id: str, owner: str = Depends(current_visitor)):
    from . import strategic_map as sm
    with closing(store._connect()) as conn:
        record, run = _run(conn, task_id, run_id, owner)
        active_id = json.loads(record["state_json"]).get("activeRunId")
        active_row = conn.execute("SELECT input_id FROM strategic_assessment_run WHERE id=? AND assessment_id=?", (active_id, task_id)).fetchone()
        jobs.init(conn); conn.commit()
        rows = conn.execute("SELECT id FROM strategic_web_investigation WHERE run_id=? ORDER BY created_at DESC,id DESC", (run_id,)).fetchall()
    engines = {e.strip().lower() for e in os.getenv("USE_SEARCH_ENGINE", "ddg").split(",")}
    supported = engines & {"ddg", "bing", "exa", "google", "serper", "serp", "jina", "sogou"}
    configured = bool(sm._llm_config()) and bool(supported)
    can_collect = "collection:run" in access.capabilities(owner)["permissions"]
    return store._reply({"jobs": [jobs.get(row[0]) for row in rows], "configured": configured,
        "canStart": can_collect and active_id == run_id and run.get("mode") != "domain",
        "canRetry": can_collect and bool(active_row and active_row[0] == run["inputVersionId"]),
        "configurationNotice": "当前访客没有联网采集权限；本地研判和已保存资料仍可查看" if not can_collect else
            ("已登记搜索与模型配置；尚未通过本次真实联网调用验证" if configured else "缺少搜索或模型配置，本地研判仍可使用"),
        "teams": [{"teamId": t["id"], "teamName": t["teamName"], "institutionName": t["institutionName"]} for t in _teams(run)],
        "scopeNotice": "只补充所选范围内已有科研单元；时间为检索偏好，不保证所有来源均有发布日期。成果仍须独立复核后发布。"})


@router.post("/assessments/{task_id}/runs/{run_id}/investigations", status_code=202)
def start_investigation(task_id: str, run_id: str, body: StartInvestigation, owner: str = Depends(current_visitor)):
    with closing(store._connect()) as conn:
        record, run = _run(conn, task_id, run_id, owner)
    _require_active(record, run)
    access.authorized(owner, "collection:run")
    options = body.options.model_dump(mode="json")
    if len(set(options["teamIds"])) != len(options["teamIds"]) or not set(options["teamIds"]) <= {t["id"] for t in _teams(run)}:
        problem(422, "INVALID_TEAM_SCOPE", "调查对象必须是当前领域范围内的不同科研单元")
    if len(set(options["criterionIds"])) != len(options["criterionIds"]) or not set(options["criterionIds"]) <= {c["id"] for c in run.get("criteria", [])}:
        problem(422, "INVALID_CRITERIA_SCOPE", "调查问题必须来自本次确认的任务条件")
    if body.options.publishedAfter and body.options.publishedAfter > date.today():
        problem(422, "INVALID_DATE", "检索起始日期不能晚于今天")
    job_id = jobs.start(run_id, private_task=run, options=options, request_id=body.requestId)
    return store._reply(jobs.get(job_id), body.requestId)


@router.get("/assessments/{task_id}/investigations/{job_id}")
def investigation(task_id: str, job_id: str, owner: str = Depends(current_visitor)):
    _owned_job(task_id, job_id, owner)
    return store._reply(jobs.get(job_id))


@router.post("/assessments/{task_id}/investigations/{job_id}/cancel")
def cancel_investigation(task_id: str, job_id: str, owner: str = Depends(current_visitor)):
    _owned_job(task_id, job_id, owner)
    return store._reply(jobs.cancel(job_id))


@router.post("/assessments/{task_id}/investigations/{job_id}/retry", status_code=202)
def retry_investigation(task_id: str, job_id: str, body: store.Mutation, owner: str = Depends(current_visitor)):
    record, run = _owned_job(task_id, job_id, owner)
    access.authorized(owner, "collection:run")
    # A successful subset may already have produced an evidence-only descendant.
    active_id = json.loads(record["state_json"]).get("activeRunId")
    with closing(store._connect()) as conn:
        _, active_run = _run(conn, task_id, active_id, owner)
    if active_run["inputVersionId"] != run["inputVersionId"]:
        problem(409, "INPUT_CHANGED", "任务条件已经变化，请从当前结果重新选择调查范围")
    previous = jobs.get(job_id)
    if previous["state"] not in {"failed", "partial", "interrupted", "cancelled"}:
        problem(409, "NOT_RETRYABLE", "仅失败、中断、部分完成或已取消的调查可重试")
    next_id = jobs.start(run["runId"], retry=True, private_task=run, options=previous["investigationOptions"],
                         request_id=body.requestId, retry_job_id=job_id)
    return store._reply(jobs.get(next_id), body.requestId)
