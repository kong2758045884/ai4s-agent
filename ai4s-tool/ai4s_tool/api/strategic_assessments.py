"""Private, versioned assessments; additive storage alongside legacy public runs."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from . import task_recommendations as tasks
from .strategic_identity import current_visitor, problem
from .strategic_interpretation import Criterion, InterpretationRequest, Scope, digest, interpret, matching_criteria

router = APIRouter(prefix="/strategic-map", tags=["strategic_assessments"])


def init(conn: sqlite3.Connection) -> None:
    """No executescript: schema and associated mutations share the caller transaction."""
    for statement in (
        """CREATE TABLE IF NOT EXISTS strategic_assessment (
          id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, title TEXT NOT NULL, mode TEXT NOT NULL,
          revision INTEGER NOT NULL, state_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS ix_assessment_owner ON strategic_assessment(owner_id,updated_at)",
        """CREATE TABLE IF NOT EXISTS strategic_assessment_input (
          id TEXT PRIMARY KEY, assessment_id TEXT NOT NULL REFERENCES strategic_assessment(id),
          version INTEGER NOT NULL, payload_json TEXT NOT NULL, evidence_version TEXT NOT NULL,
          created_at TEXT NOT NULL, UNIQUE(assessment_id,version))""",
        """CREATE TABLE IF NOT EXISTS strategic_assessment_run (
          id TEXT PRIMARY KEY, assessment_id TEXT NOT NULL REFERENCES strategic_assessment(id),
          input_id TEXT NOT NULL REFERENCES strategic_assessment_input(id), previous_run_id TEXT,
          result_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS ix_assessment_run ON strategic_assessment_run(assessment_id,created_at)",
        """CREATE TABLE IF NOT EXISTS strategic_assessment_run_selection (
          run_id TEXT PRIMARY KEY REFERENCES strategic_assessment_run(id), selection_json TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS strategic_assessment_audit (
          id TEXT PRIMARY KEY, assessment_id TEXT NOT NULL REFERENCES strategic_assessment(id),
          actor_id TEXT NOT NULL, action TEXT NOT NULL, revision INTEGER NOT NULL,
          payload_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS strategic_assessment_request (
          owner_id TEXT NOT NULL, request_id TEXT NOT NULL, operation TEXT NOT NULL,
          body_hash TEXT NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY(owner_id,request_id))""",
    ):
        conn.execute(statement)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _connect():
    conn = tasks._db(write=True)
    conn.execute("PRAGMA foreign_keys=ON")
    with conn:
        init(conn)
    return conn


def _domains() -> list[dict]:
    with closing(tasks._db()) as conn:
        return [dict(r) for r in conn.execute("SELECT id,name,parent_id FROM strategic_map_domain WHERE deleted=0 ORDER BY sort_order,id")]


def _validate_scope(scope: Scope):
    rows = {r["id"]: r for r in _domains()}
    if len(scope.domainIds) != len(set(scope.domainIds)):
        problem(422, "DUPLICATE_DOMAIN", "领域不能重复")
    if any(d not in rows or rows[d]["parent_id"] for d in scope.domainIds):
        problem(422, "INVALID_DOMAIN", "请选择现有研究领域")
    if scope.mode == "selected" and not scope.domainIds:
        problem(422, "DOMAIN_REQUIRED", "手动范围至少选择一个领域")
    if scope.subdomainId and (scope.subdomainId not in rows or len(scope.domainIds) != 1 or
                             rows[scope.subdomainId]["parent_id"] != scope.domainIds[0]):
        problem(422, "SUBDOMAIN_MISMATCH", "子领域必须属于所选的唯一领域")


def _owned(conn, task_id: str, owner: str):
    row = conn.execute("SELECT * FROM strategic_assessment WHERE id=? AND owner_id=?", (task_id, owner)).fetchone()
    if row is None:
        problem(404, "ASSESSMENT_NOT_FOUND", "研判不存在或当前访客无权读取")
    return row


def _result(conn, row):
    value = {"taskId": row["id"], "title": row["title"], "mode": row["mode"], "revision": row["revision"],
             "state": json.loads(row["state_json"]), "createdAt": row["created_at"], "updatedAt": row["updated_at"]}
    value["runs"] = [dict(r) for r in conn.execute("""SELECT r.id AS runId,r.input_id AS inputVersionId,
        i.version AS inputVersion,r.previous_run_id AS previousRunId,r.created_at AS createdAt
        FROM strategic_assessment_run r JOIN strategic_assessment_input i ON i.id=r.input_id
        WHERE r.assessment_id=? ORDER BY i.version DESC,r.created_at DESC,r.id DESC""", (row["id"],))]
    active = value["state"].get("activeRunId")
    if active:
        value["run"] = json.loads(conn.execute("SELECT result_json FROM strategic_assessment_run WHERE id=? AND assessment_id=?", (active, row["id"])).fetchone()[0])
        value["run"]["selection"] = _selection(conn, active)
    return value


def _selection(conn, run_id):
    row = conn.execute("SELECT selection_json FROM strategic_assessment_run_selection WHERE run_id=?", (run_id,)).fetchone()
    return json.loads(row[0]) if row else {"comparedTeamIds": [], "combination": []}


def _scope_snapshot(scope):
    rows = {d["id"]: d for d in _domains()}
    return {**scope, "domainNames": [rows[d]["name"] for d in scope["domainIds"]],
            "subdomainName": rows.get(scope.get("subdomainId"), {}).get("name", "")}


def _reply(data, request_id=""):
    return {"data": data, "requestId": request_id}


def _replay(conn, owner, request_id, operation, body):
    row = conn.execute("SELECT * FROM strategic_assessment_request WHERE owner_id=? AND request_id=?", (owner, request_id)).fetchone()
    if row:
        if row["operation"] != operation or row["body_hash"] != digest(body):
            problem(409, "REQUEST_ID_REUSED", "同一个提交编号不能用于不同内容")
        return json.loads(row["response_json"])
    return None


def _record_request(conn, owner, request_id, operation, body, response):
    conn.execute("INSERT INTO strategic_assessment_request VALUES(?,?,?,?,?,?)",
                 (owner, request_id, operation, digest(body), _json(response), _now()))


def _audit(conn, row, owner, action, payload):
    conn.execute("INSERT INTO strategic_assessment_audit VALUES(?,?,?,?,?,?,?)", (
        "audit-" + uuid.uuid4().hex, row["id"], owner, action, row["revision"], _json(payload), _now()))


class Mutation(BaseModel):
    requestId: str = Field(min_length=8, max_length=100)


class CreateAssessment(Mutation):
    title: str = Field(default="新建研判", min_length=1, max_length=150)
    mode: Literal["task", "domain"] = "task"
    taskDraft: str = Field(default="", max_length=2000)
    domainDraft: str = Field(default="", max_length=500)


class FollowUp(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    teamId: str = Field(min_length=1, max_length=80)
    claimIds: list[str] = Field(default_factory=list, max_length=40)
    question: str = Field(min_length=1, max_length=1000)
    method: str = Field(default="", max_length=500)
    owner: str = Field(default="", max_length=100)
    dueDate: date | None = None
    result: str = Field(default="", max_length=2000)
    status: Literal["open", "in_progress", "done", "cancelled"] = "open"
    judgment: Literal["supported", "limited", "contradicted", "unchanged"] | None = None


class Role(BaseModel):
    teamId: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=150)
    rationale: str = Field(default="", max_length=1000)
    claimIds: list[str] = Field(default_factory=list, max_length=10)


class PatchAssessment(Mutation):
    expectedRevision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=150)
    mode: Literal["task", "domain"] | None = None
    taskDraft: str | None = Field(default=None, max_length=2000)
    domainDraft: str | None = Field(default=None, max_length=500)
    comparedTeamIds: list[str] | None = Field(default=None, max_length=3)
    combination: list[Role] | None = Field(default=None, max_length=3)
    followUps: list[FollowUp] | None = Field(default=None, max_length=100)
    internalNotes: str | None = Field(default=None, max_length=10000)
    taskScope: Scope | None = None
    domainScope: Scope | None = None
    requestedLimit: int | None = Field(default=None, ge=1, le=20)
    windowDays: Literal[30, 90, 180, 365] | None = None


class ConfirmAssessment(Mutation):
    expectedRevision: int = Field(ge=1)
    taskText: str = Field(min_length=2, max_length=2000)
    scope: Scope
    limit: int = Field(default=5, ge=1, le=20)
    criteria: list[Criterion] = Field(min_length=1, max_length=40)
    evidenceVersion: str = Field(min_length=1, max_length=100)
    scopeResolution: Literal["keep", "switch", "expand"] | None = None


class ObserveAssessment(Mutation):
    expectedRevision: int = Field(ge=1)
    scope: Scope
    limit: int = Field(default=5, ge=1, le=20)
    windowDays: Literal[30, 90, 180, 365] = 90


@router.post("/assessments/{task_id}/observe")
def observe_assessment(task_id: str, body: ObserveAssessment, owner: str = Depends(current_visitor)):
    payload, operation = body.model_dump(mode="json"), "observe:" + task_id
    with closing(_connect()) as conn:
        row = _owned(conn, task_id, owner)
        replay = _replay(conn, owner, body.requestId, operation, payload)
        if replay:
            return replay
        if row["revision"] != body.expectedRevision:
            problem(409, "REVISION_CONFLICT", "研判已修改，请重新载入")
    _validate_scope(body.scope)
    if body.scope.mode != "selected" or len(body.scope.domainIds) != 1:
        problem(422, "OBSERVATION_DOMAIN_REQUIRED", "领域观察请选择一个明确领域")
    teams, claims, version = tasks._catalogue_evidence()
    selected = {t["id"]: t for t in teams if t["domainId"] in body.scope.domainIds and
                (not body.scope.subdomainId or t.get("subdomainId") == body.scope.subdomainId)}
    by_team: dict[str, list] = {}
    for c in claims:
        if c[1] in selected:
            by_team.setdefault(c[1], []).append(c)
    today = datetime.now(timezone(timedelta(hours=8))).date()
    cutoff = (today - timedelta(days=body.windowDays)).isoformat()
    units = []
    direction_ids: dict[str, set] = {}
    for team_id, team in selected.items():
        evidence = by_team.get(team_id, [])
        outcomes = [c for c in evidence if c[3] == "outcome"]
        recent = [c for c in outcomes if c[7] and cutoff <= str(c[7])[:10] <= today.isoformat()]
        for direction in team.get("researchDirections") or [team.get("subdomainName") or "方向尚未细分"]:
            direction_ids.setdefault(direction, set()).add(team_id)
        units.append({"teamId": team_id, "teamName": team.get("teamName") or team["name"],
            "identityEvidence": team.get("institutionEvidence", []),
            "institutionName": team.get("institutionName") or team["name"], "outcomeCount": len(outcomes),
            "recentOutcomeCount": len(recent), "undatedOutcomeCount": sum(not c[7] for c in outcomes),
            "reason": "已有具体团队成果，可按任务继续查证" if outcomes else "有身份与方向来源，具体成果尚缺",
            "citations": [{"id": c[0], "kind": c[3], "text": c[4], "quote": c[5], "url": c[6], "publishedAt": c[7],
                           "provenance": team.get("claimProvenance", {}).get(c[0])}
                          for c in (outcomes or evidence)[:3]]})
    units.sort(key=lambda t: (-t["outcomeCount"], t["teamId"]))
    run_id = "observation-" + uuid.uuid4().hex
    run = {"runId": run_id, "taskId": task_id, "mode": "domain", "scope": _scope_snapshot(body.scope.model_dump()),
        "dataVersion": version, "matchVersion": "domain-observation-v1", "createdAt": _now(),
        "domainId": body.scope.domainIds[0], "domainIds": body.scope.domainIds, "subdomainId": body.scope.subdomainId,
        "requestedLimit": body.limit, "taskText": "", "items": [], "shortfall": 0,
        "observation": {"totalUnits": len(units), "outcomeBackedUnits": sum(bool(t["outcomeCount"]) for t in units),
            "identityOnlyUnits": sum(not t["outcomeCount"] for t in units), "units": units[:body.limit],
            "directions": [{"name": name, "teamCount": len(ids), "teamIds": sorted(ids)} for name, ids in direction_ids.items()],
            "windowDays": body.windowDays, "recentCutoff": cutoff,
            "notice": "关注清单按可追溯成果数量排列，不代表任务匹配或机构影响力；近期窗口仅用于动态计数，历史能力成果仍保留。"}}
    with closing(_connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _owned(conn, task_id, owner)
        replay = _replay(conn, owner, body.requestId, operation, payload)
        if replay:
            return replay
        if row["revision"] != body.expectedRevision:
            problem(409, "REVISION_CONFLICT", "生成期间研判已修改，原记录未被覆盖")
        state = json.loads(row["state_json"])
        previous = state.get("activeRunId")
        version_number = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM strategic_assessment_input WHERE assessment_id=?", (task_id,)).fetchone()[0]
        input_id = "input-" + uuid.uuid4().hex
        run.update(inputVersion=version_number, inputVersionId=input_id, previousRunId=previous)
        from .assessment_graph import freeze
        freeze(run)
        conn.execute("INSERT INTO strategic_assessment_input VALUES(?,?,?,?,?,?)", (input_id, task_id, version_number, _json(payload), version, _now()))
        conn.execute("INSERT INTO strategic_assessment_run VALUES(?,?,?,?,?,?)", (run_id, task_id, input_id, previous, _json(run), run["createdAt"]))
        conn.execute("INSERT INTO strategic_assessment_run_selection VALUES(?,?)", (run_id, _json({"comparedTeamIds": [], "combination": []})))
        state.update(activeRunId=run_id, comparedTeamIds=[], combination=[], domainScope=run["scope"],
                     requestedLimit=body.limit, windowDays=body.windowDays)
        conn.execute("UPDATE strategic_assessment SET mode='domain',revision=revision+1,state_json=?,updated_at=? WHERE id=?", (_json(state), _now(), task_id))
        updated = _owned(conn, task_id, owner)
        _audit(conn, updated, owner, "observed", {"runId": run_id, "inputVersionId": input_id})
        result = _reply(_result(conn, updated), body.requestId)
        _record_request(conn, owner, body.requestId, operation, payload, result)
        return result


@router.post("/task-interpretations")
def task_interpretation(body: InterpretationRequest):
    _validate_scope(body.scope)
    result = interpret(body, [d for d in _domains() if not d["parent_id"]])
    result["resolvedDomainNames"] = _scope_snapshot({"domainIds": result["resolvedDomainIds"]})["domainNames"]
    result["proposedDomainNames"] = _scope_snapshot({"domainIds": result["proposedDomainIds"]})["domainNames"]
    result["evidenceVersion"] = tasks._candidate_evidence()[2]
    return _reply(result)


@router.post("/assessments", status_code=201)
def create_assessment(body: CreateAssessment, owner: str = Depends(current_visitor)):
    payload = body.model_dump(mode="json")
    with closing(_connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        replay = _replay(conn, owner, body.requestId, "create", payload)
        if replay:
            return replay
        task_id, now = "assessment-" + uuid.uuid4().hex, _now()
        state = {"taskDraft": body.taskDraft, "domainDraft": body.domainDraft,
                 "activeRunId": None, "comparedTeamIds": [], "combination": [], "followUps": [], "internalNotes": ""}
        conn.execute("INSERT INTO strategic_assessment VALUES(?,?,?,?,?,?,?,?)",
            (task_id, owner, body.title, body.mode, 1, _json(state), now, now))
        row = _owned(conn, task_id, owner)
        _audit(conn, row, owner, "created", {"mode": body.mode})
        result = _reply(_result(conn, row), body.requestId)
        _record_request(conn, owner, body.requestId, "create", payload, result)
        return result


@router.get("/assessments")
def list_assessments(page: int = Query(1, ge=1), owner: str = Depends(current_visitor)):
    with closing(_connect()) as conn:
        rows = conn.execute("""SELECT id AS taskId,title,mode,revision,updated_at AS updatedAt FROM strategic_assessment
            WHERE owner_id=? ORDER BY updated_at DESC,id LIMIT 20 OFFSET ?""", (owner, (page - 1) * 20))
        return _reply({"items": [dict(r) for r in rows], "page": page, "size": 20,
            "total": conn.execute("SELECT COUNT(*) FROM strategic_assessment WHERE owner_id=?", (owner,)).fetchone()[0]})


@router.get("/assessments/{task_id}")
def get_assessment(task_id: str, owner: str = Depends(current_visitor)):
    with closing(_connect()) as conn:
        return _reply(_result(conn, _owned(conn, task_id, owner)))


@router.patch("/assessments/{task_id}")
def patch_assessment(task_id: str, body: PatchAssessment, owner: str = Depends(current_visitor)):
    payload = body.model_dump(mode="json", exclude_none=True)
    with closing(_connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _owned(conn, task_id, owner)
        replay = _replay(conn, owner, body.requestId, "patch:" + task_id, payload)
        if replay:
            return replay
        if row["revision"] != body.expectedRevision:
            problem(409, "REVISION_CONFLICT", "研判已在其他窗口修改，请重新载入后再保存")
        state = json.loads(row["state_json"])
        current = _result(conn, row).get("run", {})
        team_ids = {v["teamId"] for v in current.get("items", [])}
        selected = body.comparedTeamIds
        if selected is not None and (len(set(selected)) != len(selected) or not set(selected) <= team_ids):
            problem(422, "INVALID_COMPARISON", "比较只能选择当前推荐中的最多三支不同团队")
        if body.combination is not None and (len({r.teamId for r in body.combination}) != len(body.combination) or
                                             any(r.teamId not in team_ids for r in body.combination)):
            problem(422, "INVALID_COMBINATION", "组合中的角色必须对应当前推荐的不同团队")
        if body.combination is not None:
            compared = set(selected if selected is not None else state.get("comparedTeamIds", []))
            sources = {item["teamId"]: {c["id"] for c in item["citations"] if c["kind"] == "outcome"}
                       for item in current.get("items", [])}
            for role in body.combination:
                if (role.teamId not in compared or not role.role.strip() or not role.rationale.strip() or
                    not role.claimIds or len(set(role.claimIds)) != len(role.claimIds) or
                    not set(role.claimIds) <= sources[role.teamId]):
                    problem(422, "INVALID_COMBINATION_EVIDENCE", "分工须来自已比较团队，填写依据并选择该团队本次研判的成果引文")
        if body.followUps is not None:
            if len({f.id for f in body.followUps}) != len(body.followUps):
                problem(422, "DUPLICATE_FOLLOWUP", "跟进编号不能重复")
            previous_followups = {value["id"]: value for value in state.get("followUps", [])}
            # Existing follow-ups may refer to historical evidence; new ones need a saved snapshot.
            refs: dict[str, set[str]] = {}
            outcome_refs: dict[str, set[str]] = {}
            for saved in conn.execute("SELECT result_json FROM strategic_assessment_run WHERE assessment_id=?", (task_id,)):
                for item in json.loads(saved[0]).get("items", []):
                    refs.setdefault(item["teamId"], set()).update(c["id"] for c in item["citations"])
                    outcome_refs.setdefault(item["teamId"], set()).update(
                        c["id"] for c in item["citations"] if c["kind"] == "outcome")
            for follow in body.followUps:
                if follow.teamId not in refs or not set(follow.claimIds) <= refs[follow.teamId]:
                    problem(422, "INVALID_FOLLOWUP_EVIDENCE", "跟进对象和依据必须来自本研判保存的团队与引文")
                # Allow untouched records created before these requirements; edits must complete the plan.
                unchanged_legacy = previous_followups.get(follow.id) == follow.model_dump(mode="json", exclude_none=True)
                if not follow.question.strip():
                    problem(422, "INCOMPLETE_FOLLOWUP", "跟进须写明具体问题")
                if follow.status != "cancelled" and not unchanged_legacy and (
                    not follow.method.strip() or not follow.owner.strip() or follow.dueDate is None):
                    problem(422, "INCOMPLETE_FOLLOWUP", "跟进须填写验证方法、负责人和计划日期")
                if follow.status == "done" and not unchanged_legacy and (
                    not follow.result.strip() or not follow.claimIds or follow.judgment is None):
                    problem(422, "INCOMPLETE_FOLLOWUP", "完成跟进须记录结果、所依据的成果引文及对原判断的影响")
                if follow.status == "done" and not unchanged_legacy and not set(follow.claimIds) <= outcome_refs[follow.teamId]:
                    problem(422, "INVALID_FOLLOWUP_EVIDENCE", "完成判断只能引用该团队已保存的成果依据")
        for scope in (body.taskScope, body.domainScope):
            if scope:
                _validate_scope(scope)
        for key in ("taskDraft", "domainDraft", "comparedTeamIds", "combination", "followUps", "internalNotes", "taskScope", "domainScope", "requestedLimit", "windowDays"):
            if key in payload:
                state[key] = payload[key]
        if selected is not None and body.combination is None:
            # A removed comparison cannot retain an unseen saved role.
            state["combination"] = [role for role in state.get("combination", []) if role["teamId"] in selected]
        if state.get("activeRunId") and any(k in payload for k in ("comparedTeamIds", "combination")):
            conn.execute("INSERT OR REPLACE INTO strategic_assessment_run_selection VALUES(?,?)", (
                state["activeRunId"], _json({k: state[k] for k in ("comparedTeamIds", "combination")})))
        conn.execute("UPDATE strategic_assessment SET title=?,mode=?,revision=revision+1,state_json=?,updated_at=? WHERE id=?",
            (body.title or row["title"], body.mode or row["mode"], _json(state), _now(), task_id))
        updated = _owned(conn, task_id, owner)
        _audit(conn, updated, owner, "edited", {"before": json.loads(row["state_json"]), "after": state})
        result = _reply(_result(conn, updated), body.requestId)
        _record_request(conn, owner, body.requestId, "patch:" + task_id, payload, result)
        return result


@router.post("/assessments/{task_id}/confirm")
def confirm_assessment(task_id: str, body: ConfirmAssessment, owner: str = Depends(current_visitor)):
    payload = body.model_dump(mode="json")
    operation = "confirm:" + task_id
    with closing(_connect()) as conn:
        row = _owned(conn, task_id, owner)
        replay = _replay(conn, owner, body.requestId, operation, payload)
        if replay:
            return replay
        if row["revision"] != body.expectedRevision:
            problem(409, "REVISION_CONFLICT", "条件或比较已被修改，请重新载入再确认")
    _validate_scope(body.scope)
    interpretation = interpret(InterpretationRequest(taskText=body.taskText, scope=body.scope),
                               [d for d in _domains() if not d["parent_id"]])
    if interpretation["scopeConflict"] and not body.scopeResolution:
        problem(409, "SCOPE_CONFIRMATION_REQUIRED", "任务可能涉及其他领域，请明确保留、切换或扩大范围")
    domain_ids = interpretation["resolvedDomainIds"]
    if interpretation["scopeConflict"] and body.scopeResolution == "switch":
        domain_ids = interpretation["proposedDomainIds"]
    elif interpretation["scopeConflict"] and body.scopeResolution == "expand":
        domain_ids = sorted(set(domain_ids + interpretation["proposedDomainIds"]))
    subdomain = body.scope.subdomainId
    if domain_ids != body.scope.domainIds:
        subdomain = None
    criteria = [c.model_dump() for c in body.criteria]
    if len({c["id"] for c in criteria}) != len(criteria):
        problem(422, "DUPLICATE_CRITERION", "条件编号不能重复")
    for criterion in criteria:
        span = criterion["sourceSpan"]
        valid = (isinstance(span, dict) and isinstance(span.get("start"), int) and isinstance(span.get("end"), int)
                 and 0 <= span["start"] < span["end"] <= len(body.taskText)
                 and body.taskText[span["start"]:span["end"]] == criterion["text"])
        criterion["sourceSpan"] = span if valid else None
        criterion["origin"] = "original" if valid else "user_edited"
    parsed, blockers = matching_criteria(criteria)
    if tasks._candidate_evidence()[2] != body.evidenceVersion:
        problem(409, "EVIDENCE_CHANGED", "成果资料已更新，请重新解析并确认当前证据版本")
    run = tasks._recommend(tasks.TaskRequest(taskText=body.taskText[:500],
        domainId=domain_ids[0] if len(domain_ids) == 1 else None, subdomainId=subdomain, limit=body.limit),
        persist=False, confirmed_criteria=parsed, domain_ids=domain_ids)
    # Unknown mandatory constraints cannot be presented as satisfied.
    if blockers:
        run.update(items=[], matchedTeamCount=0, shortfall=body.limit)
    run["coverage"] = _coverage(domain_ids, subdomain, run)
    run.update(taskText=body.taskText, taskId=task_id, domainIds=domain_ids, criteria=criteria,
               unresolvedConditions=blockers, evidenceVersion=body.evidenceVersion,
               scope=_scope_snapshot({"mode": "selected" if domain_ids else "auto", "domainIds": domain_ids,
                      "subdomainId": subdomain, "domesticOnly": True}))
    run["matchVersion"] = "confirmed-criteria-v1+" + tasks.MATCH_VERSION
    for item in run["items"]:
        item["matchVersion"] = run["matchVersion"]
        item["criteriaMatrix"] = _matrix(criteria, item)
    with closing(_connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _owned(conn, task_id, owner)
        replay = _replay(conn, owner, body.requestId, operation, payload)
        if replay:
            return replay
        if row["revision"] != body.expectedRevision:
            problem(409, "REVISION_CONFLICT", "研判在生成期间已修改，本次结果未覆盖原记录")
        # Recheck the exact evidence source after evaluation, before committing a frozen run.
        if tasks._candidate_evidence()[2] != body.evidenceVersion:
            problem(409, "EVIDENCE_CHANGED", "生成期间资料已更新，请重新确认")
        state = json.loads(row["state_json"])
        previous = state.get("activeRunId")
        version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM strategic_assessment_input WHERE assessment_id=?", (task_id,)).fetchone()[0]
        input_id = "input-" + uuid.uuid4().hex
        run.update(inputVersionId=input_id, inputVersion=version, previousRunId=previous)
        from .assessment_graph import freeze
        freeze(run)
        if previous:
            before = json.loads(conn.execute("SELECT result_json FROM strategic_assessment_run WHERE id=?", (previous,)).fetchone()[0])
            run["changes"] = _diff(before, run)
        conn.execute("INSERT INTO strategic_assessment_input VALUES(?,?,?,?,?,?)", (input_id, task_id, version,
            _json({"taskText": body.taskText, "criteria": criteria, "scope": run["scope"], "limit": body.limit,
                   "interpretation": interpretation, "scopeResolution": body.scopeResolution}), body.evidenceVersion, _now()))
        conn.execute("INSERT INTO strategic_assessment_run VALUES(?,?,?,?,?,?)", (run["runId"], task_id,
            input_id, previous, _json(run), run["createdAt"]))
        conn.execute("INSERT INTO strategic_assessment_run_selection VALUES(?,?)", (run["runId"], _json({"comparedTeamIds": [], "combination": []})))
        state.update(activeRunId=run["runId"], taskDraft=body.taskText, comparedTeamIds=[], combination=[])
        conn.execute("UPDATE strategic_assessment SET mode='task',revision=revision+1,state_json=?,updated_at=? WHERE id=?", (_json(state), _now(), task_id))
        updated = _owned(conn, task_id, owner)
        _audit(conn, updated, owner, "confirmed", {"inputVersionId": input_id, "runId": run["runId"], "evidenceVersion": body.evidenceVersion})
        result = _reply(_result(conn, updated), body.requestId)
        _record_request(conn, owner, body.requestId, operation, payload, result)
        return result


def _coverage(domain_ids, subdomain, run):
    """Snapshot the three non-additive stages used to explain a shortfall."""
    catalogue_teams = tasks._catalogue_evidence()[0]
    scope_count = sum(1 for team in catalogue_teams
        if (not domain_ids or team["domainId"] in domain_ids)
        and (not subdomain or team.get("subdomainId") == subdomain))
    return {"scopeTeamCount": scope_count,
        "outcomeBackedTeamCount": run["eligibleTeamCount"],
        "conditionMatchedTeamCount": run["matchedTeamCount"]}


def _matrix(criteria, item):
    from .strategic_text import _concepts, normalize
    rows = []
    for criterion in criteria:
        terms = _concepts(criterion["text"])
        citations = [c["id"] for c in item["citations"] if terms and
                     any(t in normalize(c["text"] + " " + c["quote"]) for t in terms)]
        matched = [any(t in normalize(c["text"] + " " + c["quote"]) for c in item["citations"]) for t in terms]
        supported = bool(terms) and (any(matched) if criterion.get("operator") == "any" else all(matched))
        if criterion["kind"] in {"constraint", "organization", "unresolved"}:
            supported, citations = False, []
        status = "supported" if supported else "insufficient"
        if criterion["necessity"] == "excluded":
            status, citations = "not_observed", []
        rows.append({"criterionId": criterion["id"], "text": criterion["text"], "necessity": criterion["necessity"],
                     "status": status, "claimIds": citations,
                     "notice": "已有引文未观察到排除项，不代表全面排查" if status == "not_observed" else ""})
    return rows


def _diff(before, after):
    old, new = ({v["teamId"]: v for v in run["items"]} for run in (before, after))
    same = all(before.get(k) == after.get(k) for k in ("taskText", "scope", "criteria", "requestedLimit", "matchVersion"))
    return {"added": sorted(new.keys() - old.keys()), "removed": sorted(old.keys() - new.keys()),
            "updated": sorted(k for k in old.keys() & new.keys() if old[k] != new[k]),
            "coverageChanged": before.get("coverage") != after.get("coverage"),
            "sameConditions": same, "reason": "证据版本更新" if same else "用户修改研判条件；不作为科研变化",
            "beforeRunId": before["runId"], "afterRunId": after["runId"]}


@router.get("/assessments/{task_id}/runs/{run_id}/graph")
def get_run_graph(task_id: str, run_id: str, owner: str = Depends(current_visitor)):
    from .assessment_graph import read
    with closing(_connect()) as conn:
        _owned(conn, task_id, owner)
        row = conn.execute("SELECT result_json FROM strategic_assessment_run WHERE id=? AND assessment_id=?", (run_id, task_id)).fetchone()
        if not row:
            problem(404, "RUN_NOT_FOUND", "找不到此研判版本")
        return _reply(read(json.loads(row[0])))


@router.get("/assessments/{task_id}/runs/{run_id}")
def get_run(task_id: str, run_id: str, owner: str = Depends(current_visitor)):
    with closing(_connect()) as conn:
        _owned(conn, task_id, owner)
        row = conn.execute("SELECT result_json FROM strategic_assessment_run WHERE id=? AND assessment_id=?", (run_id, task_id)).fetchone()
        if not row:
            problem(404, "RUN_NOT_FOUND", "该版本不属于此研判")
        return _reply({**json.loads(row[0]), "selection": _selection(conn, run_id)})


@router.get("/assessments/{task_id}/export")
def export_assessment(task_id: str, run_id: str | None = None, owner: str = Depends(current_visitor)):
    with closing(_connect()) as conn:
        assessment = _result(conn, _owned(conn, task_id, owner))
        selected = run_id or assessment["state"].get("activeRunId")
        if not selected:
            problem(409, "NO_SAVED_RUN", "请先确认条件并生成推荐")
        row = conn.execute("SELECT result_json FROM strategic_assessment_run WHERE id=? AND assessment_id=?", (selected, task_id)).fetchone()
        if not row:
            problem(404, "RUN_NOT_FOUND", "该版本不属于此研判")
        # An allowlist rather than copying assessment state: no internal notes, names, contact records or follow-up results.
        return _reply({"taskId": task_id, "title": assessment["title"], "run": json.loads(row[0]),
                       "evidenceCutoff": json.loads(row[0])["createdAt"], "exportPolicy": "public-evidence-only-v1"})


@router.get("/assessments/{task_id}/audit")
def assessment_audit(task_id: str, owner: str = Depends(current_visitor)):
    with closing(_connect()) as conn:
        _owned(conn, task_id, owner)
        return _reply([dict(r) for r in conn.execute("SELECT action,revision,created_at AS createdAt FROM strategic_assessment_audit WHERE assessment_id=? ORDER BY revision,id", (task_id,))])


@router.get("/assessments/{task_id}/follow-up-history")
def follow_up_history(task_id: str, before_revision: int | None = Query(None, ge=1),
                      owner: str = Depends(current_visitor)):
    """Project private follow-up changes from immutable edit audits, newest first."""
    with closing(_connect()) as conn:
        _owned(conn, task_id, owner)
        query = """SELECT revision,created_at,payload_json FROM strategic_assessment_audit
            WHERE assessment_id=? AND action='edited'"""
        args: list = [task_id]
        if before_revision is not None:
            query += " AND revision<?"
            args.append(before_revision)
        rows = conn.execute(query + " ORDER BY revision DESC,id DESC", args)
        events = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            old = {v["id"]: v for v in payload.get("before", {}).get("followUps", [])}
            new = {v["id"]: v for v in payload.get("after", {}).get("followUps", [])}
            changes = []
            for follow_id in sorted(old.keys() | new.keys()):
                before, after = old.get(follow_id), new.get(follow_id)
                if before == after:
                    continue
                changes.append({"followUpId": follow_id,
                    "kind": "added" if before is None else "removed" if after is None else "updated",
                    "changedFields": sorted(k for k in (before or {}).keys() | (after or {}).keys()
                                            if (before or {}).get(k) != (after or {}).get(k)),
                    "before": before, "after": after})
            if changes:
                events.append({"revision": row["revision"], "createdAt": row["created_at"],
                               "runId": payload.get("after", {}).get("activeRunId") or payload.get("before", {}).get("activeRunId"),
                               "changes": changes})
            if len(events) > 20:
                break
        more = len(events) > 20
        events = events[:20]
        wanted = {(snapshot["teamId"], claim_id) for event in events for change in event["changes"]
                  for snapshot in (change["after"] or change["before"],)
                  for claim_id in snapshot.get("claimIds", [])}
        evidence, fallback = {}, {}
        if wanted:
            for saved in conn.execute("SELECT id,result_json FROM strategic_assessment_run WHERE assessment_id=? ORDER BY created_at DESC,id DESC", (task_id,)):
                run = json.loads(saved["result_json"])
                for item in run.get("items", []):
                    for citation in item.get("citations", []):
                        key = item["teamId"], citation["id"]
                        if key in wanted:
                            source = {"claimId": citation["id"], "runId": saved["id"],
                                "inputVersion": run.get("inputVersion"), "text": citation["text"],
                                "quote": citation["quote"], "url": citation["url"]}
                            evidence[(saved["id"], key[0], key[1])] = source
                            fallback.setdefault(key, source)
        for event in events:
            for change in event["changes"]:
                snapshot = change["after"] or change["before"]
                change["evidence"] = [evidence.get((event["runId"], snapshot["teamId"], claim_id))
                    or fallback.get((snapshot["teamId"], claim_id)) or {"claimId": claim_id, "missing": True}
                    for claim_id in snapshot.get("claimIds", [])]
        return _reply({"events": events, "nextBeforeRevision": events[-1]["revision"] if more else None})
