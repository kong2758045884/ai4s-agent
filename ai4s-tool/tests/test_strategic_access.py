import json
import sqlite3

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ai4s_tool.api import strategic_access as access, strategic_identity as identity
from ai4s_tool.api import strategic_map as sm, task_recommendations as tasks
from ai4s_tool.api import impact_triage, strategic_graph, research_fusion


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    path = tmp_path / "access.db"
    engine = create_engine(f"sqlite:///{path}")
    sm._Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(sm, "_SESSION_FACTORY", factory)
    monkeypatch.setattr(sm, "_DB_PATH", path)
    with factory() as session:
        session.add(sm.StrategicDomainRow(id="domain", name="生命科学与医学"))
        session.add(sm.StrategicTeamRow(id="t1", domain_id="domain", name="高校甲", team_name="实验室甲",
            contact_record="内部会谈内容", internal_review="内部评价内容", next_action="内部跟进计划"))
        session.commit()
    with sqlite3.connect(path) as conn:
        access.init(conn)
    def visitor(request):
        # Explicit offline identity fixture, not a production header identity.
        if request.cookies.get("signed_fixture") != "valid":
            raise HTTPException(401, "signed visitor required")
        return "trusted-visitor"
    original_identity = identity.current_visitor
    monkeypatch.setattr(identity, "current_visitor", visitor)
    app = FastAPI()
    for module in (sm, tasks, access, impact_triage, strategic_graph, research_fusion):
        app.include_router(module.router)
    app.dependency_overrides[original_identity] = lambda: "trusted-visitor"
    with TestClient(app) as client:
        yield client, path, factory
    engine.dispose()


def set_role(path, role):
    with sqlite3.connect(path) as conn:
        access.grant(conn, "trusted-visitor", role, operator="server-operator", reason="离线权限验收")


def test_public_projection_omits_internal_values_without_modifying_database(fixture):
    client, path, _ = fixture
    for url in ("/strategic-map", "/strategic-map/domains", "/strategic-map/teams/t1", "/strategic-map/domains/domain/teams"):
        res = client.get(url)
        assert res.status_code == 200, res.text
        assert "内部会谈内容" not in res.text and "内部评价内容" not in res.text and "内部跟进计划" not in res.text
        assert '"internalReview"' not in res.text
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT internal_review FROM strategic_map_team WHERE id='t1'").fetchone()[0] == "内部评价内容"
    assert client.get("/strategic-map/teams/t1/internal").status_code == 403


def test_legacy_http_mutations_fail_closed_and_refresh_cannot_be_a_get(fixture):
    client, _, _ = fixture
    targets = [
        ("PUT", "/strategic-map/teams/t1"), ("POST", "/strategic-map/domains/domain/refreshes"),
        ("POST", "/strategic-map/nationwide/refreshes"), ("POST", "/strategic-map/graph/scans"),
        ("POST", "/impact-triage/entities/e1/eligibility"), ("POST", "/impact-triage/candidates/review"),
        ("POST", "/impact-triage/identity-links/e1/rollback"), ("POST", "/strategic-map/integration/changes/retry"),
        ("POST", "/strategic-map/task-recommendations/run/expand"),
    ]
    for method, url in targets:
        response = client.request(method, url, json={}, headers={"X-Visitor-Id": "admin", "X-Role": "maintainer"})
        assert response.status_code == 401, (url, response.text)
    client.cookies.set("signed_fixture", "valid")
    for method, url in targets:
        assert client.request(method, url, json={}).status_code == 403
    for url in ("/strategic-map?refresh=true", "/strategic-map/domains/domain/teams?refresh=1"):
        assert client.get(url).status_code == 405
    assert client.get("/impact-triage/audits").status_code == 403


def test_editor_changes_are_attributed_transactionally_and_revocation_is_immediate(fixture):
    client, path, _ = fixture
    client.cookies.set("signed_fixture", "valid")
    set_role(path, "editor")
    response = client.put("/strategic-map/teams/t1", json={"attention": "重点关注", "contact": "已交流",
        "ai_level": "较高", "contact_record": "新内部记录"})
    assert response.status_code == 200, response.text
    internal = client.get("/strategic-map/teams/t1/internal").json()["data"]
    assert internal["fields"]["contactRecord"] == "新内部记录"
    assert internal["audit"][0]["actorId"] == "trusted-visitor"
    assert internal["audit"][0]["before"]["contact_record"] == "内部会谈内容"
    assert client.post("/impact-triage/candidates/review", json={}).status_code == 403
    with sqlite3.connect(path) as conn:
        payloads = conn.execute("SELECT payload_json FROM strategic_change_event").fetchall()
        assert payloads and "新内部记录" not in json.dumps(payloads, ensure_ascii=False)
        assert "trusted-visitor" not in json.dumps(payloads)
    set_role(path, "revoked")
    assert client.get("/strategic-map/teams/t1/internal").status_code == 403
    assert client.put("/strategic-map/teams/t1", json={"attention": "未标记", "contact": "未接触"}).status_code == 403
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM strategic_maintenance_audit").fetchone()[0] == 2


def test_role_grant_and_its_audit_rollback_together(fixture):
    _, path, _ = fixture
    with sqlite3.connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        access.grant(conn, "other", "maintainer", operator="server", reason="test rollback")
        conn.rollback()
        assert conn.execute("SELECT count(*) FROM strategic_maintenance_role").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM strategic_maintenance_audit").fetchone()[0] == 0


def test_designated_reviewer_can_edit_but_cannot_start_global_paid_collection(fixture):
    client, path, _ = fixture
    client.cookies.set("signed_fixture", "valid")
    set_role(path, "reviewer")
    assert client.get("/strategic-map/teams/t1/internal").status_code == 200
    assert client.put("/strategic-map/teams/t1", json={"attention": "重点关注", "contact": "未接触"}).status_code == 200
    assert client.post("/strategic-map/nationwide/refreshes", json={}).status_code == 403
