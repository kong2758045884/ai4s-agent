"""Read-only integration of Hyper triage and scoped knowledge graphs.

Source institutions/authors remain source nodes. Only the reviewed AI4S
catalogue supplies teams; source rankings never become team capability scores.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import closing
from datetime import date

from fastapi import APIRouter, HTTPException

from . import impact_store, strategic_graph as graph, task_recommendations as tasks

router = APIRouter(prefix='/strategic-map/integration', tags=['research_fusion'])


def _directions(conn, domain_id, subdomain_id):
    tasks._validate_scope(domain_id, subdomain_id)
    rows = impact_store.list_directions(conn)
    if subdomain_id:
        seeds = {r['id'] for r in rows if r.get('ai4s_subdomain_id') == subdomain_id}
    else:
        seeds = {r['id'] for r in rows if not domain_id or r.get('ai4s_domain_id') == domain_id}
    allowed = set(seeds)
    while True:
        children = {r['id'] for r in rows if r.get('parent_id') in allowed}
        if children <= allowed:
            break
        allowed |= children
    return allowed


@router.get('/status')
def integration_status():
    root = graph._snapshot_root()
    snapshots = []
    if root:
        for scope, folder in [('domestic', 'ZN'), ('international', 'GW')]:
            path = root / folder / 'data.json'
            if not path.is_file():
                continue
            raw = path.read_bytes()
            data = json.loads(raw)
            snapshots.append({'scope': scope, 'nodes': len(data.get('nodes', [])),
                'edges': len(data.get('edges', [])), 'sha256': hashlib.sha256(raw).hexdigest(),
                'updatedAt': graph._snapshot_updated_at(root, scope)})
    triage = {'ready': False, 'entities': 0, 'events': 0, 'periods': 0, 'dates': []}
    conn = impact_store.connect()
    if conn:
        with closing(conn):
            triage.update(ready=True,
                entities=conn.execute('SELECT COUNT(*) FROM impact_entity').fetchone()[0],
                events=conn.execute('SELECT COUNT(*) FROM impact_event').fetchone()[0],
                periods=conn.execute('SELECT COUNT(*) FROM impact_score_period').fetchone()[0],
                dates=[r[0] for r in conn.execute('''SELECT day FROM (
                    SELECT scan_date day FROM impact_score_period UNION SELECT event_date day FROM impact_event)
                    WHERE day IS NOT NULL ORDER BY day DESC''')])
    return {'knowledgeGraph': snapshots, 'triage': triage,
            'teamSource': 'AI4S verified catalogue', 'automaticPaidRefresh': False}


@router.get('/graph')
def integrated_graph(scope: graph.GraphScope = 'domestic', cluster: graph.GraphCluster = '高峰',
                     domain_id: str | None = None, subdomain_id: str | None = None):
    domain, subdomain = graph._context_names(domain_id, subdomain_id)
    # Avoid legacy graph_data's unreviewed team overlay. This overlay is from
    # the same published team catalogue as the recommendation UI.
    base = graph._filter_persisted_graph_scope(
        graph._context_graph_base(scope, cluster, domain), domain_name=domain,
        subdomain_name=subdomain, subdomain_id=subdomain_id, cluster=cluster)
    overlay = tasks.verified_graph(domain_id, subdomain_id) if scope == 'domestic' and (
        not domain or graph._ROOT_CLUSTER.get(domain) == cluster
    ) else {'nodes': [], 'edges': []}
    if not domain:
        roots = {n['id'] for n in overlay['nodes']
                 if graph._element_raw(n).get('level') == '领域方向'
                 and graph._ROOT_CLUSTER.get(graph._element_raw(n).get('name')) == cluster}
        team_nodes = {n['id'] for n in overlay['nodes'] if graph._element_raw(n).get('level') == '科研团队'}
        selected_teams = {e['source'] for e in overlay['edges'] if e['target'] in roots} & team_nodes
        selected = roots | selected_teams
        selected |= {e['target'] for e in overlay['edges'] if e['source'] in selected_teams}
        selected |= {e['source'] for e in overlay['edges'] if e['target'] in selected_teams}
        overlay = {'nodes': [n for n in overlay['nodes'] if n['id'] in selected],
                   'edges': [e for e in overlay['edges'] if e['source'] in selected and e['target'] in selected]}
    nodes, edges, by_name = [], [], {}
    for item in base.get('nodes', []):
        raw = graph._element_raw(item)
        node = {**item, 'id': 'hyper:' + item['id'],
                'data': {**item.get('data', {}), 'raw': {**raw, 'provenance': 'Hyper-Extract 领域知识图谱'}}}
        nodes.append(node)
        if raw.get('level') == '机构':
            by_name[str(raw.get('name', '')).strip()] = node['id']
    edges.extend({**e, 'id': 'hyper:' + e['id'], 'source': 'hyper:' + e['source'],
                  'target': 'hyper:' + e['target']} for e in base.get('edges', []))
    for node in overlay['nodes']:
        nodes.append(node)
        raw = graph._element_raw(node)
        source_id = by_name.get(str(raw.get('name', '')).strip())
        if raw.get('level') == '机构' and source_id:
            edges.append(graph._edge(source_id, node['id'], '同名机构'))
    edges.extend(overlay['edges'])
    return {'nodes': nodes, 'edges': edges, 'provider': 'Hyper-Extract + AI4S', 'searchResults': [],
        'meta': {'stats': {'Nodes': len(nodes), 'Edges': len(edges)},
                 'features': {'search': True, 'chat': False, 'scan': False},
                 'sourceNodes': len(base.get('nodes', [])), 'reviewedNodes': len(overlay['nodes'])}}


@router.get('/daily/{day}')
def integrated_daily(day: date, domain_id: str | None = None, subdomain_id: str | None = None):
    conn = impact_store.connect()
    if conn is None:
        raise HTTPException(503, '影响力数据尚未接入')
    with closing(conn):
        allowed_directions = _directions(conn, domain_id, subdomain_id)
        # Hyper stores L3 as (parent L2 ID, label), not as event.direction_id.
        mapped_labels = {(r['parent_id'], r['name']) for r in impact_store.list_directions(conn)
                         if r['id'] in allowed_directions and r['level'] == 3}
        eligible = {r['id'] for r in impact_store.list_ranking(conn, view='official')}
        historic = []
        for row in conn.execute('''SELECT id,entity_id,direction_id,l3_label,title,summary,event_date,origin
                FROM impact_event v WHERE event_date=? AND NOT EXISTS
                (SELECT 1 FROM impact_event n WHERE n.supersedes_event_id=v.id) ORDER BY id''', (day.isoformat(),)):
            if row['entity_id'] not in eligible or not (row['direction_id'] in allowed_directions
                    or (row['direction_id'], row['l3_label']) in mapped_labels):
                continue
            item = dict(row)
            item['sources'] = [dict(s) for s in conn.execute(
                'SELECT title,url,content_sha256,excerpt FROM impact_event_source WHERE event_id=? ORDER BY id', (row['id'],))]
            historic.append(item)
        scored = [dict(r) for r in conn.execute('''SELECT p.*,e.name
            FROM impact_score_period p JOIN impact_entity e ON e.id=p.entity_id WHERE p.scan_date=? ORDER BY p.id''', (day.isoformat(),))
            if r['entity_id'] in eligible and conn.execute('''SELECT 1 FROM impact_entity_direction
            WHERE entity_id=? AND direction_id IN (%s)''' % (','.join('?' for _ in allowed_directions) or 'NULL'),
            [r['entity_id'], *sorted(allowed_directions)]).fetchone()]
        base = impact_store.daily_report(conn, day.isoformat())
        changes = [r for r in base['tree_changes'] if (r.get('direction_id') or r.get('target_direction_id')) in allowed_directions]
    teams = tasks.verified_daily(day)
    team_ids = {t['id'] for t in tasks.verified_teams()['teams']
                if (not domain_id or t['domainId'] == domain_id)
                and (not subdomain_id or t.get('subdomainId') == subdomain_id)}
    team_changes = [r for r in teams['teamChanges'] if r['teamId'] in team_ids]
    recommendations = []
    for change in teams['recommendationChanges']:
        before = [team for team in change['before'] if team in team_ids]
        after = [team for team in change['after'] if team in team_ids]
        if before != after:
            recommendations.append({**change, 'before': before, 'after': after})
    result = {'date': day.isoformat(), 'sourceEvents': historic, 'scoreChanges': scored,
        'treeChanges': changes, 'teamChanges': team_changes, 'recommendationChanges': recommendations,
        'frozen': teams['frozen'], 'revision': teams['revision'],
        'scope': {'domainId': domain_id, 'subdomainId': subdomain_id}}
    result['inputHash'] = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return result
