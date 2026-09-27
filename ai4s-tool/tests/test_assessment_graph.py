import copy
import json

from ai4s_tool.api import assessment_graph as graph, task_recommendations as tasks
from tests.test_strategic_assessments import app_client, create, confirm
from tests.test_assessment_updates import event
from ai4s_tool.api import strategic_changes as changes


def graph_url(record):
    return f"/strategic-map/assessments/{record['taskId']}/runs/{record['run']['runId']}/graph"


def test_saved_graph_survives_catalogue_removal_and_does_not_read_live_sources(app_client, monkeypatch):
    client, db, owner, version = app_client
    team = tasks._candidate_evidence()[0][0]
    team["institutionEvidence"] = [{"quote": "大学甲蛋白质研究组", "url": "https://example.org/identity"}]
    record = confirm(client, create(client)).json()["data"]
    saved = record["run"]["relationshipGraph"]
    assert saved["meta"]["snapshot"]["legacyReconstruction"] is False
    assert {n["type"] for n in saved["nodes"]} >= {"任务", "科研团队", "成果", "机构"}
    factual = [e for e in saved["edges"] if e["data"]["raw"]["status"] == "source"]
    assert len(factual) == 2
    assert all(e["data"]["raw"]["citations"][0]["quote"] for e in factual)
    monkeypatch.setattr(tasks, "_catalogue_evidence", lambda: (_ for _ in []).throw(AssertionError("live read forbidden")))
    monkeypatch.setattr(tasks, "_candidate_evidence", lambda: (_ for _ in []).throw(AssertionError("live read forbidden")))
    assert client.get(graph_url(record)).json()["data"] == saved
    assert client.get(f"/strategic-map/assessments/{record['taskId']}/export").json()["data"]["run"]["relationshipGraph"] == saved
    # Graph route enforces both assessment ownership and run membership.
    other = create(client, requestId="other-record")
    assert client.get(graph_url({**other, "run": record["run"]})).status_code == 404
    owner[0] = "visitor-b"
    assert client.get(graph_url(record)).status_code == 404


def test_legacy_reconstruction_is_pure_and_does_not_rewrite_history(app_client, monkeypatch):
    client, db, _, _ = app_client
    record = confirm(client, create(client)).json()["data"]
    old = {k: v for k, v in record["run"].items() if k not in {"relationshipGraph", "selection"}}
    old["items"][0].pop("identityEvidence", None)
    serialized = json.dumps(old, ensure_ascii=False)
    with db() as conn:
        conn.execute("UPDATE strategic_assessment_run SET result_json=? WHERE id=?", (serialized, old["runId"]))
    monkeypatch.setattr(tasks, "_candidate_evidence", lambda: ([], [], "removed"))
    first = client.get(graph_url(record)).json()["data"]
    assert first == client.get(graph_url(record)).json()["data"]
    assert first["meta"]["snapshot"]["legacyReconstruction"] is True
    affiliation = next(e for e in first["edges"] if e["label"] == "档案所列归属")
    assert affiliation["data"]["raw"]["status"] == "unconfirmed"
    assert affiliation["data"]["raw"]["citations"] == []
    with db() as conn:
        assert conn.execute("SELECT result_json FROM strategic_assessment_run WHERE id=?", (old["runId"],)).fetchone()[0] == serialized


def test_evidence_update_freezes_child_graph_and_preserves_parent(app_client, monkeypatch):
    client, db, _, _ = app_client
    record = confirm(client, create(client)).json()["data"]
    before = client.get(graph_url(record)).json()["data"]
    monkeypatch.setattr(tasks, "_candidate_evidence", lambda: ([], [], "withdrawn"))
    event(db)
    assert changes.process_pending()["privateAssessments"]["updated"] == 1
    after_record = client.get(f"/strategic-map/assessments/{record['taskId']}").json()["data"]
    after = client.get(graph_url(after_record)).json()["data"]
    assert not any(n["type"] == "科研团队" for n in after["nodes"])
    assert after["meta"]["snapshot"]["hash"] != before["meta"]["snapshot"]["hash"]
    assert before == client.get(graph_url(record)).json()["data"]


def test_unproven_criteria_no_shared_name_merge_no_private_notes():
    run = {"taskId": "t", "runId": "r", "inputVersion": 2, "criteria": [
        {"id": "ability", "text": "蛋白质结构预测", "kind": "capability"},
        {"id": "deadline", "text": "6个月交付", "kind": "constraint"}],
        "state": {"internalNotes": "PRIVATE"}, "items": []}
    for tid in ("a", "b"):
        run["items"].append({"teamId": tid, "teamName": tid, "institutionName": "同名大学", "citations": [],
            "criteriaMatrix": [{"criterionId": "ability", "status": "supported", "claimIds": ["missing"]},
                               {"criterionId": "deadline", "status": "insufficient", "claimIds": []}]})
    saved_input = copy.deepcopy(run)
    result = graph.build_v1(run)
    assert run == saved_input
    assert "PRIVATE" not in json.dumps(result)
    assert sum(n["type"] == "机构" for n in result["nodes"]) == 2
    assert any(n["type"] == "能力要求" for n in result["nodes"])
    matches = [e for e in result["edges"] if e["data"]["raw"].get("criterionId")]
    assert len(matches) == 4 and all(e["data"]["raw"]["status"] == "insufficient" for e in matches)
    assert not any(e["source"] == "team:a" and e["target"] == "team:b" for e in result["edges"])
    assert not any(n["type"] == "作者" for n in result["nodes"])


def test_domain_observation_graph_freezes_only_its_saved_units(app_client):
    client, _, _, _ = app_client
    record = create(client, mode="domain")
    response = client.post(f"/strategic-map/assessments/{record['taskId']}/observe", json={
        "requestId": "observe-test", "expectedRevision": record["revision"],
        "scope": {"mode": "selected", "domainIds": ["life"]}})
    assert response.status_code == 200, response.text
    observation = response.json()["data"]
    saved = client.get(graph_url(observation)).json()["data"]
    assert [n["id"] for n in saved["nodes"] if n["type"] == "科研团队"] == ["team:t1"]
    assert any(n["type"] == "领域观察" for n in saved["nodes"])
    assert not any(e["label"] == "推荐候选" for e in saved["edges"])
