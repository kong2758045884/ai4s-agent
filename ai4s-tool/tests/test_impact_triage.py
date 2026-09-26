"""Contract tests for isolated snapshot import, review rollback and daily output."""
from __future__ import annotations

import sqlite3
import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai4s_tool.api import impact_store as store
from ai4s_tool.api.impact_triage import router
from scripts.reconcile_impact_triage import audit, import_snapshot, readonly


@pytest.fixture
def source_db(tmp_path):
    path = tmp_path / "source.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
      CREATE TABLE direction_tree(id INTEGER PRIMARY KEY,name TEXT,parent_id INTEGER,level INTEGER,status TEXT);
      CREATE TABLE entity_registry(id INTEGER PRIMARY KEY,name TEXT,type TEXT,country TEXT,profile TEXT,follow_status TEXT);
      CREATE TABLE entity_directions(entity_id INTEGER,direction_id INTEGER);
      CREATE TABLE entity_aliases(alias TEXT,entity_id INTEGER);
      CREATE TABLE l3_label_alias(source TEXT,target TEXT,created_at TEXT);
      CREATE TABLE direction_tree_snapshot(id INTEGER PRIMARY KEY,snapshot_date TEXT,tree_json TEXT,change_reason TEXT);
      CREATE TABLE direction_pending_members(node_id INTEGER,event_id INTEGER,similarity REAL);
      CREATE TABLE events(id INTEGER PRIMARY KEY,entity_id INTEGER,direction_id INTEGER,title TEXT,desc TEXT,date TEXT,is_flagship INTEGER,l3_label TEXT,pending_label TEXT);
      CREATE TABLE event_sources(id INTEGER PRIMARY KEY,event_id INTEGER,source_title TEXT,url TEXT,source_name TEXT,source_date TEXT);
      CREATE TABLE score_history(id INTEGER PRIMARY KEY,entity_id INTEGER,scan_date TEXT,raw_achievement INTEGER,raw_status INTEGER,raw_trend INTEGER,
        eff_achievement REAL,eff_status REAL,eff_trend REAL,tier TEXT,change_reason TEXT,reasons_json TEXT,anomaly_note TEXT,below_streak INTEGER);
      INSERT INTO direction_tree VALUES(1,'高原',NULL,1,'formal');
      INSERT INTO direction_tree VALUES(2,'通用AI',1,2,'formal');
      INSERT INTO entity_registry VALUES(21,'智谱','机构','zn','','unfollowed');
      INSERT INTO entity_registry VALUES(41,'Z.ai','机构','zn','','unfollowed');
      INSERT INTO entity_registry VALUES(196,'集智俱乐部','机构','zn','','unfollowed');
      INSERT INTO entity_directions VALUES(21,2),(41,2),(196,2);
      INSERT INTO l3_label_alias VALUES('模型保障','模型安全','2026-09-20');
      INSERT INTO direction_tree_snapshot VALUES(1,'2026-09-19','{"nodes":[]}','源树快照');
      INSERT INTO events VALUES(1,21,2,'成果甲','摘要','2026-09-20',1,'模型安全',NULL);
      INSERT INTO events VALUES(2,41,2,'成果乙','摘要','2026-09-20',0,'模型安全',NULL);
      INSERT INTO events VALUES(3,196,2,'成果丙','摘要','2026-09-20',0,'模型安全',NULL);
      INSERT INTO events VALUES(4,21,NULL,'树外成果','摘要','2026-09-21',0,NULL,'新方向');
      INSERT INTO event_sources VALUES(1,1,'原文甲','https://example.com/a','来源','2026-09-20');
      INSERT INTO event_sources VALUES(2,2,'原文乙','https://example.com/b','来源','2026-09-20');
      INSERT INTO event_sources VALUES(3,3,'原文丙','https://example.com/c','来源','2026-09-20');
      INSERT INTO event_sources VALUES(4,4,'树外原文','https://example.com/d','来源','2026-09-21');
      INSERT INTO score_history VALUES(1,21,'2026-09-20',70,60,70,70,60,70,'B','首期定级','{"achievement":"依据甲"}','',0);
      INSERT INTO score_history VALUES(2,21,'2026-09-21',75,65,72,73.5,63.5,71.4,'A','升级 B→A','{"achievement":"依据乙"}','',0);
      INSERT INTO score_history VALUES(3,41,'2026-09-20',60,60,60,60,60,60,'B','首期定级','{}','',0);
      INSERT INTO score_history VALUES(4,196,'2026-09-20',60,60,60,60,60,60,'B','首期定级','{}','',0);
    """)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def client(tmp_path, source_db, monkeypatch):
    target = tmp_path / "impact.db"
    with readonly(source_db) as source:
        result = import_snapshot(source, target, {"通用 AI": "ai4s-ai"})
    assert result["target_counts"]["impact_event_source"] == 4
    monkeypatch.setattr(store, "DB_PATH", target)
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    return TestClient(app)


def test_import_reconciles_and_never_overwrites(source_db, tmp_path):
    target = tmp_path / "impact.db"
    with readonly(source_db) as source:
        with pytest.raises(ValueError, match="lack reviewed AI4S roots"):
            import_snapshot(source, target, {})
        assert not target.exists()
        before = audit(source, {"通用 AI": "ai4s-ai"})
        assert before["review_watch"] == {
            "possible_same_organization": [21, 41],
            "possible_nonresearch_subject": 196,
        }
        after = import_snapshot(source, target, {"通用 AI": "ai4s-ai"})
        assert after["target_counts"]["impact_score_period"] == before["source_counts"]["score_history"]
        with pytest.raises(FileExistsError):
            import_snapshot(source, target, {"通用 AI": "ai4s-ai"})
    with readonly(source_db) as source:
        assert source.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 4
    with store.connect(target) as conn:
        assert conn.execute("SELECT ai4s_domain_id FROM impact_direction WHERE level=2").fetchone()[0] == "ai4s-ai"
        assert conn.execute("SELECT COUNT(*) FROM impact_team_link").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM impact_source_tree_snapshot").fetchone()[0] == 1
        assert store.normalize_l3_label(conn, "模型保障") == "模型安全"


def test_reviewed_institution_team_link_is_audited_and_reversible(client, tmp_path, monkeypatch):
    from ai4s_tool.api import strategic_map
    team_path = tmp_path / "teams.db"
    with sqlite3.connect(team_path) as teams:
        teams.execute("""CREATE TABLE strategic_map_team (
            id TEXT,institution_name TEXT,team_name TEXT,domain_id TEXT,
            source_urls TEXT,evidence_urls TEXT,is_domestic INTEGER,deleted INTEGER)""")
        teams.execute("""INSERT INTO strategic_map_team VALUES
            ('team-1','智谱研究院','模型研究团队','ai4s-ai',
             '[\"https://example.com/team\"]','[]',1,0)""")
    monkeypatch.setattr(strategic_map, "_DB_PATH", team_path)
    base = "/v1/impact-triage"
    entity_id = store.stable_id("ent", 21)
    request = {"team_id": "team-1", "relation": "affiliated",
               "evidence_url": "https://example.com/team", "note": "官网团队页明确写明归属"}
    assert client.post(f"{base}/entities/{entity_id}/team-links", json=request).status_code == 400
    assert client.post(f"{base}/entities/{entity_id}/eligibility", json={
        "decision": "eligible", "note": "官网证实为科研机构", "research_type": "corporate_research",
        "evidence_url": "https://example.com/about", "mainland_confirmed": True}).status_code == 200
    assert client.get(f"{base}/entities/{entity_id}/team-candidates?q=模型研究").json()["items"][0]["id"] == "team-1"
    linked = client.post(f"{base}/entities/{entity_id}/team-links", json=request)
    assert linked.status_code == 200 and linked.json()["team_events_inherited"] is False
    assert len(client.get(f"{base}/entities/{entity_id}").json()["team_links"]) == 1
    assert client.post(f"{base}/entities/{entity_id}/team-links", json=request).status_code == 409
    audit_id = linked.json()["audit_id"]
    assert client.post(f"{base}/team-links/{audit_id}/rollback").json()["team_events_preserved"] is True
    assert client.get(f"{base}/entities/{entity_id}").json()["team_links"] == []
    assert client.post(f"{base}/team-links/{audit_id}/rollback").status_code == 409


def test_ranking_history_evidence_and_quality_cases(client):
    base = "/v1/impact-triage"
    assert client.get(base + "/ranking?country=zn&tier=A").json()["items"] == []
    ranking = client.get(base + "/ranking?view=reference&country=zn&tier=A&include_pending=true").json()["items"]
    assert [x["name"] for x in ranking] == ["智谱"]
    assert ranking[0]["calibration_status"] == "uncalibrated"
    assert len(ranking[0]["direction_ids"]) == 1
    assert ranking[0]["review_cases"][0]["kind"] == "possible_duplicate"
    detail = client.get(base + "/entities/" + ranking[0]["id"]).json()
    assert len(detail["history"]) == 2
    assert detail["history"][0]["scan_date"] < detail["history"][1]["scan_date"]
    assert any(e["sources"][0]["url"] == "https://example.com/a" for e in detail["events"])
    assert client.post(base + "/entities/" + detail["id"] + "/follow", json={"followed": True}).json()["follow_status"] == "followed"
    assert any(x["id"] == detail["id"] for x in client.get(base + "/ranking?view=reference&include_pending=true&followed=true").json()["items"])
    assert len(client.get(base + "/reviews").json()["items"]) == 2
    assert client.get(base + "/ranking?direction_id=missing").status_code == 404


def test_review_keeps_confirmed_duplicate_quarantined_and_excludes_nonresearch(client):
    base = "/v1/impact-triage"
    cases = client.get(base + "/reviews").json()["items"]
    duplicate = next(x for x in cases if x["kind"] == "possible_duplicate")
    extra_direction = store.stable_id("dir", 9)
    with store.connect(write=True) as conn:
        conn.execute("""INSERT INTO impact_direction (id,name,parent_id,level,status,review_status,created_on)
            VALUES(?,?,?,?,?,?,?)""", (extra_direction, "别名独有方向", store.stable_id("dir", 1), 2,
                                     "formal", "source_unreviewed", store.business_today()))
        conn.execute("INSERT INTO impact_entity_direction VALUES(?,?)", (store.stable_id("ent", 41), extra_direction))
        conn.commit()
    reviewed = client.post(base + "/reviews/" + duplicate["id"] + "/identity",
                           json={"decision": "confirmed_duplicate", "note": "官网确认品牌归属，历史待重评",
                                 "canonical_id": store.stable_id("ent", 21), "evidence_url": "https://example.com/about"})
    assert reviewed.json()["scores_merged"] is False
    assert reviewed.json()["history_recheck_required"] is True
    assert [r["name"] for r in client.get(base + f"/ranking?view=reference&include_pending=true&direction_id={extra_direction}").json()["items"]] == ["智谱"]
    assert client.get(base + "/ranking?tier=A").json()["items"] == []
    reference = client.get(base + "/ranking?view=reference&include_pending=true").json()["items"]
    assert all(x["name"] != "Z.ai" for x in reference)
    consolidated = client.get(base + "/entities/" + store.stable_id("ent", 21)).json()
    assert len(consolidated["history"]) == 3 and len(consolidated["events"]) == 3
    assert all(isinstance(score["reasons"], dict) for score in consolidated["history"])
    assert "身份归一与重评" in client.get(base + "/daily/" + store.business_today()).json()["markdown"]
    club = next(x for x in reference if x["name"] == "集智俱乐部")
    excluded = client.post(base + "/entities/" + club["id"] + "/eligibility",
                           json={"decision": "excluded", "note": "审核证据表明该条不是科研机构"})
    assert excluded.status_code == 200
    assert all(x["name"] != "集智俱乐部" for x in client.get(base + "/ranking?view=reference&include_pending=true").json()["items"])
    assert client.post(base + "/identity-links/" + store.stable_id("ent", 41) + "/rollback").json()["source_scores_preserved"] is True
    assert any(x["name"] == "Z.ai" for x in client.get(base + "/ranking?view=reference&include_pending=true").json()["items"])


def test_identity_history_reconciliation_versions_without_replacing_source_scores(client):
    base = "/v1/impact-triage"
    duplicate = next(x for x in client.get(base + "/reviews").json()["items"] if x["kind"] == "possible_duplicate")
    canonical_id = store.stable_id("ent", 21)
    assert client.post(base + f"/entities/{canonical_id}/reconcile-history").status_code == 400
    accepted = client.post(base + "/reviews/" + duplicate["id"] + "/identity", json={
        "decision": "confirmed_duplicate", "note": "两条来源指向同一主体", "canonical_id": canonical_id,
        "evidence_url": "https://example.com/about"})
    assert accepted.status_code == 200
    with store.connect() as conn:
        source_before = conn.execute("SELECT COUNT(*) FROM impact_score_period").fetchone()[0]
    first = client.post(base + f"/entities/{canonical_id}/reconcile-history").json()
    repeated = client.post(base + f"/entities/{canonical_id}/reconcile-history").json()
    assert first["created"] == 2 and repeated["created"] == 0
    detail = client.get(base + f"/entities/{canonical_id}").json()
    revisions = [row for row in detail["history"] if row.get("revision")]
    assert len(revisions) == 2
    assert all(row["tier"] is None and row["calibration_status"] == "pending_calibration" for row in revisions)
    assert len([row for row in detail["history"] if not row.get("revision")]) == 3
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM impact_score_period").fetchone()[0] == source_before


def test_candidate_review_rollback_and_deterministic_daily(client):
    base = "/v1/impact-triage"
    data = client.get(base + "/directions").json()
    candidate = data["candidates"][0]
    assert candidate["event_count"] == 3
    assert data["l2_candidates"][0]["name"] == "新方向"
    assert client.get(base + "/unclassified/events?label=新方向").json()["items"][0]["sources"][0]["url"] == "https://example.com/d"
    tree_events = client.get(base + "/directions/" + candidate["parent_id"] + "/events?l3_label=" + candidate["name"]).json()["items"]
    assert len(tree_events) == 3 and all(event["sources"] for event in tree_events)
    reviewed = client.post(base + "/candidates/review", json={**candidate, "decision": "approve", "note": "按三条原文审核"})
    assert reviewed.status_code == 200
    audit_id = reviewed.json()["audit_id"]
    assert client.get(base + "/directions").json()["candidates"] == []
    day = store.business_today()
    first = client.get(base + "/daily/" + day).json()
    assert "类目审核" in first["markdown"] and "approve" in first["markdown"]
    assert first == client.get(base + "/daily/" + day).json()
    assert client.post(base + f"/audits/{audit_id}/rollback").json()["rolled_back"] is True
    assert len(client.get(base + "/directions").json()["candidates"]) == 1
    assert client.post(base + f"/audits/{audit_id}/rollback").status_code == 409


def test_manual_ingest_is_disabled_by_default_and_idempotent(client, monkeypatch):
    base = "/v1/impact-triage"
    items = client.get(base + "/ranking?view=reference&include_pending=true").json()["items"]
    entity_id = next(x["id"] for x in items if x["name"] == "智谱")
    direction_id = next(x["id"] for x in client.get(base + "/directions").json()["items"] if x["level"] == 2)
    body = {"batch_id": "reviewed-batch-1", "events": [{"external_id": "paper-1", "title": "已核验新论文", "event_date": "2026-09-25",
        "entity_id": entity_id, "direction_id": direction_id, "source_url": "https://example.com/paper", "l3_label": "模型保障",
        "source_content_sha256": hashlib.sha256(b"Original source body one").hexdigest(),
        "source_excerpt": "Original source body one"}]}
    assert client.post(base + "/ingest", json=body).status_code == 403
    monkeypatch.setenv("AI4S_IMPACT_MANUAL_INGEST_ENABLED", "1")
    assert client.post(base + "/ingest", json=body).status_code == 400
    case = next(x for x in client.get(base + "/reviews").json()["items"] if x["kind"] == "possible_duplicate")
    assert client.post(base + "/reviews/" + case["id"] + "/identity",
                       json={"decision": "false_positive", "note": "测试样例已核实为不同主体"}).status_code == 200
    assert client.post(base + "/entities/" + entity_id + "/eligibility",
                       json={"decision": "eligible", "note": "来源原文已核验"}).status_code == 400
    assert client.post(base + "/entities/" + entity_id + "/eligibility",
                       json={"decision": "eligible", "note": "来源原文已核验", "research_type": "corporate_research",
                             "evidence_url": "https://example.com/research", "mainland_confirmed": True}).status_code == 200
    assert client.post(base + "/directions/" + direction_id + "/review",
                       json={"decision": "approve", "note": "方向映射已核验"}).status_code == 200
    first = client.post(base + "/ingest", json=body).json()
    assert first["created"] == 1 and first["paid_calls"] == 0 and first["scored"] == 0
    assert client.post(base + "/ingest", json=body).json()["duplicate_batch"] is True
    assert "新增事件版本 1 条" in client.get(base + "/daily/" + store.business_today()).json()["markdown"]
    detail = client.get(base + "/entities/" + entity_id).json()
    assert any(e["sources"][0]["url"] == "https://example.com/paper" for e in detail["events"])
    assert any(e["l3_label"] == "模型安全" for e in detail["events"] if e["origin"] == "manual_reviewed")
    rank_before = next(x for x in client.get(base + "/ranking?view=reference&include_pending=true").json()["items"] if x["id"] == entity_id)["event_count"]
    duplicate = {"batch_id": "reviewed-batch-2", "events": [{**body["events"][0],
        "external_id": "paper-2", "source_url": "https://example.com/paper?utm_source=news"}]}
    assert client.post(base + "/ingest", json=duplicate).json()["created"] == 0
    revised = {"batch_id": "reviewed-batch-3", "events": [{**body["events"][0],
        "external_id": "paper-3", "source_content_sha256": hashlib.sha256(b"Original source body revised").hexdigest(),
        "source_excerpt": "Original source body revised"}]}
    assert client.post(base + "/ingest", json=revised).json()["revised"] == 1
    rank_after = next(x for x in client.get(base + "/ranking?view=reference&include_pending=true").json()["items"] if x["id"] == entity_id)["event_count"]
    assert rank_after == rank_before
    assert len([e for e in client.get(base + "/entities/" + entity_id).json()["events"]
                if e["origin"] == "manual_reviewed"]) == 2


def test_official_rank_requires_domestic_research_evidence_and_search(client):
    base = "/v1/impact-triage"
    reference = client.get(base + "/ranking?view=reference&include_pending=true&q=成果甲").json()["items"]
    assert [r["name"] for r in reference] == ["智谱"]
    entity_id = reference[0]["id"]
    case = next(x for x in client.get(base + "/reviews").json()["items"] if x["kind"] == "possible_duplicate")
    assert client.post(base + "/reviews/" + case["id"] + "/identity",
                       json={"decision": "false_positive", "note": "测试暂作不同主体"}).status_code == 200
    assert client.post(base + "/entities/" + entity_id + "/eligibility",
                       json={"decision": "eligible", "note": "具体研发机构官网材料",
                             "research_type": "public_research", "evidence_url": "https://example.com/institute",
                             "mainland_confirmed": True}).status_code == 200
    assert [r["id"] for r in client.get(base + "/ranking").json()["items"]] == [entity_id]
    author_id = store.stable_id("ent", 99)
    with store.connect(write=True) as conn:
        conn.execute("INSERT INTO impact_entity (id,name,kind,country) VALUES(?,?,?,?)", (author_id, "某作者", "author", "zn"))
        conn.commit()
    assert client.post(base + f"/entities/{author_id}/eligibility",
                       json={"decision": "eligible", "note": "作者不是机构", "research_type": "university",
                             "evidence_url": "https://example.com/author"}).status_code == 400


def test_active_calibrated_revision_is_read_without_changing_source_scores(client):
    base = "/v1/impact-triage"
    entity_id = store.stable_id("ent", 21)
    case = next(x for x in client.get(base + "/reviews").json()["items"] if x["kind"] == "possible_duplicate")
    assert client.post(base + "/reviews/" + case["id"] + "/identity", json={
        "decision": "false_positive", "note": "已核实为不同主体"}).status_code == 200
    assert client.post(base + f"/entities/{entity_id}/eligibility", json={
        "decision": "eligible", "note": "官网证实科研机构及内地所在地",
        "research_type": "public_research", "evidence_url": "https://example.com/institute",
        "mainland_confirmed": True}).status_code == 200
    with store.connect(write=True) as conn:
        source_before = [tuple(row) for row in conn.execute(
            "SELECT id,scan_date,tier,eff_achievement,eff_status,eff_trend FROM impact_score_period ORDER BY id")]
        conn.execute("""INSERT INTO impact_score_revision
            (id,entity_id,scan_date,score_version,calibration_status,
             raw_achievement,raw_status,raw_trend,eff_achievement,eff_status,eff_trend,
             tier,change_reason,reasons_json,input_hash,active,created_on)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                "revision-calibrated", entity_id, "2026-09-21", "ai4s-calibrated-v1", "calibrated",
                50, 55, 60, 50, 55, 60, "C", "样本校准后重评", "{}", "reviewed-input", 1,
                store.business_today()))
        conn.commit()
    official = client.get(base + "/ranking?tier=C").json()["items"]
    assert len(official) == 1
    assert official[0]["id"] == entity_id
    assert official[0]["tier"] == "C"
    assert official[0]["score_source"] == "calibrated_revision"
    assert official[0]["score_version"] == "ai4s-calibrated-v1"
    assert client.get(base + "/ranking?tier=A").json()["items"] == []
    with store.connect() as conn:
        source_after = [tuple(row) for row in conn.execute(
            "SELECT id,scan_date,tier,eff_achievement,eff_status,eff_trend FROM impact_score_period ORDER BY id")]
    assert source_after == source_before


def test_existing_label_collision_maps_and_rolls_back(client):
    base = "/v1/impact-triage"
    direction_id = store.stable_id("dir", "existing-l3")
    parent_id = store.stable_id("dir", 2)
    with store.connect(write=True) as conn:
        conn.execute("""INSERT INTO impact_direction (id,name,parent_id,level,status,review_status,created_on)
            VALUES(?,?,?,?,?,?,?)""", (direction_id, "新方向", parent_id, 3, "formal", "source_unreviewed", store.business_today()))
        conn.commit()
    response = client.post(base + "/unclassified/resolve-existing", json={
        "label": "新方向", "direction_id": direction_id, "note": "同名原文已核对"})
    assert response.status_code == 200 and response.json()["mapped_events"] == 1
    assert response.json()["new_node"] is False
    assert client.get(base + "/unclassified/events?label=新方向").json()["items"] == []
    assert any(x["title"] == "树外成果" for x in client.get(base + f"/directions/{direction_id}/events").json()["items"])
    assert "归一标签" in client.get(base + "/daily/" + store.business_today()).json()["markdown"]
    audit_id = response.json()["audit_id"]
    assert client.post(base + f"/event-mappings/{audit_id}/rollback").json()["events"] == 1
    assert len(client.get(base + "/unclassified/events?label=新方向").json()["items"]) == 1
