import json
import sqlite3
from contextlib import closing
from datetime import date

import pytest
from fastapi import HTTPException

from ai4s_tool.api import strategic_changes as changes, strategic_outcomes as outcomes
from ai4s_tool.api import strategic_investigations as investigations, task_recommendations as tasks


@pytest.fixture
def storage(tmp_path, monkeypatch):
    path = tmp_path / 'team.db'
    def db(*, write=False):
        c = sqlite3.connect(path); c.row_factory = sqlite3.Row
        return c
    monkeypatch.setattr(tasks, '_db', db)
    monkeypatch.setattr(tasks, '_validate_scope', lambda *a: None)
    monkeypatch.setattr(tasks, '_institution_links', lambda: {})
    with closing(db()) as c, c:
        tasks._schema(c); changes.init(c); investigations.init(c); outcomes.init(c)
        c.execute('CREATE TABLE strategic_map_team (id TEXT PRIMARY KEY,domain_id TEXT)')
        c.execute("INSERT INTO strategic_map_team VALUES('t','life')")
    return db


def test_outcome_requires_specific_ownership_and_commits_outbox_together(storage):
    team = {'id': 't', 'teamName': '测试研究中心', 'sourceUrls': ['https://example.edu.cn/team'], 'domainId': 'life'}
    quote = '测试研究中心的团队揭示了蛋白质结构预测实验中的关键机制，并发表研究论文。'
    page = {'url': team['sourceUrls'][0], 'status': 'ok', 'text': quote}
    identity = {'teamId': 't', 'url': page['url'], 'quote': '测试研究中心', 'sourceText': quote}
    with closing(storage()) as conn:
        with pytest.raises(ValueError):
            outcomes.publish(conn, team, page, [{'quote': quote}], batch_id='x', ownership={**identity, 'quote': '团队'})
        with conn:
            result = outcomes.publish(conn, team, page, [{'quote': quote}], batch_id='x', ownership=identity)
        assert len(result['added']) == 1
        assert conn.execute('SELECT count(*) FROM strategic_change_event').fetchone()[0] == 1
        assert outcomes.valid_source(conn.execute('SELECT * FROM strategic_team_outcome').fetchone())
        assert outcomes.publish(conn, team, page, [{'quote': quote}], batch_id='x', ownership=identity)['duplicates'] == result['added']
        conn.rollback()
        conn.execute("UPDATE strategic_team_outcome SET updated_at='edited'")
        with pytest.raises(ValueError, match='changed after import'):
            outcomes.revoke_batch(conn, 'x')
        conn.rollback()
        with conn:
            assert outcomes.revoke_batch(conn, 'x') == 1
        assert conn.execute("SELECT kind FROM strategic_change_event WHERE kind='outcome_revoked'").fetchone()
        conn.execute("DELETE FROM strategic_change_event")
        conn.rollback()
        assert conn.execute('SELECT count(*) FROM strategic_change_event').fetchone()[0] == 2


def evidence(monkeypatch):
    team = {'id': 't', 'teamName': '测试组', 'name': '大学', 'institutionName': '大学', 'domainId': 'life', 'subdomainId': None,
            'domainName': '生命科学与医学', 'researchDirections': ['蛋白质'], 'description': '蛋白质', 'scoreTotal': 80}
    claim = ('c', 't', 'r', 'outcome', '蛋白质研究', '蛋白质研究成果原文', 'https://example.edu.cn/paper', '')
    state = {'version': 'v1', 'teams': [team], 'claims': [claim]}
    monkeypatch.setattr(tasks, '_candidate_evidence', lambda: (state['teams'], state['claims'], state['version']))
    monkeypatch.setattr(tasks, 'verified_teams', lambda: {'teams': state['teams']})
    return state


def test_withdrawal_recommends_new_version_once_and_preserves_old_run(storage, monkeypatch):
    state = evidence(monkeypatch)
    original = tasks.recommend(tasks.TaskRequest(taskText='蛋白质', domainId='life', limit=5))
    state.update(version='v2', claims=[], teams=[])
    with closing(storage()) as conn, conn:
        changes.record(conn, subject_type='team', subject_id='t', domain_id='life', kind='revoked', source_id='r')
    assert changes.process_pending()['processed'] == 1
    old = tasks.recommendation(original['runId'])
    updated = tasks.recommendation(old['updatedRunId'])
    assert len(old['items']) == 1 and updated['items'] == []
    assert updated['previousRunId'] == original['runId']
    assert updated['changes']['removed'] == ['t']
    assert changes.process_pending()['processed'] == 0
    with closing(storage()) as conn:
        assert conn.execute('SELECT count(*) FROM strategic_task_recommendation_run').fetchone()[0] == 2


def test_comparison_key_includes_limit_scope_and_match_version():
    base = {'taskText': '量子计算', 'requestedLimit': 5, 'matchVersion': 'v3', 'domainId': 'q', 'subdomainId': 'sub'}
    assert changes.task_key(base) == changes.task_key({**base, 'taskText': '请帮我推荐能够开展量子计算的国内团队'})
    for patch in ({'requestedLimit': 10}, {'domainId': None}, {'subdomainId': None}, {'matchVersion': 'v4'}):
        assert changes.task_key(base) != changes.task_key({**base, **patch})


def test_search_all_59_rows_pagination_and_subdomain_isolation(storage, monkeypatch):
    state = evidence(monkeypatch)
    state['teams'][0]['subdomainId'] = 'protein'
    rows = [(f'c-{i}', 't', 'r', 'outcome', '天气预报', f'中国科学院天气预报证据{i}', f'https://example.edu.cn/{i}', '') for i in range(59)]
    monkeypatch.setattr(tasks, '_catalogue_evidence', lambda: (state['teams'], rows, 'v1'))
    found = []
    snapshot_id = None
    for page in range(1, 4):
        result = tasks.intelligence_search(q='中科院 预测', domain_id='life', subdomain_id='protein', page=page, size=20,
                                           verified_only=True, snapshot_id=snapshot_id)
        snapshot_id = result['snapshotId']
        assert result['total'] == 59
        found.extend(row['id'] for row in result['items'])
    assert len(found) == len(set(found)) == 59
    assert tasks.intelligence_search(q='天气', domain_id='life', subdomain_id='other', page=1, size=20, verified_only=True)['total'] == 0


def test_search_filters_profiles_sources_dates_and_preserves_saved_snapshot(storage, monkeypatch):
    state = evidence(monkeypatch)
    team = state['teams'][0]
    team.update(teamAliases=['蛋白结构组'], institutionAliases=[], researchDirections=['蛋白质设计'],
                catalogueBasis='official_directory',
                claimProvenance={
                    'official': {'sourceType': 'official_institution',
                                 'humanReview': {'status': 'reviewed', 'decision': 'supported'}},
                    'paper': {'sourceType': 'publication_or_webpage',
                              'humanReview': {'status': 'not_recorded'}},
                })
    claims = [
        ('official', 't', 'r', 'outcome', '蛋白质结构成果', '蛋白质结构原文',
         'https://example.edu.cn/official', '2026-05-01'),
        ('paper', 't', 'r', 'outcome', '蛋白质论文', '蛋白质论文原文',
         'https://example.org/paper', '2026-06-01'),
    ]
    current = {'version': 'v1'}
    monkeypatch.setattr(tasks, '_catalogue_evidence', lambda: ([team], claims, current['version']))

    all_items = tasks.intelligence_search(q='蛋白', page=1, size=20, entity_type='all')
    assert [(item['type'], item['id']) for item in all_items['items']] == [
        ('team_profile', 't'), ('team_claim', 'official'), ('team_claim', 'paper')]
    assert all_items['items'][0]['matchReason'] == '团队曾用名'
    assert all_items['items'][1]['humanReviewStatus'] == 'reviewed'
    assert all_items['snapshotId'] and all_items['pageSize'] == 20

    official = tasks.intelligence_search(q='蛋白', page=1, size=20, entity_type='team_claim',
                                         source_status='official', date_from=date(2026, 5, 1),
                                         date_to=date(2026, 5, 31))
    assert [item['id'] for item in official['items']] == ['official']
    reviewed = tasks.intelligence_search(q='蛋白', page=1, size=20, entity_type='all',
                                         source_status='human_reviewed')
    assert [item['id'] for item in reviewed['items']] == ['official']
    current['version'] = 'v2'
    claims.append(('new', 't', 'r', 'outcome', '蛋白质新增论文', '新增原文',
                   'https://example.edu.cn/new', '2026-07-01'))
    old_page = tasks.intelligence_search(q='蛋白', page=2, size=20, entity_type='all',
                                         snapshot_id=all_items['snapshotId'])
    assert old_page['total'] == 3 and old_page['items'] == []
    assert old_page['dataVersion'] == 'v1' and old_page['snapshotId'] == all_items['snapshotId']
    assert tasks.intelligence_search(q='蛋白', page=1, size=20, entity_type='all')['total'] == 4
    with pytest.raises(HTTPException) as changed:
        tasks.intelligence_search(q='蛋白', page=2, size=10, entity_type='all',
                                  snapshot_id=all_items['snapshotId'])
    assert changed.value.status_code == 409
    claims.remove(claims[0])
    with pytest.raises(HTTPException, match='核验状态已改变') as withdrawn:
        tasks.intelligence_search(q='蛋白', page=2, size=20, entity_type='all',
                                  snapshot_id=all_items['snapshotId'])
    assert withdrawn.value.status_code == 409


def test_search_snapshot_rechecks_review_state(storage, monkeypatch):
    state = evidence(monkeypatch)
    team = state['teams'][0]
    team['claimProvenance'] = {'c': {'humanReview': {'status': 'reviewed', 'decision': 'supported'}}}
    monkeypatch.setattr(tasks, '_catalogue_evidence', lambda: (state['teams'], state['claims'], state['version']))
    first = tasks.intelligence_search(q='蛋白质', page=1, size=20, entity_type='team_claim')
    team['claimProvenance']['c']['humanReview']['decision'] = 'conditional'
    with pytest.raises(HTTPException, match='核验状态已改变'):
        tasks.intelligence_search(q='蛋白质', page=1, size=20, entity_type='team_claim',
                                  snapshot_id=first['snapshotId'])


def test_search_snapshot_survives_new_connection_and_expires(storage, monkeypatch):
    state = evidence(monkeypatch)
    monkeypatch.setattr(tasks, '_catalogue_evidence', lambda: (state['teams'], state['claims'], state['version']))
    first = tasks.intelligence_search(q='蛋白质', page=1, size=20, entity_type='all')
    assert first['snapshotAt'] < first['expiresAt']
    assert tasks.intelligence_search(q='蛋白质', page=1, size=20, entity_type='all',
                                     snapshot_id=first['snapshotId'])['items'] == first['items']
    monkeypatch.setattr(tasks.time, 'time', lambda: first['expiresAt'] + 1)
    with pytest.raises(HTTPException, match='过期') as expired:
        tasks.intelligence_search(q='蛋白质', page=2, size=20, entity_type='all',
                                  snapshot_id=first['snapshotId'])
    assert expired.value.status_code == 409


def test_aliases_require_evidenced_rename_and_keep_canonical_team_id(storage, monkeypatch):
    team = {'id': 'team-1', 'teamName': '新名称', 'institutionName': '哈尔滨工业大学',
            'domainId': 'ai', 'subdomainId': None, 'description': '机器人研究'}
    approved = {'status': 'verified', 'payload': {'published': True,
        'before': {'teamName': '旧名称', 'institutionName': '旧机构名'},
        'run': {'reviewed': {'entity_relation': 'rename', 'entity_citations': [{'url': 'https://example.edu.cn/name'}]}}}}
    unapproved = {'status': 'verified', 'payload': {'published': False,
        'before': {'teamName': '未发布的别名'},
        'run': {'reviewed': {'entity_relation': 'rename', 'entity_citations': [{'url': 'https://example.edu.cn/name'}]}}}}
    disputed = {'status': 'verified', 'payload': {'published': True,
        'before': {'teamName': '未证明同一主体'},
        'run': {'reviewed': {'entity_relation': 'different', 'entity_citations': [{'url': 'https://example.edu.cn/name'}]}}}}
    team_names, institutions = tasks._trusted_aliases(team, [approved, unapproved, disputed])
    assert team_names == ['旧名称']
    assert institutions == ['哈工大', '旧机构名']
    team.update(teamAliases=team_names, institutionAliases=institutions)
    claim = ('claim-1', 'team-1', 'run-1', 'outcome', '机器人', '机器人研究成果',
             'https://example.edu.cn/paper', '')
    monkeypatch.setattr(tasks, '_validate_scope', lambda *_: None)
    monkeypatch.setattr(tasks, '_catalogue_evidence', lambda: ([team], [claim], 'alias-v1'))
    for query in ('新名称', '旧名称', '哈工大'):
        found = tasks.intelligence_search(q=query, entity_type='all', page=1, size=20)
        assert found['total'] == 2
        assert {item['teamId'] for item in found['items']} == {'team-1'}
        if query == '旧名称':
            assert all(item['matchReason'] == '团队曾用名' and item['matchedAlias'] == '旧名称' for item in found['items'])
        if query == '哈工大':
            assert all(item['matchReason'] == '机构别名' and item['matchedAlias'] == '哈工大' for item in found['items'])
    canonical = tasks.intelligence_search(q='哈尔滨工业大学', entity_type='all', page=1, size=20)
    assert all(item['matchReason'] == '所属机构' and item['matchedAlias'] is None for item in canonical['items'])
    assert tasks.intelligence_search(q='未证明同一主体', entity_type='all', page=1, size=20)['total'] == 0


def test_explicit_investigation_duplicate_retry_restart_and_failed_results_retained(storage, monkeypatch):
    evidence(monkeypatch)
    original = tasks.recommend(tasks.TaskRequest(taskText='蛋白质', domainId='life'))
    monkeypatch.setattr(tasks.strategic_map, '_llm_config', lambda: ('configured', 'local', 'test'))
    queued = []
    monkeypatch.setattr(investigations._POOL, 'submit', lambda *args: queued.append(args))
    job = investigations.start(original['runId'])
    assert investigations.start(original['runId']) == job and len(queued) == 1
    investigations.recover_interrupted()
    assert investigations.get(job)['state'] == 'interrupted'
    assert tasks.recommendation(original['runId'])['items'] == original['items']
    retry = investigations.start(original['runId'], retry=True)
    assert retry != job
    monkeypatch.setattr(investigations, '_research_one', lambda *args: {'published': True, 'calls': {'search': 1}, 'reason': ''})
    investigations._execute(retry, original)
    assert investigations.get(retry)['state'] == 'completed'
    assert tasks.recommendation(original['runId'])['updatedRunId']


def test_search_retry_only_transient(monkeypatch):
    from ai4s_tool.api import team_research
    calls = []
    class Transient(Exception):
        status_code = 429
    async def flaky(query):
        calls.append(query)
        if len(calls) <= 3:
            raise Transient()
        return [{'url': 'https://example.edu.cn'}]
    monkeypatch.setattr(team_research, 'search_links', flaky)
    monkeypatch.setattr(investigations.time, 'sleep', lambda *a: None)
    assert investigations._search('task') and len(calls) == 4
    class Auth(Exception):
        status_code = 401
    async def auth(query):
        raise Auth()
    monkeypatch.setattr(team_research, 'search_links', auth)
    with pytest.raises(Auth):
        investigations._search('task')


def test_retry_skips_already_published_units(storage, monkeypatch):
    evidence(monkeypatch)
    original = tasks.recommend(tasks.TaskRequest(taskText='蛋白质', domainId='life'))
    monkeypatch.setattr(tasks.strategic_map, '_llm_config', lambda: ('test', 'test', 'test'))
    monkeypatch.setattr(investigations._POOL, 'submit', lambda *a: None)
    job = investigations.start(original['runId'])
    investigations._save(job, state='partial', stage='调查完成', publishedTeams=['t'], batchTeamIds=['t'])
    retry = investigations.start(original['runId'], retry=True)
    def forbidden(*a):
        pytest.fail('已发布团队不得在失败重试中重复调用提供方')
    monkeypatch.setattr(investigations, '_research_one', forbidden)
    investigations._execute(retry, original)
    assert investigations.get(retry)['state'] == 'completed'
    assert investigations.get(retry)['calls']['llm'] == 0


def test_identity_change_revises_recommendation_without_overwriting_original(storage, monkeypatch):
    evidence(monkeypatch)
    original = tasks.recommend(tasks.TaskRequest(taskText='蛋白质', domainId='life'))
    monkeypatch.setattr(tasks, '_institution_links', lambda: {'t': {'id': 'institution-1', 'name': '大学', 'eligibility': 'eligible'}})
    with closing(storage()) as conn, conn:
        changes.record(conn, subject_type='institution', subject_id='institution-1', kind='identity', source_id='identity-1')
    assert changes.process_pending()['processed'] == 1
    old = tasks.recommendation(original['runId'])
    new = tasks.recommendation(old['updatedRunId'])
    assert new['items'][0]['institutionId'] == 'institution-1'
    assert old['items'][0]['institutionId'] is None


def test_fetch_retry_cap_and_cross_job_concurrency(monkeypatch):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    from ai4s_tool.api import team_research as research
    active = maximum = calls = 0
    lock = threading.Lock()
    def fetch(*a, **kw):
        nonlocal active, maximum, calls
        with lock:
            active += 1; maximum = max(maximum, active); calls += 1
        time.sleep(.015)
        with lock:
            active -= 1
        return {'status': 'ok', 'http_status': 200, 'request_count': 1}
    monkeypatch.setattr(research, '_fetch_page_once', fetch)
    with ThreadPoolExecutor(max_workers=12) as pool:
        assert all(p['status'] == 'ok' for p in pool.map(research.fetch_page, ['https://example.edu.cn'] * 12))
    assert maximum <= 4 and calls == 12
    monkeypatch.setattr(research, '_fetch_page_once', lambda *a, **kw: {'status': 'fetch_failed', 'http_status': 503, 'request_count': 1})
    monkeypatch.setattr(research.time, 'sleep', lambda *a: None)
    assert research.fetch_page('https://example.edu.cn')['request_count'] == 4
    monkeypatch.setattr(research, '_fetch_page_once', lambda *a, **kw: {'status': 'fetch_failed', 'http_status': 401, 'request_count': 1})
    assert research.fetch_page('https://example.edu.cn')['request_count'] == 1
