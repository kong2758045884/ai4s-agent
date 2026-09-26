import sqlite3

from ai4s_tool.api import verified_team_catalogue as catalogue
from ai4s_tool.api import task_recommendations as task
from ai4s_tool.api import triage_discovery
from ai4s_tool.api.official_team_directory import _signature


def directory():
    url = "https://collegeai.tsinghua.edu.cn/kxyj/ktzjs.htm"
    record = {"team_name": "测试课题组", "institution_name": "清华大学人工智能学院",
              "domain": "通用 AI", "description": "具身智能机器人的研究",
              "directions": ["具身智能"], "source_urls": [url],
              "identity_basis": "official_named_group_card",
              "citations": [{"url": url, "quote": q} for q in
                            ("测试课题组 PI 张某", "研究具身智能机器人", "发表机器人控制论文")]}
    return {"id": "official-1", "status": "official_directory", "payload": {
        "published": True, "signature": _signature(record), "record": record}}


def team():
    return {"id": "t", "institutionName": "清华大学人工智能学院", "teamName": "测试课题组",
            "isDomestic": True, "verificationStatus": "verified", "candidateQualified": False,
            "scoreTotal": 48, "description": "未审核的旧简介", "domainId": "ai"}


def test_official_identity_and_outcome_allow_recommendation_without_roster_or_score_gate(monkeypatch):
    value, claims = catalogue.project(team(), [directory()])
    assert value["description"] == "具身智能机器人的研究"
    assert value["candidateQualified"] is False and value["scoreTotal"] == 48
    assert any(c[3] == "outcome" for c in claims)
    monkeypatch.setattr(task, "_catalogue_evidence", lambda: ([value], claims, "v2"))
    assert task.verified_teams()["teamIds"] == ["t"]
    assert task._candidate_evidence()[0] == [value]
    monkeypatch.setattr(task, "ELIGIBILITY_VERSION", "staffing-v1")
    assert task._candidate_evidence()[0] == []
    value["candidateQualified"] = True
    assert task._candidate_evidence()[0] == [value]


def test_manual_core_direction_is_preserved_in_public_catalogue():
    value = {**team(), 'coreDirection': '人工保存的完整方向' * 30, 'coreDirectionSource': 'manual'}
    public, _ = catalogue.project(value, [directory()])
    assert public['coreDirection'] == value['coreDirection']
    value['coreDirectionSource'] = 'research'
    public, _ = catalogue.project(value, [directory()])
    assert public['coreDirection'] == '、'.join(public['researchDirections'])


def test_directory_cannot_publish_tampered_foreign_or_parent_identity():
    record = directory()
    record["payload"]["record"]["description"] = "伪造"
    assert catalogue.project(team(), [record]) is None
    value = team(); value["isDomestic"] = False
    assert catalogue.project(value, [directory()]) is None
    value = team(); value["teamName"] = "清华大学"
    assert catalogue.project(value, [directory()]) is None
    value = team(); value["verificationStatus"] = "conflict"
    assert catalogue.project(value, [directory()]) is None


def test_later_withdrawal_blocks_old_directory_and_failed_attempt_does_not():
    record = directory()
    assert catalogue.project(team(), [{"status": "revoked"}, record]) is None
    assert catalogue.project(team(), [{"status": "model_failed"}, record])
    assert catalogue.project(team(), [{"status": "official_directory_conflict"}, record]) is None


def test_description_is_not_automatically_a_team_outcome():
    entry = directory()
    entry["payload"]["record"]["identity_basis"] = "current_group_head_directory_and_personal_group_profile"
    entry["payload"]["signature"] = _signature(entry["payload"]["record"])
    _, claims = catalogue.project(team(), [entry])
    assert not any(c[3] == "outcome" for c in claims)


def test_triage_events_seed_discovery_without_cross_domain_or_author_pollution(tmp_path, monkeypatch):
    path = tmp_path / "source.db"
    with sqlite3.connect(path) as c:
        c.executescript("""CREATE TABLE impact_entity (id TEXT,name TEXT,kind TEXT,country TEXT);
        CREATE TABLE impact_entity_direction (entity_id TEXT,direction_id TEXT);
        CREATE TABLE impact_direction (id TEXT,ai4s_domain_id TEXT);
        CREATE TABLE impact_event (entity_id TEXT,title TEXT,summary TEXT);
        INSERT INTO impact_direction VALUES ('a','ai'),('m','materials');
        INSERT INTO impact_entity VALUES ('1','清华大学','institution','zn'),
            ('2','企业品牌','institution','zn'),('3','作者某','author','zn'),
            ('4','境外大学','institution','gw'),('5','材料研究所','institution','zn');
        INSERT INTO impact_entity_direction VALUES ('1','a'),('2','a'),('3','a'),('4','a'),('5','m');
        INSERT INTO impact_event VALUES ('1','具身智能控制成果','机器人控制');""")
    monkeypatch.setattr(triage_discovery.impact_store, "DB_PATH", path)
    assert triage_discovery.institution_seeds("ai") == ["清华大学"]
    assert triage_discovery.institution_seeds("ai", "量子计算") == []
    assert triage_discovery.institution_seeds("ai", "具身智能") == ["清华大学"]


def test_enrichment_stays_in_scope_and_reserves_discovery_time(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from ai4s_tool.api import strategic_map as sm, team_enrichment as enrichment
    engine = create_engine(f"sqlite:///{tmp_path / 'scoped.db'}")
    sm._Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        session.add_all([sm.StrategicDomainRow(id="a", name="领域甲"),
                         sm.StrategicDomainRow(id="b", name="领域乙")])
        session.add_all([sm.StrategicTeamRow(id="a1", name="大学甲", domain_id="a", team_name="量子组"),
                         sm.StrategicTeamRow(id="b1", name="大学乙", domain_id="b", team_name="材料组")])
        session.commit()
        assert [t.team_id for t in enrichment._collect_targets(session, domain_id="a")] == ["a1"]
    monkeypatch.setattr(enrichment, "_fetch_all", lambda _: (_ for _ in ()).throw(AssertionError("no time for network")))
    result = enrichment.enrich_teams(factory, domain_id="a", deadline=0)
    assert result["scanned"] == 1 and result["deferred"] == 1
    engine.dispose()
