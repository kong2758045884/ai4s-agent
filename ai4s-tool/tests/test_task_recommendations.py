"""Task recommendations keep their evidence and scores separate from paid scans."""
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai4s_tool.api import task_recommendations as task


def _claim(team_id, text, quote, kind="outcome"):
    return (f"claim-{team_id}", team_id, "reviewed-run", kind, text, quote,
            "https://example.org/original-paper", "")


def test_cached_recommendation_is_scoped_versioned_and_never_expands(tmp_path, monkeypatch):
    db = tmp_path / "recommendations.db"
    original_db = task._db

    def local_db(*, write=False):
        connection = sqlite3.connect(db if write else f"file:{db}?mode=ro", uri=not write)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(task, "_db", local_db)
    monkeypatch.setattr(task, "_validate_scope", lambda domain, subdomain: None)
    monkeypatch.setattr(task, "_institution_links", lambda: {})
    monkeypatch.setattr(task, "_pending_leads", lambda *args: (_ for _ in ()).throw(AssertionError("public recommendation must not publish leads")))
    teams = [
        {"id": "t1", "name": "大学甲", "teamName": "量子计算组", "institutionName": "大学甲",
         "domainId": "quantum", "subdomainId": "sub-q", "domainName": "量子科技", "subdomainName": "量子计算",
         "researchDirections": ["量子计算"], "description": "量子计算实验和论文",
         "scoreTotal": 82, "scoreVersion": "team-v3", "updatedAt": "2026-09-25"},
        {"id": "t2", "name": "大学乙", "teamName": "量子材料组", "institutionName": "大学乙",
         "domainId": "materials", "subdomainId": "sub-m", "domainName": "化学与材料", "subdomainName": "材料",
         "researchDirections": ["量子材料"], "description": "量子材料成果",
         "scoreTotal": 94, "scoreVersion": "team-v3", "updatedAt": "2026-09-25"},
    ]
    claims = [_claim("t1", "量子计算论文", "团队发表量子计算论文"),
              _claim("t2", "量子材料论文", "团队发表量子材料论文")]
    monkeypatch.setattr(task, "_candidate_evidence", lambda: (teams, claims, "snapshot-v1"))
    from ai4s_tool.api import strategic_graph
    monkeypatch.setattr(strategic_graph, "start_graph_scan", lambda *_: (_ for _ in ()).throw(AssertionError("paid scan called")))

    app = FastAPI()
    app.include_router(task.router)
    client = TestClient(app)
    response = client.post("/strategic-map/task-recommendations",
                           json={"taskText": "量子计算", "domainId": "quantum", "limit": 5})
    assert response.status_code == 200, response.text
    result = response.json()
    assert [item["teamId"] for item in result["items"]] == ["t1"]
    assert result["items"][0]["teamScore"] == 82
    assert result["items"][0]["taskMatchScore"] != 82
    assert result["items"][0]["citations"][0]["url"].startswith("https://")
    assert result["shortfall"] == 4
    assert result["pendingLeads"] == []
    loaded = client.get(f"/strategic-map/task-recommendations/{result['runId']}")
    assert loaded.json() == result
    with local_db() as connection:
        assert connection.execute("SELECT COUNT(*) FROM strategic_task_recommendation_run").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM strategic_team_evidence_claim").fetchone()[0] == 2
    monkeypatch.setattr(task, "_db", original_db)


def test_daily_freeze_is_idempotent_and_late_evidence_creates_revision(tmp_path, monkeypatch):
    db = tmp_path / "daily.db"

    def local_db(*, write=False):
        connection = sqlite3.connect(db if write else f"file:{db}?mode=ro", uri=not write)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(task, "_db", local_db)
    source = {"date": "2026-09-25", "impact": {"events": []}, "teamChanges": [],
              "recommendationChanges": [], "domains": {}}
    monkeypatch.setattr(task, "_daily_payload", lambda _: dict(source))
    first = task.freeze_intelligence_daily(task.date(2026, 9, 25))
    again = task.freeze_intelligence_daily(task.date(2026, 9, 25))
    assert first["revision"] == again["revision"] == 1
    source["teamChanges"] = [{"teamId": "new"}]
    revised = task.freeze_intelligence_daily(task.date(2026, 9, 25))
    assert revised["revision"] == 2
    assert task.intelligence_daily(task.date(2026, 9, 25))["teamChanges"] == [{"teamId": "new"}]


def test_search_includes_team_directory_without_unrelated_single_token_hits(tmp_path, monkeypatch):
    db = tmp_path / "search.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("""CREATE TABLE strategic_map_team
            (id TEXT,institution_name TEXT,team_name TEXT,domain_id TEXT,core_direction TEXT,
             focus TEXT,description TEXT,source_urls TEXT,deleted INTEGER);
            CREATE TABLE strategic_map_person (team_id TEXT,name TEXT,deleted INTEGER);
            INSERT INTO strategic_map_team VALUES
              ('q','大学甲','量子计算研究组','quantum','量子计算','','','[\"https://example.org/q\"]',0),
              ('n','大学乙','神经计算组','ai','神经计算','','','[]',0);""")
    def local_db(*, write=False):
        connection = sqlite3.connect(db if write else f"file:{db}?mode=ro", uri=not write)
        connection.row_factory = sqlite3.Row
        return connection
    monkeypatch.setattr(task, "_db", local_db)
    monkeypatch.setattr(task, "_validate_scope", lambda *_: None)
    monkeypatch.setattr(task.impact_store, "connect", lambda: None)
    found = task.intelligence_search(q="量子计算", page=1, size=10, verified_only=False)
    assert [(item["type"], item["id"]) for item in found["items"]] == [("team_profile", "q")]
    assert found["items"][0]["reviewNotice"]


def test_public_search_catalogue_and_graph_use_current_reviewed_evidence_only(monkeypatch):
    teams = [{"id": "approved", "institutionName": "大学甲", "teamName": "量子计算组",
              "domainId": "quantum", "domainName": "量子科技", "description": "有原文的量子计算成果"}]
    claims = [_claim("approved", "量子计算论文", "团队发表量子计算论文")]
    monkeypatch.setattr(task, "_validate_scope", lambda *_: None)
    monkeypatch.setattr(task, "_catalogue_evidence", lambda: (teams, claims, "approved-v1"))
    monkeypatch.setattr(task, "_db", lambda **_: (_ for _ in ()).throw(AssertionError("must not read stale index or raw profiles")))
    assert task.verified_teams()["teamIds"] == ["approved"]
    result = task.intelligence_search(q="量子计算", page=1, size=10)
    assert len(result["items"]) == 1 and result["items"][0]["type"] == "team_claim"
    assert task.intelligence_search(q="量子计算", domain_id="materials", page=1, size=10)["items"] == []
    graph = task.verified_graph(domain_id="quantum")
    assert "team:approved" in {node["id"] for node in graph["nodes"]}
    assert graph["meta"]["features"] == {"scan": False, "chat": False}
    teams.clear()
    assert task.intelligence_search(q="量子计算", page=1, size=10)["items"] == []
    assert task.verified_graph(domain_id="quantum")["nodes"] == []


def test_public_daily_omits_unreviewed_events_and_withdrawn_teams(monkeypatch):
    monkeypatch.setattr(task, "_catalogue_evidence", lambda: ([{"id": "approved"}], [], "v1"))
    monkeypatch.setattr(task.impact_store, "connect", lambda: None)
    monkeypatch.setattr(task, "intelligence_daily", lambda _: {
        "impact": {"events": [{"id": "imported-unreviewed"}]},
        "teamChanges": [{"teamId": "approved"}, {"teamId": "withdrawn"}],
        "recommendationChanges": [{"before": ["withdrawn"], "after": ["approved"]}],
    })
    result = task.verified_daily(task.date(2026, 9, 26))
    assert result["events"] == []
    assert result["teamChanges"] == [{"teamId": "approved"}]
    assert result["recommendationChanges"] == [{"before": [], "after": ["approved"]}]
