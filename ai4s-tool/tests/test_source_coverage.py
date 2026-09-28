from ai4s_tool.api.source_coverage import pilot_roster, summarize
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


def test_pilot_roster_uses_published_scope_and_does_not_invent_outcomes():
    teams = [
        {"id": "a", "teamName": "甲组", "institutionName": "甲大学", "domainId": "life",
         "subdomainId": "protein", "subdomainName": "蛋白质结构与设计",
         "sourceUrls": ["https://a.edu.cn/team"], "claimProvenance": {
             "outcome": {"humanReview": {"status": "not_recorded"}}}},
        {"id": "b", "teamName": "乙组", "institutionName": "乙大学", "domainId": "life",
         "subdomainId": None, "sourceUrls": ["https://b.edu.cn/team"]},
    ]
    claims = [("identity", "a", "r", "identity", "身份", "原文", "https://a.edu.cn/team", ""),
              ("outcome", "a", "r", "outcome", "成果", "原文", "https://a.edu.cn/paper", "2026-01-01"),
              ("orphan", "other", "r", "outcome", "其他", "原文", "https://other.edu.cn/paper", "")]
    roster = pilot_roster(teams, claims, "v1")
    assert roster["totalUnits"] == 2 and roster["outcomeBackedUnits"] == 1
    assert roster["unclassifiedUnits"] == 1 and roster["shortfall"] == 18
    by_id = {item["teamId"]: item for item in roster["items"]}
    assert by_id["a"]["identitySourceUrl"] == "https://a.edu.cn/team"
    assert by_id["b"]["outcomeCount"] == 0
    assert by_id["a"]["teamAliases"] == []
    scoped = pilot_roster(teams[:1], claims, "v1")
    assert scoped["totalUnits"] == 1 and scoped["unclassifiedUnits"] == 0


def test_public_pilot_roster_honors_scope_and_omits_private_fields(app_client):
    client, _, _, _ = app_client
    teams, _, _ = tasks._catalogue_evidence()
    teams[0]["internalNotes"] = "PRIVATE"
    response = client.get("/strategic-map/intelligence/pilot-roster?domain_id=life")
    assert response.status_code == 200
    assert response.json()["totalUnits"] == 1
    assert "PRIVATE" not in response.text
    assert client.get("/strategic-map/intelligence/pilot-roster?domain_id=quantum").json()["items"] == []
