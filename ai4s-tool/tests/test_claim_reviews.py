import copy
import json

import pytest

from ai4s_tool.api import claim_reviews as reviews, strategic_access as access
from ai4s_tool.api import claim_provenance, strategic_changes as changes, task_recommendations as tasks
from tests.test_strategic_assessments import app_client, create, confirm

candidate_projection = tasks._candidate_evidence


@pytest.fixture
def review_client(app_client, monkeypatch):
    client, db, owner, version = app_client
    teams, claims, _ = tasks._candidate_evidence()
    teams[0]["claimProvenance"] = {"c1": claim_provenance._base(claims[0][6], claims[0][5],
        run_id="s1", text=claims[0][5], fetched_at="2026-09-25", method="fixture-source-rule")}
    def catalogue(*, reviewed=True):
        current = (copy.deepcopy(teams), copy.deepcopy(claims), version[0])
        if not reviewed:
            return current
        with db() as conn:
            return reviews.overlay(*current, conn)
    monkeypatch.setattr(tasks, "_catalogue_evidence", catalogue)
    monkeypatch.setattr(tasks, "_candidate_evidence", candidate_projection)
    client.app.include_router(reviews.router)
    with db() as conn:
        reviews.init(conn); access.init(conn)
        access.grant(conn, owner[0], "reviewer", operator="offline-test", reason="明确的离线测试授权")
    return client, db, owner, teams, claims, version


def state(client, claim="c1"):
    response = client.get(f"/strategic-map/claims/{claim}/review")
    assert response.status_code == 200, response.text
    return response.json()["data"]


def body(client, **fields):
    current = state(client)
    return {"requestId": f"review-{current['revision'] + 1:03d}", "expectedRevision": current["revision"],
        "sourceFingerprint": current["claim"]["fingerprint"], "signedName": "测试审核人",
        "decision": "supported", "scope": "仅支持这条论文成果及团队归属，不证明资源可用性",
        "reason": "核对了原文中的具体成果和团队归属", **fields}


def save(client, **fields):
    response = client.post("/strategic-map/claims/c1/reviews", json=body(client, **fields))
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_claim_review_is_authorized_attributed_idempotent_and_validates_external_refs(review_client):
    client, db, owner, *_ = review_client
    payload = body(client, relatedEvidence=[{"title": "对照论文", "url": "https://example.org/other", "quote": "对照原文不能自动视为新团队成果"}])
    first = client.post("/strategic-map/claims/c1/reviews", json=payload)
    assert first.status_code == 200, first.text
    assert client.post("/strategic-map/claims/c1/reviews", json=payload).json() == first.json()
    assert client.post("/strategic-map/claims/c1/reviews", json={**payload, "reason": "改变内容重用请求编号"}).status_code == 409
    assert client.post("/strategic-map/claims/c1/reviews", json={**payload, "actorId": "forged-admin"}).status_code == 422
    assert client.post("/strategic-map/claims/c1/reviews", json=body(client,
        relatedEvidence=[{"title": "危险地址", "url": "javascript:alert(1)", "quote": "不应允许"}])).status_code == 422
    current = state(client)
    assert current["history"][0]["actorId"] == owner[0]
    with db() as conn:
        assert conn.execute("SELECT count(*) FROM strategic_claim_review").fetchone()[0] == 1
        event = json.loads(conn.execute("SELECT payload_json FROM strategic_change_event").fetchone()[0])
        assert event["claimId"] == "c1" and "测试审核人" not in json.dumps(event, ensure_ascii=False)
    owner[0] = "not-designated"
    assert client.get("/strategic-map/claim-reviews", headers={"X-Role": "maintainer"}).status_code == 403
    assert client.post("/strategic-map/claims/c1/reviews", json=payload).status_code == 403


def test_conflicting_review_cannot_overwrite_and_revocation_is_immediate(review_client):
    client, db, owner, *_ = review_client
    stale = body(client)
    save(client)
    with db() as conn:
        access.grant(conn, "reviewer-b", "reviewer", operator="offline-test", reason="第二位指定测试人员")
    owner[0] = "reviewer-b"
    response = client.post("/strategic-map/claims/c1/reviews", json={**stale, "requestId": "reviewer-b-01", "decision": "conflict"})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "REVIEW_CONFLICT"
    assert state(client)["review"]["decision"] == "supported"
    with db() as conn:
        access.grant(conn, owner[0], "revoked", operator="offline-test", reason="撤销测试授权")
    assert client.get("/strategic-map/claims/c1/review").status_code == 403


def test_conflict_withdraws_only_this_evidence_and_refreshes_frozen_private_result(review_client):
    client, db, _, _, claims, _ = review_client
    initial = confirm(client, create(client)).json()["data"]
    old = initial["run"]
    assert old["items"][0]["citations"][0]["id"] == "c1"
    save(client, decision="conflict", reason="归属原文与对照材料矛盾，需要复核", relatedEvidence=[
        {"title": "归属对照", "url": "https://example.org/conflict", "quote": "此实验实际来自另外一个团队"}])
    assert tasks._candidate_evidence()[0] == []
    # Identity remains browsable; only this disputed claim leaves usable evidence.
    assert len(tasks._catalogue_evidence()[0]) == 1
    assert tasks._catalogue_evidence()[1] == []
    assert claims[0][0] == "c1"
    processed = changes.process_pending()
    assert processed["privateAssessments"] == {"evaluated": 1, "updated": 1}, processed
    latest = client.get(f"/strategic-map/assessments/{initial['taskId']}").json()["data"]["run"]
    assert latest["items"] == [] and latest["changes"]["removed"] == ["t1"]
    assert "存在冲突" in latest["changes"]["reason"]
    assert client.get(f"/strategic-map/assessments/{initial['taskId']}/runs/{old['runId']}").json()["data"] == old
    assert changes.process_pending()["processed"] == 0


def test_link_unavailable_keeps_saved_quote_and_source_change_requires_new_review(review_client):
    client, _, _, teams, _, version = review_client
    save(client, linkStatus="unavailable")
    current_teams, current_claims, _ = tasks._candidate_evidence()
    assert current_claims[0][0] == "c1"
    assert current_teams[0]["researchDirections"] == teams[0]["researchDirections"]
    meta = current_teams[0]["claimProvenance"]["c1"]
    assert meta["humanReview"]["decision"] == "supported"
    assert meta["linkCheck"]["status"] == "unavailable" and meta["linkCheck"]["method"] == "reviewer_report"
    old = body(client)
    teams[0]["claimProvenance"]["c1"]["contentHash"] = "new-page-version"
    version[0] = "new-source"
    assert client.post("/strategic-map/claims/c1/reviews", json=old).status_code == 409
    assert state(client)["review"]["status"] == "source_changed"
    assert tasks._candidate_evidence()[0] == []  # No silent AI overwrite of a human decision.
    save(client, requestId="fresh-source-review", reason="重新核对更新后的原文，主张仍有依据")
    assert len(tasks._candidate_evidence()[0]) == 1


def test_conditional_claim_is_searchable_but_not_unconditionally_recommended(review_client):
    client, *_ = review_client
    save(client, decision="conditional", scope="仅适用于原文蛋白质样本，不能推广到其他样本")
    assert len(tasks._catalogue_evidence()[1]) == 1
    assert tasks._candidate_evidence()[0] == []
    queue = client.get("/strategic-map/claim-reviews?status=conditional&team_id=t1").json()["data"]
    assert queue["total"] == 1 and queue["items"][0]["review"]["scope"].startswith("仅适用于")


def test_rollback_appends_revision_restores_previous_decision_and_preserves_newer_edits(review_client):
    client, db, *_ = review_client
    first = save(client)
    second = save(client, decision="withdrawn")
    current = state(client)
    payload = {"requestId": "rollback-001", "expectedRevision": current["revision"],
        "sourceFingerprint": current["claim"]["fingerprint"], "signedName": "测试审核人", "reason": "纠正误撤回，恢复原有支持结论"}
    target = f"/strategic-map/claims/c1/reviews/{second['reviewId']}/revert"
    response = client.post(target, json=payload)
    assert response.status_code == 200, response.text
    assert client.post(target, json=payload).json() == response.json()
    assert state(client)["review"]["decision"] == first["decision"]
    assert len(tasks._candidate_evidence()[0]) == 1
    assert client.post(target, json={**payload, "requestId": "rollback-newer", "expectedRevision": 3}).status_code == 409
    with db() as conn:
        assert conn.execute("SELECT count(*) FROM strategic_claim_review").fetchone()[0] == 3


def test_review_outbox_and_audit_fail_atomically(review_client, monkeypatch):
    client, db, *_ = review_client
    monkeypatch.setattr(access, "audit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline audit failure")))
    with pytest.raises(RuntimeError, match="audit failure"):
        save(client)
    with db() as conn:
        assert conn.execute("SELECT count(*) FROM strategic_claim_review").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM strategic_change_event").fetchone()[0] == 0


def test_queue_pagination_and_scoping_are_complete(review_client):
    client, _, _, _, claims, _ = review_client
    claims.extend((f"c{i}", *claims[0][1:]) for i in range(2, 42))
    pages = [client.get(f"/strategic-map/claim-reviews?team_id=t1&page={page}").json()["data"] for page in range(1, 4)]
    assert [len(p["items"]) for p in pages] == [20, 20, 1]
    assert len({r["claim"]["id"] for p in pages for r in p["items"]}) == 41
    assert all(p["total"] == 41 for p in pages)
    assert client.get("/strategic-map/claim-reviews?team_id=unrelated").json()["data"]["total"] == 0


def test_replaced_claim_id_retains_old_human_decision_for_recheck(review_client):
    client, _, _, _, claims, version = review_client
    save(client)
    claims[0] = ("new-quote-id", *claims[0][1:])
    version[0] = "replaced-quote"
    queue = client.get("/strategic-map/claim-reviews?status=source_changed").json()["data"]
    assert queue["total"] == 1 and queue["items"][0]["claim"]["id"] == "c1"
    assert queue["items"][0]["available"] is False
    assert state(client)["history"][0]["decision"] == "supported"
    assert state(client, "new-quote-id")["review"]["status"] == "not_recorded"
