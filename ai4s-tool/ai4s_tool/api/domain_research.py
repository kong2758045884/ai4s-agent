"""Bounded, resumable domain research used by every explicit refresh entry.

Jobs and leases are internal; the synchronous API still waits for the batch.
Successful teams commit separately. An incomplete batch raises the existing
quality error after preserving progress, never reports an enqueue as completion.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlsplit

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
    candidates: list[Candidate] = Field(default_factory=list, max_length=100)
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
    leader = value.get('leader') or {}
    return run.get('status') == 'reviewed' and (run.get('qualification_review') or {}).get('version')==2 and value.get('entity_relation') in ('same','rename') and all(
        value.get(k, {}).get('status') == 'verified' and value[k].get('value') and value[k].get('citations')
        for k in ('team_name','institution_name','concrete_team','domestic','domain_relevance','advantage')) and (
        leader.get('status') == 'verified' and leader.get('name') and leader.get('citations'))


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


_OFFICIAL_RESEARCH_HOST_SUFFIXES = (
    ".edu.cn",
    ".ac.cn",
    ".cas.cn",
    ".gov.cn",
    ".org.cn",
)
_NON_PERSON_LEADER_VALUES = {
    "负责制",
    "联系我们",
    "领导下",
    "等组成",
    "单位为",
    "委员会",
    "工作报告",
    "会议主持",
    "相关人员",
}


def _official_research_url(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").casefold()
    except ValueError:
        return False
    return bool(host) and (
        host.endswith(_OFFICIAL_RESEARCH_HOST_SUFFIXES)
        or host in {"edu.cn", "ac.cn", "cas.cn", "gov.cn", "org.cn"}
    )


def _team_aliases(team_name: str) -> tuple[str, ...]:
    values = [team_name.strip()]
    values.extend(
        item.strip()
        for item in re.findall(r"[（(]([^）)]+)[）)]", team_name)
        if len(item.strip()) >= 3
    )
    outside = re.sub(r"[（(][^）)]+[）)]", "", team_name).strip()
    if outside:
        values.append(outside)
    return tuple(dict.fromkeys(value for value in values if len(value) >= 3))


def _extract_explicit_leader(
    text: str,
    *,
    published_at: str = "",
) -> dict[str, str] | None:
    """Extract only an explicitly assigned current team leadership role."""
    normalized = " ".join(text.split())
    published_year = next(
        (
            int(value)
            for value in re.findall(r"(?<!\d)(20\d{2})(?!\d)", published_at)
        ),
        0,
    )
    name_pattern = (
        r"([\u4e00-\u9fff]{2,4}?(?=研究员|教授|院士|博士|[\s,，。；;（(]|$)|"
        r"[A-Z][A-Za-z.-]+(?:\s+[A-Z][A-Za-z.-]+){1,3})"
    )
    patterns = (
        (
            "PI",
            re.compile(
                rf"(?i)\b(?:principal investigator|PI)\b\s*[:：]?\s*{name_pattern}"
            ),
        ),
        (
            "团队负责人",
            re.compile(
                rf"(?:现任)?(?:团队负责人|实验室负责人|课题组负责人|课题组长)"
                rf"\s*(?:为|是|[:：])\s*{name_pattern}"
            ),
        ),
        (
            "主任",
            re.compile(
                rf"(?:现任)?(?:实验室主任|中心主任|主任)"
                rf"(?!助理|负责制)\s*(?:(?:为|是|[:：])\s*)?{name_pattern}"
            ),
        ),
        (
            "主任",
            re.compile(
                r"(?:现任)?(?:实验室主任|中心主任|主任)(?!助理|负责制)"
                r"\s*([\u4e00-\u9fff]{2,4})(?=研究员|教授|院士|博士)"
            ),
        ),
        (
            "主任",
            re.compile(
                r"(?:中国(?:科学院|工程院)院士\s*)?"
                r"([\u4e00-\u9fff]{2,4})"
                r"(?:院士|研究员|教授|博士)?\s*(?:现任|任|担任)"
                r"(?:该|本)?(?:实验室主任|中心主任|主任)"
            ),
        ),
    )
    for role, pattern in patterns:
        for match in pattern.finditer(normalized):
            context_prefix = normalized[max(0, match.start() - 80) : match.start()]
            prefix = context_prefix[-20:]
            if role == "主任" and re.search(
                r"(?:学术委员会|委员会|学委会)\s*$",
                prefix,
            ):
                continue
            if role == "主任" and prefix.endswith("副"):
                continue
            years = [
                int(value)
                for value in re.findall(r"(?<!\d)(20\d{2})(?!\d)", context_prefix)
            ]
            if (
                years
                and max(years) < datetime.now(timezone.utc).year - 1
                and published_year < datetime.now(timezone.utc).year - 1
                and "现任" not in normalized[max(0, match.start() - 24) : match.end()]
            ):
                return None
            name = re.sub(r"^(?:由|为)", "", match.group(1)).strip()
            tail = normalized[match.end() : match.end() + 36]
            chinese = re.match(r"\s*[（(]\s*([\u4e00-\u9fff]{2,4})", tail)
            if chinese:
                name = chinese.group(1)
            if name in _NON_PERSON_LEADER_VALUES:
                continue
            quote = normalized[max(0, match.start() - 40) : match.end() + 60]
            return {"name": name, "role": role, "quote": quote}
    return None


def _leader_near_exact_team(
    text: str,
    aliases: tuple[str, ...],
    *,
    published_at: str = "",
) -> dict[str, str] | None:
    """Find a leadership statement near an exact team-name occurrence."""
    folded = text.casefold()
    for alias in aliases:
        needle = alias.casefold()
        offset = 0
        while True:
            index = folded.find(needle, offset)
            if index < 0:
                break
            leader = _extract_explicit_leader(
                text[max(0, index - 160) : index + 1200],
                published_at=published_at,
            )
            if leader:
                return leader
            offset = index + len(needle)
    return None


def enrich_official_team_leaders(factory, domain_id: str, subdomain_id: str | None) -> int:
    """Follow exact official team links and persist explicit leadership claims."""
    from . import strategic_map as sm
    from .team_research_store import upsert_people

    with factory() as session:
        query = session.query(sm.StrategicTeamRow).filter_by(
            domain_id=domain_id,
            deleted=False,
        )
        if subdomain_id:
            query = query.filter(sm.StrategicTeamRow.subdomain_id == subdomain_id)
        pending = []
        for team in query.all():
            leaders = session.query(sm.StrategicPersonRow).filter_by(
                team_id=team.id,
                deleted=False,
                is_leader=True,
            ).all()
            trusted_leader = next(
                (
                    person
                    for person in leaders
                    if person.verification_status == "verified"
                    and not (person.source_type or "").startswith("Hyper-Extract")
                    and any(
                        str(url).startswith(("http://", "https://"))
                        for url in (person.source_urls or [])
                    )
                ),
                None,
            )
            if trusted_leader:
                continue
            for person in leaders:
                person.is_leader = False
                if (person.source_type or "").startswith("Hyper-Extract"):
                    person.role = "关联作者"
                    person.verification_status = "collected"
                    person.confidence = min(float(person.confidence or 0), 0.6)
                    person.last_verified_at = None
            if leaders:
                team.leader_confidence = 0
                sm._update_team_score(session, team)
            urls = [
                url
                for url in dict.fromkeys(
                    [*(team.source_urls or []), *(team.evidence_urls or [])]
                )
                if isinstance(url, str)
                and url.startswith(("http://", "https://"))
                and _official_research_url(url)
            ]
            pending.append(
                (
                    team.id,
                    team.institution_name or team.name,
                    team.team_name,
                    urls,
                )
            )
        session.commit()
    if not pending:
        return 0

    source_urls = sorted({url for _, _, _, urls in pending for url in urls})
    with ThreadPoolExecutor(max_workers=max(1, min(6, len(source_urls)))) as pool:
        futures = {url: pool.submit(tr.fetch_page, url) for url in source_urls}
        source_pages = {url: future.result() for url, future in futures.items()}

    discoveries: dict[str, dict[str, str]] = {}
    detail_requests: dict[str, list[tuple[str, str]]] = {}
    for team_id, _, team_name, urls in pending:
        aliases = _team_aliases(team_name)
        for source_url in urls:
            page = source_pages.get(source_url) or {}
            if page.get("status") != "ok":
                continue
            for link in page.get("links", []):
                label = " ".join(str(link.get("label") or "").split())
                if not any(alias.casefold() in label.casefold() for alias in aliases):
                    continue
                leader = _extract_explicit_leader(
                    label,
                    published_at=str(page.get("published_at") or ""),
                )
                target_url = str(link.get("url") or source_url)
                if leader:
                    discoveries[team_id] = {
                        **leader,
                        "url": source_url,
                    }
                    break
                target = (team_id, team_name)
                if target not in detail_requests.setdefault(target_url, []):
                    detail_requests[target_url].append(target)
            if team_id in discoveries:
                break
            page_text = " ".join(str(page.get("text") or "").split())
            leader = _leader_near_exact_team(
                page_text,
                aliases,
                published_at=str(page.get("published_at") or ""),
            )
            if leader:
                discoveries[team_id] = {
                    **leader,
                    "url": source_url,
                }
            if team_id in discoveries:
                break
            if any(alias.casefold() in page_text.casefold() for alias in aliases):
                target = (team_id, team_name)
                if target not in detail_requests.setdefault(source_url, []):
                    detail_requests[source_url].append(target)

    if detail_requests:
        with ThreadPoolExecutor(max_workers=min(6, len(detail_requests))) as pool:
            futures = {url: pool.submit(tr.fetch_page, url) for url in detail_requests}
            detail_pages = {url: future.result() for url, future in futures.items()}
        for url, targets in detail_requests.items():
            page = detail_pages.get(url) or {}
            if page.get("status") != "ok":
                continue
            text = " ".join(str(page.get("text") or "").split())
            for team_id, team_name in targets:
                if team_id in discoveries:
                    continue
                leader = _leader_near_exact_team(
                    text,
                    _team_aliases(team_name),
                    published_at=str(page.get("published_at") or ""),
                )
                if leader:
                    discoveries[team_id] = {**leader, "url": url}

    unresolved = [
        item
        for item in pending
        if item[0] not in discoveries
    ]
    if unresolved:
        def official_search(
            item: tuple[str, str, str, list[str]],
        ) -> tuple[str, str, list[str]]:
            team_id, institution_name, team_name, _ = item
            aliases = _team_aliases(team_name)
            found: list[str] = []
            queries = [
                f'"{team_name}" 主任 {institution_name}',
                f'"{team_name}" 负责人 PI {institution_name}',
            ]
            for query_text in queries:
                hits = sm._bing_html_search(query_text, max_results=6)
                if not hits:
                    hits = sm._duckduckgo_html_search(query_text, max_results=6)
                for hit in hits:
                    url = str(hit.get("link") or "")
                    searchable = " ".join(
                        (
                            str(hit.get("title") or ""),
                            str(hit.get("snippet") or ""),
                        )
                    ).casefold()
                    if (
                        _official_research_url(url)
                        and any(alias.casefold() in searchable for alias in aliases)
                        and url not in found
                    ):
                        found.append(url)
            return team_id, team_name, found[:6]

        with ThreadPoolExecutor(max_workers=min(6, len(unresolved))) as pool:
            searched = list(pool.map(official_search, unresolved))
        searched_targets: dict[str, list[tuple[str, str]]] = {}
        for team_id, team_name, urls in searched:
            for url in urls:
                target = (team_id, team_name)
                if target not in searched_targets.setdefault(url, []):
                    searched_targets[url].append(target)
        if searched_targets:
            with ThreadPoolExecutor(max_workers=min(6, len(searched_targets))) as pool:
                futures = {url: pool.submit(tr.fetch_page, url) for url in searched_targets}
                searched_pages = {url: future.result() for url, future in futures.items()}
            for url, targets in searched_targets.items():
                page = searched_pages.get(url) or {}
                if page.get("status") != "ok":
                    continue
                text = " ".join(str(page.get("text") or "").split())
                for team_id, team_name in targets:
                    if team_id in discoveries:
                        continue
                    leader = _leader_near_exact_team(
                        text,
                        _team_aliases(team_name),
                        published_at=str(page.get("published_at") or ""),
                    )
                    if leader:
                        discoveries[team_id] = {**leader, "url": url}

    if not discoveries:
        return 0
    with factory() as session:
        added = 0
        for team_id, leader in discoveries.items():
            team = session.get(sm.StrategicTeamRow, team_id)
            if not team or team.deleted:
                continue
            existing = session.query(sm.StrategicPersonRow).filter_by(
                team_id=team.id,
                deleted=False,
                is_leader=True,
            ).first()
            if existing:
                continue
            upsert_people(
                session,
                team,
                {
                    "name": leader["name"],
                    "role": leader["role"],
                    "bio": "",
                    "profile_url": leader["url"],
                    "source_urls": [leader["url"]],
                    "source_type": "官方团队页面·规则核验",
                    "verification_status": "verified",
                    "confidence": 0.95,
                    "evidence": json.dumps(
                        {
                            "url": leader["url"],
                            "quote": leader["quote"],
                            "rule": "exact-team-link+explicit-leadership-role",
                        },
                        ensure_ascii=False,
                    ),
                },
                [],
            )
            team.leader_confidence = max(float(team.leader_confidence or 0), 0.95)
            sm._update_team_score(session, team)
            team.updated_at = sm._now()
            added += 1
        session.commit()
    return added


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


def _prioritize_team_work(work, previous):
    """Spend a bounded run on fresh and deferred teams before stale rechecks.

    Stable sorting retains the candidate order within a tier. Oldest deferred
    jobs go first, so a large domain can make progress across daily runs.
    """
    def priority(item):
        old = previous.get(item[0])
        if not old:
            return (1, 0)
        payload = old.get('payload') or {}
        run = payload.get('run') or {}
        state = old.get('state')
        if state != 'complete' and run.get('status') == 'reviewed':
            tier = 0  # Only the independent qualification may remain.
        elif state == 'budget_exhausted':
            tier = 2
        elif state in ('running', 'failed', 'blocked_provider', 'queued'):
            tier = 3
        elif state == 'complete':
            tier = 5
        else:
            tier = 4
        return (tier, float(old.get('updated') or 0))

    return sorted(work, key=priority)


class BatchCache(dict):
    """24h evidence TTL, including original capture time; coalesce concurrent URLs."""
    def __init__(self):
        super().__init__(); self.lock = threading.RLock(); self.url_locks = {}; self.search_results = {}; self.query_locks = {}
        self.unavailable=None; self.on_failure=None; self.transient_failures=0

    def trip(self, failure):
        with self.lock:
            if self.unavailable:return
            self.unavailable=failure
            if self.on_failure:self.on_failure(failure)

    def record_transient(self, failure):
        """Open the shared batch circuit after three consecutive 429/5xx model errors."""
        with self.lock:
            self.transient_failures += 1
            if self.transient_failures >= 3:
                self.trip({**failure, 'kind': 'transient_circuit_open'})

    def clear_transient(self):
        with self.lock:
            self.transient_failures = 0

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


def discovery(
    domain_name,
    existing,
    research,
    rounds=2,
    scope=None,
    graph_seeds=None,
    coverage_offset=0,
):
    """LLM plans queries; configured MixSearch yields candidate hints, not facts."""
    graph_seeds = list(graph_seeds or [])
    hits = []

    def run_search(query, initiator):
        if query in research.queries or len(research.queries) >= 4:
            return
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
            research.event('search',initiator=initiator,tool='MixSearch',query=query,
                           seconds=round(time.monotonic()-started,3),results=result,reused=reused)
        except Exception as exc:
            research.counts['search'] += 1
            research.errors.append({'stage':'discovery-search','kind':type(exc).__name__})
            research.event('search_error',query=query,error=type(exc).__name__,seconds=round(time.monotonic()-started,3))

    existing_institutions={
        str(item.get('institution_name') or '').strip().casefold()
        for item in existing
        if item.get('institution_name')
    }
    uncovered_seeds=[
        seed for seed in graph_seeds
        if seed.strip().casefold() not in existing_institutions
    ]
    subjects=[
        str(subject).strip()
        for subject in (scope or {}).get('included_subjects',[])
        if str(subject).strip()
    ]
    for index in range(rounds):
        plan = research.call('domain-plan', tr.Plan, {'domain':domain_name,'existing_teams':existing,
            'domain_scope':scope,
            'graph_seed_institutions':graph_seeds,
            'results':hits,'already_queried':sorted(research.queries),
            'instruction_detail':'发现中国境内本领域全部可检索到的优势具体科研团队，不设置8个或其它固定数量目标。graph_seed_institutions来自Hyper图谱，只能作为优先检索机构线索，不能直接证明其拥有具体团队或优势。先根据domain_scope把用户领域拆成不同技术任务和常见学术表达，每轮查询覆盖不同included_subjects；输入词、上位学科和相邻方向不是自动同义词，候选仍须用具体任务原文核实。针对现有缺口找不同具体实验室/研究部/PI团队；不要列整所整校或泛新能源机构。只规划最多2条简短查询，避免重复；不提供预置团队答案。'})
        for query in plan.queries:
            run_search(query,'model')
        coverage_index=coverage_offset+index
        if coverage_index<len(uncovered_seeds):
            seed=uncovered_seeds[coverage_index]
            run_search(f'"{seed}" "{domain_name}" 实验室 研究组 团队','graph-seed')
        if subjects:
            subject=subjects[coverage_index%len(subjects)]
            run_search(f'"{subject}" 中国 实验室 研究组 团队','scope-coverage')
    value=research.call('domain-candidates',Candidates,{'domain':domain_name,'existing_teams':existing,'search_results':hits,
        'domain_scope':scope,
        'graph_seed_institutions':graph_seeds,
        'instruction_detail':'仅从检索结果提出有明确名称的具体团队及机构，返回本轮命中且满足命名证据要求的全部候选，不设置固定数量上限；合并同一实体的别名/重复。不要重复existing_teams，不把机构作为团队，不用记忆补名单。concrete_team_named只在标题或摘要明确提到具体实验室/科研组/研究部/PI团队时为true，并在name_evidence逐字引用该标题/摘要；没有具体名称不能编造“研究团队”占位。source_urls只能取实际检索URL。来源不足就少返回，绝不填充模糊机构。后续仍需正文独立核验，这里仅为命名线索。'})
    allowed={h['url'] for h in hits}
    candidates=[]
    for c in value.candidates:
        item=c.model_dump(); item['source_urls']=[u for u in item['source_urls'] if u in allowed]
        cited=bool(item['name_evidence']) and all(any(h['url']==cite['url'] and
            ''.join(cite['quote'].split()) in ''.join((h.get('label','')+' '+h.get('snippet','')).split())
            and len(''.join(cite['quote'].split()))>=2 for h in hits) for cite in item['name_evidence'])
        normalized_name=''.join(item['team_name'].split()).casefold()
        name_in_result=bool(normalized_name) and any(
            hit['url'] in item['source_urls']
            and normalized_name in ''.join((hit.get('label','')+' '+hit.get('snippet','')).split()).casefold()
            for hit in hits
        )
        if item['source_urls'] and item['concrete_team_named'] and (cited or name_in_result):
            candidates.append(item)
        else:
            research.event('candidate_deferred',candidate=item,reason='缺少可核对的具体团队命名线索；未消耗逐队调查预算')
    return candidates


def sync_domain(session, domain, *, subdomain=None, seconds=2400, team_seconds=210, workers=2):
    from . import strategic_map as sm
    from .team_research_store import history, persist, append_history, apply_reviewed_run
    started=time.monotonic(); deadline=started+seconds
    domain_id,root_domain_name=domain.id,domain.name
    subdomain_id=subdomain.id if subdomain is not None else None
    domain_name=subdomain.name if subdomain is not None else root_domain_name
    scope_key='domain:'+domain_id+(f':{subdomain_id}' if subdomain_id else '')
    scope_job_id='scope:'+domain_id+(f':{subdomain_id}' if subdomain_id else '')
    engine=session.get_bind(); factory=sessionmaker(bind=engine,expire_on_commit=False)
    session.commit()
    # Serialize first-use DDL too: two domains may start in different workers.
    from .team_research_store import HISTORY
    with engine.connect() as connection:
        connection.exec_driver_sql('BEGIN IMMEDIATE')
        META.create_all(connection); HISTORY.create(connection,checkfirst=True)
        connection.commit()
    owner=uuid.uuid4().hex; key='domain:'+domain_id
    if not acquire(factory,key,owner,seconds+300):
        raise sm._SyncQualityError('该领域正在处理，未重复执行；请稍后读取已有结果')
    cache=BatchCache(); results=[]; discovered=[]; discovery_run=None; scope_run=None
    try:
        from .strategic_graph import graph_institution_seeds
        graph_seeds = graph_institution_seeds(root_domain_name, domain_name if subdomain_id else "", limit=None)
    except Exception:
        graph_seeds = []
    from .triage_discovery import institution_seeds
    graph_seeds = list(dict.fromkeys([
        *institution_seeds(domain_id, domain_name if subdomain_id else ""), *graph_seeds,
    ]))
    cache.on_failure=lambda failure:save_job(factory,'provider:shared-agent',None,None,'blocked_provider',failure)
    official_leader_count = 0
    try:
        from . import official_team_directory
        from . import team_enrichment
        directory_summary = official_team_directory.sync(session, domain, subdomain)
        official_leader_count = enrich_official_team_leaders(
            factory, domain_id, subdomain_id,
        )
        # Generic per-team enrichment: covers teams that no directory adapter
        # matches yet (e.g. a brand-new sub-domain), by fetching each team's
        # official URL and running the shared roster extractors.
        enrichment_summary = team_enrichment.enrich_teams(
            factory, only_missing=True, domain_id=domain_id, subdomain_id=subdomain_id,
            deadline=min(deadline - team_seconds - 180, time.monotonic() + min(300, seconds * 0.2)),
        )
        session.expire_all()
        if not sm._llm_config():
            query = session.query(sm.StrategicTeamRow).filter_by(domain_id=domain_id, deleted=False)
            if subdomain_id:
                query = query.filter_by(subdomain_id=subdomain_id)
            current = query.all()
            people = [sm._team_people(session, row.id) for row in current]
            summary = {
                "provider": "官方课题组目录", "pipeline": "official-directory→incremental",
                **directory_summary, "teamCount": len(current), "namedTeamCount": len(current),
                "leaderCount": sum(bool(heads) for heads, _ in people),
                "officialLeaderCount": official_leader_count,
                "memberTeamCount": sum(bool(members) for _, members in people),
                "memberCount": sum(len(members) for _, members in people),
                "genericLeadersAdded": enrichment_summary["leaders_added"],
                "genericMembersAdded": enrichment_summary["members_added"],
                "seconds": round(time.monotonic()-started, 3),
                "updatedAt": sm._now().isoformat(), "incompleteReason": "model_not_configured",
            }
            session.commit()
            save_job(factory, scope_key, domain_id, None, "incomplete", summary)
            raise sm._SyncQualityError(
                f'{domain_name} 已保存 {len(current)} 个团队；官方目录本轮新增 '
                f'{directory_summary["directoryCreated"]} 个、补充 '
                f'{directory_summary["directoryMembers"]} 条成员资料。'
                '模型服务未配置，深度联网调查尚未完成；已保存资料可立即查看'
            )
        with factory() as check:
            circuit=check.execute(select(JOBS).where(JOBS.c.id=='provider:shared-agent')).mappings().first()
            if circuit and circuit['state']=='blocked_provider' and time.time()-circuit['updated']<300:
                raise sm._SyncQualityError(
                    '官方团队页已完成增量核验；原模型服务不可用（'
                    +str(circuit['payload'].get('provider_code','authorization'))
                    +'），5分钟内不重复调用'
                )
        with factory() as s:
            query=s.query(sm.StrategicTeamRow).filter_by(domain_id=domain_id,deleted=False)
            if subdomain_id:
                query=query.filter(sm.StrategicTeamRow.subdomain_id==subdomain_id)
            rows=query.all()
            rows.sort(key=lambda r:(r.verification_status!='conflict',bool(r.leader_confidence),bool(r.description),bool(r.research_directions)))
            previous={r['team_id']:dict(r) for r in s.execute(select(JOBS).where(JOBS.c.domain_id==domain_id)).mappings() if r['team_id']}
            if subdomain_id:
                scoped_ids={row.id for row in rows}
                previous={team_id:job for team_id,job in previous.items() if team_id in scoped_ids}
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
            # Order the full queue after discovery below: fresh candidates and
            # never-attempted backlog must not trail stale completed rechecks.

        with factory() as s:
            scope_job=s.execute(select(JOBS).where(JOBS.c.id==scope_job_id)).mappings().first()
        scope=None
        if scope_job and scope_job['payload'].get('domain_name')==domain_name and time.time()-scope_job['updated']<86400:
            scope=scope_job['payload']['scope']
        elif deadline-time.monotonic()>60:
            planner=tr.Research(sm._shared_agent_llm_text,seconds=min(60,int(deadline-time.monotonic())),cached_pages=cache)
            try:
                scope=planner.call('domain-scope',DomainScope,{'input_domain':domain_name,
                    'instruction_detail':'在看到任何候选之前，解释用户输入学科的准确范围。给出核心研究对象、应纳入的研究内容、仅相邻但不足以纳入的方向及必须提供的联系证据。不能把具体下位领域泛化为整个上位大类；不要推荐团队/机构/URL，不编造团队事实。这是检索定义，不是团队优势评价。'}).model_dump()
                save_job(factory,scope_job_id,domain_id,None,'complete',{'domain_name':domain_name,'scope':scope})
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
                old = previous.get(team_id)
                # A deferred recheck is not a failed investigation. Preserve
                # an earlier reviewed/complete job, its evidence and timestamp.
                # New or explicitly queued candidates still get a durable
                # budget marker for the next run.
                if not old or old['state'] == 'queued':
                    prior = (old or {}).get('payload') or {}
                    save_job(factory,job_id,domain_id,team_id,'budget_exhausted',{
                        **prior,'context':context,'reason':'领域预算不足以启动单团队',
                    })
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
                if run.get('status') == 'collected' and run.get('extracted') and deadline-time.monotonic()>30:
                    run=tr.review_cached_result(
                        run,
                        context,
                        domain_name,
                        sm._shared_agent_llm_text,
                    )
                if run.get('status') == 'reviewed' and deadline-time.monotonic()>15:
                    run=review_qualification(
                        run,
                        domain_name,
                        sm._shared_agent_llm_text,
                        seconds=min(90, max(15, int(deadline-time.monotonic()))),
                        cache=cache,
                        existing=context,
                        scope=scope,
                    )
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
                            row=sm.StrategicTeamRow(id=team_id,domain_id=domain_id,subdomain_id=subdomain_id,
                                name=value['institution_name']['value'],institution_name=value['institution_name']['value'],
                                team_name=value['team_name']['value'])
                            s.add(row);s.flush()
                            published=persist(s,row,run)
                        elif row is None and qualified(run):
                            published=apply_reviewed_run(s,team_id,run,allowed_domains={root_domain_name},domain_id=domain_id)
                            if published and subdomain_id:
                                s.get(sm.StrategicTeamRow,team_id).subdomain_id=subdomain_id
                        else:
                            published=bool(row is not None and persist(s,row,run))
                            if row is None: append_history(s,team_id,'pending',{'run':run,'published':False})
                        s.commit()
                        state='blocked_provider' if cache.unavailable else (
                            'complete' if published and qualified(run) else
                            'needs_review' if published else 'no_new_data')
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

        discovery_runs=[]
        attempted=[context for _,context in work]
        discovered_work=[]
        max_discovery_rounds=max(1,min(12,int(os.getenv('STRATEGIC_MAP_DISCOVERY_ROUNDS','6'))))
        max_stagnant_rounds=max(1,min(4,int(os.getenv('STRATEGIC_MAP_DISCOVERY_STAGNANT_ROUNDS','2'))))
        stagnant_rounds=0
        for discovery_round in range(max_discovery_rounds):
            if cache.unavailable or deadline-time.monotonic()<=max(180,team_seconds+60):
                break
            research=tr.Research(sm._shared_agent_llm_text,seconds=min(180,int(deadline-time.monotonic()-120)),cached_pages=cache)
            existing=[{'institution_name':r.institution_name,'team_name':r.team_name} for r in rows]+[
                {'institution_name':c['institution_name'],'team_name':c['team_name']} for c in attempted]
            more=[]
            try:
                candidates=discovery(
                    domain_name,
                    existing,
                    research,
                    scope=scope,
                    graph_seeds=graph_seeds,
                    coverage_offset=discovery_round*2,
                )
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
                if more:
                    with factory() as s:
                        s.execute(text('BEGIN IMMEDIATE'))
                        for team_id,context in more:
                            row=s.get(sm.StrategicTeamRow,team_id)
                            if row is None:
                                row=sm.StrategicTeamRow(
                                    id=team_id,
                                    domain_id=domain_id,
                                    subdomain_id=subdomain_id,
                                    name=context['institution_name'],
                                    institution_name=context['institution_name'],
                                    team_name=context['team_name'],
                                )
                                s.add(row)
                            if row.verification_status not in ('verified','conflict','duplicate','out_of_scope'):
                                urls=list(dict.fromkeys([*(row.source_urls or []),*context.get('source_urls',[])]))
                                row.deleted=False
                                row.subdomain_id=subdomain_id or row.subdomain_id
                                row.source='公开检索命名线索'
                                row.source_urls=urls
                                row.evidence_urls=list(dict.fromkeys([*(row.evidence_urls or []),*urls]))
                                row.evidence_summary='检索结果已明确命名该具体团队；负责人、领域优势与正文证据待逐队核验。'
                                row.verification_status='collected'
                                row.team_confidence=max(float(row.team_confidence or 0),0.3)
                                row.updated_at=sm._now()
                                sm._update_team_score(s,row)
                        s.commit()
                    discovered_work.extend(more)
            except Exception as exc:
                research.errors.append({'stage':'discovery','kind':type(exc).__name__})
            discovery_runs.append({'round':discovery_round+1,'trace':research.trace,'counts':research.counts,'errors':research.errors})
            stagnant_rounds=0 if more else stagnant_rounds+1
            if stagnant_rounds>=max_stagnant_rounds:
                break
        batch(_prioritize_team_work([*work,*discovered_work], previous))
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
        qualified_ids=[r['team_id'] for r in results if r['state'] in ('complete','reused_complete') and qualified(r.get('run',{}))]
        with factory() as s:
            published_query=s.query(sm.StrategicTeamRow).filter_by(domain_id=domain_id,deleted=False)
            if subdomain_id:
                published_query=published_query.filter(sm.StrategicTeamRow.subdomain_id==subdomain_id)
            published_ids=[row.id for row in published_query.all()]
            published_people=[sm._team_people(s,i) for i in published_ids]
        count=len(set(published_ids))
        summary={'provider':'公开来源','pipeline':'resumable-domain→team-evidence→incremental',
            **directory_summary,
            'teamCount':count,'namedTeamCount':count,'candidateCount':len(results),'reportCount':0,
            'leaderCount':sum(bool(leaders) for leaders,members in published_people),
            'officialLeaderCount':official_leader_count,
            'memberTeamCount':sum(bool(members) for leader,members in published_people),
            'memberCount':sum(len(members) for leader,members in published_people),
            'pendingCount':sum(r['state'] not in ('complete','reused_complete','duplicate') for r in results),
            'deferredCount':sum(r['state']=='budget_exhausted' for r in results),
            'duplicateCount':sum(r['state']=='duplicate' for r in results),
            'seconds':round(time.monotonic()-started,3),'results':results,'discovery':discovery_run,'deduplication':dedupe_run,
            'scope':scope,'scope_run':scope_run,'graphSeeds':graph_seeds,
            'updatedAt':sm._now().isoformat()}
        dedupe_complete = len(valid)<2 or bool(dedupe_run and 'decision' in dedupe_run)
        summary['deduplicationComplete']=dedupe_complete
        discovery_failed = any(
            round_run.get('errors') for round_run in discovery_runs
        ) or bool((scope_run or {}).get('errors'))
        incomplete = (
            not dedupe_complete
            or discovery_failed
            or (not results and not discovered)
            or any(r['state'] not in ('complete','reused_complete','duplicate') for r in results)
        )
        save_job(factory,scope_key,domain_id,None,'incomplete' if incomplete else 'complete',summary)
        session.expire_all()
        if incomplete:
            provider_reason=('；原模型服务不可用：'+str(cache.unavailable.get('provider_code','authorization'))) if cache.unavailable else ''
            deferred_reason=(f'；{summary["deferredCount"]} 个团队因本轮时间预算待下轮继续'
                if summary['deferredCount'] else '')
            raise sm._SyncQualityError(f'{domain_name} 已保存本轮 {count} 个团队的资料，部分资料获取尚未完成{deferred_reason}{provider_reason}；已有数据保持展示')
        return {k:v for k,v in summary.items() if k not in ('results','discovery','deduplication','scope','scope_run')}
    finally:
        release(factory,key,owner)
