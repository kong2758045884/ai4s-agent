"""Server-granted maintenance roles; visitor names and request IDs are not roles.

Only the server CLI can grant roles. Public reads and private owned assessments
remain available to ordinary visitors. All legacy maintenance HTTP routes fail
closed, including reads with refresh=true. No role is granted during startup.
"""
from __future__ import annotations

import json
import uuid
from contextlib import closing
from contextvars import ContextVar
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from . import strategic_identity as identity

router = APIRouter(prefix="/strategic-map", tags=["strategic_access"])
ACTOR = ContextVar("strategic_maintenance_actor", default=None)
ACTION = ContextVar("strategic_maintenance_action", default="")
PERMISSIONS = {
    "maintainer": {"team:write", "internal:read", "review:write", "collection:run", "maintenance:run"},
    "editor": {"team:write", "internal:read"},
    "reviewer": {"team:write", "internal:read", "review:write"},
}
PRIVATE_FIELDS = {"contactRecord", "internalReview", "nextAction", "contact", "attention", "recentUpdate"}


def init(conn, *, include_roles=True):
    # Individual execute preserves the caller's transaction (no executescript).
    if include_roles:
        conn.execute("""CREATE TABLE IF NOT EXISTS strategic_maintenance_role (
            visitor_id TEXT PRIMARY KEY, role TEXT NOT NULL, assigned_by TEXT NOT NULL,
            reason TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS strategic_maintenance_audit (
        id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, operation TEXT NOT NULL,
        resource_id TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL)""")


def audit(conn, *, actor, operation, resource_id, payload):
    conn.execute("""INSERT INTO strategic_maintenance_audit VALUES (?,?,?,?,?,?)""",
        (uuid.uuid4().hex, actor, operation, resource_id,
         json.dumps(payload, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))


def grant(conn, visitor_id, role, *, operator, reason):
    if not visitor_id or len(visitor_id) > 128 or role not in {*PERMISSIONS, "revoked"} or not reason.strip() or not operator.strip():
        raise ValueError("角色、访客编号、操作者及授予／撤销原因均须有效")
    init(conn)
    previous = conn.execute("SELECT role FROM strategic_maintenance_role WHERE visitor_id=?", (visitor_id,)).fetchone()
    conn.execute("""INSERT INTO strategic_maintenance_role VALUES (?,?,?,?,?)
        ON CONFLICT(visitor_id) DO UPDATE SET role=excluded.role,assigned_by=excluded.assigned_by,
        reason=excluded.reason,updated_at=excluded.updated_at""",
        (visitor_id, role, operator, reason, datetime.now(timezone.utc).isoformat()))
    audit(conn, actor=operator, operation="role_grant" if role != "revoked" else "role_revoke", resource_id=visitor_id,
          payload={"before": previous[0] if previous else None, "after": role, "reason": reason})


def capabilities(visitor):
    from .task_recommendations import _db, _has_table
    with closing(_db()) as conn:
        row = conn.execute("SELECT role FROM strategic_maintenance_role WHERE visitor_id=?", (visitor,)).fetchone() \
            if _has_table(conn, "strategic_maintenance_role") else None
    role = row[0] if row and row[0] in PERMISSIONS else "visitor"
    return {"role": role, "permissions": sorted(PERMISSIONS.get(role, set()))}


def authorized(visitor, permission):
    if permission not in capabilities(visitor)["permissions"]:
        identity.problem(403, "MAINTENANCE_ROLE_REQUIRED", "当前访客没有此维护权限；公开查询和自己的研判不受影响")
    return visitor


async def maintenance_guard(request: Request):
    """Mounted only on legacy public routers, not private assessment routers."""
    name = request.scope["route"].name
    if request.method in {"GET", "HEAD"}:
        if request.query_params.get("refresh", "").lower() in {"1", "true", "yes", "on"}:
            raise HTTPException(405, "读取接口不启动采集；请使用有权限的显式更新操作")
        if name not in {"reviews", "audits", "classification_reviews"}:
            yield
            return
        permission = "review:write"
    elif request.method == "OPTIONS" or name in {"recommend", "graph_search"}:
        yield
        return
    else:
        permission = "review:write" if name in {"submit_classification_review", "revert_classification_review"} else \
            "team:write" if name == "update_team_status" else \
            "review:write" if "/impact-triage/" in request.url.path else \
            "collection:run" if name in {"start_domain_refresh", "start_nationwide_refresh", "sync_domain", "start_graph_scan",
                                         "expand_recommendation", "retry_expansion", "graph_chat"} else "maintenance:run"
    visitor = await run_in_threadpool(identity.current_visitor, request)
    await run_in_threadpool(authorized, visitor, permission)
    token = ACTOR.set(visitor)
    action_token = ACTION.set(request.method + " " + request.url.path)
    try:
        yield
    finally:
        ACTOR.reset(token)
        ACTION.reset(action_token)


def current_editor(visitor: str = Depends(identity.current_visitor)):
    return authorized(visitor, "internal:read")


@router.get("/access")
def access(visitor: str = Depends(identity.current_visitor)):
    return {"data": {**capabilities(visitor), "visitorId": visitor,
        "notice": "维护角色仅由服务器管理员授予；修改访客昵称不会获得权限。"}}


@router.get("/teams/{team_id}/internal")
def internal_team(team_id: str, visitor: str = Depends(current_editor)):
    from . import strategic_map as sm
    from .team_research_store import history
    with sm._SESSION_FACTORY() as session:
        team = session.query(sm.StrategicTeamRow).filter_by(id=team_id, deleted=False).first()
        if not team:
            raise HTTPException(404, "团队不存在")
        payload = sm._team_to_dict(team, session, include_private=True)
        # Private manual history stays behind the same role boundary.
        changes = [{"id": row["id"], "createdAt": row["created_at"].isoformat(),
                    "actorRecorded": bool(row["payload"].get("actorId")),
                    "actorId": row["payload"].get("actorId"),
                    "fields": row["payload"].get("fields", []),
                    "before": row["payload"].get("before", {}),
                    "changes": row["payload"].get("changes", {})}
                   for row in history(session, team_id, "manual")[:50]]
        return {"data": {"teamId": team_id, "updatedAt": payload["updatedAt"],
            "fields": {k: payload.get(k) for k in PRIVATE_FIELDS}, "audit": changes}}
