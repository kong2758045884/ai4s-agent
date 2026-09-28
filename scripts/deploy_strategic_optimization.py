"""Stage on a server DB copy, then incrementally activate Python/UI only.

No database replacement, dependency installation, Java code change or paid scans.
Restore Java after Python restarts because the existing unit depends on it.
Run stage before activate. Rollback restores code/UI; retained additive data can
be separately undone with the guarded batch journals.
"""
import argparse
import gzip
import hashlib
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
from urllib.request import urlopen, Request, build_opener, HTTPCookieProcessor

ROOT = Path('/www/wwwroot/lab')
OLD = ROOT / 'releases/ai4s-20260926T054442'
PYTHON = ROOT / 'reactor-tool/.venv/bin/python'
DROP = Path('/etc/systemd/system/ai4s-reactor-tool.service.d/90-ai4s-release.conf')


def run(*args, **kwargs):
    subprocess.run(list(map(str, args)), check=True, **kwargs)


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))


def environment():
    from dotenv import dotenv_values
    env = {**os.environ, **dotenv_values(ROOT/'reactor-tool/.env'), **dotenv_values(OLD/'tool.env')}
    env.update(STRATEGIC_MAP_SKIP_STARTUP_SYNC='true', STRATEGIC_MAP_SCHEDULER_ENABLED='false',
               AI4S_SKIP_EMBEDDING_HEALTH='1', AI4S_CHANGE_WORKER_ENABLED='false')
    return {k: str(v) for k, v in env.items() if v is not None}


def api(port, path, body=None):
    req = Request(f'http://127.0.0.1:{port}/v1/strategic-map{path}',
                  data=json.dumps(body).encode() if body is not None else None,
                  headers={'Content-Type': 'application/json'})
    with urlopen(req, timeout=15) as response:
        return json.load(response)


def healthy(port):
    for _ in range(50):
        try:
            return api(port, '/integration/status')
        except Exception:
            time.sleep(1)
    raise RuntimeError('Tool API did not become healthy')


def restore_java():
    # The existing Java unit Requires=ai4s-reactor-tool. systemctl stop(tool)
    # propagates to Java, but starting tool does not start its dependents.
    run('systemctl', 'start', 'ai4s-reactor-backend')
    for _ in range(60):
        try:
            with urlopen('http://127.0.0.1:8100/api/agent/visitor/bootstrap', timeout=5) as response:
                if response.status == 200 and json.load(response).get('code') == '0000':
                    return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError('Java visitor bootstrap did not become healthy')


def backup(source, target):
    assert source.is_file() and not target.exists()
    with sqlite3.connect(source.as_uri()+'?mode=ro', uri=True) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
        assert dst.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    return target


def compress_verified_backup(source):
    """Keep a checked compressed backup without retaining a duplicate raw copy."""
    target = Path(str(source) + '.gz')
    assert source.is_file() and not target.exists()
    with source.open('rb') as raw, gzip.open(target, 'wb', compresslevel=3) as zipped:
        shutil.copyfileobj(raw, zipped)
    def digest(stream):
        value = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
        return value.digest()
    with source.open('rb') as raw, gzip.open(target, 'rb') as zipped:
        assert digest(raw) == digest(zipped), f'Compressed backup differs: {target}'
    source.unlink()
    return target


def migrate(release, db, directory):
    directory.mkdir(exist_ok=True)
    run(PYTHON, release/'tool/scripts/migrate_strategic_optimization.py', '--db', db, '--report', directory/'migration.json', cwd=release/'tool')
    config = release/'release-config.json'
    if config.is_file() and not json.loads(config.read_text()).get('applyDataBatches', True):
        return
    for script, name in [('import_scoped_sources.py', 'new-units.json'), ('collect_team_outcomes.py', 'life-outcomes.json')]:
        manifest = directory/name
        if not manifest.exists():
            shutil.copy2(release/'batches'/name, manifest)
        run(PYTHON, release/'tool/scripts'/script, '--db', db, '--manifest', manifest, '--apply', cwd=release/'tool')


def preflight():
    # A historical state row is not an active process. Require evidence that any
    # orphan belongs to the previous service lifetime, plus terminal refreshes.
    stamp = subprocess.check_output(['systemctl', 'show', 'ai4s-reactor-tool', '-p', 'ExecMainStartTimestamp', '--value'], text=True).strip()
    started = float(subprocess.check_output(['date', '-d', stamp, '+%s'], text=True))
    with sqlite3.connect((OLD/'data/strategic_map.db').as_uri()+'?mode=ro', uri=True) as c:
        for table in ['strategic_map_refresh_task', 'strategic_map_graph_scan_task', 'strategic_web_investigation']:
            cols = {r[1] for r in c.execute(f'PRAGMA table_info({table})')}
            if 'state' in cols:
                assert c.execute(f"SELECT count(*) FROM {table} WHERE state IN ('running','accepted','queued')").fetchone()[0] == 0, f'Active work in {table}'
        rows = c.execute("SELECT id,updated FROM strategic_map_research_job WHERE state='running'").fetchall()
        assert all(updated < started for _, updated in rows), 'Current process has active research'
    return {'previousServiceStart': stamp, 'historicalOrphanJobs': rows}


def stage(release):
    env = environment()
    directory = release/'preview'; directory.mkdir(exist_ok=False, mode=0o700)
    team = backup(Path(env['STRATEGIC_MAP_DB_PATH']), directory/'strategic_map.db')
    impact = backup(Path(env['AI4S_IMPACT_DB_PATH']), directory/'impact_triage.db')
    migrate(release, team, directory/'journal')
    run(PYTHON, release/'tool/scripts/migrate_strategic_optimization.py', '--db', impact,
        '--access-audit-only', '--report', directory/'journal/impact-access-migration.json', cwd=release/'tool')
    env.update(STRATEGIC_MAP_DB_PATH=str(team), AI4S_IMPACT_DB_PATH=str(impact),
               SQLITE_DB_PATH=str(directory/'autobots.db'), SQLITE_PATH=str(directory/'mrag.db'),
               LOG_PATH=str(directory/'server.log'))
    with (directory/'process.log').open('w') as log:
        process = subprocess.Popen([str(PYTHON), 'server.py', '--host', '127.0.0.1', '--port', '1608', '--workers', '1'], cwd=release/'tool', env=env, stdout=log, stderr=log)
        try:
            status = healthy(1608)
            result = api(1608, '/task-recommendations', {'taskText': '蛋白质', 'limit': 5})
            assert result['parsedTask']['goals'] and len(result['items']) >= 3
            search = api(1608, '/intelligence/search?q=%E4%B8%AD%E7%A7%91%E9%99%A2&page=2&size=20&verified_only=true')
            assert search['total'] > 20 and search['items']
            config = release/'release-config.json'
            private = private_assessment_gate(1608) if config.is_file() and json.loads(config.read_text()).get('verifyPrivateAssessments') else None
            save(release/'stage.json', {'passed': True, 'status': status, 'recommendationCount': len(result['items']), 'searchTotal': search['total'], 'privateAssessmentGate': private, 'paidCalls': 0})
            print('STAGE_PASSED', flush=True)
        finally:
            process.terminate()
            try: process.wait(timeout=20)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
    compress_verified_backup(team)
    compress_verified_backup(impact)


def private_assessment_gate(port):
    """Exercise visitor authorization and private APIs against the staged DB only."""
    from datetime import datetime, timedelta, timezone
    import uuid
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    with opener.open('http://127.0.0.1:8100/api/agent/visitor/bootstrap', timeout=15) as response:
        assert json.load(response)['code'] == '0000'
    def request(path, body=None):
        req = Request(f'http://127.0.0.1:{port}/v1/strategic-map{path}',
            data=json.dumps(body).encode() if body is not None else None, headers={'Content-Type': 'application/json'})
        with opener.open(req, timeout=20) as response:
            return json.load(response)['data']
    record = request('/assessments', {'requestId': str(uuid.uuid4()), 'title': '隔离发布验收', 'taskDraft': '蛋白质'})
    parsed = request('/task-interpretations', {'taskText': '蛋白质', 'scope': {'mode': 'auto'}})
    current = request(f"/assessments/{record['taskId']}/confirm", {'requestId': str(uuid.uuid4()),
        'expectedRevision': record['revision'], 'taskText': parsed['taskText'], 'scope': parsed['scope'],
        'criteria': parsed['criteria'], 'evidenceVersion': parsed['evidenceVersion']})
    result = current['run']
    assert len(result['items']) >= 3 and result['inputVersion'] == 1
    assert all(c.get('provenance', {}).get('fetchedAt') for item in result['items'] for c in item['citations'])
    graph = request(f"/assessments/{record['taskId']}/runs/{result['runId']}/graph")
    assert graph == result['relationshipGraph']
    assert graph['meta']['snapshot']['runId'] == result['runId']
    assert any(e['data']['raw']['citations'] for e in graph['edges'])
    public = api(port, '/intelligence/verified-teams')
    assert not any({'contactRecord', 'internalReview', 'nextAction'} & set(team) for team in public['teams'])
    coverage = api(port, '/intelligence/source-coverage')
    assert coverage['totalUnits'] == len(public['teamIds'])
    assert coverage['outcomeBackedUnits'] + coverage['identityOnlyUnits'] == coverage['totalUnits']
    assert coverage['claimCount'] == public['claimCount'] and coverage['sources']
    access = request('/access')
    assert access['permissions'] == []
    from urllib.error import HTTPError
    try:
        request('/teams/' + result['items'][0]['teamId'] + '/internal')
    except HTTPError as exc:
        assert exc.code == 403
    else:
        raise AssertionError('An ungranted visitor read internal team records')
    saved = request(f"/assessments/{record['taskId']}/export")
    assert saved['run']['runId'] == result['runId'] and 'internalNotes' not in saved
    investigations = request(f"/assessments/{record['taskId']}/runs/{result['runId']}/investigations")
    assert investigations['canStart'] is False and investigations['jobs'] == []
    day = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    report = request('/assessment-reports', {'requestId': str(uuid.uuid4()), 'day': day,
        'domainId': result['scope']['domainIds'][0], 'kind': 'daily'})
    assert report['revision'] == 1 and report['counts']['taskUpdates'] == 0
    assert request('/assessment-updates')['total'] == 0
    return {'privateRecommendationCount': len(result['items']), 'privateReportId': report['reportId'],
            'frozenGraphHash': graph['meta']['snapshot']['hash'],
            'sourceCoverage': {k: coverage[k] for k in ('totalUnits', 'outcomeBackedUnits', 'identityOnlyUnits', 'claimCount', 'humanReviewedClaims')},
            'visitorIdentityVerified': True, 'paidCalls': 0}


def require_review_compatible(database, previous_tool):
    """Do not silently republish disputed claims through a pre-review projection."""
    if (previous_tool/'ai4s_tool/api/claim_reviews.py').is_file():
        return
    with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='strategic_claim_review'").fetchone()
        if exists and conn.execute('SELECT COUNT(*) FROM strategic_claim_review').fetchone()[0]:
            raise RuntimeError('Previous code cannot enforce saved human claim reviews; retain the review compatibility layer before rollback. No code or data changed.')


def rollback(release):
    journal = release/'live-journal'
    saved = json.loads((journal/'state.json').read_text())
    require_review_compatible(Path(environment()['STRATEGIC_MAP_DB_PATH']), Path(saved['frontend']).parents[1]/'tool')
    shutil.copy2(journal/'tool.conf', DROP)
    from deploy_canonical_frontend import deploy
    deploy(saved['frontend'])
    run('systemctl', 'daemon-reload')
    run('systemctl', 'restart', 'ai4s-reactor-tool')
    healthy(1601)
    restore_java()
    print('CODE_ROLLED_BACK; additive records and batch journals retained')


def activate(release):
    assert json.loads((release/'stage.json').read_text())['passed']
    state = preflight()
    journal = release/'live-journal'; journal.mkdir(exist_ok=False, mode=0o700)
    state['frontend'] = str((ROOT/'ui/dist').resolve())
    save(journal/'state.json', state)
    shutil.copy2(DROP, journal/'tool.conf')
    env = environment()
    run('systemctl', 'stop', 'ai4s-reactor-tool')
    try:
        for key in ['STRATEGIC_MAP_DB_PATH', 'AI4S_IMPACT_DB_PATH']:
            src = Path(env[key]); target = backup(src, journal/src.name)
            compress_verified_backup(target)
        migrate(release, Path(env['STRATEGIC_MAP_DB_PATH']), journal)
        run(PYTHON, release/'tool/scripts/migrate_strategic_optimization.py', '--db', env['AI4S_IMPACT_DB_PATH'],
            '--access-audit-only', '--report', journal/'impact-access-migration.json', cwd=release/'tool')
        (release/'logs').mkdir(exist_ok=True)
        DROP.write_text(f'''[Service]
WorkingDirectory={release}/tool
EnvironmentFile={OLD}/tool.env
Environment=AI4S_CHANGE_WORKER_ENABLED=true
Environment=STRATEGIC_MAP_SCHEDULER_ENABLED=false
Environment=STRATEGIC_MAP_SKIP_STARTUP_SYNC=true
Environment=AI4S_SKIP_EMBEDDING_HEALTH=1
Environment=LOG_PATH={release}/logs/tool.log
ExecStart=
ExecStart={PYTHON} {release}/tool/server.py --host 127.0.0.1 --port 1601 --workers 1 --role all
''')
        run('systemctl', 'daemon-reload'); run('systemctl', 'start', 'ai4s-reactor-tool')
        status = healthy(1601)
        restore_java()
        from deploy_canonical_frontend import deploy
        deploy(release/'ui/dist')
        save(release/'activated.json', {'at': time.time(), 'status': status, 'database': env['STRATEGIC_MAP_DB_PATH'], 'javaCodeChanged': False, 'javaDependencyRestored': True, 'paidScheduler': False})
        print('ACTIVATED', release.name, flush=True)
    except Exception:
        rollback(release)
        raise


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('release'); p.add_argument('action', choices=['stage', 'activate', 'rollback'])
    args = p.parse_args(); path = (ROOT/'releases'/args.release).resolve()
    assert path.parent == ROOT/'releases' and path.name.startswith('ai4s-opt-')
    assert (path/'tool/server.py').is_file() and (path/'ui/dist/index.html').is_file()
    {'stage': stage, 'activate': activate, 'rollback': rollback}[args.action](path)
