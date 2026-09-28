"""A05: a taxonomy correction must be attributed, source-bound, and reversible."""
import sqlite3

from ai4s_tool.api import strategic_map as sm, task_recommendations as tasks
from tests.test_strategic_access import fixture, set_role


def test_classification_review_requires_role_and_current_published_source(fixture, monkeypatch):
    client, path, factory = fixture
    with factory() as session:
        session.add(sm.StrategicDomainRow(id="protein", name="蛋白质结构与设计", parent_id="domain"))
        session.add(sm.StrategicDomainRow(id="foreign", name="量子计算", parent_id="other"))
        session.commit()
    version = ["catalogue-v1"]
    claims = [("identity-1", "t1", "run", "identity", "实验室甲身份", "官网原文",
               "https://example.edu.cn/team", "")]
    monkeypatch.setattr(tasks, "_catalogue_evidence", lambda: ([{"id": "t1"}], claims, version[0]))
    endpoint = "/strategic-map/teams/t1/classification-reviews"
    payload = {"requestId": "classification-001", "expectedRevision": 0,
               "subdomainId": "protein", "evidenceClaimId": "identity-1",
               "sourceVersion": version[0], "reason": "官网原文确认该具体团队属于该细分方向"}
    assert client.get(endpoint).status_code == 401
    client.cookies.set("signed_fixture", "valid")
    assert client.get(endpoint).status_code == 403
    set_role(path, "editor")
    assert client.post(endpoint, json=payload).status_code == 403
    set_role(path, "reviewer")
    assert client.get(endpoint).json()["data"]["revision"] == 0
    for patch, expected in [({"subdomainId": "foreign"}, 422),
                            ({"evidenceClaimId": "other-team"}, 409),
                            ({"sourceVersion": "stale-version"}, 409)]:
        assert client.post(endpoint, json={**payload, **patch}).status_code == expected
    saved = client.post(endpoint, json=payload)
    assert saved.status_code == 200, saved.text
    state = saved.json()["data"]
    assert state["subdomainId"] == "protein" and state["revision"] == 1
    assert state["history"][0]["actorId"] == "trusted-visitor"
    assert state["history"][0]["evidenceClaimId"] == "identity-1"
    assert client.post(endpoint, json=payload).json() == saved.json()
    assert client.post(endpoint, json={**payload, "requestId": "classification-002"}).status_code == 409
    assert client.post(endpoint, json={**payload, "reason": "同一编号对应另一项分类审核决定"}).status_code == 409
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT subdomain_id FROM strategic_map_team WHERE id='t1'").fetchone()[0] == "protein"
        assert conn.execute("SELECT COUNT(*) FROM strategic_team_classification_review").fetchone()[0] == 1
    review_id = state["history"][0]["reviewId"]
    revert = client.post(f"{endpoint}/{review_id}/revert", json={"requestId": "classification-revert-001",
        "expectedRevision": 1, "reason": "原分类依据不足，撤销本次审核并恢复未分类状态"})
    assert revert.status_code == 200, revert.text
    restored = revert.json()["data"]
    assert restored["subdomainId"] is None and restored["revision"] == 2
    assert restored["history"][0]["revertOf"] == review_id
    assert client.post(f"{endpoint}/{review_id}/revert", json={"requestId": "classification-revert-002",
        "expectedRevision": 2, "reason": "再次撤销已非最新的旧审核决定"}).status_code == 409
