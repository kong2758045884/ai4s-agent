import json

import pytest

from ai4s_tool.api import assessment_reports as reports, assessment_updates as updates, task_recommendations as tasks
from tests.test_strategic_assessments import app_client, create, confirm


@pytest.fixture
def setup(app_client, monkeypatch):
    client, db, owner, version = app_client
    client.app.include_router(reports.router)
    record = confirm(client, create(client)).json()["data"]
    monkeypatch.setattr(tasks, "_candidate_evidence", lambda: ([], [], "withdrawn"))
    child = updates.refresh(record["taskId"], record["run"]["runId"])
    with db() as conn:
        # UTC Sep 26 16:05 belongs to Beijing Sep 27.
        conn.execute("UPDATE strategic_assessment_update SET created_at='2026-09-26T16:05:00+00:00'")
    return client, db, owner, record, child


def freeze(client, key="report-request-01", day="2026-09-27", domain="life", kind="daily"):
    return client.post("/strategic-map/assessment-reports", json={"requestId": key, "day": day, "domainId": domain, "kind": kind})


def test_daily_beijing_scope_owner_and_same_frozen_run(setup):
    client, _, owner, record, child = setup
    response = freeze(client)
    assert response.status_code == 200, response.text
    value = response.json()["data"]
    assert value["counts"]["taskUpdates"] == 1 and value["counts"]["removed"] == 1
    change = value["changes"][0]
    assert change["before"]["runId"] == record["run"]["runId"] and change["after"]["runId"] == child
    assert change["before"]["items"][0]["citations"] == record["run"]["items"][0]["citations"]
    assert value["publicDaily"] is None  # absent public report is not fabricated
    assert freeze(client, key="report-request-02", day="2026-09-26").json()["data"]["counts"]["taskUpdates"] == 0
    assert freeze(client, key="report-request-03", domain="quantum").json()["data"]["counts"]["taskUpdates"] == 0
    assert client.get(f"/strategic-map/assessment-reports/{value['reportId']}").json()["data"] == value
    owner[0] = "visitor-b"
    assert client.get(f"/strategic-map/assessment-reports/{value['reportId']}").status_code == 404
    assert client.get("/strategic-map/assessment-reports").json()["data"]["total"] == 0
    assert freeze(client).json()["data"]["counts"]["taskUpdates"] == 0


def test_idempotence_revision_and_weekly_replay_use_saved_daily(setup):
    client, db, _, _, _ = setup
    first = freeze(client).json()["data"]
    assert freeze(client).json()["data"] == first
    assert freeze(client, key="report-request-02").json()["data"]["reportId"] == first["reportId"]
    weekly = freeze(client, key="report-week-01", kind="weekly").json()["data"]
    assert weekly["counts"] == first["counts"] and len(weekly["missingDays"]) == 6
    assert weekly["dailyInputs"][0]["reportId"] == first["reportId"]
    # A late inserted input creates a daily revision; old week still refers to v1.
    with db() as conn:
        conn.execute("UPDATE strategic_assessment_update SET reason='迟到资料修订说明'")
    revised = freeze(client, key="report-revision").json()["data"]
    assert revised["revision"] == 2 and revised["inputHash"] != first["inputHash"]
    assert client.get(f"/strategic-map/assessment-reports/{weekly['reportId']}").json()["data"] == weekly
    new_week = freeze(client, key="report-week-02", kind="weekly").json()["data"]
    assert new_week["revision"] == 2 and new_week["dailyInputs"][0]["reportId"] == revised["reportId"]
    assert new_week["changes"][0]["reason"] == "迟到资料修订说明"


def test_missing_daily_inputs_are_not_zero_and_conditions_are_required(setup):
    client, _, _, _, _ = setup
    monthly = freeze(client, kind="monthly").json()["data"]
    assert monthly["dailyInputs"] == [] and len(monthly["missingDays"]) == 27
    assert monthly["counts"]["taskUpdates"] == 0 and "缺失日期没有当作零变化" in monthly["notice"]
    assert freeze(client, key="report-invalid-scope", domain="unknown").status_code == 422
    assert freeze(client, key="report-invalid-date", day="2099-01-01").status_code == 422


def test_private_followups_and_notes_do_not_enter_report(setup):
    client, db, _, record, _ = setup
    with db() as conn:
        row = conn.execute("SELECT state_json FROM strategic_assessment WHERE id=?", (record["taskId"],)).fetchone()
        state = json.loads(row[0]); state.update(internalNotes="private-secret", followUps=[{"owner": "internal-person"}])
        conn.execute("UPDATE strategic_assessment SET state_json=? WHERE id=?", (json.dumps(state), record["taskId"]))
    response = freeze(client)
    assert "private-secret" not in response.text and "internal-person" not in response.text
