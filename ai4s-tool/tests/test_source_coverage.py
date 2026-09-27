from ai4s_tool.api.source_coverage import summarize
from ai4s_tool.api import task_recommendations as tasks
from tests.test_strategic_assessments import app_client


def test_coverage_deduplicates_units_and_claims_without_implying_human_review():
    teams = [{"id": "a", "catalogueBasis": "official_directory", "claimProvenance": {
        "c1": {"fetchedAt": "2026-09-20", "humanReview": {"status": "not_recorded"}},
        "c2": {"fetchedAt": "2026-09-22", "humanReview": {"status": "reviewed"}},
    }}, {"id": "b", "catalogueBasis": "verified"}]
    claims = [("c1", "a", "r", "description", "title", "quote", "https://a.edu.cn/team", ""),
              ("c2", "a", "r", "outcome", "title", "quote", "https://b.edu.cn/paper", ""),
              ("c3", "b", "r", "description", "title", "quote", "https://a.edu.cn/group", "")]
    value = summarize(teams, claims + [claims[0]], "v1")
    assert value["totalUnits"] == 2 and value["claimCount"] == 3
    assert value["outcomeBackedUnits"] == value["identityOnlyUnits"] == 1
    assert value["officialDirectoryUnits"] == value["modelReviewedUnits"] == 1
    assert value["humanReviewedClaims"] == 1 and value["latestFetchedAt"] == "2026-09-22"
    assert value["sources"][0]["unitCount"] == 2
    scoped = summarize(teams[1:], claims, "v1")
    assert scoped["claimCount"] == 1 and scoped["outcomeBackedUnits"] == 0
    assert scoped["humanReviewedClaims"] == 0 and scoped["latestFetchedAt"] is None


def test_public_coverage_honors_scope_and_omits_private_fields(app_client):
    client, _, _, _ = app_client
    teams, _, _ = tasks._catalogue_evidence()
    teams[0]["internalNotes"] = "PRIVATE"
    value = client.get("/strategic-map/intelligence/source-coverage?domain_id=life")
    assert value.status_code == 200 and value.json()["outcomeBackedUnits"] == 1
    assert "PRIVATE" not in value.text
    empty = client.get("/strategic-map/intelligence/source-coverage?domain_id=quantum").json()
    assert empty["totalUnits"] == empty["claimCount"] == 0
    assert empty["sources"] == []
