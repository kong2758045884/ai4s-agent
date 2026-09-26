"""Package production files and consistent SQLite snapshots, without secrets."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tarfile
import zipfile


def copy_tree(source, target):
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(
        '__pycache__', '*.pyc', '.env', '.env.*', '.venv', 'node_modules', '.git',
        'output', 'outputs', '*.log', 'cookies.json', 'vector_index.json'))


def package(project, release):
    root = Path(project).resolve()
    work = root/'runtime/deploy'/release
    stage = work/'payload'
    stage.mkdir(exist_ok=False)
    copy_tree(root/'ui/dist', stage/'ui/dist')
    (stage/'tool').mkdir()
    copy_tree(root/'ai4s-tool/ai4s_tool', stage/'tool/ai4s_tool')
    for name in ['server.py', 'pyproject.toml', 'uv.lock']:
        shutil.copy2(root/'ai4s-tool'/name, stage/'tool'/name)
    (stage/'scripts').mkdir()
    for name in ['merge_release_database.py', 'server_release_backup.py', 'activate_server_release.py', 'ensure_spa_cache.py', 'deploy_canonical_frontend.py']:
        shutil.copy2(root/'scripts'/name, stage/'scripts'/name)
    shutil.copy2(root/'ai4s-tool/scripts/serve_hyper_scan.py', stage/'scripts/serve_hyper_scan.py')
    copy_tree(root/'runtime/hyper-fusion-20260926/graphs', stage/'graphs')
    copy_tree(root/'runtime/hyper-fusion-20260926/tokenizer-cache', stage/'tokenizer-cache')
    copy_tree(root/'runtime/skills', stage/'runtime/skills')
    source = root.parent/'Hyper-Extract-1/Hyper-Extract'
    copy_tree(source/'hyperextract', stage/'hyper-source/hyperextract')
    (stage/'hyper-source/daily-hier').mkdir()
    shutil.copy2(source/'daily-hier/scan.py', stage/'hyper-source/daily-hier/scan.py')
    shutil.copy2(source/'LICENSE', stage/'hyper-source/LICENSE')
    (stage/'data').mkdir()
    for source_name, dest in [('strategic_map.db','local-strategic.db'), ('impact_triage.db','impact_triage.db')]:
        src = root/'runtime/integration-preview-20260925'/source_name
        with sqlite3.connect(src.as_uri()+'?mode=ro',uri=True) as source_db, sqlite3.connect(stage/'data'/dest) as target:
            source_db.backup(target)
            assert target.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    shutil.copy2(root/'AI4S-agent-app/src/main/resources/db/ai4s-report-contract.sql', stage/'scripts/report-contract.sql')
    old = json.loads((work/'server-jar-entries.json').read_text())
    jar = work/'java-build/AI4S-agent-app/target/AI4S-agent-app.jar'
    entries = []
    with zipfile.ZipFile(jar) as source_jar, zipfile.ZipFile(stage/'app-delta.zip','w') as delta:
        for entry in source_jar.infolist():
            body = source_jar.read(entry)
            digest = hashlib.sha256(body).hexdigest()
            from_old = old.get(entry.filename,{}).get('sha256') == digest
            if not from_old: delta.writestr(entry,body)
            entries.append({'name':entry.filename,'sha256':digest,'reuse':from_old,
                            'compression':entry.compress_type,'date':entry.date_time,
                            'externalAttr':entry.external_attr,'createSystem':entry.create_system})
    (stage/'app-entries.json').write_text(json.dumps(entries),encoding='utf-8')
    manifest = {'release':release,'jarEntries':len(entries),'reusedJarEntries':sum(x['reuse'] for x in entries),'files':{}}
    for p in sorted(stage.rglob('*')):
        if p.is_file():
            assert not p.name.startswith('.env')
            manifest['files'][p.relative_to(stage).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    (stage/'release-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    archive=work/'release.tar.gz'
    with tarfile.open(archive,'w:gz',compresslevel=3) as tar:
        for p in stage.iterdir():tar.add(p,arcname=p.name)
    (work/'archive.sha256').write_text(hashlib.sha256(archive.read_bytes()).hexdigest())
    print(json.dumps({'archive':str(archive),'bytes':archive.stat().st_size,'files':len(manifest['files']),
                      'jarEntries':len(entries),'reusedJarEntries':manifest['reusedJarEntries']}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('release');parser.add_argument('--project',default='.')
    args=parser.parse_args();package(args.project,args.release)
