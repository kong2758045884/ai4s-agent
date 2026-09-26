"""Prepare, activate or roll back this release. Run on the production host only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
from urllib.parse import urlsplit
from urllib.request import urlopen

from server_release_backup import env_file
from merge_release_database import merge

ROOT = Path('/www/wwwroot/lab')
SERVICES = ['ai4s-reactor-tool', 'ai4s-reactor-backend']


def run(*args):
    subprocess.run(args, check=True)


def private(path, body):
    path.write_text(body, encoding='utf-8'); path.chmod(0o600)


def env_write(path, values):
    private(path, ''.join(k+'='+json.dumps(str(v), ensure_ascii=False)+'\n' for k,v in values.items()))


def health(url, timeout=150):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        try:
            with urlopen(url,timeout=5) as response:
                if response.status==200:
                    body=response.read().decode()
                    try:return json.loads(body)
                    except ValueError:return body
        except Exception: pass
        time.sleep(2)
    raise RuntimeError('Health check failed: '+url)


def mysql(sql):
    values=env_file('/etc/ai4s/backend.env')
    url=urlsplit(values['SPRING_DATASOURCE_MYSQL_URL'].removeprefix('jdbc:'))
    return subprocess.check_output(['/www/server/mysql/bin/mysql','-h',url.hostname,'-P',str(url.port or 3306),
        '-u',values['SPRING_DATASOURCE_MYSQL_USERNAME'],'--default-character-set=utf8mb4','-N','-B',url.path.lstrip('/')],
        input=sql,text=True,env={**os.environ,'MYSQL_PWD':values['SPRING_DATASOURCE_MYSQL_PASSWORD']})


def prepare(release):
    from ensure_spa_cache import ensure_spa_cache
    ensure_spa_cache()
    assert (release/'app.jar').exists()
    assert (ROOT/'release-backups'/release.name/'mysql.sql.gz').exists()
    env=env_file(ROOT/'reactor-tool/.env')
    values={k.replace('REACTOR_', 'AI4S_', 1):v for k,v in env.items() if k.startswith('REACTOR_')}
    values.update({'ENV':'production','STRATEGIC_MAP_DB_PATH':release/'data/strategic_map.db',
        'AI4S_IMPACT_DB_PATH':release/'data/impact_triage.db',
        'STRATEGIC_MAP_HYPER_SNAPSHOT_DIR':release/'graphs','HYPEREXTRACT_BASE_URL':'http://127.0.0.1:1606',
        'STRATEGIC_MAP_SCHEDULER_ENABLED':'false','STRATEGIC_MAP_SKIP_STARTUP_SYNC':'true','AI4S_SKIP_EMBEDDING_HEALTH':'1',
        'SQLITE_DB_PATH':ROOT/'reactor-tool/autobots.db','SQLITE_PATH':ROOT/'reactor-tool/mrag_sqlite.db',
        'FILE_SAVE_PATH':ROOT/'reactor-tool/skilloutput','PYTHONUNBUFFERED':'1',
        'TOKENIZERS_PARALLELISM':'false','LOG_PATH':release/'logs/tool.log'})
    (release/'logs').mkdir(exist_ok=True)
    env_write(release/'tool.env',values)
    tool=f'''[Service]
WorkingDirectory={release}/tool
EnvironmentFile={release}/tool.env
ExecStart=
ExecStart={ROOT}/reactor-tool/.venv/bin/python {release}/tool/server.py --host 127.0.0.1 --port 1601 --workers 1 --role all
'''
    backend=f'''[Service]
WorkingDirectory={release}
ExecStart=
ExecStart=/usr/lib/jvm/java-21-konajdk-21.0.12-1.oc9/bin/java -jar {release}/app.jar --spring.config.location=classpath:/,optional:file:{ROOT}/config/
'''
    (release/'tool.conf').write_text(tool);(release/'backend.conf').write_text(backend)
    he=env_file('/opt/hyper-extract-8088/current/.env')
    # Separate config file: preserve the existing Hyper deployment and its credentials.
    config=''
    for section,model in [('llm',he.get('HE_TRIAGE_LLM_MODEL','gpt-4o-mini')),('embedder',he.get('HE_TEST_EMBED_MODEL','text-embedding-3-small'))]:
        config+='['+section+']\n'+''.join(k+' = '+json.dumps(v)+'\n' for k,v in {
            'provider':'openai','model':model,'api_key':he['OPENAI_API_KEY'],'base_url':he['OPENAI_BASE_URL']}.items())
    private(release/'hyper-config.toml',config)
    hyper=f'''[Unit]
Description=AI4S Hyper graph scan adapter
After=network.target
[Service]
Type=simple
User=root
WorkingDirectory={release}/hyper-source
EnvironmentFile=/opt/hyper-extract-8088/current/.env
Environment=TIKTOKEN_CACHE_DIR={release}/tokenizer-cache
Environment=TOKENIZERS_PARALLELISM=false
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/hyper-extract-8088/venv/bin/python {release}/scripts/serve_hyper_scan.py --source {release}/hyper-source --snapshots {release}/graphs --config {release}/hyper-config.toml --port 1606
Restart=on-failure
RestartSec=10
[Install]
WantedBy=multi-user.target
'''
    (release/'hyper.service').write_text(hyper)
    # Retain pre-release hashed assets so already open browser tabs can load chunks.
    oldassets=ROOT/'ui/dist/assets';newassets=release/'ui/dist/assets'
    for p in oldassets.glob('*'):
        if p.is_file() and not (newassets/p.name).exists():shutil.copy2(p,newassets/p.name)
    print('Prepared service overrides; no running services changed',flush=True)


def rollback(release):
    run('systemctl','stop','ai4s-hyper-scan',*SERVICES)
    for service in SERVICES:
        drop=Path('/etc/systemd/system')/(service+'.service.d')/'90-ai4s-release.conf'
        if drop.exists():drop.rename(drop.with_name('90-ai4s-release.conf.disabled-'+release.name))
    link=ROOT/'ui/dist';previous=ROOT/'ui'/('dist.before-'+release.name)
    if link.is_symlink() and link.resolve().is_relative_to(release/'ui'):link.unlink()
    if previous.exists() and not link.exists():previous.rename(link)
    run('systemctl','disable','ai4s-hyper-scan')
    run('systemctl','daemon-reload');run('systemctl','start',*SERVICES)
    print('Rolled back code and frontend; original databases retained. New database also retained.',flush=True)


def activate(release):
    original=ROOT/'reactor-tool/strategic_map.db'
    with sqlite3.connect(original.as_uri()+'?mode=ro',uri=True) as c:
        for table in ['strategic_map_refresh_task','strategic_map_research_job']:
            cols={r[1] for r in c.execute('PRAGMA table_info('+table+')')}
            state='state' if 'state' in cols else 'status'
            if state in cols:
                active=c.execute('SELECT COUNT(*) FROM '+table+' WHERE '+state+" IN ('running','queued','pending')").fetchone()[0]
                assert active==0,(table,active,'active work, defer cutover')
    # Application runs can be old interrupted rows: only inspect recent active work.
    recent=mysql("SELECT COUNT(*) FROM ai_agent_dialogue_run WHERE finished_at IS NULL AND update_time > NOW() - INTERVAL 10 MINUTE;")
    assert int(recent.strip())==0,'Recent running conversation, defer cutover'
    print('Stopping application services for database merge',flush=True)
    run('systemctl','stop',*SERVICES)
    try:
        report=merge(release/'data/local-strategic.db',original,release/'data/strategic_map.db')
        print(json.dumps({'merge':report},ensure_ascii=False),flush=True)
        for service,name in zip(SERVICES,['tool','backend']):
            directory=Path('/etc/systemd/system')/(service+'.service.d');directory.mkdir(exist_ok=True)
            target=directory/'90-ai4s-release.conf';assert not target.exists()
            shutil.copy2(release/(name+'.conf'),target)
        shutil.copy2(release/'hyper.service','/etc/systemd/system/ai4s-hyper-scan.service')
        run('systemctl','daemon-reload')
        run('systemctl','start','ai4s-reactor-tool')
        status=health('http://127.0.0.1:1601/v1/strategic-map/integration/status')
        print('Tool API healthy',flush=True)
        run('systemctl','start','ai4s-reactor-backend')
        health('http://127.0.0.1:8100/web/health')
        print('Java API healthy',flush=True)
        run('systemctl','enable','--now','ai4s-hyper-scan')
        metadata=health('http://127.0.0.1:1606/api/meta',90)
        assert metadata['jobs']==0
        print('Hyper adapter healthy, no scan jobs',flush=True)
        patch=(release/'scripts/report-contract.sql').read_text()
        mysql('START TRANSACTION;\n'+patch+'\nCOMMIT;')
        front=ROOT/'ui/dist';previous=ROOT/'ui'/('dist.before-'+release.name)
        assert not previous.exists();front.rename(previous);front.symlink_to(release/'ui/dist',target_is_directory=True)
        (release/'activated.json').write_text(json.dumps({'release':release.name,'activatedAt':time.time(),
            'status':status,'hyper':metadata},ensure_ascii=False,indent=2))
        print('ACTIVATED '+release.name,flush=True)
    except Exception:
        rollback(release)
        raise


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('release');p.add_argument('action',choices=['prepare','activate','rollback'])
    a=p.parse_args();release=(ROOT/'releases'/a.release).resolve()
    assert release.parent==ROOT/'releases' and a.release.startswith('ai4s-')
    {'prepare':prepare,'activate':activate,'rollback':rollback}[a.action](release)
