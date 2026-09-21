"""Bounded, resumable domain research used by every explicit refresh entry.

Jobs and leases are internal; the synchronous API still waits for the batch.
Successful teams commit separately. An incomplete batch raises the existing
quality error after preserving progress, never reports an enqueue as completion.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from pydantic import Field
from typing import Literal
from sqlalchemy import Table, Column, String, JSON, Float, MetaData, select, update, insert, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from . import team_research as tr

META = MetaData()
JOBS = Table('strategic_map_research_job', META,
    Column('id', String(64), primary_key=True), Column('domain_id', String(64), index=True),
    Column('team_id', String(64)), Column('state', String(32)), Column('payload', JSON),
    Column('updated', Float))
LEASES = Table('strategic_map_research_lease', META,
    Column('id', String(100), primary_key=True), Column('owner', String(64)), Column('expires', Float))


class Candidate(tr.Schema):
    institution_name: str
    team_name: str
    source_urls: list[str]
    reason: str
    concrete_team_named: bool = False
    name_evidence: list[tr.Citation] = Field(default_factory=list)


class Candidates(tr.Schema):
    candidates: list[Candidate] = Field(default_factory=list, max_length=24)
    unresolved: list[str] = Field(default_factory=list)


class Duplicate(tr.Schema):
    team_id: str
    duplicate_of: str
    reason: str
    citations: list[tr.Citation]


class Duplicates(tr.Schema):
    duplicates: list[Duplicate] = Field(default_factory=list)


class DomainScope(tr.Schema):
    definition: str
    included_subjects: list[str]
    adjacent_but_insufficient: list[str]
    required_connection_evidence: str


class Eligibility(tr.Schema):
    concrete_team: tr.Fact
    domestic: tr.Fact
    domain_relevance: tr.Fact
    advantage: tr.Fact
    relationship: Literal['direct','application','adjacent','unrelated','unclear']
    entity_level: Literal['institution','specific_research_team','unclear']
    leadership_review: 'LeadershipReview | None' = None


class LeadershipReview(tr.Schema):
    name: str
    source_authority: Literal['official_team','official_institution','third_party','unclear']
    current_exact_team_head: bool
    claim_status: Literal['supported','unsupported_claim','not_checked','conflict'] = 'not_checked'
    reason: str
    citations: list[tr.Citation]


Eligibility.model_rebuild()


def review_qualification(run, domain, llm, *, seconds=90, cache=None, existing=None, scope=None):
    """Separate domain scope from team existence and roster completeness."""
    if run.get('status') != 'reviewed' or (run.get('qualification_review') or {}).get('version')==2:
        return run
    import copy
    result=copy.deepcopy(run)
    if scope:
        result['domain_scope']=scope
    if existing is not None:
        result['existing']=tr.public_context(existing)
    research=tr.Research(llm,seconds=seconds,cached_pages=cache)
    research.pages={p['url']:p for p in run['pages']}
    value=run['reviewed']
    old_leaders=[p for p in (result.get('existing') or {}).get('people',[])
                 if p.get('isLeader') and p.get('verificationStatus') in ('verified','conflict')]
    try:
        judgment=research.call('domain-qualification',Eligibility,{'domain':domain,
            'domain_scope':scope or run.get('domain_scope'),
            'scope_constraint':'不得把输入的具体下位学科扩大成上位大类。来源必须明示团队的研究对象与definition的联系；仅落在adjacent_but_insufficient内则不通过。拟建/计划获得的能力不是已经具备的优势。',
            # Do not show the extraction's proposed eligibility verdicts: they
            # anchor the supposedly independent scope check to its own answer.
            'team':{k:value.get(k) for k in ('institution_name','team_name','entity_reason','entity_citations')},
            'leadership_claim':value.get('leader') or (old_leaders[0] if old_leaders else None),
            'existing_leadership_claims':old_leaders,
            'old_claim_rule':'必须审查旧负责人主张，不能因本轮leader=null就默认旧记录可信。只有本轮原文明确显示历史任职、错误角色层级、第三方旧转载被冒充官方任命，或旧引文本身只证明普通归属而不支持负责人，才判unsupported_claim/conflict。本轮没查到其来源应not_checked并保留；不能把缺证据等同错误。',
            'pages':research.evidence(),
            'instruction_detail':'严格独立审核领域资格，不为凑8队放宽输入领域含义。先理解用户领域本体与边界，再核对该具体团队研究对象/技术。仅同一院所、同一上位大类、相邻学科或可能合作不够。direct表示直接研究该领域；application需原文明确将该领域技术或对象作为实质研究任务，不能猜测潜在应用；adjacent/unrelated/unclear不得计入。具体平台/实验室需有科研组织与任务依据，整所整校或只有名称的机构占位不够。优势须与这个团队的领域任务相关，不能挪用母机构荣誉。正向满足才能verified，不满足rejected，缺证据pending。四项全部返回事实与原文。另独立核验leadership_claim引用页面的发布主体和时效，不能相信上轮自行声明current_official_listing。门户、百科、论文平台、商业网站转载的实验室简介属于third_party，即便它写“现任”也不能作为当前官方任命。只在当前本团队或所属机构官方名单/任命明确支持本团队负责人时令current_exact_team_head=true，并引用原文；否则false。无负责人则leadership_review=null。'})
        data=judgment.model_dump()
        result['reviewed'].update({k:v for k,v in data.items() if k not in ('relationship','entity_level','leadership_review')})
        if data['relationship'] not in ('direct','application'):
            result['reviewed']['domain_relevance']['status']='rejected' if data['relationship'] in ('adjacent','unrelated') else 'pending'
        if data['entity_level']!='specific_research_team':
            result['reviewed']['concrete_team']['status']='rejected' if data['entity_level']=='institution' else 'pending'
        leader=result['reviewed'].get('leader')
        authority=data.get('leadership_review')
        if authority and authority.get('claim_status') in ('unsupported_claim','conflict') and research.citations_valid(authority['citations']):
            for old in old_leaders:
                if old['name']==authority['name']:
                    result['reviewed']['old_people'].append({'person_id':old['id'],'claim':'leadership',
                        'decision':'conflict','reason':authority['reason'],'citations':authority['citations']})
        if leader and leader.get('status')=='verified' and not (authority and authority['name']==leader['name']
                and authority['source_authority'] in ('official_team','official_institution')
                and authority['current_exact_team_head'] and research.citations_valid(authority['citations'])):
            leader.update(status='pending',reason=(authority or {}).get('reason','独立来源权威性审核未确认本团队现任负责人'))
            if authority and authority['source_authority']=='third_party' and research.citations_valid(authority['citations']):
                for old in (result.get('existing') or {}).get('people',[]):
                    if old.get('name')==leader['name'] and old.get('isLeader'):
                        result['reviewed']['old_people'].append({'person_id':old['id'],'claim':'leadership',
                            'decision':'conflict','reason':authority['reason'],'citations':authority['citations']})
        research.validate_evidence(result['reviewed'])
        result['qualification_review']={'version':2,'decision':data,'trace':research.trace,'counts':research.counts,'errors':research.errors,
                                        'seconds':round(time.monotonic()-research.started,3)}
    except Exception as exc:
        result['reviewed']['domain_relevance'].update(status='pending',reason='独立领域边界审核失败：'+type(exc).__name__)
        result['qualification_review']={'error':type(exc).__name__,'counts':research.counts,'errors':research.errors}
    return result


def qualified(run):
    value = run.get('reviewed') or {}
    return run.get('status') == 'reviewed' and (run.get('qualification_review') or {}).get('version')==2 and value.get('entity_relation') in ('same','rename') and all(
        value.get(k, {}).get('status') == 'verified' and value[k].get('value') and value[k].get('citations')
        for k in ('team_name','institution_name','concrete_team','domestic','domain_relevance','advantage'))


def missing_fields(run):
    value = run.get('reviewed') or {}
    accepted = lambda fact: bool(fact and fact.get('status') == 'verified')
    return [key for key, present in {
        'leader': accepted(value.get('leader')),
        'description': accepted(value.get('description')),
        'research_directions': any(accepted(f) for f in value.get('research_directions', [])),
        'members': any(accepted(p) and accepted(p.get('core_membership')) for p in value.get('members', [])),
    }.items() if not present]


def retryable_evidence_gap(run):
    """Return whether an explicit refresh can materially improve this run.

    A conclusive rejection is cached, while pending scope/entity facts and a
    qualified team with missing people/details are resumed.  The old blanket
    24-hour needs_review cache made the real refresh button incapable of
    filling precisely the gaps it reported.
    """
    if run.get('status') != 'reviewed':
        return True
    value = run.get('reviewed') or {}
    facts = [value.get(key) or {} for key in (
        'team_name', 'institution_name', 'concrete_team', 'domestic',
        'domain_relevance', 'advantage')]
    if any(fact.get('status') == 'rejected' for fact in facts):
        return False
    if value.get('entity_relation') not in ('same', 'rename'):
        return True
    if (run.get('qualification_review') or {}).get('version') != 2:
        return True
    if any(fact.get('status') != 'verified' for fact in facts):
        return True
    return bool(missing_fields(run))


def persist_duplicates(factory, valid, decision, reviewer):
    """Quarantine evidenced aliases, retaining stable IDs and all manual data."""
    from . import strategic_map as sm
    from .team_research_store import append_history
    # Reject chains/cycles rather than arbitrarily choosing a canonical entity.
    aliases = {d['team_id'] for d in decision['duplicates']}
    for item in decision['duplicates']:
        loser, winner = item['team_id'], item['duplicate_of']
        if loser not in valid or winner not in valid or winner in aliases or loser == winner:
            continue
        if not reviewer.citations_valid(item['citations']):
            continue
        with factory() as s:
            s.execute(text('BEGIN IMMEDIATE'))
            row = s.get(sm.StrategicTeamRow, loser)
            target = s.get(sm.StrategicTeamRow, winner)
            if not row or not target or row.deleted or target.deleted or row.domain_id != target.domain_id:
                continue
            before = row.verification_status
            row.verification_status = 'duplicate'
            append_history(s, loser, 'duplicate', {'duplicate_of':winner,'decision':item,
                'previous_status':before,'published':False})
            job = s.execute(select(JOBS).where(JOBS.c.id==loser)).mappings().first()
            if job:
                s.execute(update(JOBS).where(JOBS.c.id==loser).values(state='duplicate',
                    payload={**job['payload'],'duplicate':item},updated=time.time()))
            s.commit()
        valid[loser]['state']='duplicate'
        valid[loser]['duplicate']=item


def acquire(factory, key, owner, seconds):
    with factory() as s:
        now = time.time()
        result = s.execute(update(LEASES).where(LEASES.c.id == key, LEASES.c.expires < now)
                           .values(owner=owner, expires=now+seconds))
        if result.rowcount:
            s.commit(); return True
        try:
            s.execute(insert(LEASES).values(id=key, owner=owner, expires=now+seconds))
            s.commit(); return True
        except IntegrityError:
            s.rollback(); return False


def release(factory, key, owner):
    with factory() as s:
        s.execute(LEASES.delete().where(LEASES.c.id == key, LEASES.c.owner == owner)); s.commit()


def save_job(factory, job_id, domain_id, team_id, state, payload):
    with factory() as s:
        values = dict(domain_id=domain_id, team_id=team_id, state=state, payload=payload, updated=time.time())
        exists = s.execute(select(JOBS.c.id).where(JOBS.c.id == job_id)).first()
        s.execute(update(JOBS).where(JOBS.c.id == job_id).values(**values) if exists else insert(JOBS).values(id=job_id, **values))
        s.commit()


def fresh(page):
    age = tr.page_age_seconds(page)
    return tr.cached_page_usable(page) and 0 <= age < 86400


class BatchCache(dict):
    """24h evidence TTL, including original capture time; coalesce concurrent URLs."""
    def __init__(self):
        super().__init__(); self.lock = threading.RLock(); self.url_locks = {}; self.search_results = {}; self.query_locks = {}
        self.unavailable=None; self.on_failure=None

    def trip(self, failure):
        with self.lock:
            if self.unavailable:return
            self.unavailable=failure
            if self.on_failure:self.on_failure(failure)

    def search(self, query):
        with self.lock:
            guard=self.query_locks.setdefault(query,threading.Lock())
        with guard:
            with self.lock:
                if query in self.search_results:return self.search_results[query],True
            result=asyncio.run(tr.search_links(query))
            with self.lock:self.search_results[query]=result
            return result,False

    def read(self, url):
        with self.lock:
            guard = self.url_locks.setdefault(url, threading.Lock())
        with guard:
            with self.lock:
                page = self.get(url)
            if page and fresh(page):
                return page, True
            observed = tr.fetch_page(url, page if tr.cached_page_usable(page or {}) else None)
            # A transient failure is evidence about this attempt, not a durable
            # assertion that the source has no content. Keep an older good body
            # available for a later conditional retry, but do not use it as fresh.
            if tr.cached_page_usable(observed):
                with self.lock:
                    self[url] = observed
            return observed, False


def discovery(domain_name, existing, research, rounds=2, scope=None):
    """LLM plans queries; configured MixSearch yields candidate hints, not facts."""
    hits = []
    for index in range(rounds):
        plan = research.call('domain-plan', tr.Plan, {'domain':domain_name,'existing_teams':existing,
            'domain_scope':scope,
            'results':hits,'already_queried':sorted(research.queries),
            'instruction_detail':'发现中国境内本领域优势具体科研团队，至少8个目标。先根据domain_scope把用户领域拆成不同技术任务和常见学术表达，每轮查询覆盖不同included_subjects；输入词、上位学科和相邻方向不是自动同义词，候选仍须用具体任务原文核实。针对现有缺口找不同具体实验室/研究部/PI团队；不要列整所整校或泛新能源机构。只规划最多2条简短查询，避免重复；不提供预置团队答案。'})
        for query in plan.queries:
            if query in research.queries or len(research.queries) >= 4:
                continue
            research.queries.add(query); research.counts['logical_queries'] += 1
            started=time.monotonic()
            try:
                if hasattr(research.cached_pages,'search'):
                    result,reused=research.cached_pages.search(query)
                else:
                    result,reused=asyncio.run(tr.search_links(query)),False
                research.counts['search'] += int(not reused);research.counts['search_reuse'] += int(reused)
                hits.extend(result)
                research.add_links(result)
                research.event('search',initiator='model',tool='MixSearch',query=query,
                               seconds=round(time.monotonic()-started,3),results=result,reused=reused)
            except Exception as exc:
                research.counts['search'] += 1
                research.errors.append({'stage':'discovery-search','kind':type(exc).__name__})
                research.event('search_error',query=query,error=type(exc).__name__,seconds=round(time.monotonic()-started,3))
    value=research.call('domain-candidates',Candidates,{'domain':domain_name,'existing_teams':existing,'search_results':hits,
        'domain_scope':scope,
        'instruction_detail':'仅从检索结果提出有明确名称的具体团队及机构，合并同一实体的别名/重复。不要重复existing_teams，不把机构作为团队，不用记忆补名单。concrete_team_named只在标题或摘要明确提到具体实验室/科研组/研究部/PI团队时为true，并在name_evidence逐字引用该标题/摘要；没有具体名称不能编造“研究团队”占位。source_urls只能取实际检索URL。目标12个候选以应对过滤，来源不足就少返回，绝不填充模糊机构。后续仍需正文独立核验，这里仅为命名线索。'})
    allowed={h['url'] for h in hits}
    candidates=[]
    for c in value.candidates:
        item=c.model_dump(); item['source_urls']=[u for u in item['source_urls'] if u in allowed]
        cited=bool(item['name_evidence']) and all(any(h['url']==cite['url'] and
            ''.join(cite['quote'].split()) in ''.join((h.get('label','')+' '+h.get('snippet','')).split())
            and len(''.join(cite['quote'].split()))>=2 for h in hits) for cite in item['name_evidence'])
        if item['source_urls'] and item['concrete_team_named'] and cited:
            candidates.append(item)
        else:
            research.event('candidate_deferred',candidate=item,reason='缺少可核对的具体团队命名线索；未消耗逐队调查预算')
    return candidates


def sync_domain(session, domain, *, seconds=2400, team_seconds=210, workers=2):
    from . import strategic_map as sm
    from .team_research_store import history, persist, append_history, apply_reviewed_run
    started=time.monotonic(); deadline=started+seconds
    domain_id,domain_name=domain.id,domain.name
    engine=session.get_bind(); factory=sessionmaker(bind=engine,expire_on_commit=False)
    session.commit()
    # Serialize first-use DDL too: two domains may start in different workers.
    from .team_research_store import HISTORY
    with engine.connect() as connection:
        connection.exec_driver_sql('BEGIN IMMEDIATE')
        META.create_all(connection); HISTORY.create(connection,checkfirst=True)
        connection.commit()
    with factory() as check:
        circuit=check.execute(select(JOBS).where(JOBS.c.id=='provider:shared-agent')).mappings().first()
        if circuit and circuit['state']=='blocked_provider' and time.time()-circuit['updated']<300:
            raise sm._SyncQualityError('原模型服务不可用（'+str(circuit['payload'].get('provider_code','authorization'))+'）；已保留进度，5分钟内不重复调用')
    owner=uuid.uuid4().hex; key='domain:'+domain_id
    if not acquire(factory,key,owner,seconds+300):
        raise sm._SyncQualityError('该领域正在处理，未重复执行；请稍后读取已有结果')
    cache=BatchCache(); results=[]; discovered=[]; discovery_run=None; scope_run=None
    cache.on_failure=lambda failure:save_job(factory,'provider:shared-agent',None,None,'blocked_provider',failure)
    try:
        with factory() as s:
            rows=s.query(sm.StrategicTeamRow).filter_by(domain_id=domain_id,deleted=False).all()
            rows.sort(key=lambda r:(r.verification_status!='conflict',bool(r.leader_confidence),bool(r.description),bool(r.research_directions)))
            previous={r['team_id']:dict(r) for r in s.execute(select(JOBS).where(JOBS.c.domain_id==domain_id)).mappings() if r['team_id']}
            work=[]
            for row in rows:
                context=tr.public_context(sm._research_existing(s,row))
                old=previous.get(row.id)
                if old and old['state']=='duplicate' and time.time()-old['updated']<86400:
                    results.append({'team_id':row.id,'state':'duplicate','duplicate':old['payload']['duplicate'],
                                    'run':old['payload'].get('run',{})}); continue
                if old and old['state']=='complete' and tr.review_cache_compatible(old['payload']['run']) and not retryable_evidence_gap(old['payload']['run']) and time.time()-old['updated']<86400:
                    results.append({'team_id':row.id,'state':'reused_complete','run':old['payload']['run']}); continue
                if old and old['state']=='needs_review' and tr.review_cache_compatible(old['payload'].get('run') or {}) and not retryable_evidence_gap(old['payload'].get('run') or {}) and time.time()-old['updated']<86400:
                    results.append({'team_id':row.id,'state':'needs_review','run':old['payload']['run']}); continue
                for entry in history(s,row.id):
                    for page in (entry['payload'].get('run') or {}).get('pages',[]):
                        if tr.cached_page_usable(page): cache.setdefault(page['url'],page)
                if old:
                    for page in (old['payload'].get('checkpoint') or old['payload'].get('run') or {}).get('pages',[]):
                        if tr.cached_page_usable(page): cache.setdefault(page['url'],page)
                work.append((row.id,context))
            # Previously discovered unfinished candidates resume without rediscovery.
            for team_id,job in previous.items():
                if not any(r.id==team_id for r in rows):
                    for page in (job['payload'].get('checkpoint') or job['payload'].get('run') or {}).get('pages',[]):
                        if tr.cached_page_usable(page):cache.setdefault(page['url'],page)
                    if job['state']=='complete' and tr.review_cache_compatible(job['payload'].get('run') or {}) and not retryable_evidence_gap(job['payload'].get('run') or {}) and time.time()-job['updated']<86400:
                        results.append({'team_id':team_id,'state':'reused_complete','run':job['payload']['run']})
                    else:
                        work.append((team_id,job['payload']['context']))
            # A source-contract upgrade needs only the independent check, not
            # another network investigation. Finish these cheap saved steps first.
            work.sort(key=lambda item: not ((previous.get(item[0],{}).get('payload',{}).get('run') or {}).get('status')=='reviewed'))

        with factory() as s:
            scope_job=s.execute(select(JOBS).where(JOBS.c.id=='scope:'+domain_id)).mappings().first()
        scope=None
        if scope_job and scope_job['payload'].get('domain_name')==domain_name and time.time()-scope_job['updated']<86400:
            scope=scope_job['payload']['scope']
        elif deadline-time.monotonic()>60:
            planner=tr.Research(sm._shared_agent_llm_text,seconds=min(60,int(deadline-time.monotonic())),cached_pages=cache)
            try:
                scope=planner.call('domain-scope',DomainScope,{'input_domain':domain_name,
                    'instruction_detail':'在看到任何候选之前，解释用户输入学科的准确范围。给出核心研究对象、应纳入的研究内容、仅相邻但不足以纳入的方向及必须提供的联系证据。不能把具体下位领域泛化为整个上位大类；不要推荐团队/机构/URL，不编造团队事实。这是检索定义，不是团队优势评价。'}).model_dump()
                save_job(factory,'scope:'+domain_id,domain_id,None,'complete',{'domain_name':domain_name,'scope':scope})
            except Exception as exc:
                planner.errors.append({'stage':'domain-scope','kind':type(exc).__name__})
            scope_run={'trace':planner.trace,'counts':planner.counts,'errors':planner.errors}

        def process(item, queued_at=None):
            team_started=time.monotonic()
            queue_wait=round(team_started-(queued_at or team_started),3)
            team_id,context=item; job_id=team_id
            if cache.unavailable:
                save_job(factory,job_id,domain_id,team_id,'blocked_provider',{'context':context,'reason':cache.unavailable,
                    **({'checkpoint':previous[team_id]['payload'].get('checkpoint',{}),'run':previous[team_id]['payload'].get('run')} if team_id in previous else {})})
                return {'team_id':team_id,'state':'blocked_provider'}
            remaining=deadline-time.monotonic()
            if remaining < 105:
                prior = previous.get(team_id, {}).get('payload', {})
                save_job(factory,job_id,domain_id,team_id,'budget_exhausted',{**prior,'context':context,'reason':'领域预算不足以启动单团队'})
                return {'team_id':team_id,'state':'budget_exhausted'}
            token=uuid.uuid4().hex
            if not acquire(factory,'team:'+team_id,token,min(team_seconds,remaining)+90):
                return {'team_id':team_id,'state':'busy'}
            latest_checkpoint = {}
            def checkpoint(value):
                latest_checkpoint.clear(); latest_checkpoint.update(value)
                save_job(factory,job_id,domain_id,team_id,'running',{'context':context,'checkpoint':value})
            try:
                old=previous.get(team_id)
                resume=(old['payload'].get('checkpoint') or old['payload'].get('run') or {}) if old else {}
                research=tr.Research(sm._shared_agent_llm_text,seconds=min(team_seconds,int(remaining-45)),cached_pages=cache,checkpoint=checkpoint,resume=resume)
                if old and tr.review_cache_compatible(old['payload'].get('run') or {}) and (old['payload'].get('run') or {}).get('status')=='reviewed' and not retryable_evidence_gap(old['payload'].get('run') or {}) and time.time()-old['updated']<86400:
                    run=old['payload']['run']
                else:
                    run=research.run(context,domain_name)
                storage_started=time.monotonic()
                with factory() as s:
                    s.execute(text('BEGIN IMMEDIATE'))
                    row=s.get(sm.StrategicTeamRow,team_id)
                    current_domain=s.get(sm.StrategicDomainRow,domain_id)
                    # Re-read after network work; a changed personnel claim must be reviewed again.
                    if current_domain.deleted or (row is not None and row.deleted):
                        append_history(s,team_id,'deleted_preserved',{'run':run,'published':False})
                        s.commit();state='needs_review';published=False
                    elif row and tr.public_context(sm._research_existing(s,row)).get('people') != context.get('people',[]):
                        append_history(s,team_id,'concurrent_change',{'run':run,'published':False})
                        s.commit(); state='concurrent_change'; published=False
                    else:
                        from .team_research_store import collected_identity
                        if row is None and run.get('extracted') and collected_identity(run):
                            value=run['extracted']
                            row=sm.StrategicTeamRow(id=team_id,domain_id=domain_id,
                                name=value['institution_name']['value'],institution_name=value['institution_name']['value'],
                                team_name=value['team_name']['value'])
                            s.add(row);s.flush()
                            published=persist(s,row,run)
                        elif row is None and qualified(run):
                            published=apply_reviewed_run(s,team_id,run,allowed_domains={domain_name},domain_id=domain_id)
                        else:
                            published=bool(row is not None and persist(s,row,run))
                            if row is None: append_history(s,team_id,'pending',{'run':run,'published':False})
                        s.commit()
                        state='blocked_provider' if cache.unavailable else ('complete' if published else 'no_new_data')
                storage_seconds=round(time.monotonic()-storage_started,3)
                save_job(factory,job_id,domain_id,team_id,state,{'context':context,'run':run,
                    'missing_fields':missing_fields(run)})
                return {'team_id':team_id,'state':state,'published':published,'run':run,
                    'queue_wait_seconds':queue_wait,'storage_seconds':storage_seconds,
                    'completed_at_seconds':round(time.monotonic()-started,3),
                    'seconds':round(time.monotonic()-team_started,3)}
            except Exception as exc:
                prior = previous.get(team_id, {}).get('payload', {})
                save_job(factory,job_id,domain_id,team_id,'failed',{**prior,'context':context,
                    'checkpoint':latest_checkpoint or prior.get('checkpoint', {}),'error':type(exc).__name__})
                return {'team_id':team_id,'state':'failed','error':type(exc).__name__}
            finally:
                release(factory,'team:'+team_id,token)

        def batch(work):
            # Fixed two workers, one transaction per result; no unbounded fan-out.
            with ThreadPoolExecutor(max_workers=max(1,min(2,workers))) as pool:
                futures=[pool.submit(process,item,time.monotonic()) for item in work]
                for future in as_completed(futures): results.append(future.result())

        batch(work)
        discovery_runs=[]
        attempted=[context for _,context in work]
        for discovery_round in range(2):
            if cache.unavailable or sum(qualified(r.get('run',{})) for r in results)>=8 or deadline-time.monotonic()<=300:
                break
            research=tr.Research(sm._shared_agent_llm_text,seconds=min(300,int(deadline-time.monotonic())),cached_pages=cache)
            existing=[{'institution_name':r.institution_name,'team_name':r.team_name} for r in rows]+[
                {'institution_name':c['institution_name'],'team_name':c['team_name']} for c in attempted]
            try:
                candidates=discovery(domain_name,existing,research,scope=scope)
                discovered.extend(candidates)
                known={sm._canonical_team_key(c['institution_name'],c['team_name']) for c in existing}
                more=[]
                for candidate in candidates:
                    ck=sm._canonical_team_key(candidate['institution_name'],candidate['team_name'])
                    if ck in known: continue
                    known.add(ck)
                    team_id=sm._candidate_id(domain_id,ck)
                    context=tr.public_context({**candidate,'team_id':team_id})
                    save_job(factory,team_id,domain_id,team_id,'queued',{'context':context})
                    more.append((team_id,context))
                    attempted.append(context)
                batch(more)
            except Exception as exc:
                research.errors.append({'stage':'discovery','kind':type(exc).__name__})
            discovery_runs.append({'round':discovery_round+1,'trace':research.trace,'counts':research.counts,'errors':research.errors})
        if discovery_runs:
            discovery_run={'rounds':discovery_runs,'candidates':discovered}
        # Independent semantic alias review, never count different labels twice.
        valid={r['team_id']:r for r in results if r['state'] in ('complete','reused_complete') and qualified(r.get('run',{}))}
        dedupe_run=None
        if not cache.unavailable and len(valid)>1 and deadline-time.monotonic()>30:
            reviewer=tr.Research(sm._shared_agent_llm_text,seconds=min(100,int(deadline-time.monotonic())))
            reviewer.pages={p['url']:p for r in valid.values() for p in r['run'].get('pages',[])}
            try:
                decision=reviewer.call('domain-deduplicate',Duplicates,{'domain':domain_name,
                    'existing_stable_ids':[row.id for row in rows],
                    'teams':[{'id':i,**{k:v for k,v in r['run']['reviewed'].items() if k in
                              ('institution_name','team_name','description','entity_reason','entity_citations')}} for i,r in valid.items()],
                    'instruction_detail':'仅依据已核验具体实体与原文引文判断同一实体别名/重复。不同研究部、母机构与下属独立团队不是同一实体。不能因领域相近就合并。明确相同才给duplicate_of，并引用给定原文。优先保留existing_stable_ids中的旧ID；每组只能有一个保留ID，不产生循环或链条。'}).model_dump()
                persist_duplicates(factory, valid, decision, reviewer)
                dedupe_run={'decision':decision,'trace':reviewer.trace,'counts':reviewer.counts,'errors':reviewer.errors}
            except Exception as exc:
                dedupe_run={'error':type(exc).__name__,'counts':reviewer.counts,'errors':reviewer.errors}
        qualified_ids=[r['team_id'] for r in results if r['state'] in ('complete','reused_complete')]
        count=len(set(qualified_ids))
        with factory() as s:
            published_people=[sm._team_people(s,i) for i in qualified_ids]
        summary={'provider':'公开来源','pipeline':'resumable-domain→team-evidence→incremental',
            'teamCount':count,'namedTeamCount':count,'candidateCount':len(results),'reportCount':0,
            'leaderCount':sum(bool(leaders) for leaders,members in published_people),
            'memberTeamCount':sum(bool(members) for leader,members in published_people),
            'memberCount':sum(len(members) for leader,members in published_people),
            'pendingCount':sum(r['state'] not in ('complete','reused_complete','duplicate') for r in results),
            'duplicateCount':sum(r['state']=='duplicate' for r in results),
            'seconds':round(time.monotonic()-started,3),'results':results,'discovery':discovery_run,'deduplication':dedupe_run,
            'scope':scope,'scope_run':scope_run,
            'updatedAt':sm._now().isoformat()}
        dedupe_complete = len(valid)<2 or bool(dedupe_run and 'decision' in dedupe_run)
        summary['deduplicationComplete']=dedupe_complete
        incomplete = count<8 or not dedupe_complete or any(r['state'] in ('failed','budget_exhausted','busy','concurrent_change','blocked_provider') for r in results)
        save_job(factory,'domain:'+domain_id,domain_id,None,'incomplete' if incomplete else 'complete',summary)
        session.expire_all()
        if incomplete:
            provider_reason=('；原模型服务不可用：'+str(cache.unavailable.get('provider_code','authorization'))) if cache.unavailable else ''
            raise sm._SyncQualityError(f'{domain_name} 已保存本轮 {count} 个团队的资料，部分资料获取尚未完成{provider_reason}；已有数据保持展示')
        return {k:v for k,v in summary.items() if k not in ('results','discovery','deduplication','scope','scope_run')}
    finally:
        release(factory,key,owner)
