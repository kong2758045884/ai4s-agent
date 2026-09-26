"""Transactional, append-only evidence history for the strategic-map write path.

The table is deliberately outside the API's import-time metadata: GET/import
cannot create it. Only an explicit refresh or manual update initializes it.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from sqlalchemy import Table, Column, String, JSON, DateTime, MetaData, inspect, select

HISTORY = Table('strategic_map_research_run', MetaData(),
    Column('id', String(64), primary_key=True),
    Column('team_id', String(64), nullable=False, index=True),
    Column('status', String(48), nullable=False),
    Column('payload', JSON, nullable=False),
    Column('created_at', DateTime, nullable=False))


def history(session, team_id, status=None):
    if not inspect(session.connection()).has_table(HISTORY.name):
        return []
    query = select(HISTORY).where(HISTORY.c.team_id == team_id)
    if status:
        query = query.where(HISTORY.c.status == status)
    return session.execute(query.order_by(HISTORY.c.created_at.desc())).mappings().all()


def append_history(session, team_id, status, payload):
    from .strategic_map import _now
    HISTORY.create(session.connection(), checkfirst=True)
    session.execute(HISTORY.insert().values(id=uuid.uuid4().hex, team_id=team_id,
                    status=status, payload=payload, created_at=_now()))


def manual_fields(session, team_id):
    return {name for run in history(session, team_id, 'manual') for name in run['payload']['fields']}


def capability_history(session, team_id):
    """Only load opinion/audit metadata, not thousands of source page bodies."""
    if not inspect(session.connection()).has_table(HISTORY.name):
        return []
    payload = HISTORY.c.payload
    query = select(HISTORY.c.id, HISTORY.c.status, HISTORY.c.created_at,
        payload['fields'].label('fields'), payload['published'].label('published'),
        payload['run']['reviewed']['ai_assessment'].label('ai'),
        payload['run']['reviewed']['science_assessment'].label('science'))
    rows = session.execute(query.where(HISTORY.c.team_id == team_id,
        HISTORY.c.status.in_(('manual', 'verified'))).order_by(HISTORY.c.created_at.desc())).mappings()
    return [{'id': r['id'], 'status': r['status'], 'created_at': r['created_at'],
        'payload': {'fields': r['fields'] or [], 'published': r['published'],
            'run': {'reviewed': {'ai_assessment': r['ai'], 'science_assessment': r['science']}}}}
        for r in rows]


def capability_assessments(team, runs):
    """Resolve each dimension independently; audited human values always win.

    Old seed levels and free-text dual judgements are not model assessments.
    No schema migration or write occurs when building this projection.
    """
    output = {}
    for dimension in ('ai', 'science'):
        field = dimension + '_level'
        manual = next((r for r in runs if r['status'] == 'manual'
                       and field in r['payload'].get('fields', [])), None)
        if manual:
            output[dimension] = {'level': getattr(team, field), 'source': 'manual',
                'reason': '人工修改', 'citations': [], 'runId': manual['id'],
                'updatedAt': manual['created_at'].isoformat()}
            continue
        output[dimension] = {'level': '待核实', 'source': 'none',
            'reason': '尚无附原文依据的 AI 初评', 'citations': []}
        for entry in runs:
            if entry['status'] != 'verified' or not entry['payload'].get('published'):
                continue
            reviewed = entry['payload'].get('run', {}).get('reviewed') or {}
            assessment = reviewed.get(dimension + '_assessment')
            if not assessment:
                continue
            # A newer explicit insufficient-evidence decision supersedes old AI opinions.
            if (assessment.get('level') in ('较低', '一般', '较高')
                    and assessment.get('reason', '').strip() and assessment.get('citations')):
                output[dimension] = {**assessment, 'source': 'ai', 'runId': entry['id'],
                    'updatedAt': entry['created_at'].isoformat()}
            else:
                output[dimension]['reason'] = assessment.get('reason') or output[dimension]['reason']
            break
    return output


def _fact(value):
    return value.get('value', '') if value and value.get('status') == 'verified' else ''


def _verified_urls(value):
    if isinstance(value, list):
        return [url for item in value for url in _verified_urls(item)]
    if not isinstance(value, dict) or ('status' in value and value['status'] != 'verified'):
        return []
    own = [c['url'] for c in value.get('citations', [])] if value.get('status') == 'verified' else []
    return own + [url for key, item in value.items() if key != 'citations' for url in _verified_urls(item)]


def _person_value(person, run):
    cites = person['citations']
    pages = {page['url']: page for page in run['pages']}
    evidence = {'relation': cites, 'core_membership': person.get('core_membership'),
                'team_relation':person.get('team_relation'),'leadership_recency':person.get('leadership_recency'),
                'fields': {k: person[k] for k in ('title','research_direction','bio','tenure')},
                'sources': [{k: pages[c['url']].get(k, '') for k in ('url','fetched_at','published_at')}
                            for c in cites if c['url'] in pages]}
    return {'name': person['name'], 'role': person['role'],
            **{k: _fact(person[k]) for k in ('title','research_direction','bio')},
            'profile_url': person['profile_url'], 'source_urls': list(dict.fromkeys(c['url'] for c in cites)),
            'evidence': json.dumps(evidence, ensure_ascii=False), 'confidence': 1.0,
            'verification_status': person['status'], 'source_type': '公开网页原文·独立审核'}


def upsert_people(session, team, leader, members, *, preserve_existing=False):
    """No inferred roles/status, no blank overwrite, stable team+person identity."""
    from .strategic_map import StrategicPersonRow, _now
    rows = session.query(StrategicPersonRow).filter_by(team_id=team.id).all()
    by_name = {p.name.casefold(): p for p in rows}
    for value, is_leader in [(leader, True)] + [(v, False) for v in members]:
        if not value or not value.get('name') or not value.get('role'):
            continue
        name = value['name'].strip()
        person = by_name.get(name.casefold())
        if person is not None and preserve_existing:
            if not person.deleted:
                for field in ('title', 'research_direction', 'bio', 'profile_url', 'avatar_url', 'evidence', 'source_type'):
                    if value.get(field) and not getattr(person, field):
                        setattr(person, field, value[field])
                person.source_urls = list(dict.fromkeys([*(person.source_urls or []), *(value.get('source_urls') or [])]))
            continue
        incoming = value.get('verification_status', 'pending')
        if person is not None and person.verification_status == 'verified' and incoming != 'verified':
            continue
        if person is None:
            digest = hashlib.sha1(f'{team.id}:{name.casefold()}'.encode()).hexdigest()[:24]
            person = StrategicPersonRow(id='person_'+digest, team_id=team.id, name=name)
            session.add(person)
            by_name[name.casefold()] = person
        person.deleted = False
        for field in ('title', 'role', 'research_direction', 'bio', 'profile_url', 'avatar_url', 'evidence', 'source_type'):
            if value.get(field):
                setattr(person, field, value[field])
        person.source_urls = list(dict.fromkeys([*(value.get('source_urls') or []), *(person.source_urls or [])]))
        person.verification_status = incoming
        person.is_leader = is_leader
        person.confidence = float(value.get('confidence') or 0)
        if incoming == 'verified':
            person.last_verified_at = _now()


def persist(session, team, run):
    """Store an observation even on failure; publish only independently reviewed fields.

    Does not commit: caller owns the team, people and history transaction.
    """
    from .strategic_map import StrategicPersonRow, _now, _team_to_dict, _update_team_score
    if run.get('extracted') and not run.get('reviewed'):
        return persist_collected(session, team, run)
    before = _team_to_dict(team, session)
    reviewed = run.get('reviewed') or {}
    accepted = (run.get('status') == 'reviewed'
                and reviewed.get('entity_relation') in ('same', 'rename')
                and _fact(reviewed.get('team_name')) and _fact(reviewed.get('institution_name')))
    status = 'verified' if accepted else run.get('status', 'pending')
    if not accepted:
        append_history(session, team.id, status, {'run': run, 'before': before, 'published': False})
        return False
    protected = manual_fields(session, team.id)
    old_directions = team.research_directions or []
    old_focus = team.focus or ''
    new_directions = list(dict.fromkeys(_fact(v) for v in reviewed['research_directions'] if _fact(v)))
    previous_runs = history(session, team.id, 'verified')
    if previous_runs:
        decisions = {v['value']: v['status'] for v in reviewed['research_directions']
                     if v['status'] in ('verified','rejected','conflict') and v['citations']}
        for entry in previous_runs:
            previous = entry['payload'].get('run', {}).get('reviewed') or {}
            for direction in previous.get('research_directions', []):
                if direction['status'] in ('verified','rejected','conflict') and direction['citations']:
                    decisions.setdefault(direction['value'], direction['status'])
        new_directions = list(dict.fromkeys([*new_directions, *(value for value, status in decisions.items() if status == 'verified')]))
    for key in ('institution_name', 'team_name', 'description', 'location'):
        value = _fact(reviewed.get(key))
        if value:
            setattr(team, key, value)
        elif key in ('description', 'location') and reviewed[key]['status'] in ('rejected', 'conflict') and reviewed[key]['citations']:
            setattr(team, key, '')
    team.name = team.institution_name
    if new_directions:
        # Current reviewed directions take precedence over legacy broad keywords.
        team.research_directions = new_directions
        team.focus = '、'.join(new_directions)[:255]
        if 'core_direction' not in protected and (not team.core_direction or team.core_direction in (old_focus, '、'.join(old_directions))):
            team.core_direction = '、'.join(new_directions)[:500]
    team.verification_status = 'verified'
    if run.get('qualification_review') and any(reviewed.get(k,{}).get('status') == 'rejected'
            for k in ('concrete_team','domestic','domain_relevance','advantage')):
        team.verification_status = 'out_of_scope'
    team.team_confidence = 1.0
    # Independent model opinions; manual values survive later research runs.
    for field, key in (('ai_level', 'ai_assessment'), ('science_level', 'science_assessment')):
        if field not in protected:
            assessment = reviewed.get(key) or {}
            level = assessment.get('level', '待核实')
            supported = assessment.get('reason') and assessment.get('citations')
            setattr(team, field, level if supported else '待核实')
    if 'dual_judgement' not in protected and team.dual_judgement in ('AI 较高｜科学 较高', '', 'AI 待核实｜科学 待核实'):
        team.dual_judgement = 'AI 待核实｜科学 待核实'
    people = session.query(StrategicPersonRow).filter_by(team_id=team.id, deleted=False).all()
    for member in reviewed['members']:
        core = member.get('core_membership') or {}
        if core.get('status') in ('rejected', 'conflict') and core.get('citations'):
            for person in people:
                if person.name == member['name'] and not person.is_leader:
                    person.verification_status = core['status']
    for person in people:
      for decision in [v for v in reviewed['old_people'] if v['person_id'] == person.id]:
        if decision['decision'] in ('historical','rejected','conflict') and decision['citations']:
            claim = decision.get('claim', 'membership')
            if claim == 'membership' or (claim == 'leadership' and person.is_leader):
                person.verification_status = decision['decision']
            elif claim in ('title','research_direction','bio'):
                # Quarantine only the disputed field; the full previous value
                # remains in append-only history, and membership remains valid.
                setattr(person, claim, '')
    leader = reviewed.get('leader')
    incoming_leader = leader if leader and leader['status'] == 'verified' else None
    if incoming_leader:
        rivals = [p for p in people if p.is_leader and p.name != incoming_leader['name']
                  and p.verification_status in ('verified','conflict')]
        if rivals:
            # An unresolved dispute hides both claims, never arbitrarily picks newest.
            for person in rivals:
                person.verification_status = 'conflict'
            incoming_leader = {**incoming_leader, 'status': 'conflict'}
    # Avoid inserting the leader twice if the membership roster includes them.
    members = [v for v in reviewed['members'] if v['status'] == 'verified'
               and (not leader or v['name'] != leader['name'])]
    member_values = []
    for member in members:
        old = next((p for p in people if p.name == member['name'] and p.is_leader), None)
        if old is not None and old.verification_status in ('verified', 'conflict'):
            # Membership is not by itself evidence that a leader's tenure ended.
            old.verification_status = 'conflict'
            member = {**member, 'status': 'conflict'}
        member_values.append(_person_value(member, run))
    upsert_people(session, team, _person_value(incoming_leader, run) if incoming_leader else None, member_values)
    session.flush()
    team.leader_confidence = 1.0 if incoming_leader and incoming_leader['status'] == 'verified' else team.leader_confidence
    team.member_confidence = 1.0 if members else team.member_confidence
    citations = []
    for key in ('institution_name', 'team_name', 'description', 'location'):
        if _fact(reviewed[key]):
            citations.extend(reviewed[key]['citations'])
    for fact in reviewed['research_directions']:
        if _fact(fact):
            citations.extend(fact['citations'])
    urls = list(dict.fromkeys(c['url'] for c in citations))
    for person in ([incoming_leader] if incoming_leader else []) + members:
        if person['status'] == 'verified':
            urls.extend(c['url'] for c in person['citations'])
    previous_urls = [url for h in previous_runs[:1]
                     for url in _verified_urls(h['payload'].get('run', {}).get('reviewed'))]
    team.source_urls = list(dict.fromkeys([*urls, *previous_urls]))
    team.evidence_urls = team.source_urls
    team.evidence_summary = '\n'.join(dict.fromkeys(c['quote'] for c in citations))
    team.source = '公开网页原文·独立审核'
    if 'recent_update' not in protected and (not team.recent_update or team.recent_update == '近期' or any(
            h['payload'].get('managed_recent_update') == team.recent_update for h in history(session, team.id, 'verified'))):
        team.recent_update = _now().strftime('%Y-%m-%d')
    team.updated_at = _now()
    session.flush()
    _update_team_score(session, team)
    append_history(session, team.id, status, {'run': run, 'before': before, 'published': True,
                   'after_score': team.score_total, 'after_score_version': team.score_version,
                   'managed_recent_update': team.recent_update if 'recent_update' not in protected else None})
    return True


def collected_identity(run):
    """Require source-backed names, not a review status, before creating a team."""
    from .team_research import Research
    value = run.get('extracted') or {}
    guard = Research(lambda **kwargs: '', seconds=1)
    guard.pages = {p['url']: p for p in run.get('pages', [])}
    return all(value.get(k, {}).get('value') and guard.citations_valid(value[k].get('citations', []))
               for k in ('institution_name', 'team_name'))


def persist_collected(session, team, run):
    """Add sourced discoveries immediately without approval or destructive replacement."""
    from .strategic_map import _now, _team_to_dict, _update_team_score
    from .team_research import Research
    value = run.get('extracted') or {}
    before = _team_to_dict(team, session)
    guard = Research(lambda **kwargs: '', seconds=1)
    guard.pages = {p['url']: p for p in run.get('pages', [])}
    def sourced(fact):
        return fact.get('value', '') if fact and guard.citations_valid(fact.get('citations', [])) else ''
    if not collected_identity(run) or value.get('entity_relation') in ('different', 'successor'):
        append_history(session, team.id, 'collected', {'run': run, 'before': before, 'published': False})
        return False
    # Never attach a different named entity to an existing stable identity.
    unknown = ('', '公开资料未注明具体团队')
    if team.team_name not in unknown and team.team_name != sourced(value.get('team_name')) and value.get('entity_relation') != 'rename':
        append_history(session, team.id, 'identity_conflict', {'run': run, 'before': before, 'published': False})
        return False
    for field in ('institution_name', 'team_name', 'description', 'location'):
        incoming = sourced(value.get(field))
        if incoming and (field in ('institution_name', 'team_name') or not getattr(team, field)):
            setattr(team, field, incoming)
    team.name = team.institution_name
    team.research_directions = list(dict.fromkeys([*(team.research_directions or []),
        *(sourced(v) for v in value.get('research_directions', []) if sourced(v))]))
    def person(p):
        if not p or not p.get('name') or not p.get('role') or not guard.citations_valid(p.get('citations', [])):
            return None
        item = _person_value(p, run)
        item.update({k: sourced(p.get(k)) for k in ('title', 'research_direction', 'bio')})
        item.update(verification_status='collected', confidence=0.0, source_type='公开来源')
        return item
    leader = person(value.get('leader'))
    members = [v for p in value.get('members', []) if (v := person(p)) and (not leader or v['name'] != leader['name'])]
    upsert_people(session, team, leader, members, preserve_existing=True)
    urls = [c['url'] for key in ('institution_name', 'team_name', 'description', 'location')
            for c in (value.get(key) or {}).get('citations', []) if sourced(value.get(key))]
    team.source_urls = list(dict.fromkeys([*(team.source_urls or []), *urls]))
    team.evidence_urls = list(dict.fromkeys([*(team.evidence_urls or []), *team.source_urls]))
    team.verification_status = 'collected'
    team.source = '公开来源'
    team.updated_at = _now()
    session.flush()
    _update_team_score(session, team)
    append_history(session, team.id, 'collected', {'run': run, 'before': before, 'published': True})
    return True


def apply_reviewed_run(session, team_id, run, *, allowed_domains, domain_id=None, allow_out_of_scope=False):
    """Fresh-read, evidence-only controlled import using the normal merge logic.

    Caller owns the transaction/backup. Personnel changes since the evidence
    review force a new review. Manual fields are loaded fresh and protected by
    persist; this function never copies a whole snapshot or creates fake rows.
    """
    from .strategic_map import StrategicTeamRow, StrategicDomainRow, _research_existing, _canonical_team_key
    from .team_research import public_context
    from .domain_research import qualified
    reviewed=run.get('reviewed') or {}
    out_of_scope=(allow_out_of_scope and run.get('status')=='reviewed'
        and (run.get('qualification_review') or {}).get('version')==2
        and reviewed.get('entity_relation') in ('same','rename')
        and _fact(reviewed.get('team_name')) and _fact(reviewed.get('institution_name'))
        and any(reviewed.get(k,{}).get('status')=='rejected' and reviewed[k].get('citations')
                for k in ('concrete_team','domestic','domain_relevance')))
    if not qualified(run) and not out_of_scope:
        raise ValueError('domain qualification has not passed evidence review')
    session.expire_all()
    team = session.get(StrategicTeamRow, team_id)
    expected = run.get('existing') or {}
    if expected.get('team_id') != team_id:
        raise ValueError('review belongs to another team id')
    if team is None:
        if out_of_scope:
            raise ValueError('out-of-scope observations never create teams')
        domain=session.get(StrategicDomainRow,domain_id) if domain_id else None
        if not domain or domain.deleted or domain.name not in allowed_domains:
            raise ValueError('new team requires an explicitly allowed local domain')
        if expected.get('people'):
            raise ValueError('new target must be reviewed against its empty personnel state')
        value=run['reviewed'];institution=_fact(value['institution_name']);name=_fact(value['team_name'])
        key=_canonical_team_key(institution,name)
        if any(_canonical_team_key(r.institution_name,r.team_name)==key for r in
               session.query(StrategicTeamRow).filter_by(domain_id=domain.id,deleted=False)):
            raise ValueError('candidate now matches an existing team; resolve stable identity before import')
        team=StrategicTeamRow(id=team_id,domain_id=domain.id,name=institution,
                              institution_name=institution,team_name=name)
        session.add(team);session.flush()
        return persist(session,team,run)
    domain = session.get(StrategicDomainRow, team.domain_id)
    if domain.name not in allowed_domains or team.deleted or domain.deleted:
        raise ValueError('outside controlled import scope')
    current = public_context(_research_existing(session, team))
    # Order is immaterial; values, IDs and relationship states are not.
    normalize = lambda people: sorted(people, key=lambda p:p.get('id',''))
    if normalize(current.get('people',[])) != normalize(expected.get('people',[])):
        raise ValueError('personnel changed since review; rereview before import')
    if current['institution_name'] != expected.get('institution_name') or current['team_name'] != expected.get('team_name'):
        raise ValueError('team identity changed since review; rereview before import')
    return persist(session, team, run)


def rollback_import(session, before, after, history_ids):
    """Restore only this import's unchanged write set; preserve concurrent edits.

    Snapshots are internal ORM column dictionaries, never untrusted API input.
    Any concurrent change in the write set is reported instead of overwritten.
    """
    from .strategic_map import StrategicTeamRow, StrategicPersonRow
    conflicts=[]
    def restore(row, previous, written):
        for key,value in previous.items():
            if value == written[key]: continue
            if getattr(row,key) == written[key]: setattr(row,key,value)
            else: conflicts.append({'id':row.id,'field':key})
    team_id=after['team']['id']
    team=session.get(StrategicTeamRow,team_id)
    if before['team'] is not None:
        restore(team,before['team'],after['team'])
    for person_id,written in after['people'].items():
        person=session.get(StrategicPersonRow,person_id)
        if person is None: continue
        if person_id in before['people']:
            restore(person,before['people'][person_id],written)
        elif all(getattr(person,k)==v for k,v in written.items()):
            session.delete(person)
        else: conflicts.append({'id':person_id,'field':'new_person_changed_concurrently'})
    if before['team'] is None:
        session.flush()
        if team is not None and all(getattr(team,k)==v for k,v in after['team'].items()) and not session.query(StrategicPersonRow).filter_by(team_id=team_id).count():
            session.delete(team)
        else:conflicts.append({'id':team_id,'field':'new_team_changed_concurrently'})
    if history_ids:
        session.execute(HISTORY.update().where(HISTORY.c.id.in_(history_ids),HISTORY.c.team_id==team_id).values(status='rolled_back'))
    append_history(session,team_id,'rollback',{'history_ids':history_ids,'conflicts':conflicts})
    return conflicts
