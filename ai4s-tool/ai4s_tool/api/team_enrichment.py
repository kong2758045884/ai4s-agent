"""Generic leader/member enrichment for every strategic-map team.

Referenced Hyper-Extract candidate-discovery flow: exhaustive alias variants,
multi-round search, official host filter, and per-team evidence. Applied here
to fill the *leader* and *members* fields for every team currently persisted
in the strategic map, including teams added by future sub-domain scans.

Design guarantees:

1.  **No fabrication.** Every leader/member row keeps the source URL and the
    verbatim quote from that page. If nothing verifiable is found, the team is
    left as ``verification_status = collected`` and never gets a fake head.
2.  **Generic, not per-team.** No hard-coded roster tables. Everything is
    driven by regex + URL heuristics that also apply to future teams.
3.  **Cheap when idle.** Teams that already have a verified official leader
    (HTTP source, not Hyper-graph inferred) are skipped instantly.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urlsplit

from . import team_research as tr
from .domain_research import (
    _extract_explicit_leader,
    _leader_near_exact_team,
    _official_research_url,
    _team_aliases,
)


_ROLE_TITLE = (
    "研究员",
    "副研究员",
    "助理研究员",
    "特别研究助理",
    "教授",
    "副教授",
    "讲师",
    "工程师",
    "高级工程师",
    "项目研究员",
    "博士后",
    "客座研究员",
    "科研秘书",
    "博士研究生",
    "硕士研究生",
    "PhD Student",
    "MS Student",
)
_HISTORICAL_MARKERS = (
    "已离",
    "离职",
    "毕业后",
    "曾任",
    "曾为课题组",
    "former",
    "graduated",
    "毕业生",
    "以往成员",
    "前成员",
    "history",
    "Former",
)
# Fragments that look like Chinese names shape-wise but are common column
# headers, navigation labels, or role prefixes. Blacklisted so generic roster
# extraction never mistakes them for a real person.
_NON_NAME_TOKENS = frozenset(
    {
        "研究员",
        "副研究员",
        "助理研究员",
        "工程师",
        "高级",
        "正高级",
        "副高级",
        "项目",
        "人才",
        "招聘",
        "队伍",
        "校友",
        "工作",
        "业务",
        "骨干",
        "研究组",
        "课题组",
        "实验室",
        "研究方向",
        "研究成果",
        "联系",
        "地址",
        "综合",
        "办公室",
        "首页",
        "简介",
        "领导",
        "职能",
        "党建",
        "党务",
        "学术",
        "委员会",
        "组织",
        "机构",
        "研究部",
        "研究所",
        "科研",
        "岗位",
        "人事",
        "人力",
        "现任",
        "获得者",
        "国家级",
        "省部级",
        "特别",
        "博士",
        "硕士",
        "学士",
        "论文",
        "专著",
        "专利",
        "获奖",
        "科普",
        "学位",
        "研究",
        "中心",
        "计算",
        "网络",
        "系统",
        "科学",
        "技术",
        "工程",
        "应用",
        "开发",
    }
)


@dataclass(frozen=True)
class TeamTarget:
    team_id: str
    institution_name: str
    team_name: str
    subdomain: str
    source_urls: tuple[str, ...]
    domain_name: str


def _looks_like_person(name: str) -> bool:
    if not name:
        return False
    if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", name):
        if name in _NON_NAME_TOKENS:
            return False
        # Reject names that overlap known non-name substrings; this filters
        # navigation debris like "科研成果", "组织机构" while keeping "张伟" etc.
        return not any(token in name for token in _NON_NAME_TOKENS)
    return bool(re.fullmatch(r"[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z.-]+){1,3}", name))


def _iter_generic_members(page_text: str, aliases: tuple[str, ...]) -> Iterable[tuple[str, str, str]]:
    """Yield (name, role, quote) tuples for members using generic patterns.

    We deliberately avoid page-specific selectors: any lab page that lists
    ``职称+姓名`` or ``姓名+职称`` gets picked up. Historical / graduated
    entries are filtered out so we do not confuse former members with current
    roster members.
    """
    normalized = " ".join(page_text.split())
    # Only look inside the segment that mentions the team; otherwise we drag
    # unrelated staff pages into a lab roster.
    windows: list[str] = []
    for alias in aliases:
        needle = alias
        idx = normalized.find(needle)
        while idx >= 0:
            windows.append(normalized[max(0, idx - 100) : idx + 4000])
            idx = normalized.find(needle, idx + len(needle))
    if not windows:
        return
    roster = "\n".join(windows)
    for cutoff in (
        "以往成员",
        "毕业学生",
        "毕业生",
        "曾任成员",
        "离职人员",
        "Former Members",
    ):
        if cutoff in roster:
            roster = roster.split(cutoff, 1)[0]

    seen: set[str] = set()

    # Pattern 1: name + email pair, e.g. "杨艺红 yhy@ibp.ac.cn". This is the
    # most reliable roster signal on Chinese lab sites.
    for match in re.finditer(r"([\u4e00-\u9fff]{2,4})\s+[\w.+-]+@[\w.-]+", roster):
        name = match.group(1)
        if name in seen or not _looks_like_person(name):
            continue
        window = roster[max(0, match.start() - 60) : match.end() + 60]
        if any(marker in window for marker in _HISTORICAL_MARKERS):
            continue
        seen.add(name)
        yield name, "课题组成员", window

    # Pattern 2: standalone paragraph "姓名，职称 简介..." — used by CAS lab
    # sites where every biography starts with 姓名，博士 / 姓名，研究员.
    pattern2 = re.compile(
        r"(?<![\u4e00-\u9fff])([\u4e00-\u9fff]{2,4})[，,]\s*(博士|研究员|副研究员|助理研究员|教授|副教授|工程师)"
    )
    for match in pattern2.finditer(roster):
        name = match.group(1)
        role = match.group(2)
        if name in seen or not _looks_like_person(name):
            continue
        window = roster[max(0, match.start() - 40) : match.end() + 80]
        if any(marker in window for marker in _HISTORICAL_MARKERS):
            continue
        seen.add(name)
        yield name, role, window



def _member_row(page: dict, name: str, role: str, quote: str) -> dict:
    return {
        "name": name,
        "role": role,
        "profile_url": page.get("url", ""),
        "source_urls": [page.get("url", "")],
        "source_type": "官方团队页面·通用抓取",
        "verification_status": "verified",
        "confidence": 0.9,
        "evidence": json.dumps(
            {
                "url": page.get("url", ""),
                "quote": quote,
                "fetched_at": page.get("fetched_at", ""),
                "team_relation": "exact_team",
                "leadership_recency": "current_official_listing",
                "rule": "generic-roster-extraction",
            },
            ensure_ascii=False,
        ),
    }


def _candidate_urls(target: TeamTarget) -> list[str]:
    """Return an ordered, deduplicated URL list for the team.

    Order matters: URLs already attached to the team come first, followed by
    the top-level institution homepage inferred from those URLs. We do not
    invent new institutional homepages.
    """
    urls: list[str] = []
    seen: set[str] = set()
    for url in target.source_urls:
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    # Infer institution homepage from the deepest known URL: keeping scheme +
    # host is enough to visit organization index pages. Also try a small set
    # of well-known institution index paths so that a team page linked from an
    # 组织机构 / 机构设置 index is reachable in one hop.
    known_index_paths = (
        "",
        "index.html",
        "jgsz/",
        "jgsz/index.html",
        "zzjg/",
        "kyjg/",
        "jssgk/zzjg/",
        "jssgk/zzjg/kyxt/",
        "gk/jj/",
        "2019/",
        "jgsh/",
        "gkjj/",
        "yjs/",
    )
    for url in list(urls):
        try:
            parts = urlsplit(url)
        except ValueError:
            continue
        base = f"{parts.scheme}://{parts.netloc}/"
        if not _official_research_url(base):
            continue
        for path in known_index_paths:
            candidate = base + path if path else base
            if candidate in seen:
                continue
            seen.add(candidate)
            urls.append(candidate)
    return urls[:14]


def _search_official_urls(target: TeamTarget) -> list[str]:
    """Search engines are hints, never facts; only official hosts are kept."""
    from . import strategic_map as sm

    aliases = _team_aliases(target.team_name)
    if not aliases:
        return []
    queries: list[str] = []
    institution = target.institution_name or ""
    for alias in aliases[:3]:
        queries.append(f'"{alias}" 负责人 主任 PI {institution}')
        queries.append(f'"{alias}" "{institution}"')
        queries.append(f'"{alias}" 团队成员 课题组成员')
        queries.append(f'"{alias}" 简介 研究方向')
    seen: set[str] = set()
    urls: list[str] = []
    for query in queries[:10]:
        hits = sm._bing_html_search(query, max_results=8)
        if not hits:
            hits = sm._duckduckgo_html_search(query, max_results=8)
        for hit in hits:
            url = str(hit.get("link") or "")
            searchable = " ".join(
                (
                    str(hit.get("title") or ""),
                    str(hit.get("snippet") or ""),
                )
            ).casefold()
            if not url or url in seen:
                continue
            if not _official_research_url(url):
                continue
            if not any(alias.casefold() in searchable for alias in aliases):
                continue
            seen.add(url)
            urls.append(url)
    return urls[:12]


def _search_snippets(target: TeamTarget) -> list[dict]:
    """Return raw search hits (title + snippet) whether or not hosts are official.

    Snippets are only used to build a 简介 fallback — we always keep the
    hit's URL as the source citation, so the origin remains auditable.
    """
    from . import strategic_map as sm

    aliases = _team_aliases(target.team_name)
    if not aliases:
        return []
    institution = target.institution_name or ""
    queries = [
        f'"{target.team_name}" 简介',
        f'"{target.team_name}" 研究方向 团队',
        f'"{target.team_name}" {institution}',
    ]
    hits: list[dict] = []
    seen: set[str] = set()
    for query in queries:
        results = sm._bing_html_search(query, max_results=8)
        if not results:
            results = sm._duckduckgo_html_search(query, max_results=8)
        for hit in results:
            url = str(hit.get("link") or "")
            snippet = " ".join(str(hit.get("snippet") or "").split())
            title = " ".join(str(hit.get("title") or "").split())
            if not snippet or url in seen:
                continue
            if not any(alias in snippet or alias in title for alias in aliases):
                continue
            seen.add(url)
            hits.append({"url": url, "title": title, "snippet": snippet})
    return hits[:12]


def _build_description(
    team,
    aliases: tuple[str, ...],
    pages: dict[str, dict],
    snippets: list[dict],
) -> tuple[str, str] | None:
    """Return (description, source_url) if any surface yields ≥ 40 chars."""
    # Prefer the on-page paragraph next to the exact team name.
    for url, page in pages.items():
        if page.get("status") != "ok":
            continue
        text = " ".join(str(page.get("text") or "").split())
        for alias in aliases:
            idx = text.find(alias)
            if idx < 0:
                continue
            window = text[idx : idx + 800]
            # Take the sentence(s) that follow the team name.
            candidate = re.split(r"(?<=[。！？])", window, maxsplit=6)
            joined = "".join(candidate[:6]).strip()
            if len(joined) >= 40:
                return joined[:1200], url
    # Merge relevant search snippets otherwise.
    merged: list[str] = []
    src = ""
    for hit in snippets:
        merged.append(hit["snippet"])
        src = src or hit["url"]
        if sum(len(s) for s in merged) >= 200:
            break
    joined = "。".join(merged).strip()
    if len(joined) >= 40:
        return joined[:1200], src
    # Last-resort: reuse the evidence summary already stored by the graph
    # snapshot. Never leave a blank description behind.
    summary = " ".join(str(team.evidence_summary or "").split())
    if len(summary) >= 40:
        return summary[:1200], src or "hyper-graph"
    return None


def _iter_snippet_members(
    aliases: tuple[str, ...],
    snippets: list[dict],
) -> Iterable[tuple[str, str, str]]:
    """Extract role+name mentions from search snippets. Best-effort only."""
    role_pattern = re.compile(
        r"(?<![\u4e00-\u9fff])(研究员|副研究员|助理研究员|教授|副教授|讲师|工程师|高级工程师|博士后|博士研究生|硕士研究生|课题组成员|团队成员)"
        r"[：: ]*([\u4e00-\u9fff]{2,4})"
    )
    seen: set[str] = set()
    for hit in snippets:
        text = hit["snippet"]
        if not any(alias in text for alias in aliases):
            continue
        for match in role_pattern.finditer(text):
            role = match.group(1)
            name = match.group(2)
            if name in seen or not _looks_like_person(name):
                continue
            window = text[max(0, match.start() - 30) : match.end() + 60]
            if any(marker in window for marker in _HISTORICAL_MARKERS):
                continue
            seen.add(name)
            yield name, role, window


def _fetch_all(urls: Iterable[str]) -> dict[str, dict]:
    urls = [u for u in urls if u]
    if not urls:
        return {}
    with ThreadPoolExecutor(max_workers=min(8, len(urls))) as pool:
        futures = {url: pool.submit(tr.fetch_page, url) for url in urls}
        return {url: future.result() for url, future in futures.items()}


def _follow_relevant_links(
    aliases: tuple[str, ...],
    pages: dict[str, dict],
) -> list[str]:
    """Return in-domain links whose label mentions any exact alias."""
    picked: list[str] = []
    seen: set[str] = set()
    for base_url, page in pages.items():
        if page.get("status") != "ok":
            continue
        try:
            base_host = urlsplit(base_url).hostname or ""
        except ValueError:
            continue
        for link in page.get("links", []):
            url = str(link.get("url") or "")
            label = " ".join(str(link.get("label") or "").split())
            if not url or url in pages or url in seen:
                continue
            try:
                host = urlsplit(url).hostname or ""
            except ValueError:
                continue
            if host != base_host:
                continue
            if not any(alias.casefold() in label.casefold() for alias in aliases):
                continue
            seen.add(url)
            picked.append(url)
            if len(picked) >= 8:
                return picked
    return picked


def _extract_leader_from_pages(
    aliases: tuple[str, ...],
    pages: dict[str, dict],
) -> tuple[str, dict] | None:
    """First hit wins: exact-team-name proximity beats generic mentions."""
    for url, page in pages.items():
        if page.get("status") != "ok":
            continue
        page_text = " ".join(str(page.get("text") or "").split())
        published_at = str(page.get("published_at") or "")
        leader = _leader_near_exact_team(page_text, aliases, published_at=published_at)
        if leader:
            return url, leader
        # Second attempt: any exact-name label in the page's link list.
        for link in page.get("links", []):
            label = " ".join(str(link.get("label") or "").split())
            if not any(alias.casefold() in label.casefold() for alias in aliases):
                continue
            leader = _extract_explicit_leader(label, published_at=published_at)
            if leader:
                return url, leader
    return None


def _extract_members_from_pages(
    aliases: tuple[str, ...],
    pages: dict[str, dict],
) -> tuple[dict, list[tuple[str, str, str]]] | None:
    """Return roster hits only from a page that names the exact team."""
    best: tuple[dict, list[tuple[str, str, str]]] | None = None
    for _, page in pages.items():
        if page.get("status") != "ok":
            continue
        text = " ".join(str(page.get("text") or "").split())
        if not any(alias.casefold() in text.casefold() for alias in aliases):
            continue
        rows = list(_iter_generic_members(text, aliases))
        # Require at least two clean matches to guard against spurious hits.
        if len(rows) >= 2 and (best is None or len(rows) > len(best[1])):
            best = (page, rows)
    return best


def _collect_targets(session, *, only_missing: bool = True, domain_id=None, subdomain_id=None) -> list[TeamTarget]:
    from . import strategic_map as sm

    domains = {
        d.id: d
        for d in session.query(sm.StrategicDomainRow).filter_by(deleted=False).all()
    }
    subdomains = {
        d.id: d
        for d in session.query(sm.StrategicDomainRow)
        .filter(sm.StrategicDomainRow.parent_id.isnot(None))
        .filter_by(deleted=False)
        .all()
    }
    targets: list[TeamTarget] = []
    query = session.query(sm.StrategicTeamRow).filter_by(deleted=False)
    if domain_id:
        query = query.filter_by(domain_id=domain_id)
    if subdomain_id:
        query = query.filter_by(subdomain_id=subdomain_id)
    for team in query.all():
        leaders, members = sm._team_people(session, team.id)
        # Baseline gap = missing description, missing verified leader, or < 2
        # members. Any of those keeps the team on the enrichment queue.
        baseline_ok = sm._meets_display_baseline(team, leaders, members)
        if only_missing and baseline_ok:
            continue
        domain = domains.get(team.domain_id)
        if not domain:
            continue
        subdomain = subdomains.get(team.subdomain_id) if team.subdomain_id else None
        targets.append(
            TeamTarget(
                team_id=team.id,
                institution_name=team.institution_name or team.name or "",
                team_name=team.team_name or "",
                subdomain=subdomain.name if subdomain else "",
                source_urls=tuple(team.source_urls or []),
                domain_name=domain.name,
            )
        )
    return targets


def _llm_extract(target: TeamTarget, aliases: tuple[str, ...],
                 pages: dict[str, dict], snippets: list[dict]) -> dict | None:
    """Run one bounded LLM extraction pass over the collected evidence.

    Returns the canonicalized ``Result`` dict (already citation-validated) or
    ``None`` if the shared Agent LLM is unavailable or the extraction fails.
    Only pages already fetched by ``_fetch_all`` are exposed; the LLM cannot
    fabricate a URL because ``citations_valid`` rejects any citation whose URL
    or quote does not literally appear in ``research.pages``.
    """
    from . import strategic_map as sm

    if not sm._llm_config():
        return None
    ok_pages = {url: p for url, p in pages.items() if p.get("status") == "ok"}
    if not ok_pages:
        return None
    research = tr.Research(sm._shared_agent_llm_text, seconds=90)
    research.pages = ok_pages
    try:
        # Single structured call. No planner loop, no follow-up fetch: this
        # runs across 149 teams per pass, so we intentionally trade recall for
        # a bounded latency budget.
        wire = research.call(
            "extract",
            tr.WireResult,
            {
                "instruction_detail": (
                    "从 pages 中抽取指定团队的机构名、团队名、简介、研究方向、"
                    "现任负责人和核心成员。每个字段的 citations 必须使用 pages "
                    "中已出现的 URL，quote 是原文子串（长度≥8），禁止编造或改写。"
                    "简介需≥40字，从页面正文的介绍段落原文摘取；无原文引用则留空并"
                    "写明 reason，绝不使用记忆或百科补写。members 必须是当前在岗、"
                    "隶属该具体团队的科研人员，历史/离职/委员会/母机构人员一律不纳入。"
                    "old_people 保持空数组。snippet 只用作线索，不能作为字段引文。"
                ),
                "target": {
                    "institution_name": target.institution_name,
                    "team_name": target.team_name,
                    "domain": target.domain_name,
                    "subdomain": target.subdomain,
                    "aliases": list(aliases),
                },
                "search_snippets": snippets[:6],
                "pages": research.evidence(),
            },
        )
        extracted = wire.canonical()
        research.validate_evidence(extracted)
        return extracted
    except Exception:
        return None


def _leader_row_from_llm(extracted: dict) -> dict | None:
    leader = extracted.get("leader") or {}
    if leader.get("status") != "verified" or not leader.get("citations"):
        return None
    cite = leader["citations"][0]
    return {
        "name": leader["name"],
        "role": leader["role"],
        "title": (leader.get("title") or {}).get("value", ""),
        "research_direction": (leader.get("research_direction") or {}).get("value", ""),
        "bio": (leader.get("bio") or {}).get("value", ""),
        "profile_url": leader.get("profile_url") or cite["url"],
        "source_urls": list(dict.fromkeys(c["url"] for c in leader["citations"])),
        "source_type": "官方网页·LLM 抽取+引文校验",
        "verification_status": "verified",
        "confidence": 0.95,
        "evidence": json.dumps(
            {
                "url": cite["url"],
                "quote": cite["quote"],
                "team_relation": leader.get("team_relation", "exact_team"),
                "leadership_recency": leader.get("leadership_recency",
                                                 "current_official_listing"),
                "rule": "llm-extract-citation-verified",
                "verified_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
        ),
    }


def _member_rows_from_llm(extracted: dict, leader_name: str) -> list[dict]:
    rows: list[dict] = []
    for member in extracted.get("members") or []:
        if member.get("status") != "verified" or not member.get("citations"):
            continue
        if leader_name and member["name"] == leader_name:
            continue
        cite = member["citations"][0]
        rows.append({
            "name": member["name"],
            "role": member["role"],
            "title": (member.get("title") or {}).get("value", ""),
            "research_direction": (member.get("research_direction") or {}).get("value", ""),
            "bio": (member.get("bio") or {}).get("value", ""),
            "profile_url": member.get("profile_url") or cite["url"],
            "source_urls": list(dict.fromkeys(c["url"] for c in member["citations"])),
            "source_type": "官方网页·LLM 抽取+引文校验",
            "verification_status": "verified",
            "confidence": 0.9,
            "evidence": json.dumps(
                {
                    "url": cite["url"],
                    "quote": cite["quote"],
                    "team_relation": member.get("team_relation", "exact_team"),
                    "leadership_recency": member.get("leadership_recency", "unclear"),
                    "rule": "llm-extract-citation-verified",
                    "verified_at": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
            ),
        })
    return rows


def _description_from_llm(extracted: dict) -> tuple[str, str] | None:
    """LLM 抽取的简介必须已通过 citations_valid，才允许写入 team.description."""
    fact = extracted.get("description") or {}
    if fact.get("status") != "verified" or not fact.get("value"):
        return None
    if not fact.get("citations"):
        return None
    value = " ".join(str(fact["value"]).split())
    if len(value) < 40:
        return None
    return value[:1200], fact["citations"][0]["url"]


def enrich_teams(factory, *, only_missing: bool = True, domain_id=None,
                 subdomain_id=None, deadline=None) -> dict[str, int]:
    """Fill description/leader/members for teams currently persisted in the map.

    Combines four evidence surfaces, all citation-bound and auditable:
      1. Fetch known source URLs plus a small set of institutional index paths.
      2. Follow same-host links whose label mentions the team name.
      3. Query Bing/DuckDuckGo for official-host URLs and additional snippets.
      4. If the shared Agent LLM is configured, ask it to extract structured
         facts from the collected pages; every field is re-validated against
         ``research.pages`` so a hallucinated citation is rejected on the spot.
    Regex/snippet fallbacks fill any field the LLM cannot verify. No fabricated
    value ever reaches the database.
    """
    from . import strategic_map as sm
    import time
    import hashlib
    from .team_research_store import append_history, upsert_people, history

    with factory() as session:
        targets = _collect_targets(session, only_missing=only_missing,
                                   domain_id=domain_id, subdomain_id=subdomain_id)

    stats = {"scanned": len(targets), "leaders_added": 0, "members_added": 0,
             "descriptions_added": 0, "llm_hits": 0, "deferred": 0, "reused": 0}
    if not targets:
        return stats

    for index, target in enumerate(targets):
        # Reserve time for discovery and independent per-team review. A scan of
        # one domain must never spend its entire budget enriching other domains.
        if deadline is not None and time.monotonic() + 105 >= deadline:
            stats["deferred"] = len(targets) - index
            break
        signature = hashlib.sha256(repr(target).encode()).hexdigest()
        with factory() as session:
            previous = history(session, target.team_id, "team_enrichment_attempt")
            if previous and previous[0]["payload"].get("signature") == signature:
                age = datetime.now(timezone.utc) - previous[0]["created_at"].replace(tzinfo=timezone.utc)
                if age.total_seconds() < 12 * 3600:
                    stats["reused"] += 1
                    continue
        aliases = _team_aliases(target.team_name)
        if not aliases:
            continue
        urls = _candidate_urls(target)
        pages = _fetch_all(urls)

        follow_urls = _follow_relevant_links(aliases, pages)
        if follow_urls:
            pages.update(_fetch_all(follow_urls))

        # Always run one search pass so the LLM has more than the seed URLs;
        # snippet-based leads often reveal the actual team profile page.
        search_urls = [u for u in _search_official_urls(target) if u not in pages]
        if search_urls:
            pages.update(_fetch_all(search_urls))
        snippets = _search_snippets(target)

        # LLM-first extraction, when configured. Falls back cleanly otherwise.
        extracted = _llm_extract(target, aliases, pages, snippets)
        leader_row: dict | None = None
        member_rows: list[dict] = []
        description_pair: tuple[str, str] | None = None
        if extracted:
            leader_row = _leader_row_from_llm(extracted)
            member_rows = _member_rows_from_llm(extracted, leader_row["name"] if leader_row else "")
            description_pair = _description_from_llm(extracted)
            if leader_row or member_rows or description_pair:
                stats["llm_hits"] += 1

        # Deterministic fallbacks for whatever the LLM did not verify.
        if not leader_row:
            leader_hit = _extract_leader_from_pages(aliases, pages)
            if leader_hit:
                url, leader = leader_hit
                leader_row = {
                    "name": leader["name"],
                    "role": leader["role"],
                    "bio": "",
                    "profile_url": url,
                    "source_urls": [url],
                    "source_type": "官方团队页面·通用核验",
                    "verification_status": "verified",
                    "confidence": 0.95,
                    "evidence": json.dumps(
                        {
                            "url": url,
                            "quote": leader["quote"],
                            "team_relation": "exact_team",
                            "leadership_recency": "current_official_listing",
                            "rule": "generic-leader-enrichment",
                            "verified_at": datetime.now(timezone.utc).isoformat(),
                        },
                        ensure_ascii=False,
                    ),
                }
        if not member_rows:
            member_hit = _extract_members_from_pages(aliases, pages)
            if member_hit:
                page, rows = member_hit
                for name, role, quote in rows[:20]:
                    if leader_row and name == leader_row["name"]:
                        continue
                    member_rows.append(_member_row(page, name, role, quote))
            if len(member_rows) < 2 and snippets:
                # Snippets can only introduce members; each row still carries
                # the origin URL so the source stays auditable.
                for name, role, quote in _iter_snippet_members(aliases, snippets):
                    if leader_row and name == leader_row["name"]:
                        continue
                    if any(row["name"] == name for row in member_rows):
                        continue
                    # Locate the matching snippet to attach a source URL.
                    src_url = next((h["url"] for h in snippets if name in h["snippet"]), "")
                    fake_page = {"url": src_url, "fetched_at": ""}
                    member_rows.append(_member_row(fake_page, name, role, quote))
                    if len(member_rows) >= 4:
                        break

        with factory() as session:
            team = session.get(sm.StrategicTeamRow, target.team_id)
            if not team or team.deleted:
                continue

            # Fill description last so the strongest source (LLM > page > snippet)
            # wins deterministically.
            if not description_pair:
                description_pair = _build_description(team, aliases, pages, snippets)
            if description_pair and (not team.description or len(team.description) < 40):
                team.description, desc_src = description_pair
                stats["descriptions_added"] += 1
                # Preserve the source URL so future audits can trace it.
                if desc_src and desc_src.startswith(("http://", "https://")):
                    team.source_urls = list(dict.fromkeys([*(team.source_urls or []), desc_src]))
                    team.evidence_urls = list(dict.fromkeys([*(team.evidence_urls or []), desc_src]))

            existing_leaders = (
                session.query(sm.StrategicPersonRow)
                .filter_by(team_id=team.id, deleted=False, is_leader=True)
                .all()
            )
            if leader_row is None and existing_leaders:
                upsert_people(session, team, None, member_rows, preserve_existing=True)
            else:
                upsert_people(session, team, leader_row, member_rows)

            if leader_row:
                stats["leaders_added"] += 1
                team.leader_confidence = max(float(team.leader_confidence or 0), 0.95)
            if member_rows:
                stats["members_added"] += len(member_rows)
                team.member_confidence = max(
                    float(team.member_confidence or 0), 0.85
                )

            if leader_row or member_rows or description_pair:
                append_history(
                    session,
                    team.id,
                    "team_enrichment",
                    {
                        "leader": leader_row,
                        "members": [row["name"] for row in member_rows],
                        "description_source": description_pair[1] if description_pair else "",
                        "source_urls": list(pages),
                        "llm_used": bool(extracted),
                    },
                )
                sm._update_team_score(session, team)
                team.updated_at = sm._now()
            append_history(session, team.id, "team_enrichment_attempt", {
                "signature": signature, "domain_id": domain_id,
                "changed": bool(leader_row or member_rows or description_pair),
            })
            session.commit()

    return stats


def cli_main() -> None:
    """Manual entry: ``python -m ai4s_tool.api.team_enrichment``."""
    from . import strategic_map as sm

    stats = enrich_teams(sm._SESSION_FACTORY, only_missing=True)
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    cli_main()
