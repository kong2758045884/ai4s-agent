"""Isolated failure, lease, budget and cache regressions; no network claims."""
import copy
import json
import time
import unittest
from unittest.mock import patch
from datetime import datetime,timezone,timedelta
from tests import test_team_research_pipeline as fixtures
sm,tr,store,run,fact=fixtures.sm,fixtures.tr,fixtures.store,fixtures.run,fixtures.fact
from ai4s_tool.api import domain_research as dr


def valid():
    value=run()
    value['contract_version']=3
    value['qualification_review']={'version':2,'decision':{'relationship':'direct'}}
    for k in ('concrete_team','domestic','domain_relevance','advantage'):
        value['reviewed'][k]=fact('有具体原文依据')
    return value


class DomainTest(unittest.TestCase):
    def setUp(self):
        fixtures.PipelineTest.setUp(self)
        dr.META.create_all(sm._ENGINE)
        with sm._SESSION_FACTORY() as s:
            s.execute(dr.JOBS.delete());s.execute(dr.LEASES.delete());s.commit()
        dr.save_job(sm._SESSION_FACTORY,'scope:d','d',None,'complete',{'domain_name':'测试领域',
            'scope':{'definition':'测试定义','included_subjects':[],'adjacent_but_insufficient':[],
                     'required_connection_evidence':'具体任务原文'}})

    def test_cross_session_lease_and_owner_safe_release(self):
        self.assertTrue(dr.acquire(sm._SESSION_FACTORY,'team:t','a',30))
        self.assertFalse(dr.acquire(sm._SESSION_FACTORY,'team:t','b',30))
        dr.release(sm._SESSION_FACTORY,'team:t','b')
        self.assertFalse(dr.acquire(sm._SESSION_FACTORY,'team:t','c',30))
        dr.release(sm._SESSION_FACTORY,'team:t','a')
        self.assertTrue(dr.acquire(sm._SESSION_FACTORY,'team:t','b',30))

    def test_expired_lease_recovers(self):
        self.assertTrue(dr.acquire(sm._SESSION_FACTORY,'team:t','dead',-1))
        self.assertTrue(dr.acquire(sm._SESSION_FACTORY,'team:t','new',30))

    def test_qualification_does_not_count_institution_or_empty_advantage(self):
        self.assertTrue(dr.qualified(valid()))
        value=valid();value['reviewed']['advantage']['status']='pending'
        self.assertFalse(dr.qualified(value))

    def test_review_cache_signature_rejects_changed_model_or_rules(self):
        value=valid();value['review_context']=tr.review_context()
        self.assertTrue(tr.review_cache_compatible(value))
        value['review_context']={**value['review_context'],'model':'different-model'}
        self.assertFalse(tr.review_cache_compatible(value))

    def test_missing_fields_uses_review_status_not_truthy_objects(self):
        value=valid();value['reviewed']['description']['status']='pending'
        value['reviewed']['leader']['status']='conflict'
        self.assertIn('description',dr.missing_fields(value))
        self.assertIn('leader',dr.missing_fields(value))

    def test_only_actionable_review_gaps_bypass_the_daily_cache(self):
        pending=valid();pending['reviewed']['domain_relevance']['status']='pending'
        self.assertTrue(dr.retryable_evidence_gap(pending))
        missing_people=valid();missing_people['reviewed']['members']=[]
        self.assertTrue(dr.retryable_evidence_gap(missing_people))
        rejected=valid();rejected['reviewed']['domain_relevance']['status']='rejected'
        self.assertFalse(dr.retryable_evidence_gap(rejected))
        self.assertFalse(dr.retryable_evidence_gap(valid()))

    def test_public_context_remains_idempotent_for_resume(self):
        original={'team_id':'t','institution_name':'测试所','team_name':'测试组','contact_record':'不能外发',
            'people':[{'id':'p','name':'甲','evidence':json.dumps({'relation':[],'sources':[],
                'fields':{'title':fact('研究员')},'secret_internal':'不能外发'})}]}
        clean=tr.public_context(original)
        self.assertEqual(clean,tr.public_context(clean))
        self.assertNotIn('不能外发',json.dumps(clean,ensure_ascii=False))

    def test_empty_database_subdomain_get_never_seeds(self):
        with sm._SESSION_FACTORY() as s:
            s.query(sm.StrategicTeamRow).delete();s.query(sm.StrategicDomainRow).delete();s.commit()
        with patch.object(sm,'_seed_defaults',side_effect=AssertionError('GET must not seed')):
            with self.assertRaises(sm.HTTPException) as error:sm.get_subdomain('missing')
            self.assertEqual(404,error.exception.status_code)
        with sm._SESSION_FACTORY() as s:self.assertEqual(0,s.query(sm.StrategicDomainRow).count())

    def test_discovery_only_returns_urls_actually_found(self):
        def fake_call(stage,schema,payload):
            if stage=='domain-plan':return tr.Plan(queries=['generic field team'],reason='test')
            return dr.Candidates(candidates=[dr.Candidate(institution_name='测试所',team_name='甲组',
                source_urls=['https://example.org/real','https://invented.invalid'],reason='hint',concrete_team_named=True,
                name_evidence=[tr.Citation(url='https://example.org/real',quote='甲组')])])
        research=tr.Research(lambda **kwargs:'')
        with patch.object(research,'call',side_effect=fake_call),patch.object(tr,'search_links',return_value=[{'url':'https://example.org/real','label':'甲组'}]) as search:
            result=dr.discovery('测试领域',[],research)
        self.assertEqual(1,search.call_count)
        self.assertEqual(['https://example.org/real'],result[0]['source_urls'])

    def test_failed_team_keeps_latest_checkpoint(self):
        def fail(research,context,domain):
            research.pages={'https://example.org':{'url':'https://example.org','text':'captured'}}
            research.event('fetch',url='https://example.org')
            raise RuntimeError('storage or network interruption')
        with patch.object(tr.Research,'run',fail):
            with sm._SESSION_FACTORY() as s:
                with self.assertRaises(sm._SyncQualityError):dr.sync_domain(s,s.get(sm.StrategicDomainRow,'d'),seconds=120)
        with sm._SESSION_FACTORY() as s:
            job=s.execute(dr.select(dr.JOBS).where(dr.JOBS.c.team_id=='t')).mappings().one()
            self.assertEqual('captured',job['payload']['checkpoint']['pages'][0]['text'])

    def test_semantic_duplicate_hidden_without_deleting_ids_or_manual_fields(self):
        with sm._SESSION_FACTORY() as s:
            s.add(sm.StrategicTeamRow(id='alias',domain_id='d',name='测试所',institution_name='测试所',
                team_name='甲研究组别名',verification_status='verified',contact_record='人工别名记录'))
            s.commit()
        valid_rows={'t':{'state':'complete'},'alias':{'state':'complete'}}
        reviewer=tr.Research(lambda **kwargs:'');reviewer.pages={p['url']:p for p in valid()['pages']}
        decision={'duplicates':[{'team_id':'alias','duplicate_of':'t','reason':'明确连续更名',
                                'citations':valid()['reviewed']['team_name']['citations']}]}
        dr.persist_duplicates(sm._SESSION_FACTORY,valid_rows,decision,reviewer)
        self.assertEqual('duplicate',valid_rows['alias']['state'])
        with sm._SESSION_FACTORY() as s:
            row=s.get(sm.StrategicTeamRow,'alias')
            self.assertFalse(row.deleted);self.assertEqual('duplicate',row.verification_status)
            self.assertEqual('人工别名记录',row.contact_record)
            self.assertEqual('t',store.history(s,'alias')[0]['payload']['duplicate_of'])
        value=valid();value['reviewed']['concrete_team']['status']='rejected'
        self.assertFalse(dr.qualified(value))

    def test_cache_ttl_and_repeated_url(self):
        cache=dr.BatchCache()
        page={'url':'https://example.org','status':'ok','text':'body','fetched_at':datetime.now(timezone.utc).isoformat()}
        with patch.object(tr,'fetch_page',return_value=page) as fetch:
            self.assertFalse(cache.read(page['url'])[1]);self.assertTrue(cache.read(page['url'])[1])
            self.assertEqual(1,fetch.call_count)
            stale={**page,'fetched_at':(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()}
            cache[page['url']]=stale
            self.assertFalse(cache.read(page['url'])[1]);self.assertEqual(2,fetch.call_count)
            self.assertEqual(stale,fetch.call_args.args[1])

    def test_transient_fetch_failure_does_not_replace_stale_good_body(self):
        cache=dr.BatchCache()
        stale={'url':'https://example.org','status':'ok','text':'last known body',
               'fetched_at':(datetime.now(timezone.utc)-timedelta(days=2)).isoformat(),
               'etag':'v1'}
        cache[stale['url']]=stale
        failed={'url':stale['url'],'status':'fetch_failed','text':'','links':[],
                'fetched_at':datetime.now(timezone.utc).isoformat(),'seconds':1,'error':'ReadTimeout'}
        with patch.object(tr,'fetch_page',return_value=failed):
            observed,reused=cache.read(stale['url'])
        self.assertFalse(reused)
        self.assertEqual('fetch_failed',observed['status'])
        self.assertEqual('last known body',cache[stale['url']]['text'])

    def test_changed_source_replaces_stale_body_and_content_identity(self):
        cache=dr.BatchCache()
        stale={'url':'https://example.org','status':'ok','text':'old body',
               'fetched_at':(datetime.now(timezone.utc)-timedelta(days=2)).isoformat(),
               'content_sha256':tr.page_content_id({'text':'old body'})}
        changed={'url':stale['url'],'status':'ok','text':'new body','links':[],
                 'fetched_at':datetime.now(timezone.utc).isoformat(),'seconds':1,
                 'content_sha256':tr.page_content_id({'text':'new body'})}
        cache[stale['url']]=stale
        with patch.object(tr,'fetch_page',return_value=changed) as fetch:
            observed,reused=cache.read(stale['url'])
        self.assertFalse(reused)
        self.assertEqual(stale,fetch.call_args.args[1])
        self.assertEqual('new body',observed['text'])
        self.assertEqual(changed['content_sha256'],cache[stale['url']]['content_sha256'])

    def test_rate_limit_has_one_finite_outer_backoff(self):
        class RateLimit(Exception):
            status_code=429
        # Use an explicit callable rather than SDK retry machinery: the team
        # layer owns exactly one retry and records its finite wait.
        answers=iter([RateLimit('too many requests'),tr.Plan(reason='retry succeeded').model_dump_json()])
        def llm(**kwargs):
            value=next(answers)
            if isinstance(value,Exception):raise value
            return value
        research=tr.Research(llm)
        with patch.object(tr.time,'sleep') as sleep:
            result=research.call('plan',tr.Plan,{})
        self.assertEqual('retry succeeded',result.reason)
        self.assertEqual(1,research.counts['llm_retries'])
        self.assertEqual(8.0,research.counts['retry_wait_seconds'])
        sleep.assert_called_once_with(8.0)

    def test_provider_billing_error_trips_circuit_without_retry(self):
        class BillingError(Exception):
            status_code=400
            body={'code':'Arrearage','message':'provider billing issue'}
        calls=[]
        def llm(**kwargs):calls.append(kwargs);raise BillingError()
        cache=dr.BatchCache();research=tr.Research(llm,cached_pages=cache)
        with self.assertRaises(tr.ProviderUnavailable):research.call('plan',tr.Plan,{})
        with self.assertRaises(tr.ProviderUnavailable):research.call('plan',tr.Plan,{})
        self.assertEqual(1,len(calls));self.assertEqual('Arrearage',cache.unavailable['provider_code'])
        self.assertEqual('provider_unavailable',research.errors[-1]['kind'])

    def test_leader_requires_current_appointment_not_just_affiliation(self):
        value=valid()['reviewed'];research=tr.Research(lambda **kwargs:'')
        research.pages={p['url']:p for p in valid()['pages']}
        value['leader'].update(kind='head',basis='affiliation')
        research.validate_evidence(value)
        self.assertEqual('pending',value['leader']['status'])
        value=valid()['reviewed'];value['leader'].update(kind='head',basis='appointment',leadership_recency='historical_only')
        research.validate_evidence(value)
        self.assertEqual('pending',value['leader']['status'])

    def test_independent_authority_review_blocks_third_party_current_head(self):
        value=valid();value.pop('qualification_review')
        data={k:fact('原文支持') for k in ('concrete_team','domestic','domain_relevance','advantage')}
        data.update(relationship='direct',entity_level='specific_research_team',leadership_review={
            'name':'甲','source_authority':'third_party','current_exact_team_head':False,
            'reason':'商业转载不是当前官方任命','citations':value['reviewed']['leader']['citations']})
        with patch.object(tr.Research,'call',return_value=dr.Eligibility.model_validate(data)):
            reviewed=dr.review_qualification(value,'测试领域',lambda **kwargs:'')
        self.assertEqual('pending',reviewed['reviewed']['leader']['status'])
        self.assertEqual(2,reviewed['qualification_review']['version'])

    def test_institute_affiliation_does_not_make_core_member(self):
        value=valid()['reviewed'];value['members'][0].update(kind='member',basis='roster',team_relation='parent_institution')
        research=tr.Research(lambda **kwargs:'');research.pages={p['url']:p for p in valid()['pages']}
        research.validate_evidence(value)
        self.assertEqual('pending',value['members'][0]['status'])

    def test_committee_deputy_cannot_publish_as_research_deputy(self):
        value=valid()['reviewed']
        member=value['members'][0]
        member.update(role='管理委员会副主任',kind='management_committee',basis='roster')
        research=tr.Research(lambda **kwargs:'')
        research.pages={p['url']:p for p in valid()['pages']}
        research.validate_evidence(value)
        self.assertEqual('pending',member['status'])
        with sm._SESSION_FACTORY() as s:
            store.persist(s,s.get(sm.StrategicTeamRow,'t'),{**valid(),'reviewed':value});s.commit()
            self.assertEqual([],sm._team_people(s,'t')[1])

    def test_research_deputy_with_independent_team_evidence_is_retained(self):
        value=valid()['reviewed'];value['members'][0].update(kind='deputy',team_relation='exact_team',basis='appointment')
        research=tr.Research(lambda **kwargs:'')
        research.pages={p['url']:p for p in valid()['pages']}
        research.validate_evidence(value)
        self.assertEqual('verified',value['members'][0]['status'])

    def test_search_cache_deduplicates_query(self):
        cache=dr.BatchCache()
        with patch.object(tr,'search_links',return_value=[{'url':'https://example.org'}]) as search:
            self.assertFalse(cache.search('same query')[1]);self.assertTrue(cache.search('same query')[1])
            self.assertEqual(1,search.call_count)

    def test_budget_records_unstarted_team_without_network(self):
        with patch.object(tr.Research,'run') as research:
            with sm._SESSION_FACTORY() as s:
                with self.assertRaises(sm._SyncQualityError):dr.sync_domain(s,s.get(sm.StrategicDomainRow,'d'),seconds=1)
            research.assert_not_called()
        with sm._SESSION_FACTORY() as s:
            job=s.execute(dr.select(dr.JOBS).where(dr.JOBS.c.team_id=='t')).mappings().one()
            self.assertEqual('budget_exhausted',job['state'])

    def test_team_failure_keeps_other_committed_result_and_manual_data(self):
        with sm._SESSION_FACTORY() as s:
            s.add(sm.StrategicTeamRow(id='bad',domain_id='d',name='测试所',institution_name='测试所',team_name='乙研究组'))
            s.get(sm.StrategicTeamRow,'t').contact_record='人工记录';s.commit()
        def observe(self,context,domain):
            if context['team_id']=='bad': raise RuntimeError('network failure')
            return valid()
        with patch.object(tr.Research,'run',observe):
            with sm._SESSION_FACTORY() as s:
                with self.assertRaises(sm._SyncQualityError):dr.sync_domain(s,s.get(sm.StrategicDomainRow,'d'),seconds=120)
        with sm._SESSION_FACTORY() as s:
            self.assertEqual('verified',s.get(sm.StrategicTeamRow,'t').verification_status)
            self.assertEqual('人工记录',s.get(sm.StrategicTeamRow,'t').contact_record)
            self.assertEqual(1,len(store.history(s,'t','verified')))
            self.assertEqual('failed',s.execute(dr.select(dr.JOBS.c.state).where(dr.JOBS.c.team_id=='bad')).scalar_one())
        # The successful job is reused on retry; only the failed team is retried.
        with patch.object(tr.Research,'run',side_effect=RuntimeError('still offline')) as research:
            with sm._SESSION_FACTORY() as s:
                with self.assertRaises(sm._SyncQualityError):dr.sync_domain(s,s.get(sm.StrategicDomainRow,'d'),seconds=120)
            self.assertEqual(1,research.call_count)

    def test_saved_person_is_visible_without_new_core_review(self):
        with sm._SESSION_FACTORY() as s:
            s.add(sm.StrategicPersonRow(id='old',team_id='t',name='旧人员',role='成员',verification_status='verified',evidence='{}'))
            s.commit()
            self.assertEqual('old',sm._team_people(s,'t')[1][0]['id'])

    def test_controlled_import_blocks_changed_people_and_wrong_scope(self):
        value=valid()
        with sm._SESSION_FACTORY() as s:
            value['existing']=tr.public_context(sm._research_existing(s,s.get(sm.StrategicTeamRow,'t')))
            with self.assertRaises(ValueError):store.apply_reviewed_run(s,'t',value,allowed_domains={'其他领域'})
            s.add(sm.StrategicPersonRow(id='concurrent',team_id='t',name='并发新增',role='待审核',verification_status='pending'))
            s.commit()
            with self.assertRaises(ValueError):store.apply_reviewed_run(s,'t',value,allowed_domains={'测试领域'})

    def test_new_controlled_import_requires_scope_and_deduplicates(self):
        value=valid();value['existing']={'team_id':'new','people':[]}
        value['reviewed']['team_name']['value']='新研究组'
        with sm._SESSION_FACTORY() as s:
            with self.assertRaises(ValueError):store.apply_reviewed_run(s,'new',value,allowed_domains={'测试领域'})
            self.assertTrue(store.apply_reviewed_run(s,'new',value,allowed_domains={'测试领域'},domain_id='d'))
            s.commit()
            value['existing']['team_id']='second'
            with self.assertRaises(ValueError):store.apply_reviewed_run(s,'second',value,allowed_domains={'测试领域'},domain_id='d')
            self.assertIsNone(s.get(sm.StrategicTeamRow,'second'))

    def test_evidenced_out_of_scope_import_is_explicit_and_preserves_manual(self):
        value=valid();value['reviewed']['domain_relevance']['status']='rejected'
        with sm._SESSION_FACTORY() as s:
            t=s.get(sm.StrategicTeamRow,'t');t.contact_record='保留联系';s.commit()
            value['existing']=tr.public_context(sm._research_existing(s,t))
            with self.assertRaises(ValueError):store.apply_reviewed_run(s,'t',value,allowed_domains={'测试领域'})
            self.assertTrue(store.apply_reviewed_run(s,'t',value,allowed_domains={'测试领域'},allow_out_of_scope=True))
            s.commit();self.assertEqual('out_of_scope',t.verification_status);self.assertEqual('保留联系',t.contact_record)

    def test_rollback_new_team_preserves_other_new_records(self):
        value=valid();value['existing']={'team_id':'new','people':[]};value['reviewed']['team_name']['value']='新研究组'
        row=lambda x:{c.name:getattr(x,c.name) for c in x.__table__.columns}
        with sm._SESSION_FACTORY() as s:
            store.apply_reviewed_run(s,'new',value,allowed_domains={'测试领域'},domain_id='d');s.flush()
            after={'team':row(s.get(sm.StrategicTeamRow,'new')),'people':{p.id:row(p) for p in s.query(sm.StrategicPersonRow).filter_by(team_id='new')}}
            ids=[h['id'] for h in store.history(s,'new')];s.commit()
            self.assertEqual([],store.rollback_import(s,{'team':None,'people':{}},after,ids));s.commit()
            self.assertIsNone(s.get(sm.StrategicTeamRow,'new'))
            self.assertIsNotNone(s.get(sm.StrategicTeamRow,'t'))

    def test_unverified_old_candidate_cannot_veto_evidenced_current_leader(self):
        with sm._SESSION_FACTORY() as s:
            s.add(sm.StrategicPersonRow(id='old',team_id='t',name='未核实人名',role='负责人',is_leader=True,verification_status='pending'))
            s.commit();store.persist(s,s.get(sm.StrategicTeamRow,'t'),valid());s.commit()
            self.assertEqual('甲',sm._team_people(s,'t')[0][0]['name'])
            self.assertEqual('pending',s.get(sm.StrategicPersonRow,'old').verification_status)

    def test_rollback_preserves_concurrent_manual_edit(self):
        def snapshot(s):
            row=lambda x:{c.name:getattr(x,c.name) for c in x.__table__.columns}
            return {'team':row(s.get(sm.StrategicTeamRow,'t')),
                    'people':{p.id:row(p) for p in s.query(sm.StrategicPersonRow).filter_by(team_id='t')}}
        with sm._SESSION_FACTORY() as s:
            before=snapshot(s);store.persist(s,s.get(sm.StrategicTeamRow,'t'),valid());s.flush()
            after=snapshot(s);ids=[h['id'] for h in store.history(s,'t')];s.commit()
            s.get(sm.StrategicTeamRow,'t').contact_record='回填后人工修改';s.commit()
            self.assertEqual([{'id':'t','field':'updated_at'}],store.rollback_import(s,before,after,ids));s.commit()
            self.assertEqual(before['team']['team_name'],s.get(sm.StrategicTeamRow,'t').team_name)
            self.assertEqual('回填后人工修改',s.get(sm.StrategicTeamRow,'t').contact_record)
            self.assertEqual(0,s.query(sm.StrategicPersonRow).filter_by(team_id='t').count())


if __name__=='__main__':unittest.main()
