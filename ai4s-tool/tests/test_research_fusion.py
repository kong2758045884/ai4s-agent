"""Offline contracts for scoped fusion; no source services or live DB writes."""
import hashlib
import json
from datetime import date

import pytest

from ai4s_tool.api import research_fusion as fusion
from ai4s_tool.api import impact_store as store
from scripts.reconcile_hyper_graph import reconcile


@pytest.fixture
def impact(tmp_path, monkeypatch):
    path = tmp_path / 'impact.db'
    with store.connect(path, write=True) as conn:
        store.init_schema(conn)
        conn.executescript("""
            INSERT INTO impact_direction(id,name,parent_id,level,status,ai4s_domain_id,ai4s_subdomain_id,created_on) VALUES
              ('root','高原',NULL,1,'formal',NULL,NULL,'2026-09-01'),
              ('d','通用 AI','root',2,'formal','ai',NULL,'2026-09-01'),
              ('leaf','模型安全','d',3,'formal',NULL,'safety','2026-09-01'),
              ('other','地球科学','root',2,'formal','earth',NULL,'2026-09-01');
            INSERT INTO impact_entity(id,name,kind,country,eligibility,research_type,eligibility_basis_url,mainland_confirmed) VALUES
              ('institute','研究院','institution','zn','eligible','university','https://example.edu/about',1),
              ('club','社群','institution','zn','excluded','community','',0);
            INSERT INTO impact_entity_direction VALUES ('institute','d'),('club','d');
            INSERT INTO impact_event(id,entity_id,direction_id,title,event_date,imported_on,origin,l3_label,fingerprint) VALUES
              ('a','institute','d','安全成果','2026-09-08','2026-09-26','source_snapshot','模型安全','a'),
              ('b','institute','d','另一成果','2026-09-08','2026-09-26','source_snapshot','其他','b'),
              ('c','club','d','社群活动','2026-09-08','2026-09-26','source_snapshot','模型安全','c'),
              ('x','institute','other','其他领域成果','2026-09-08','2026-09-26','source_snapshot','气候','x');
            INSERT INTO impact_event_source(id,event_id,url,title,content_sha256) VALUES
              ('url','a','https://example.edu/paper','论文','hash-v1');
            INSERT INTO impact_score_period(id,entity_id,scan_date,tier,score_version,eff_achievement,eff_status,eff_trend) VALUES
              ('p','institute','2026-09-08','B','hyperextract-v5-source',70,60,65);
        """)
        conn.commit()
    monkeypatch.setattr(store, 'DB_PATH', path)
    monkeypatch.setattr(fusion.tasks, '_validate_scope', lambda domain, subdomain: None)
    monkeypatch.setattr(fusion.tasks, 'verified_teams', lambda: {'teams': [
        {'id': 't', 'domainId': 'ai', 'subdomainId': 'safety'},
        {'id': 'e', 'domainId': 'earth', 'subdomainId': 'climate'}]})
    monkeypatch.setattr(fusion.tasks, 'verified_daily', lambda day: {
        'frozen': False, 'revision': 0,
        'teamChanges': [{'teamId': 't'}, {'teamId': 'e'}],
        'recommendationChanges': [{'domainId': None, 'before': ['e'], 'after': ['t', 'e'], 'taskText': '跨领域任务'}]})
    return path


def test_daily_replays_readonly_with_precise_l3_mapping(impact):
    before = hashlib.sha256(impact.read_bytes()).hexdigest()
    result = fusion.integrated_daily(date(2026, 9, 8), 'ai', 'safety')
    assert [r['id'] for r in result['sourceEvents']] == ['a']
    assert [r['teamId'] for r in result['teamChanges']] == ['t']
    assert result['recommendationChanges'][0]['before'] == []
    assert result['recommendationChanges'][0]['after'] == ['t']
    assert result['scoreChanges'] == []  # An L2 institution score is not an L3 score.
    assert result == fusion.integrated_daily(date(2026, 9, 8), 'ai', 'safety')
    assert before == hashlib.sha256(impact.read_bytes()).hexdigest()
    assert not fusion.integrated_daily(date(2026, 9, 26), 'ai', None)['sourceEvents']


def test_daily_hash_tracks_scores_and_original_content(impact):
    day = date(2026, 9, 8)
    first = fusion.integrated_daily(day, 'ai', None)
    assert [r['id'] for r in first['sourceEvents']] == ['a', 'b']
    assert len(first['scoreChanges']) == 1
    with store.connect(impact, write=True) as conn:
        conn.execute("UPDATE impact_score_period SET eff_achievement=71 WHERE id='p'")
        conn.commit()
    second = fusion.integrated_daily(day, 'ai', None)
    assert first['inputHash'] != second['inputHash']
    with store.connect(impact, write=True) as conn:
        conn.execute("UPDATE impact_event_source SET content_sha256='hash-v2' WHERE id='url'")
        conn.commit()
    assert second['inputHash'] != fusion.integrated_daily(day, 'ai', None)['inputHash']


def test_fusion_preserves_subject_types_and_no_foreign_team_overlay(monkeypatch):
    graph = fusion.graph
    source = {'nodes': [graph._node('raw-org', '研究院', {'name': '研究院', 'level': '机构'}),
                        graph._node('author', '作者', {'name': '作者', 'level': '作者'})],
              'edges': [graph._edge('author', 'raw-org', '就职于')]}
    overlay = {'nodes': [graph._node('org', '研究院', {'name': '研究院', 'level': '机构'}),
                         graph._node('team:t', '已核团队', {'name': '已核团队', 'level': '科研团队'})],
               'edges': [graph._edge('team:t', 'org', '所属机构')]}
    monkeypatch.setattr(graph, '_context_names', lambda d, s: ('通用 AI', ''))
    monkeypatch.setattr(graph, '_context_graph_base', lambda *args: source)
    monkeypatch.setattr(graph, '_filter_persisted_graph_scope', lambda base, **kwargs: base)
    monkeypatch.setattr(fusion.tasks, 'verified_graph', lambda *args: overlay)
    local = fusion.integrated_graph('domestic', '高原', 'ai')
    assert len(local['nodes']) == 4
    assert graph._element_raw(local['nodes'][0])['level'] == '机构'
    assert graph._element_raw(local['nodes'][1])['level'] == '作者'
    assert [n['id'] for n in local['nodes'] if graph._element_raw(n)['level'] == '科研团队'] == ['team:t']
    assert any(e['source'] == 'hyper:raw-org' and e['target'] == 'org' for e in local['edges'])
    assert not any(e['source'].startswith('hyper:') and e['target'] == 'team:t' for e in local['edges'])
    foreign = fusion.integrated_graph('international', '高原', 'ai')
    assert all(n['id'].startswith('hyper:') for n in foreign['nodes'])
    mismatch = fusion.integrated_graph('domestic', '高峰', 'ai')
    assert not any(n['id'].startswith('team:') for n in mismatch['nodes'])


def test_snapshot_reconciliation_refuses_overwrite_and_bad_edges(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'target'
    payload = {'nodes': [{'name': '研究院'}], 'edges': []}
    for scope in ('ZN', 'GW'):
        folder = source / scope
        folder.mkdir(parents=True)
        (folder / 'data.json').write_text(json.dumps(payload), encoding='utf-8')
    result = reconcile(source, target)
    assert result['scopes']['ZN'] == {'nodes': 1, 'edges': 0, 'dangling': 0}
    assert result == reconcile(source)
    with pytest.raises(FileExistsError):
        reconcile(source, target)
    assert json.loads((source / 'ZN/data.json').read_text()) == payload
    payload['edges'] = [{'source': '研究院', 'target': '不存在'}]
    (source / 'ZN/data.json').write_text(json.dumps(payload), encoding='utf-8')
    with pytest.raises(ValueError, match='dangling'):
        reconcile(source)
