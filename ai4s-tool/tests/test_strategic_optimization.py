import json
import sqlite3
from contextlib import closing

import pytest

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
    for page in range(1, 4):
        result = tasks.intelligence_search(q='中科院 预测', domain_id='life', subdomain_id='protein', page=page, size=20, verified_only=True)
        assert result['total'] == 59
        found.extend(row['id'] for row in result['items'])
    assert len(found) == len(set(found)) == 59
    assert tasks.intelligence_search(q='天气', domain_id='life', subdomain_id='other', page=1, size=20, verified_only=True)['total'] == 0


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
