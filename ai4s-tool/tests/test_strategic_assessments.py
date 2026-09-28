"""Teacher A02/A03/A06/A09/A11/A12/A16: persisted and authorized assessment boundaries."""
import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai4s_tool.api import strategic_assessments as service
from ai4s_tool.api import strategic_identity as identity
from ai4s_tool.api import task_recommendations as tasks


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    path = tmp_path / "assessment.db"

    def db(*, write=False):
        conn = sqlite3.connect(path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(tasks, "_db", db)
    monkeypatch.setattr(tasks, "_validate_scope", lambda *_: None)
    monkeypatch.setattr(tasks, "_institution_links", lambda: {})
    monkeypatch.setattr(service, "_domains", lambda: [
        {"id": "life", "name": "生命科学与医学", "parent_id": None},
        {"id": "quantum", "name": "高能物理与量子科技", "parent_id": None},
        {"id": "qsub", "name": "量子计算", "parent_id": "quantum"},
    ])
    team = {"id": "t1", "teamName": "蛋白质研究组", "name": "大学甲", "institutionName": "大学甲",
            "domainId": "life", "domainName": "生命科学与医学", "subdomainId": None,
            "researchDirections": ["蛋白质"], "scoreTotal": 82}
    claims = [("c1", "t1", "s1", "outcome", "蛋白质论文", "该研究组发表蛋白质相关论文", "https://example.org/protein", "2026-09-20")]
    version = ["evidence-v1"]
    monkeypatch.setattr(tasks, "_candidate_evidence", lambda: ([team], claims, version[0]))
    monkeypatch.setattr(tasks, "_catalogue_evidence", lambda: ([team], claims, version[0]))
    app = FastAPI()
    app.include_router(service.router)
    app.include_router(tasks.router)
    current = ["visitor-a"]
    app.dependency_overrides[identity.current_visitor] = lambda: current[0]
    with TestClient(app) as client:
        yield client, db, current, version


def create(client, **fields):
    res = client.post("/strategic-map/assessments", json={"requestId": "create-001", **fields})
    assert res.status_code == 201, res.text
    return res.json()["data"]


def parsed(client, task="蛋白质", scope=None):
    response = client.post("/strategic-map/task-interpretations", json={"taskText": task,
        "scope": scope or {"mode": "auto"}})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def confirm(client, record, interpretation=None, **fields):
    p = interpretation or parsed(client)
    data = {"requestId": "confirm-001", "expectedRevision": record["revision"],
        "taskText": p["taskText"], "scope": p["scope"], "criteria": p["criteria"], "evidenceVersion": p["evidenceVersion"], **fields}
    return client.post(f"/strategic-map/assessments/{record['taskId']}/confirm", json=data)


def test_private_save_resume_compare_and_export_do_not_leak(app_client):
    client, db, user, _ = app_client
    record = create(client, taskDraft="蛋白质", domainDraft="量子观察")
    response = confirm(client, record)
    assert response.status_code == 200, response.text
    record = response.json()["data"]
    run = record["run"]
    assert run["requestedLimit"] == 5 and run["shortfall"] == 4
    assert run["coverage"] == {"scopeTeamCount": 1, "outcomeBackedTeamCount": 1,
                               "conditionMatchedTeamCount": 1}
    assert run["domainIds"] == ["life"]
    assert run["items"][0]["criteriaMatrix"][0]["claimIds"] == ["c1"]
    response = client.patch(f"/strategic-map/assessments/{record['taskId']}", json={
        "requestId": "patch-001", "expectedRevision": record["revision"], "comparedTeamIds": ["t1"],
        "internalNotes": "只限内部讨论的备注", "combination": [{"teamId": "t1", "role": "蛋白质分析", "rationale": "c1成果"}],
        "followUps": [{"id": "f1", "teamId": "t1", "claimIds": ["c1"], "question": "能否提供实验数据", "owner": "内部负责人"}]})
    assert response.status_code == 200, response.text
    restored = client.get(f"/strategic-map/assessments/{record['taskId']}").json()["data"]
    assert restored["state"]["comparedTeamIds"] == ["t1"]
    assert restored["state"]["domainDraft"] == "量子观察"
    export = client.get(f"/strategic-map/assessments/{record['taskId']}/export")
    assert "只限内部" not in export.text and "内部负责人" not in export.text
    assert export.json()["data"]["run"] == {k: v for k, v in run.items() if k != "selection"}
    assert client.get(f"/strategic-map/task-recommendations/{run['runId']}").status_code == 404
    user[0] = "visitor-b"
    for suffix in ("", "/export", "/audit", f"/runs/{run['runId']}"):
        assert client.get(f"/strategic-map/assessments/{record['taskId']}{suffix}").status_code == 404
    assert client.get("/strategic-map/assessments").json()["data"]["total"] == 0


def test_duplicate_submit_and_optimistic_conflict(app_client):
    client, db, _, _ = app_client
    record = create(client)
    assert create(client) == record
    first = confirm(client, record)
    second = confirm(client, record)
    assert first.json() == second.json()
    res = confirm(client, record, requestId="confirm-002")
    assert res.status_code == 409 and res.json()["detail"]["code"] == "REVISION_CONFLICT"
    # A different payload cannot silently reuse the same request ID.
    assert client.post("/strategic-map/assessments", json={"requestId": "create-001", "title": "different"}).status_code == 409
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM strategic_assessment_run").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_source_metadata_is_frozen_with_the_claim_not_replaced_on_history_read(app_client):
    client, _, _, version = app_client
    team = tasks._candidate_evidence()[0][0]
    team["claimProvenance"] = {"c1": {"fetchedAt": "2026-09-20", "sourceTitle": "当时的原文标题"}}
    record = confirm(client, create(client)).json()["data"]
    old_run = record["run"]
    assert old_run["items"][0]["citations"][0]["provenance"]["fetchedAt"] == "2026-09-20"
    team["claimProvenance"]["c1"] = {"fetchedAt": "2026-09-27", "sourceTitle": "后来更新的标题"}
    version[0] = "changed-source"
    historical = client.get(f"/strategic-map/assessments/{record['taskId']}/runs/{old_run['runId']}").json()["data"]
    exported = client.get(f"/strategic-map/assessments/{record['taskId']}/export").json()["data"]
    assert historical == old_run
    assert exported["run"]["items"][0]["citations"][0]["provenance"]["sourceTitle"] == "当时的原文标题"


def test_long_constraints_original_spans_block_unproven_conditions(app_client):
    client, _, _, _ = app_client
    record = create(client)
    p = parsed(client, "请找蛋白质研究团队，必须在6个月内交付，预算不超过50万元，优先开源代码，排除临床试验")
    assert len(p["criteria"]) == 5
    for c in p["criteria"]:
        span = c["sourceSpan"]
        assert p["taskText"][span["start"]:span["end"]] == c["text"]
    run = confirm(client, record, p).json()["data"]["run"]
    assert run["items"] == []
    assert len(run["unresolvedConditions"]) == 2
    assert run["coverage"]["conditionMatchedTeamCount"] == 0
    assert "可投入" not in json.dumps(run["criteria"], ensure_ascii=False)


def test_shortfall_keeps_scope_outcome_and_condition_stages_separate(app_client, monkeypatch):
    client, _, _, version = app_client
    backed, claims, _ = tasks._candidate_evidence()
    identity_only = {**backed[0], "id": "t2", "teamName": "待补成果研究组"}
    monkeypatch.setattr(tasks, "_catalogue_evidence", lambda: (backed + [identity_only], claims, version[0]))
    run = confirm(client, create(client), limit=5).json()["data"]["run"]
    assert run["shortfall"] == 4
    assert run["coverage"] == {"scopeTeamCount": 2, "outcomeBackedTeamCount": 1,
                               "conditionMatchedTeamCount": 1}
    assert [item["teamId"] for item in run["items"]] == ["t1"]


def test_scope_conflict_explicit_resolution_and_subdomain_guard(app_client):
    client, _, _, _ = app_client
    record = create(client)
    p = parsed(client, scope={"mode": "selected", "domainIds": ["quantum"]})
    assert p["scopeConflict"] is True
    assert confirm(client, record, p).json()["detail"]["code"] == "SCOPE_CONFIRMATION_REQUIRED"
    kept = confirm(client, record, p, scopeResolution="keep").json()["data"]
    assert kept["run"]["items"] == [] and kept["run"]["domainIds"] == ["quantum"]
    expanded = confirm(client, kept, p, scopeResolution="expand", requestId="confirm-002").json()["data"]
    assert expanded["run"]["domainIds"] == ["life", "quantum"]
    assert expanded["run"]["changes"]["sameConditions"] is False
    assert client.post("/strategic-map/task-interpretations", json={"taskText": "蛋白质", "scope": {
        "mode": "selected", "domainIds": ["life"], "subdomainId": "qsub"}}).status_code == 422


def test_frozen_old_runs_evidence_conflict_and_foreign_claim_rejection(app_client):
    client, _, _, version = app_client
    record = create(client)
    p = parsed(client)
    version[0] = "evidence-v2"
    assert confirm(client, record, p).json()["detail"]["code"] == "EVIDENCE_CHANGED"
    first = confirm(client, record).json()["data"]
    first_run = first["run"]
    edited = client.patch(f"/strategic-map/assessments/{record['taskId']}", json={
        "requestId": "patch-compare", "expectedRevision": first["revision"], "comparedTeamIds": ["t1"]}).json()["data"]
    version[0] = "evidence-v3"
    next_record = confirm(client, edited, requestId="confirm-002").json()["data"]
    old = client.get(f"/strategic-map/assessments/{record['taskId']}/runs/{first_run['runId']}").json()["data"]
    assert old == {**first_run, "selection": {"comparedTeamIds": ["t1"], "combination": []}}
    assert next_record["run"]["selection"]["comparedTeamIds"] == []
    assert next_record["run"]["inputVersion"] == 2
    response = client.patch(f"/strategic-map/assessments/{record['taskId']}", json={
        "requestId": "patch-bad", "expectedRevision": next_record["revision"], "followUps": [
            {"id": "f1", "teamId": "t1", "claimIds": ["someone-else-claim"], "question": "test"}]})
    assert response.status_code == 422


def test_missing_identity_and_forged_visitor_header_are_not_accepted(app_client):
    client, _, _, _ = app_client
    client.app.dependency_overrides.clear()
    response = client.get("/strategic-map/assessments", headers={"X-Visitor-Id": "visitor-a"})
    assert response.status_code == 401


def test_java_rotated_token_fails_closed(monkeypatch):
    import httpx
    from starlette.requests import Request
    def fake_get(*args, **kwargs):
        return httpx.Response(200, headers={"Set-Cookie": "ai_agent_visitor_token=new"},
                              json={"code": "0000", "data": {"visitorId": "new-visitor"}})
    monkeypatch.setattr(httpx.Client, "get", fake_get)
    request = Request({"type": "http", "headers": [(b"cookie", b"ai_agent_visitor_token=invalid")]})
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as failure:
        identity.current_visitor(request)
    assert failure.value.status_code == 401


def test_domain_observation_preserves_drafts_and_old_capability_evidence(app_client):
    client, _, _, _ = app_client
    record = create(client, taskDraft="保留的任务", domainDraft="观察蛋白质方向")
    body = {"requestId": "observe-001", "expectedRevision": 1, "scope": {"mode": "selected", "domainIds": ["life"]}, "limit": 5, "windowDays": 90}
    path = f"/strategic-map/assessments/{record['taskId']}/observe"
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    assert client.post(path, json=body).json() == response.json()
    value = response.json()["data"]
    assert value["mode"] == "domain" and value["state"]["taskDraft"] == "保留的任务"
    observation = value["run"]["observation"]
    assert observation["totalUnits"] == observation["outcomeBackedUnits"] == 1
    assert observation["units"][0]["citations"][0]["id"] == "c1"
    assert observation["identityOnlyUnits"] == 0


def test_explicit_unknown_capability_not_dropped_and_or_preserved(app_client):
    client, _, _, _ = app_client
    record = create(client)
    p = parsed(client, "必须开展蛋白质研究并具备国产GPU适配能力")
    assert len(p["criteria"]) == 2
    # The unknown second clause remains required; it cannot silently become informational.
    assert p["criteria"][1]["necessity"] == "required"
    assert confirm(client, record, p).json()["data"]["run"]["items"] == []
    current = client.get(f"/strategic-map/assessments/{record['taskId']}").json()["data"]
    alternative = parsed(client, "蛋白质或量子计算")
    assert alternative["criteria"][0]["operator"] == "any"
    run = confirm(client, current, alternative, requestId="confirm-alternative").json()["data"]["run"]
    assert [item["teamId"] for item in run["items"]] == ["t1"]
    assert run["items"][0]["criteriaMatrix"][0]["status"] == "supported"


def test_domain_draft_window_and_observation_restore_confirmed_options(app_client):
    client, _, _, _ = app_client
    record = create(client, mode="domain")
    path = f"/strategic-map/assessments/{record['taskId']}"
    patched = client.patch(path, json={"requestId": "save-window", "expectedRevision": record["revision"],
        "windowDays": 30, "requestedLimit": 2, "domainDraft": "观察近期变化"})
    assert patched.status_code == 200
    saved = client.get(path).json()["data"]
    assert saved["state"]["windowDays"] == 30
    result = client.post(path + "/observe", json={"requestId": "observe-window", "expectedRevision": saved["revision"],
        "scope": {"mode": "selected", "domainIds": ["life"]}, "windowDays": 180, "limit": 3})
    assert result.status_code == 200
    restored = client.get(path).json()["data"]
    assert restored["state"]["windowDays"] == restored["run"]["observation"]["windowDays"] == 180
    assert restored["state"]["requestedLimit"] == 3
    assert restored["state"]["domainScope"]["domainIds"] == ["life"]
    assert client.patch(path, json={"requestId": "invalid-window", "expectedRevision": restored["revision"], "windowDays": 17}).status_code == 422
