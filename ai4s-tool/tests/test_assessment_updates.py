import json

from ai4s_tool.api import assessment_updates as updates, strategic_changes as changes, task_recommendations as tasks
from tests.test_strategic_assessments import app_client, create, confirm


def event(db, team="t1", source="change-1", domain="life"):
    with db() as conn:
        changes.record(conn, subject_type="team", subject_id=team, kind="verified", source_id=source,
                       domain_id=domain, payload={"published": True})


def test_new_private_evidence_updates_snapshot_without_leaking_to_public_runs(app_client, monkeypatch):
    client, db, _, version = app_client
    record = confirm(client, create(client)).json()["data"]
    old = record["run"]
    teams, claims, _ = tasks._candidate_evidence()
    new_claims = [*claims, ("c2", "t1", "s2", "outcome", "蛋白质新论文", "蛋白质新成果的原文片段", "https://example.org/new", "2026-09-27")]
    version[0] = "new-evidence"
    monkeypatch.setattr(tasks, "_candidate_evidence", lambda: (teams, new_claims, version[0]))
    event(db)
    result = changes.process_pending()
    assert result.get("privateAssessments") == {"evaluated": 1, "updated": 1}, result
    current = client.get(f"/strategic-map/assessments/{record['taskId']}").json()["data"]
    assert current["run"]["inputVersionId"] == old["inputVersionId"]
    assert current["run"]["previousRunId"] == old["runId"]
    assert current["run"]["changes"]["sameConditions"] is True
    assert any(c["id"] == "c2" for c in current["run"]["items"][0]["citations"])
    assert client.get(f"/strategic-map/assessments/{record['taskId']}/runs/{old['runId']}").json()["data"] == old
    assert changes.process_pending()["processed"] == 0
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM strategic_assessment_run").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='strategic_task_recommendation_run'").fetchone()[0] == 0


def test_unrelated_changes_do_not_recompute_private_assessment(app_client, monkeypatch):
    client, db, _, version = app_client
    record = confirm(client, create(client)).json()["data"]
    version[0] = "unrelated-global-version"
    called = []
    monkeypatch.setattr(updates, "refresh", lambda *a, **k: called.append(a))
    event(db, team="earth-team", domain="earth")
    result = changes.process_pending()
    assert result["privateAssessments"]["evaluated"] == 0
    assert not called
    assert client.get(f"/strategic-map/assessments/{record['taskId']}").json()["data"]["run"]["runId"] == record["run"]["runId"]


def test_revoked_evidence_removes_team_but_retains_prior_result_and_followup(app_client, monkeypatch):
    client, db, _, _ = app_client
    record = confirm(client, create(client)).json()["data"]
    monkeypatch.setattr(tasks, "_candidate_evidence", lambda: ([], [], "withdrawn"))
    event(db)
    assert changes.process_pending()["privateAssessments"]["updated"] == 1
    result = client.get(f"/strategic-map/assessments/{record['taskId']}").json()["data"]["run"]
    assert result["items"] == [] and result["changes"]["removed"] == ["t1"]
    assert client.get(f"/strategic-map/assessments/{record['taskId']}/runs/{record['run']['runId']}").json()["data"]["items"]


def test_manual_condition_change_wins_over_background_refresh(app_client, monkeypatch):
    client, db, _, version = app_client
    record = confirm(client, create(client)).json()["data"]
    old = record["run"]["runId"]
    updated = confirm(client, record, requestId="confirm-user", limit=2).json()["data"]
    version[0] = "later-data"
    assert updates.refresh(record["taskId"], old) is None
    assert client.get(f"/strategic-map/assessments/{record['taskId']}").json()["data"]["run"]["runId"] == updated["run"]["runId"]


def test_owner_filtered_updates_and_noop_dedup(app_client, monkeypatch):
    client, db, owner, version = app_client
    client.app.include_router(updates.router)
    record = confirm(client, create(client)).json()["data"]
    version[0] = "same-proof-new-source-version"
    event(db)
    assert changes.process_pending()["privateAssessments"] == {"evaluated": 1, "updated": 0}
    assert updates.refresh(record["taskId"], record["run"]["runId"]) is None
    assert client.get("/strategic-map/assessment-updates").json()["data"]["total"] == 0
    monkeypatch.setattr(tasks, "_candidate_evidence", lambda: ([], [], "withdrawn"))
    event(db, source="change-2")
    changes.process_pending()
    assert client.get("/strategic-map/assessment-updates").json()["data"]["total"] == 1
    owner[0] = "visitor-b"
    assert client.get("/strategic-map/assessment-updates").json()["data"]["items"] == []
    assert client.get(f"/strategic-map/assessment-updates?task_id={record['taskId']}").status_code == 404
