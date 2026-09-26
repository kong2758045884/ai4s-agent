"""Prepare a NEW strategic DB, retaining the complete previous server DB.

Reviewed local taxonomy is the published taxonomy. Old server taxonomy is
archived in place (soft deleted), with original rows recorded for reversal.
Server team/person/history IDs remain available; only exact unique team names
within the same institution can carry explicit human edits to the new IDs.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3


def connect(path):
    c = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    return c


def merge(local, server, target):
    target = Path(target)
    if target.exists():
        raise FileExistsError('Destination must be new')
    target.parent.mkdir(parents=True, exist_ok=True)
    local_db, old = connect(local), connect(server)
    current = sqlite3.connect(target)
    current.row_factory = sqlite3.Row
    local_db.backup(current)
    report = {'tables': {}, 'manualTransfers': [], 'archivedDomains': 0,
              'serverSnapshotSha256': hashlib.sha256(Path(server).read_bytes()).hexdigest()}
    current.execute('CREATE TABLE deployment_archive (source_table TEXT, source_id TEXT, original_json TEXT NOT NULL, PRIMARY KEY(source_table,source_id))')
    tables = [r['name'] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for table in tables:
        if not table.replace('_', '').isalnum():
            raise ValueError('Unexpected table name')
        old_info = old.execute(f'PRAGMA table_info({table})').fetchall()
        new_info = current.execute(f'PRAGMA table_info({table})').fetchall()
        if not new_info:
            ddl = old.execute('SELECT sql FROM sqlite_master WHERE name=?', (table,)).fetchone()[0]
            current.execute(ddl)
            new_info = current.execute(f'PRAGMA table_info({table})').fetchall()
        columns = [r['name'] for r in old_info if r['name'] in {v['name'] for v in new_info}]
        defaults = {'eligibility': '{}', 'score_breakdown': '{}', 'score_total': 0,
                    'score_evidence_ids': '[]', 'score_version': 'legacy-server-unscored'}
        added = [c['name'] for c in new_info if c['name'] not in columns and c['notnull'] and c['dflt_value'] is None]
        if any(c not in defaults for c in added):
            raise ValueError('Missing migration defaults: '+table+':'+str(added))
        columns += added
        keys = [r['name'] for r in old_info if r['pk']]
        assert keys, 'Table has no stable primary key: '+table
        before = current.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        old_count, shared = 0, 0
        for row in old.execute(f'SELECT * FROM {table}'):
            old_count += 1
            row = dict(row)
            row.update({c: defaults[c] for c in added})
            prior = current.execute(f'SELECT 1 FROM {table} WHERE '+ ' AND '.join(f'"{key}"=?' for key in keys), [row[k] for k in keys]).fetchone()
            if prior:
                shared += 1
                current.execute('INSERT INTO deployment_archive VALUES (?,?,?)',
                    (table, json.dumps([row[k] for k in keys]), json.dumps(row,ensure_ascii=False)))
                continue
            if table == 'strategic_map_domain':
                current.execute('INSERT INTO deployment_archive VALUES (?,?,?)',
                    (table, row['id'], json.dumps(row,ensure_ascii=False)))
                row['deleted'] = 1
                report['archivedDomains'] += 1
            current.execute(f'INSERT INTO {table} ('+','.join(f'"{c}"' for c in columns)+') VALUES ('+','.join('?' for _ in columns)+')', [row[c] for c in columns])
        after = current.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        assert after == before + old_count - shared
        report['tables'][table] = {'local':before, 'server':old_count, 'shared':shared, 'merged':after}
    identity = lambda row: (row['institution_name'].strip(), row['team_name'].strip())
    new_by_name, old_by_name = defaultdict(list), defaultdict(list)
    for r in local_db.execute('SELECT * FROM strategic_map_team WHERE deleted=0'):
        if r['team_name'] and '未注明' not in r['team_name']:
            new_by_name[identity(r)].append(dict(r))
    for r in old.execute('SELECT * FROM strategic_map_team WHERE deleted=0'):
        old_by_name[identity(r)].append(dict(r))
    manual_columns = {'attention','contact','core_direction','dual_judgement','contact_record',
                      'internal_review','recent_update','next_action','ai_level','science_level'}
    for key, old_rows in old_by_name.items():
        if len(old_rows) != 1 or len(new_by_name.get(key, [])) != 1:
            continue
        src, dst = old_rows[0], new_by_name[key][0]
        local_times = {}
        for r in local_db.execute("SELECT payload,created_at FROM strategic_map_research_run WHERE team_id=? AND status='manual' ORDER BY created_at", (dst['id'],)):
            for field in json.loads(r['payload']).get('fields', []): local_times[field] = r['created_at']
        old_times = {}
        for r in old.execute("SELECT payload,created_at FROM strategic_map_research_run WHERE team_id=? AND status='manual' ORDER BY created_at", (src['id'],)):
            for field in json.loads(r['payload']).get('fields', []): old_times[field] = r['created_at']
        fields = [f for f, timestamp in old_times.items() if f in manual_columns and timestamp > local_times.get(f, '') and src.get(f) != dst.get(f)]
        if not fields: continue
        payload = {'fields': fields, 'before': {f:dst.get(f) for f in fields},
                   'changes': {f:src.get(f) for f in fields}, 'source':'server_deployment', 'sourceTeamId':src['id']}
        stamp = max(old_times[f] for f in fields)
        current.execute('UPDATE strategic_map_team SET '+','.join(f'"{f}"=?' for f in fields)+' WHERE id=?', [src[f] for f in fields]+[dst['id']])
        run_id = 'deploy-' + hashlib.sha256(json.dumps([src['id'],dst['id'],payload],sort_keys=True).encode()).hexdigest()[:32]
        current.execute('INSERT INTO strategic_map_research_run VALUES (?,?,?,?,?)',
                        (run_id,dst['id'],'manual',json.dumps(payload,ensure_ascii=False),stamp))
        report['manualTransfers'].append({'sourceTeamId':src['id'],'teamId':dst['id'],'fields':fields})
    current.commit()
    assert current.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert not current.execute('PRAGMA foreign_key_check').fetchall()
    report['activeRoots'] = current.execute('SELECT COUNT(*) FROM strategic_map_domain WHERE deleted=0 AND parent_id IS NULL').fetchone()[0]
    assert report['activeRoots'] == 6
    report['finishedAt'] = datetime.now(timezone.utc).isoformat()
    current.close(); old.close(); local_db.close()
    target.with_suffix('.reconciliation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--local',required=True);p.add_argument('--server',required=True);p.add_argument('--target',required=True)
    args=p.parse_args();print(json.dumps(merge(args.local,args.server,args.target),ensure_ascii=False))
