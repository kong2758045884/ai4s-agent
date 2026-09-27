"""Offline checks only: no search, model, or external provider is called."""
import json

import pytest

from ai4s_tool.api import assessment_investigations as api, strategic_investigations as jobs
from ai4s_tool.api import task_recommendations as tasks
from ai4s_tool.api import strategic_access as access
from tests.test_strategic_assessments import app_client, create, confirm


@pytest.fixture
def setup(app_client, monkeypatch):
    client, db, user, version = app_client
    client.app.include_router(api.router)
    with db() as conn:
        access.grant(conn, user[0], "maintainer", operator="offline-test", reason="离线调查测试的明确授权")
    submitted = []
    monkeypatch.setattr(jobs._POOL, "submit", lambda *args: submitted.append(args))
    monkeypatch.setattr(tasks.strategic_map, "_llm_config", lambda: ("offline", "test", "configured"))
    monkeypatch.setattr(tasks, "verified_teams", lambda: {"teams": tasks._candidate_evidence()[0]})
    record = confirm(client, create(client)).json()["data"]
    route = f"/strategic-map/assessments/{record['taskId']}/runs/{record['run']['runId']}/investigations"
    body = {"requestId": "web-request-001", "options": {"teamIds": ["t1"], "criterionIds": [record['run']['criteria'][0]['id']]}}
    return client, db, user, record, route, body, submitted


def test_ungranted_owner_cannot_collect_or_retry_but_can_cancel_existing_work(setup):
    client, db, user, record, route, body, submitted = setup
    job = client.post(route, json=body).json()["data"]
    with db() as conn:
        access.grant(conn, user[0], "reviewer", operator="offline-test", reason="撤销联网采集权限，保留审核权限")
    overview = client.get(route).json()["data"]
    assert overview["canStart"] is False and overview["canRetry"] is False
    assert client.post(route, json={**body, "requestId": "new-disallowed"}).status_code == 403
    endpoint = f"/strategic-map/assessments/{record['taskId']}/investigations/{job['jobId']}"
    assert client.post(endpoint + "/retry", json={"requestId": "retry-disallowed"}).status_code == 403
    assert client.post(endpoint + "/cancel").status_code == 200
    assert len(submitted) == 1


def test_owner_scope_idempotency_and_private_results(setup):
    client, db, user, record, route, body, submitted = setup
    info = client.get(route).json()["data"]
    assert info["configured"] and info["teams"][0]["teamId"] == "t1" and submitted == []
    assert client.post(route, json={**body, "options": {**body["options"], "teamIds": ["foreign-team"]}}).status_code == 422
    assert client.post(route, json={**body, "options": {**body["options"], "criterionIds": ["foreign-criterion"]}}).status_code == 422
    first = client.post(route, json=body)
    assert first.status_code == 202, first.text
    job = first.json()["data"]
    assert client.post(route, json=body).json()["data"]["jobId"] == job["jobId"] and len(submitted) == 1
    assert client.post(route, json={**body, "options": {**body["options"], "publishedAfter": "2026-01-01"}}).status_code == 409
    endpoint = f"/strategic-map/assessments/{record['taskId']}/investigations/{job['jobId']}"
    user[0] = "visitor-b"
    assert client.get(route).status_code == client.post(route, json=body).status_code == 404
    assert client.get(endpoint).status_code == client.post(endpoint + "/cancel").status_code == 404
    assert client.post(endpoint + "/retry", json={"requestId": "retry-001"}).status_code == 404
    assert client.get(f"/strategic-map/task-recommendations/{record['run']['runId']}").status_code == 404


def test_cancel_before_start_has_no_provider_call_and_is_retryable(setup, monkeypatch):
    client, _, _, record, route, body, submitted = setup
    job = client.post(route, json=body).json()["data"]
    endpoint = f"/strategic-map/assessments/{record['taskId']}/investigations/{job['jobId']}"
    assert client.post(endpoint + "/cancel").json()["data"]["cancelRequested"] is True
    monkeypatch.setattr(jobs, "_research_one", lambda *args: pytest.fail("cancelled work must not call a provider"))
    jobs._execute(job["jobId"], record["run"])
    assert jobs.get(job["jobId"])["state"] == "cancelled"
    retry = client.post(endpoint + "/retry", json={"requestId": "retry-001"})
    assert retry.status_code == 202, retry.text
    retry_id = retry.json()["data"]["jobId"]
    assert retry_id != job["jobId"] and not retry.json()["data"]["cancelRequested"]
    assert client.post(endpoint + "/retry", json={"requestId": "retry-001"}).json()["data"]["jobId"] == retry_id
    assert len(submitted) == 2
    # Replay remains the original request even after another investigation exists.
    assert client.post(route, json=body).json()["data"]["jobId"] == job["jobId"]


def test_restart_marks_interrupted_without_starting_paid_work(setup):
    client, _, _, _, route, body, submitted = setup
    job = client.post(route, json=body).json()["data"]
    jobs.recover_interrupted()
    state = jobs.get(job["jobId"])
    assert state["state"] == "interrupted" and len(submitted) == 1
    assert state["costCny"] is None and state["dailyBudgetCny"] is None


def test_partial_retry_preserves_published_units_and_old_run(setup, monkeypatch):
    client, _, _, record, route, body, _ = setup
    job = client.post(route, json=body).json()["data"]
    jobs._save(job["jobId"], state="partial", stage="调查完成", publishedTeams=["t1"], batchTeamIds=["t1"])
    endpoint = f"/strategic-map/assessments/{record['taskId']}/investigations/{job['jobId']}"
    retried = client.post(endpoint + "/retry", json={"requestId": "retry-001"}).json()["data"]
    monkeypatch.setattr(jobs, "_research_one", lambda *args: pytest.fail("successful team must not be collected again"))
    jobs._execute(retried["jobId"], record["run"])
    assert jobs.get(retried["jobId"])["state"] == "completed"
    assert jobs.get(retried["jobId"])["updatedRunId"] is None  # identical evidence causes no fictitious new result
    assert client.get(f"/strategic-map/assessments/{record['taskId']}").json()["data"]["run"] == record["run"]


def test_missing_config_and_historical_run_fail_before_submission(setup, monkeypatch):
    client, db, _, record, route, body, submitted = setup
    monkeypatch.setattr(tasks.strategic_map, "_llm_config", lambda: None)
    assert not client.get(route).json()["data"]["configured"]
    assert client.post(route, json=body).status_code == 503 and not submitted
    with db() as conn:
        state = {**record["state"], "activeRunId": None}
        conn.execute("UPDATE strategic_assessment SET state_json=? WHERE id=?", (json.dumps(state), record["taskId"]))
    assert client.post(route, json=body).status_code == 409 and not submitted


def test_evidence_descendant_keeps_retry_valid_but_new_input_stops_it(setup, monkeypatch):
    from ai4s_tool.api import assessment_updates as updates
    client, _, _, record, route, body, submitted = setup
    job = client.post(route, json=body).json()["data"]
    jobs._save(job["jobId"], state="partial", stage="调查完成", publishedTeams=["t1"], batchTeamIds=["t1"])
    teams, claims, _ = tasks._candidate_evidence()
    newer_claims = [(*claims[0][:5], "研究组公开了新的蛋白质研究论文", *claims[0][6:])]
    monkeypatch.setattr(tasks, "_candidate_evidence", lambda: (teams, newer_claims, "v2"))
    new_run = updates.refresh(record["taskId"], record["run"]["runId"])
    assert new_run
    endpoint = f"/strategic-map/assessments/{record['taskId']}/investigations/{job['jobId']}"
    retried = client.post(endpoint + "/retry", json={"requestId": "retry-001"})
    assert retried.status_code == 202, retried.text
    assert jobs._refresh_private(record["run"]) == new_run
    jobs._save(retried.json()["data"]["jobId"], state="failed", stage="失败")
    current = client.get(f"/strategic-map/assessments/{record['taskId']}").json()["data"]
    assert confirm(client, current, requestId="confirm-002").status_code == 200
    assert jobs._refresh_private(record["run"]) is None
    assert client.post(endpoint + "/retry", json={"requestId": "retry-002"}).status_code == 409
    assert len(submitted) == 2
