"""Failure/transaction regressions. Synthetic fixtures do not claim network accuracy."""
import copy
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

_TMP = Path(os.getenv('STRATEGIC_MAP_TEST_TMP', tempfile.gettempdir())) / ('strategic-map-test-' + uuid.uuid4().hex)
_TMP.mkdir()
os.environ['STRATEGIC_MAP_DB_PATH'] = str(_TMP / 'isolated.db')
from ai4s_tool.api import strategic_map as sm
from ai4s_tool.api import team_research as tr
from ai4s_tool.api import team_research_store as store
from sqlalchemy import event
from fastapi import FastAPI
from fastapi.testclient import TestClient

URL = 'https://example.org/team'
QUOTE = '甲研究组主任甲教授，成员乙研究员。'
CITE = [{'url': URL, 'quote': QUOTE}]


def fact(value='', status='verified'):
    return {'value': value, 'status': status, 'reason': '', 'citations': copy.deepcopy(CITE) if value else []}


def person(name='甲', role='研究组主任'):
    return tr.Person(name=name, role=role, status='verified', citations=CITE,
                     title=fact('教授'), research_direction=fact('方向一'), bio=fact('已核实简介'),
                     core_membership=fact('当前研究组科研骨干'),team_relation='exact_team',
                     leadership_recency='current_official_listing').model_dump()


def run(leader=True):
    result = tr.Result(institution_name=fact('测试所'), team_name=fact('甲研究组'),
        entity_relation='same', entity_reason='同一实体', entity_citations=CITE,
        description=fact('测试简介'), research_directions=[fact('方向一')],
        leader=person() if leader else None, members=[person('乙','研究组成员')]).model_dump()
    return {'status': 'reviewed', 'reviewed': result, 'pages': [
        {'url': URL, 'text': QUOTE, 'status': 'ok', 'fetched_at': '2026-09-20T00:00:00Z', 'published_at': ''}]}


class PipelineTest(unittest.TestCase):
    def setUp(self):
        with sm._SESSION_FACTORY() as s:
            for table in (sm.StrategicRefreshTaskRow,sm.StrategicPersonRow,sm.StrategicTeamRow,sm.StrategicDomainRow):
                s.query(table).delete()
            store.HISTORY.create(s.connection(), checkfirst=True)
            s.execute(store.HISTORY.delete())
            s.add(sm.StrategicDomainRow(id='d',name='测试领域'))
            s.add(sm.StrategicTeamRow(id='t',domain_id='d',name='测试所',institution_name='测试所',
                  team_name='甲研究组',focus='旧方向',core_direction='旧方向',recent_update='近期',
                  ai_level='较高',science_level='较高',dual_judgement='AI 较高｜科学 较高'))
            s.commit()
        app=FastAPI(); app.include_router(sm.router,prefix='/v1')
        self.client=TestClient(app)

    def save(self, value):
        with sm._SESSION_FACTORY() as s:
            published=store.persist(s,s.get(sm.StrategicTeamRow,'t'),value)
            s.commit()
            return published

    def detail(self):
        return self.client.get('/v1/strategic-map/teams/t').json()['data']

    def test_legacy_migration_preserves_records_and_does_not_promote(self):
        from sqlalchemy import create_engine
        engine = create_engine('sqlite://')
        with engine.begin() as c:
            c.exec_driver_sql('CREATE TABLE strategic_map_team (id TEXT PRIMARY KEY, name TEXT, evidence_summary TEXT, attention TEXT, contact_record TEXT)')
            c.exec_driver_sql("INSERT INTO strategic_map_team VALUES ('stable-id','历史机构','原始线索','重点关注','人工记录')")
        with patch.object(sm, '_ENGINE', engine):
            sm._ensure_team_schema()
            sm._ensure_team_schema()
        with engine.connect() as c:
            row = c.exec_driver_sql('SELECT id,name,evidence_summary,attention,contact_record,verification_status FROM strategic_map_team').one()
            self.assertEqual(('stable-id','历史机构','原始线索','重点关注','人工记录','legacy_unverified'), tuple(row))
        engine.dispose()

    def test_legacy_records_are_explicit_opt_in_with_subdomain_and_stable_detail(self):
        with sm._SESSION_FACTORY() as s:
            s.add(sm.StrategicDomainRow(id='sub', parent_id='d', name='旧子领域'))
            team = s.get(sm.StrategicTeamRow, 't')
            team.verification_status = 'legacy_unverified'
            team.subdomain_id = 'sub'
            team.contact_record = '人工记录'
            s.add(sm.StrategicTeamRow(id='conflict',domain_id='d',name='冲突记录',verification_status='conflict'))
            s.commit()
        normal = self.client.get('/v1/strategic-map').json()['data']
        self.assertEqual([], normal['teams'])
        self.assertEqual([], normal['domains'][0]['subdomains'])
        legacy = self.client.get('/v1/strategic-map?include_legacy=true').json()['data']
        self.assertEqual(['t'], [t['id'] for t in legacy['teams']])
        self.assertEqual('legacy_unverified', legacy['teams'][0]['verificationStatus'])
        self.assertEqual('sub', legacy['domains'][0]['subdomains'][0]['id'])
        filtered = self.client.get('/v1/strategic-map/domains/d/teams?include_legacy=true&subdomain_id=sub').json()['data']
        self.assertEqual(['t'], [t['id'] for t in filtered['teams']])
        self.assertEqual('人工记录', self.detail()['team']['contactRecord'])
        self.assertTrue(self.save(run()))
        self.assertEqual('verified', self.detail()['team']['verificationStatus'])
        self.assertEqual('人工记录', self.detail()['team']['contactRecord'])

    def test_incremental_preserves_ids_people_and_manual_fields(self):
        self.assertTrue(self.save(run()))
        before=self.detail()
        response=self.client.put('/v1/strategic-map/teams/t',json={
            'attention':'重点关注','contact':'已联系','contact_record':'人工联系记录',
            'core_direction':'人工研究方向','dual_judgement':'人工研判','internal_review':'内部评价',
            'next_action':'人工下一步','recent_update':'人工日期'})
        self.assertEqual(200,response.status_code)
        second=run();second['reviewed']['leader']['title']=fact('')
        second['reviewed']['leader']['bio']=fact('')
        second['reviewed']['description']=fact('')
        second['reviewed']['members']=[]
        second['reviewed']['research_directions']=[]
        self.save(second)
        after=self.detail()
        self.assertEqual(before['leader']['id'],after['leader']['id'])
        self.assertEqual(before['members'],after['members'])
        self.assertEqual('教授',after['leader']['title'])
        self.assertEqual('已核实简介',after['leader']['bio'])
        for k,v in {'attention':'重点关注','contactRecord':'人工联系记录','coreDirection':'人工研究方向',
                    'dualJudgement':'人工研判','internalReview':'内部评价','nextAction':'人工下一步','recentUpdate':'人工日期'}.items():
            self.assertEqual(v,after['team'][k])

    def test_failure_is_recorded_without_overwriting_verified_team(self):
        self.save(run());before=self.detail()
        for status in ('parse_failed','model_failed','fetch_failed','budget_exhausted'):
            self.assertFalse(self.save({'status':status,'reviewed':None,'pages':[]}))
            self.assertEqual(before,self.detail())
        with sm._SESSION_FACTORY() as s:self.assertEqual(5,len(store.history(s,'t')))

    def test_conflict_hides_both_leaders_and_retains_history(self):
        self.save(run()); changed=run(); changed['reviewed']['leader']=person('丙')
        self.save(changed)
        self.assertIsNone(self.detail()['leader'])
        with sm._SESSION_FACTORY() as s:
            leaders=s.query(sm.StrategicPersonRow).filter_by(team_id='t',is_leader=True).all()
            self.assertEqual({'conflict'},{p.verification_status for p in leaders})
            self.assertEqual(2,len(leaders))
            self.assertEqual('甲',store.history(s,'t')[0]['payload']['before']['leader']['name'])

    def test_evidence_adjudication_replaces_historical_leader(self):
        self.save(run());old=self.detail()['leader']['id']
        changed=run(); changed['reviewed']['leader']=person('丙')
        changed['reviewed']['old_people']=[{'person_id':old,'decision':'historical','reason':'任职已结束','citations':CITE}]
        self.save(changed)
        self.assertEqual('丙',self.detail()['leader']['name'])
        with sm._SESSION_FACTORY() as s:self.assertEqual('historical',s.get(sm.StrategicPersonRow,old).verification_status)

    def test_successor_is_not_grafted_on_old_team(self):
        self.save(run());before=self.detail();value=run()
        value['reviewed']['entity_relation']='successor'
        self.assertFalse(self.save(value));self.assertEqual(before,self.detail())

    def test_verified_rename_reuses_id_via_old_alias(self):
        value=run();value['reviewed']['entity_relation']='rename'
        value['reviewed']['team_name']=fact('甲研究中心')
        self.save(value)
        with sm._SESSION_FACTORY() as s:
            rows=s.query(sm.StrategicTeamRow).all();index=sm._team_alias_index(s,rows)
            self.assertEqual('t',index[sm._canonical_team_key('测试所','甲研究组')].id)
            self.assertEqual('t',index[sm._canonical_team_key('测试所','甲研究中心')].id)

    def test_repeated_empty_direction_updates_keep_verified_evidence(self):
        self.save(run());empty=run();empty['reviewed']['research_directions']=[]
        self.save(empty);self.save(empty)
        self.assertEqual(['方向一'],self.detail()['team']['researchDirections'])

    def test_rejected_old_member_is_hidden_without_deleting_history(self):
        self.save(run());old=self.detail()['members'][0]['id'];value=run()
        value['reviewed']['members']=[]
        value['reviewed']['old_people']=[{'person_id':old,'decision':'rejected','reason':'不属于团队','citations':CITE}]
        self.save(value)
        self.assertEqual([],self.detail()['members'])
        with sm._SESSION_FACTORY() as s:
            self.assertFalse(s.get(sm.StrategicPersonRow,old).deleted)
            self.assertEqual('rejected',s.get(sm.StrategicPersonRow,old).verification_status)

    def test_historical_leadership_dispute_does_not_hide_verified_membership(self):
        self.save(run());old=self.detail()['members'][0]['id'];value=run()
        value['reviewed']['members']=[]
        value['reviewed']['old_people']=[{'person_id':old,'claim':'leadership','decision':'conflict',
                                         'reason':'历史任期待查，成员归属无冲突','citations':CITE}]
        self.save(value)
        self.assertEqual(old,self.detail()['members'][0]['id'])

    def test_pending_person_not_exposed(self):
        with sm._SESSION_FACTORY() as s:
            sm._upsert_team_people(s,s.get(sm.StrategicTeamRow,'t'),{'name':'未核验','role':'主任','confidence':1},[])
            s.commit()
        self.assertIsNone(self.detail()['leader'])

    def test_get_no_dml_or_timestamp_changes_even_legacy_dates(self):
        statements=[]
        def observe(conn,cursor,statement,params,context,many):statements.append(statement)
        event.listen(sm._ENGINE,'before_cursor_execute',observe)
        try:
            before=self.detail()
            for _ in range(3):
                for path in ('','/domains','/domains/d','/domains/d/teams','/teams/t'):
                    self.assertEqual(200,self.client.get('/v1/strategic-map'+path).status_code)
            self.assertEqual(before,self.detail())
            self.assertFalse(any(s.lstrip().upper().startswith(('UPDATE','INSERT','DELETE','CREATE','ALTER')) for s in statements))
        finally:event.remove(sm._ENGINE,'before_cursor_execute',observe)
        self.assertEqual('待核实',before['team']['aiLevel'])
        self.assertEqual('',before['team']['recentUpdate'])

    def test_refresh_endpoint_reuses_active_task_and_returns_immediately(self):
        with patch.object(sm, '_launch_refresh_task') as launch:
            first = self.client.post('/v1/strategic-map/domains/d/refreshes')
            second = self.client.post('/v1/strategic-map/domains/d/refreshes')
        self.assertEqual(202, first.status_code)
        self.assertEqual(first.json()['data']['taskId'], second.json()['data']['taskId'])
        self.assertEqual('accepted', first.json()['data']['state'])
        self.assertFalse(first.json()['data']['terminal'])
        self.assertEqual(2, launch.call_count)

    def test_refresh_worker_persists_success_terminal(self):
        with sm._SESSION_FACTORY() as s:
            task = sm.StrategicRefreshTaskRow(id='refresh_success', domain_id='d', state='accepted')
            s.add(task); s.commit()
        with patch.object(sm, '_sync_domain', return_value={'teamCount': 8, 'memberCount': 3}):
            sm._run_refresh_task('refresh_success')
        with sm._SESSION_FACTORY() as s:
            task = s.get(sm.StrategicRefreshTaskRow, 'refresh_success')
            self.assertEqual('succeeded', task.state)
            self.assertEqual(8, task.result['teamCount'])
            self.assertIsNotNone(task.finished_at)

    def test_refresh_worker_exposes_partial_incremental_result(self):
        from ai4s_tool.api import domain_research as dr
        dr.META.create_all(sm._ENGINE)
        dr.save_job(sm._SESSION_FACTORY, 'domain:d', 'd', None, 'incomplete', {
            'teamCount': 2, 'candidateCount': 9, 'pendingCount': 7, 'memberCount': 1,
        })
        with sm._SESSION_FACTORY() as s:
            s.add(sm.StrategicRefreshTaskRow(id='refresh_partial', domain_id='d', state='accepted'))
            s.commit()
        with patch.object(sm, '_sync_domain', side_effect=sm._SyncQualityError('只完成一部分')):
            sm._run_refresh_task('refresh_partial')
        with sm._SESSION_FACTORY() as s:
            task = s.get(sm.StrategicRefreshTaskRow, 'refresh_partial')
            self.assertEqual('partial', task.state)
            self.assertTrue(sm._refresh_task_to_dict(task)['terminal'])
            self.assertEqual(2, task.result['teamCount'])
            self.assertIn('只完成一部分', task.message)

    def test_invalid_json_is_not_missing_leader(self):
        calls=[]
        def llm(**kw):calls.append(kw);return '# Markdown report'
        result=tr.investigate({'source_urls':[URL]},'测试领域',llm)
        self.assertEqual('parse_failed',result['status']);self.assertEqual(2,len(calls))
        self.assertIsNone(result['reviewed'])

    def test_model_failure_is_distinct(self):
        result=tr.investigate({'source_urls':[URL]},'测试领域',lambda **kw: (_ for _ in ()).throw(ConnectionError()))
        self.assertEqual('model_failed',result['status']);self.assertEqual(2,result['counts']['llm'])

    def test_failed_fetch_and_cache_are_observable(self):
        answers=iter([tr.Plan(urls=[URL],reason='官网').model_dump_json(),tr.Plan(reason='无可用证据').model_dump_json()])
        failed={'url':URL,'status':'fetch_failed','text':'','links':[],'seconds':0,'error':'ReadTimeout'}
        with patch.object(tr,'fetch_page',return_value=failed):
            result=tr.investigate({'source_urls':[URL]},'测试领域',lambda **kw:next(answers))
        self.assertEqual('fetch_failed',result['status']);self.assertEqual(0,result['counts']['fetch_success'])

    def test_citations_are_validated_and_manual_data_excluded(self):
        research=tr.Research(lambda **kw:'')
        research.pages[URL]={'status':'ok','text':QUOTE}
        self.assertTrue(research.citations_valid(CITE))
        self.assertFalse(research.citations_valid([{'url':URL,'quote':'伪造原文'}]))
        public=tr.public_context({'team_name':'公开','description':'PRIVATE','contact_record':'PRIVATE',
                                 'internal_review':'PRIVATE','people':[{'name':'公开','contact':'PRIVATE'}]})
        self.assertNotIn('PRIVATE',str(public))

    def test_url_cache_is_per_run_and_known_anchor_label_is_updated(self):
        research=tr.Research(lambda **kw:'')
        research.add_links([{'url':URL,'label':'已有来源'}])
        research.add_links([{'url':URL,'label':'官方人员入口'}])
        page={'url':URL,'status':'ok','text':QUOTE,'links':[],'seconds':0}
        with patch.object(tr,'fetch_page',return_value=page) as fetch:
            research.fetch([URL,URL]);research.fetch([URL])
            self.assertEqual(1,fetch.call_count)
            other=tr.Research(lambda **kw:'');other.add_links([{'url':URL}]);other.fetch([URL])
            self.assertEqual(2,fetch.call_count)
        self.assertEqual('官方人员入口',research.links[URL]['label'])

    def test_parallel_fetch_batch_checkpoints_once_and_deduplicates_exact_bodies(self):
        checkpoints=[]
        research=tr.Research(lambda **kw:'',checkpoint=lambda value:checkpoints.append(copy.deepcopy(value)))
        urls=[f'https://example.org/{index}' for index in range(4)]
        research.add_links([{'url':url} for url in urls])
        def page(url, cached_page=None):
            return {'url':url,'status':'ok','text':'same exact body','links':[],
                    'fetched_at':'2026-09-20T00:00:00+00:00','seconds':0}
        with patch.object(tr,'fetch_page',side_effect=page):
            research.fetch(urls)
        self.assertEqual(1,len(checkpoints))
        self.assertEqual(3,research.counts['duplicate_bodies'])
        self.assertEqual(1,len(research.evidence()))

    def test_fetch_page_revalidates_stale_body_with_http_validators(self):
        cached={'url':URL,'status':'ok','text':QUOTE,'links':[],
                'fetched_at':'2026-09-18T00:00:00+00:00','etag':'"v1"',
                'last_modified':'Fri, 18 Sep 2026 00:00:00 GMT'}
        response=tr.requests.Response();response.status_code=304;response.url=URL
        with patch.object(tr,'_public_url',return_value=URL),patch.object(tr.requests,'Session') as factory:
            client=factory.return_value.__enter__.return_value
            client.get.return_value=response
            page=tr.fetch_page(URL,cached)
        headers=client.get.call_args.kwargs['headers']
        self.assertEqual('"v1"',headers['If-None-Match'])
        self.assertEqual(304,page['http_status'])
        self.assertTrue(page['not_modified'])
        self.assertEqual(QUOTE,page['text'])
        self.assertNotEqual(cached['fetched_at'],page['validated_at'])

    def test_deepsearch_markdown_is_a_report_not_json_or_raw_evidence(self):
        class Agent:
            current_docs=[type('Doc',(),{'link':URL,'content':'search snippet'})()]
            async def run(self,**kwargs):
                yield json.dumps({'isFinal':True,'answer':'# Markdown report with {example schema}'})
        with patch('ai4s_tool.tool.deepsearch.DeepSearch',return_value=Agent()):
            result=asyncio.run(tr.deepsearch_discovery('test'))
        self.assertTrue(result['report'].startswith('# Markdown'))
        self.assertEqual([URL],result['urls'])
        self.assertNotIn('search snippet',str(result))

    def test_committee_and_project_roles_do_not_pass_research_roster_gate(self):
        research=tr.Research(lambda **kw:'');research.pages[URL]={'status':'ok','text':QUOTE}
        value=run()['reviewed']
        value['leader'].update(basis='appointment',kind='management_committee')
        value['members'][0].update(basis='project',kind='member')
        research.validate_evidence(value)
        self.assertEqual('pending',value['leader']['status'])
        self.assertEqual('pending',value['members'][0]['status'])

    def test_verified_roster_clears_stale_absence_note(self):
        research=tr.Research(lambda **kw:'');research.pages[URL]={'status':'ok','text':QUOTE}
        value=run()['reviewed'];value['members_missing_reason']='旧抽取阶段未列入'
        value['members'][0].update(basis='roster',kind='member')
        research.validate_evidence(value)
        self.assertEqual('',value['members_missing_reason'])

    def test_flat_wire_contract_keeps_person_fields_and_review_status_separate(self):
        person=tr.WirePerson(name='甲',role='主任',basis='appointment',kind='head',status='verified',
                             citations=CITE,title='教授',title_evidence=CITE,bio='待审核简介',
                             bio_evidence=CITE,verified_fields=['title'])
        canonical=person.canonical()
        self.assertEqual('教授',canonical['title']['value'])
        self.assertEqual('verified',canonical['title']['status'])
        self.assertEqual('pending',canonical['bio']['status'])
        self.assertEqual(CITE,canonical['title']['citations'])

    def test_shared_client_structured_options_are_per_call(self):
        from ai4s_tool.tool.mrag.generation import llm
        chunk=SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='{}'))],usage=None)
        usage=SimpleNamespace(choices=[],usage=SimpleNamespace(prompt_tokens=11,completion_tokens=2,total_tokens=13))
        with patch.object(llm,'OpenAI') as factory:
            sdk=factory.return_value
            sdk.with_options.return_value.chat.completions.create.return_value=iter([chunk,usage])
            client=llm.LLMClient()
            text=client.completions([{'role':'user','content':'JSON'}], timeout=10,
                max_retries=0,response_format={'type':'json_object'},include_usage=True)
            self.assertEqual('{}',text)
            self.assertEqual(13,text.usage['total_tokens'])
            sdk.with_options.assert_called_once_with(max_retries=0)
            kwargs=sdk.with_options.return_value.chat.completions.create.call_args.kwargs
            self.assertEqual(10,kwargs['timeout'])
            self.assertEqual({'type':'json_object'},kwargs['response_format'])
            self.assertEqual({'include_usage':True},kwargs['stream_options'])

    def test_strategic_map_reuses_shared_mrag_client_without_changing_text_contract(self):
        from ai4s_tool.tool.mrag.generation import llm
        client=SimpleNamespace(api_key='k',model_base_url='https://example.org/v1',model_name='m')
        client.completions=lambda *args,**kwargs:llm.CompletionText('{"ok":true}',
            usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5})
        old_client,old_config=sm._SHARED_LLM_CLIENT,sm._SHARED_LLM_CLIENT_CONFIG
        sm._SHARED_LLM_CLIENT=sm._SHARED_LLM_CLIENT_CONFIG=None
        try:
            with (
                patch.object(sm,'_llm_config',return_value=('k','https://example.org/v1','m')),
                patch('ai4s_tool.tool.mrag.generation.llm.LLMClient',return_value=client) as factory,
            ):
                first=sm._shared_agent_llm_text(task='team-test',system='s',user='u',timeout=5)
                second=sm._shared_agent_llm_text(task='team-test',system='s',user='u',timeout=5)
            self.assertEqual('{"ok":true}',first)
            self.assertEqual(5,first.observation['usage']['total_tokens'])
            self.assertEqual(1,factory.call_count)
            self.assertEqual(first,second)
        finally:
            sm._SHARED_LLM_CLIENT,sm._SHARED_LLM_CLIENT_CONFIG=old_client,old_config

    def test_stream_wall_budget_is_bounded(self):
        from ai4s_tool.tool.mrag.generation import llm
        with patch.object(llm.time,'monotonic',side_effect=[0,2]):
            with self.assertRaises(TimeoutError):list(llm._bounded_stream(iter(['chunk']),1))

def tearDownModule():
    sm._ENGINE.dispose()

if __name__=='__main__':unittest.main()
