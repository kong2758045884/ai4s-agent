"""Refresh only evidence-dependent private assessments, without public task leakage."""
from __future__ import annotations

import json
import uuid
from contextlib import closing

from fastapi import APIRouter, Depends, Query

from . import strategic_assessments as store, task_recommendations as tasks, strategic_text
from .strategic_identity import current_visitor

router = APIRouter(prefix="/strategic-map", tags=["assessment_updates"])


def init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS strategic_assessment_update (
      id TEXT PRIMARY KEY, assessment_id TEXT NOT NULL REFERENCES strategic_assessment(id),
      parent_run_id TEXT NOT NULL REFERENCES strategic_assessment_run(id),
      run_id TEXT REFERENCES strategic_assessment_run(id), evidence_version TEXT NOT NULL,
      reason TEXT NOT NULL, change_ids_json TEXT NOT NULL, created_at TEXT NOT NULL,
      UNIQUE(parent_run_id,evidence_version))""")


def affected(run, events, teams, claims):
    """An existing dependency or a newly relevant team can change a recommendation."""
    selected = {t["teamId"] for t in run["items"]}
    domain_ids = set(run["scope"]["domainIds"])
    changed = {e["subject_id"] for e in events if e["subject_type"] == "team"}
    if selected & changed:
        return True
    institutions = {t.get("institutionId") for t in run["items"] if t.get("institutionId")}
    institutions.update(link["id"] for team_id, link in tasks._institution_links().items() if team_id in selected)
    if any(e["subject_id"] in institutions for e in events if e["subject_type"] == "institution"):
        return True
    wanted = run.get("parsedTask", {}).get("goals", []) + run.get("parsedTask", {}).get("required", [])
    by_team = {}
    for claim in claims:
        by_team.setdefault(claim[1], []).append(str(claim[4]) + " " + str(claim[5]))
    for team in teams:
        if team["id"] not in changed or (domain_ids and team["domainId"] not in domain_ids):
            continue
        if run["scope"].get("subdomainId") and team.get("subdomainId") != run["scope"]["subdomainId"]:
            continue
        haystack = strategic_text.normalize(" ".join(by_team.get(team["id"], []) + [team.get("focus", ""), team.get("description", "")]))
        if any(term in haystack for term in wanted):
            return True
    return False


def refresh(task_id, parent_run_id, *, change_ids=None, reason="核验后的团队证据更新", force=False):
    """A child keeps its original input version. A changed user input wins the race."""
    with closing(store._connect()) as conn:
        init(conn); conn.commit()
        parent_row = conn.execute("SELECT * FROM strategic_assessment_run WHERE id=? AND assessment_id=?", (parent_run_id, task_id)).fetchone()
        if not parent_row:
            return None
        previous = json.loads(parent_row["result_json"])
        if previous.get("mode") == "domain":
            return None
        source_version = tasks._candidate_evidence()[2]
        version = tasks._recommendation_version(source_version, tasks._institution_links())
        if not force and previous["dataVersion"] == version:
            return None
        evaluated = conn.execute("SELECT run_id FROM strategic_assessment_update WHERE parent_run_id=? AND evidence_version=?", (parent_run_id, version)).fetchone()
        if evaluated:
            return evaluated[0]
    scope = previous["scope"]
    result = tasks._recommend(tasks.TaskRequest(taskText=previous["taskText"][:500],
        domainId=scope["domainIds"][0] if len(scope["domainIds"]) == 1 else None,
        subdomainId=scope.get("subdomainId"), limit=previous["requestedLimit"]), persist=False,
        confirmed_criteria=previous["parsedTask"], domain_ids=scope["domainIds"])
    if previous.get("unresolvedConditions"):
        result.update(items=[], matchedTeamCount=0, shortfall=previous["requestedLimit"])
    result.update({k: previous[k] for k in ("taskText", "taskId", "scope", "domainIds", "criteria", "inputVersionId", "inputVersion", "matchVersion", "unresolvedConditions") if k in previous})
    result.update(evidenceVersion=source_version, previousRunId=parent_run_id)
    for item in result["items"]:
        item["matchVersion"] = result["matchVersion"]
        item["criteriaMatrix"] = store._matrix(previous["criteria"], item)
    diff = store._diff(previous, result)
    diff["reason"] = reason
    diff["triggerChangeIds"] = sorted(change_ids or [])
    result["changes"] = diff
    changed = bool(diff["added"] or diff["removed"] or diff["updated"])
    with closing(store._connect()) as conn, conn:
        init(conn)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM strategic_assessment WHERE id=?", (task_id,)).fetchone()
        state = json.loads(row["state_json"])
        if state.get("activeRunId") != parent_run_id:
            return None  # A newer user-confirmed assessment must not be overwritten.
        evaluated = conn.execute("SELECT run_id FROM strategic_assessment_update WHERE parent_run_id=? AND evidence_version=?", (parent_run_id, version)).fetchone()
        if evaluated:
            return evaluated[0]
        if tasks._candidate_evidence()[2] != source_version:
            raise RuntimeError("研判重算期间证据发生变化，稍后重试")
        run_id = result["runId"] if changed else None
        if changed:
            conn.execute("INSERT INTO strategic_assessment_run VALUES(?,?,?,?,?,?)", (run_id, task_id,
                parent_row["input_id"], parent_run_id, store._json(result), result["createdAt"]))
            retained = {i["teamId"] for i in result["items"]}
            state["comparedTeamIds"] = [i for i in state["comparedTeamIds"] if i in retained]
            state["combination"] = [role for role in state["combination"] if role["teamId"] in retained]
            conn.execute("INSERT INTO strategic_assessment_run_selection VALUES(?,?)", (run_id, store._json({k: state[k] for k in ("comparedTeamIds", "combination")})))
            state["activeRunId"] = run_id
            conn.execute("UPDATE strategic_assessment SET revision=revision+1,state_json=?,updated_at=? WHERE id=?", (store._json(state), store._now(), task_id))
            updated = conn.execute("SELECT * FROM strategic_assessment WHERE id=?", (task_id,)).fetchone()
            store._audit(conn, updated, "system:evidence-worker", "evidence_updated", diff)
        conn.execute("INSERT INTO strategic_assessment_update VALUES(?,?,?,?,?,?,?,?)", (
            "update-" + uuid.uuid4().hex, task_id, parent_run_id, run_id, version, reason,
            store._json(sorted(change_ids or [])), store._now()))
        return run_id


def process(events):
    with closing(tasks._db()) as conn:
        if not tasks._has_table(conn, "strategic_assessment"):
            return {"evaluated": 0, "updated": 0}
        active = []
        for record in conn.execute("SELECT id,state_json FROM strategic_assessment"):
            run_id = json.loads(record["state_json"]).get("activeRunId")
            if run_id:
                row = conn.execute("SELECT result_json FROM strategic_assessment_run WHERE id=?", (run_id,)).fetchone()
                if row and json.loads(row[0]).get("mode") != "domain":
                    active.append((record["id"], json.loads(row[0])))
    if not active:
        return {"evaluated": 0, "updated": 0}
    teams, claims, _ = tasks._candidate_evidence()
    evaluated, updated = 0, 0
    for task_id, run in active:
        if affected(run, events, teams, claims):
            evaluated += 1
            reasons = list(dict.fromkeys(json.loads(e["payload_json"]).get("reason", "核验后的团队证据更新") for e in events
                                       if e["subject_id"] in {i["teamId"] for i in run["items"]}))
            updated += bool(refresh(task_id, run["runId"], change_ids=[e["id"] for e in events],
                                    reason="；".join(reasons[:5]) or "核验后的团队证据更新"))
    return {"evaluated": evaluated, "updated": updated}


@router.get("/assessment-updates")
def updates(task_id: str | None = None, page: int = Query(1, ge=1), owner: str = Depends(current_visitor)):
    with closing(store._connect()) as conn:
        init(conn); conn.commit()
        if task_id:
            store._owned(conn, task_id, owner)
        where = "a.owner_id=? AND u.run_id IS NOT NULL" + (" AND a.id=?" if task_id else "")
        args = [owner] + ([task_id] if task_id else [])
        total = conn.execute(f"SELECT COUNT(*) FROM strategic_assessment_update u JOIN strategic_assessment a ON a.id=u.assessment_id WHERE {where}", args).fetchone()[0]
        rows = conn.execute(f"""SELECT u.*,a.title,r.result_json FROM strategic_assessment_update u
          JOIN strategic_assessment a ON a.id=u.assessment_id JOIN strategic_assessment_run r ON r.id=u.run_id
          WHERE {where} ORDER BY u.created_at DESC,u.id LIMIT 20 OFFSET ?""", [*args, (page - 1) * 20])
        return store._reply({"items": [{"id": r["id"], "taskId": r["assessment_id"], "title": r["title"],
            "beforeRunId": r["parent_run_id"], "afterRunId": r["run_id"], "reason": r["reason"], "createdAt": r["created_at"],
            "changes": json.loads(r["result_json"])["changes"]} for r in rows], "total": total, "page": page})
