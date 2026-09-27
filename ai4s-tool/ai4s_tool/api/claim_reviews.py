"""Versioned human decisions on individual public claims, never whole-team approval."""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from contextlib import closing
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import strategic_access as access, strategic_changes as changes
from .strategic_identity import current_visitor, problem

router = APIRouter(prefix="/strategic-map", tags=["claim_reviews"])
VERSION = "claim-review-v1"
Decision = Literal["supported", "conditional", "insufficient", "conflict", "withdrawn"]
LABELS = {"supported": "支持", "conditional": "条件支持", "insufficient": "证据不足",
          "conflict": "存在冲突", "withdrawn": "撤回", "unreviewed": "恢复来源检查状态"}


def init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS strategic_claim_review (
        id TEXT PRIMARY KEY, claim_id TEXT NOT NULL, team_id TEXT NOT NULL, revision INTEGER NOT NULL,
        fingerprint TEXT NOT NULL, source_json TEXT NOT NULL, decision TEXT NOT NULL,
        scope TEXT NOT NULL, reason TEXT NOT NULL, actor_id TEXT NOT NULL, signed_name TEXT NOT NULL,
        created_at TEXT NOT NULL, link_status TEXT NOT NULL, related_json TEXT NOT NULL,
        previous_id TEXT, reverted_id TEXT, request_id TEXT NOT NULL, request_hash TEXT NOT NULL,
        UNIQUE(claim_id, revision), UNIQUE(actor_id,request_id))""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_claim_review_team ON strategic_claim_review(team_id,created_at)")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now():
    return datetime.now(timezone.utc).isoformat()


def _db(write=False):
    from .task_recommendations import _db as connect
    return connect(write=write)


def _exists(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name='strategic_claim_review'").fetchone() is not None


def _latest(conn):
    if not _exists(conn):
        return {}
    return {r["claim_id"]: dict(r) for r in conn.execute("""SELECT r.* FROM strategic_claim_review r
        JOIN (SELECT claim_id,MAX(revision) AS rev FROM strategic_claim_review GROUP BY claim_id) v
        ON r.claim_id=v.claim_id AND r.revision=v.rev""")}


def source(claim, team):
    metadata = copy.deepcopy(team.get("claimProvenance", {}).get(claim[0]) or {})
    value = {"id": claim[0], "teamId": claim[1], "sourceRunId": claim[2], "kind": claim[3],
             "text": claim[4], "quote": claim[5], "url": claim[6], "publishedAt": claim[7], "provenance": metadata}
    # Identical content collected again must not invalidate a human decision.
    # Bind it to the actual claim/body version, excluding run IDs and check times.
    content = {key: value[key] for key in ("id", "teamId", "kind", "text", "quote", "url", "publishedAt")}
    content.update(contentHash=metadata.get("contentHash", ""), quoteHash=metadata.get("quoteHash", ""))
    fingerprint = hashlib.sha256(_json(content).encode()).hexdigest()
    return {**value, "fingerprint": fingerprint}


def _raw():
    from .task_recommendations import _catalogue_evidence
    teams, claims, version = _catalogue_evidence(reviewed=False)
    by_team = {t["id"]: t for t in teams}
    return {c[0]: source(c, by_team[c[1]]) for c in claims}, by_team, version


def _public_review(row, *, stale=False):
    if not row:
        return {"status": "not_recorded", "reviewedAt": "", "reviewer": ""}
    return {"status": "source_changed" if stale else ("not_recorded" if row["decision"] == "unreviewed" else "reviewed"),
            "decision": row["decision"], "scope": row["scope"], "reason": row["reason"],
            "reviewedAt": row["created_at"], "reviewer": row["signed_name"],
            "reviewId": row["id"], "revision": row["revision"], "sourceFingerprint": row["fingerprint"],
            "relatedEvidence": json.loads(row["related_json"]), "revertedReviewId": row["reverted_id"]}


def overlay(teams, claims, version, conn):
    """Keep source records immutable. Apply only current-version human decisions."""
    latest = _latest(conn)
    if not latest:
        return teams, claims, version
    result = copy.deepcopy(teams)
    by_team = {t["id"]: t for t in result}
    retained, touched, stamps, removed = [], set(), [], {}
    for claim in claims:
        row = latest.get(claim[0])
        if not row:
            retained.append(claim)
            continue
        team = by_team[claim[1]]
        snapshot = source(claim, team)
        stale = snapshot["fingerprint"] != row["fingerprint"]
        metadata = team.setdefault("claimProvenance", {}).setdefault(claim[0], {})
        metadata["humanReview"] = _public_review(row, stale=stale)
        metadata["linkCheck"] = {"status": row["link_status"] if not stale else "not_checked",
            "checkedAt": row["created_at"] if row["link_status"] != "not_checked" and not stale else "",
            "method": "reviewer_report", "reviewer": row["signed_name"]}
        touched.add(claim[1]); stamps.append(row["id"])
        if not stale and row["decision"] in {"supported", "conditional", "unreviewed"}:
            retained.append(claim)
        else:
            removed.setdefault(claim[1], []).append(claim)
    # Do not continue displaying removed claims through a summary field.
    for team_id in touched:
        team = by_team[team_id]
        facts = [c for c in retained if c[1] == team_id]
        excluded = removed.get(team_id, [])
        if any(c[3] == "description" or c[4] in team.get("description", "") or c[5] in team.get("description", "") for c in excluded):
            team["description"] = "；".join(dict.fromkeys(c[4] for c in facts if c[3] == "description"))
        if any(c[3] == "direction" for c in excluded):
            team["researchDirections"] = list(dict.fromkeys(c[4] for c in facts if c[3] == "direction"))
            team["focus"] = "、".join(team["researchDirections"])
            if team.get("coreDirectionSource") != "manual":
                team["coreDirection"] = team["focus"]
        team["evidenceSummary"] = "\n".join(dict.fromkeys(c[5] for c in facts))
        team["evidenceUrls"] = list(dict.fromkeys(c[6] for c in facts))
    updated = hashlib.sha256((version + VERSION + "|".join(sorted(stamps))).encode()).hexdigest()[:16]
    return result, retained, updated


def current_reviewer(visitor: str = Depends(current_visitor)):
    return access.authorized(visitor, "review:write")


class RelatedEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=8, max_length=2048)
    quote: str = Field(min_length=2, max_length=3000)

    @field_validator("url")
    @classmethod
    def http_url(cls, value):
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            raise ValueError("对照资料须为不含凭证的 HTTP(S) 原文地址")
        return value


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    requestId: str = Field(min_length=8, max_length=128)
    expectedRevision: int = Field(ge=0)
    sourceFingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    signedName: str = Field(min_length=1, max_length=80)
    decision: Decision
    scope: str = Field(min_length=2, max_length=500)
    reason: str = Field(min_length=5, max_length=2000)
    linkStatus: Literal["not_checked", "available", "unavailable"] = "not_checked"
    relatedEvidence: list[RelatedEvidence] = Field(default_factory=list, max_length=5)


class RevertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    requestId: str = Field(min_length=8, max_length=128)
    expectedRevision: int = Field(ge=1)
    sourceFingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    signedName: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=5, max_length=2000)


def _view(value, team, row, *, available=True):
    stale = bool(row and (not available or row["fingerprint"] != value["fingerprint"]))
    return {"claim": value, "teamName": team.get("teamName", ""), "available": available,
            "revision": row["revision"] if row else 0, "review": _public_review(row, stale=stale),
            "linkStatus": row["link_status"] if row and not stale else "not_checked"}


@router.get("/claim-reviews")
def queue(team_id: str | None = None, status: str = "all", page: int = Query(1, ge=1),
          reviewer: str = Depends(current_reviewer)):
    sources, teams, version = _raw()
    with closing(_db()) as conn:
        latest = _latest(conn)
    rows = []
    # A new quote may have a different stable claim ID. Keep the previous human
    # decision discoverable as a source-change item, never transfer its approval.
    queue_sources = {**{key: json.loads(row["source_json"]) for key, row in latest.items()}, **sources}
    for key, claim in queue_sources.items():
        if team_id and claim["teamId"] != team_id:
            continue
        item = _view(claim, teams.get(claim["teamId"], {}), latest.get(key), available=key in sources)
        review = item["review"]
        bucket = review["status"] if review["status"] != "reviewed" else review["decision"]
        if status != "all" and bucket != status:
            continue
        rows.append(item)
    rows.sort(key=lambda v: (v["teamName"], v["claim"]["id"]))
    queue_version = hashlib.sha256((version + "|".join(sorted(r["id"] for r in latest.values()))).encode()).hexdigest()[:16]
    return {"data": {"items": rows[(page - 1) * 20:page * 20], "total": len(rows), "page": page,
                     "pageSize": 20, "dataVersion": queue_version}}


@router.get("/claims/{claim_id}/review")
def read_review(claim_id: str, reviewer: str = Depends(current_reviewer)):
    sources, teams, _ = _raw()
    with closing(_db()) as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM strategic_claim_review WHERE claim_id=? ORDER BY revision DESC", (claim_id,))] if _exists(conn) else []
    latest = rows[0] if rows else None
    value = sources.get(claim_id) or (json.loads(latest["source_json"]) if latest else None)
    if value is None:
        problem(404, "CLAIM_NOT_FOUND", "未找到此条原文证据")
    view = _view(value, teams.get(value["teamId"], {}), latest, available=claim_id in sources)
    view["history"] = [{**_public_review(r), "actorId": r["actor_id"], "linkStatus": r["link_status"]} for r in rows[:50]]
    return {"data": view}


def _write(claim_id, body, actor, revert_id=None):
    request_hash = hashlib.sha256(_json({"claimId": claim_id, "revert": revert_id, **body.model_dump()}).encode()).hexdigest()
    with closing(_db(True)) as conn, conn:
        init(conn); access.init(conn); changes.init(conn)
        conn.execute("BEGIN IMMEDIATE")
        replay = conn.execute("SELECT * FROM strategic_claim_review WHERE actor_id=? AND request_id=?", (actor, body.requestId)).fetchone()
        if replay:
            if replay["request_hash"] != request_hash:
                problem(409, "REQUEST_CONFLICT", "同一提交编号不能用于不同的审核内容")
            return {"data": _public_review(replay)}
        sources, teams, _ = _raw()
        value = sources.get(claim_id)
        if not value:
            problem(409, "SOURCE_WITHDRAWN", "当前来源已退出公开资料；请先核查团队及成果归属，不能用主张审核恢复整个团队")
        if value["fingerprint"] != body.sourceFingerprint:
            problem(409, "SOURCE_CHANGED", "原文版本已更新，请重新读取后核验")
        previous = conn.execute("SELECT * FROM strategic_claim_review WHERE claim_id=? ORDER BY revision DESC LIMIT 1", (claim_id,)).fetchone()
        revision = previous["revision"] if previous else 0
        if revision != body.expectedRevision:
            problem(409, "REVIEW_CONFLICT", "其他审核人员已更新此主张；本次未覆盖，请先查看最新记录")
        if revert_id:
            if not previous or previous["id"] != revert_id or previous["fingerprint"] != value["fingerprint"]:
                problem(409, "ROLLBACK_CONFLICT", "只能撤销当前原文版本的最新审核，不能覆盖后续修改")
            restored = conn.execute("SELECT * FROM strategic_claim_review WHERE id=?", (previous["previous_id"],)).fetchone()
            if restored and restored["fingerprint"] != value["fingerprint"]:
                problem(409, "ROLLBACK_SOURCE_CHANGED", "前一审核对应不同原文，不能直接恢复")
            decision, scope, link, related = (restored["decision"], restored["scope"], restored["link_status"], restored["related_json"]) if restored else ("unreviewed", "恢复来源规则和模型检查；未获人工确认", "not_checked", "[]")
        else:
            decision, scope, link, related = body.decision, body.scope, body.linkStatus, _json([e.model_dump() for e in body.relatedEvidence])
        key, stamp = "claim-review-" + uuid.uuid4().hex, _now()
        conn.execute("INSERT INTO strategic_claim_review VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            key, claim_id, value["teamId"], revision + 1, value["fingerprint"], _json(value), decision,
            scope, body.reason, actor, body.signedName, stamp, link, related,
            previous["id"] if previous else None, revert_id, body.requestId, request_hash))
        access.audit(conn, actor=actor, operation="claim_review_revert" if revert_id else "claim_review",
                     resource_id=claim_id, payload={"reviewId": key, "previousId": previous["id"] if previous else None,
                     "decision": decision, "revision": revision + 1})
        changes.record(conn, subject_type="team", subject_id=value["teamId"],
            domain_id=teams[value["teamId"]].get("domainId"), kind="claim_review", source_id=key,
            payload={"claimId": claim_id, "decision": decision, "reason": "逐条证据审核更新：" + LABELS[decision]})
        return {"data": _public_review(conn.execute("SELECT * FROM strategic_claim_review WHERE id=?", (key,)).fetchone())}


@router.post("/claims/{claim_id}/reviews")
def review(claim_id: str, body: ReviewRequest, reviewer: str = Depends(current_reviewer)):
    return _write(claim_id, body, reviewer)


@router.post("/claims/{claim_id}/reviews/{review_id}/revert")
def revert(claim_id: str, review_id: str, body: RevertRequest, reviewer: str = Depends(current_reviewer)):
    return _write(claim_id, body, reviewer, review_id)
