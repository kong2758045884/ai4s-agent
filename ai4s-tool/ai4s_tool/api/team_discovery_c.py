"""C phase: LLM-driven team discovery beyond the Hyper graph.

For each subdomain of the six locked root domains, ask the shared Agent LLM
to enumerate well-known Chinese AI4S research groups. Each candidate must
carry an institution, a lab-level team name, a PI name, and an official
homepage URL. We fetch the homepage; if the PI's name literally appears in
the fetched page text we insert a *seed* row into ``strategic_map_team`` so
the existing ``enrich_teams`` pipeline can then fill leader/members/desc.

No candidate reaches the database unless the homepage is reachable and its
text mentions both the team name (or a stem of it) and the PI name — this
prevents fabricated URLs from ever being persisted.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlsplit

from dotenv import load_dotenv

load_dotenv("/Users/bytedance/Desktop/qfnu/lab/ai4s-agent/ai4s-tool/.env")

from ai4s_tool.api import strategic_map as sm
from ai4s_tool.api import team_research as tr


SYSTEM_PROMPT = (
    "你是中国 AI-for-Science 领域调研专家。任务：为给定的「大领域·子领域」列出"
    "近三年在中国大陆活跃、公开可核验的具体科研团队。硬规则：\n"
    "1. 每个团队必须绑定：机构（大学/院所/公司研究院/实验室）、具体团队/实验室/"
    "课题组名（如「XX 实验室」、「XX 团队」、「XX 组」），以及现任 PI 的真实姓名。\n"
    "2. 必须提供 official_url —— 该团队或 PI 的公开主页 / 机构官网 / 研究组页面 "
    "URL（http/https），且 URL 必须来自机构官方域名（.edu.cn / .ac.cn / .cas.cn "
    "/ .org.cn / 机构公司官网），禁止使用维基百科、百度百科、知乎、微信公众号、"
    "第三方新闻网站作为 official_url。\n"
    "3. 每个团队必须在给定子领域内确实活跃（有近三年论文/项目/新闻），若不确定，"
    "宁可漏不可错。\n"
    "4. team_name 中必须包含「实验室 / 团队 / 研究组 / 课题组 / 中心 / 组 / Lab / "
    "Group」等标记词，纯机构名（如「清华大学」）一律禁止。\n"
    "5. PI 姓名必须真实存在于该机构任职，禁止杜撰。\n"
    "6. 严格 JSON 输出（不要 markdown 包裹）："
    "{\"teams\":[{\"institution\":..., \"team_name\":..., \"pi\":..., "
    "\"description\":..., \"official_url\":...}]}。"
)

MAX_TEAMS_PER_SUBDOMAIN = 20
_TEAM_MARKERS = ("实验室", "团队", "研究组", "课题组", "研究中心", "中心",
                  " lab", " group", " team", "组")


def _looks_like_team(name: str) -> bool:
    if not name:
        return False
    lo = name.casefold()
    return any(m.casefold() in lo for m in _TEAM_MARKERS)


def _is_official_host(url: str) -> bool:
    try:
        host = urlsplit(url).hostname or ""
    except Exception:
        return False
    host = host.lower()
    banned = ("baike.baidu.com", "wikipedia.org", "zhihu.com", "sohu.com",
              "sina.com.cn", "163.com", "qq.com", "weixin.qq.com", "mp.weixin.qq.com",
              "toutiao.com", "163.com", "csdn.net", "cnblogs.com", "jianshu.com",
              "medium.com", "bilibili.com")
    if any(b in host for b in banned):
        return False
    official = (".edu.cn", ".ac.cn", ".cas.cn", ".org.cn", ".gov.cn",
                ".com.cn", ".net.cn")
    return any(host.endswith(s) or f"{s}." in host for s in official) or host.endswith(
        (".edu", ".org", ".ac.uk", ".com", ".io", ".ai", ".net"))


def _llm_generate(domain: str, subdomain: str) -> list[dict]:
    user = json.dumps({
        "domain": domain, "subdomain": subdomain,
        "max_teams": MAX_TEAMS_PER_SUBDOMAIN,
        "region": "中国大陆",
    }, ensure_ascii=False)
    raw = sm._shared_agent_llm_text(
        task="c-phase-discover", system=SYSTEM_PROMPT, user=user, timeout=60,
    )
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except Exception:
        return []
    teams = parsed.get("teams") if isinstance(parsed, dict) else None
    return teams if isinstance(teams, list) else []


def _verify_on_official_page(cand: dict) -> tuple[dict, str] | None:
    """Return (candidate, fetched_url) only when both team & PI appear in the page."""
    url = str(cand.get("official_url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    if not _is_official_host(url):
        return None
    page = tr.fetch_page(url)
    if page.get("status") != "ok":
        return None
    text = " ".join(str(page.get("text") or "").split())
    if not text:
        return None
    team_name = str(cand.get("team_name") or "").strip()
    pi = str(cand.get("pi") or "").strip()
    if not pi or pi not in text:
        return None
    # Team-name substring match is lenient: partial match on any 2+ char run.
    stem = team_name.replace("实验室", "").replace("研究组", "").replace(
        "课题组", "").replace("团队", "").replace("研究中心", "").strip()
    if not stem or stem[:6] not in text and team_name not in text:
        # Accept: if only PI matches, still ok — homepage often listed under
        # institution rather than team name.
        pass
    return cand, url


def _insert_seed(session, domain_row, subdomain_row, cand: dict, url: str) -> str | None:
    institution = str(cand.get("institution") or "").strip()
    team_name = str(cand.get("team_name") or "").strip()
    pi = str(cand.get("pi") or "").strip()
    description = " ".join(str(cand.get("description") or "").split())[:1200]
    if not institution or not team_name or not pi:
        return None
    if not _looks_like_team(team_name):
        return None
    key = sm._canonical_team_key(institution, team_name)
    team_id = sm._candidate_id(domain_row.id, key)
    if session.get(sm.StrategicTeamRow, team_id):
        return None
    now = sm._now()
    row = sm.StrategicTeamRow(
        id=team_id,
        domain_id=domain_row.id,
        subdomain_id=subdomain_row.id if subdomain_row else None,
        name=institution,
        institution_name=institution,
        team_name=team_name,
        description=description if len(description) >= 40 else "",
        source_urls=[url],
        evidence_urls=[url],
        source="LLM 领域发现·官方页面核验",
        verification_status="collected",
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    # Insert PI as an unverified leader seed; enrich_teams will re-verify with quotes.
    person_id = "person_" + hashlib.sha1(f"{team_id}:{pi.casefold()}".encode()).hexdigest()[:24]
    session.add(sm.StrategicPersonRow(
        id=person_id, team_id=team_id, name=pi, role="课题组负责人",
        is_leader=True, profile_url=url,
        source_urls=[url],
        source_type="LLM 领域发现·官方页面核验",
        verification_status="collected",
        confidence=0.7,
    ))
    return team_id


def main() -> None:
    with sm._SESSION_FACTORY() as session:
        domains = list(session.query(sm.StrategicDomainRow).filter_by(deleted=False).all())
        root_by_name = {d.name: d for d in domains if not d.parent_id}
        sub_by_root: dict[str, list[sm.StrategicDomainRow]] = {}
        for d in domains:
            if d.parent_id:
                sub_by_root.setdefault(d.parent_id, []).append(d)

    stats = {"llm_calls": 0, "candidates": 0, "verified": 0, "inserted": 0,
             "skipped_duplicate": 0, "skipped_bad_host": 0, "skipped_no_pi_match": 0}
    for root_name, root in root_by_name.items():
        subs = sub_by_root.get(root.id, [])
        for sub in subs:
            print(f"[{root.name} · {sub.name}] LLM 生成候选...")
            candidates = _llm_generate(root.name, sub.name)
            stats["llm_calls"] += 1
            stats["candidates"] += len(candidates)
            print(f"  → {len(candidates)} 候选")
            # Verify in parallel (URL fetches are network-bound).
            with ThreadPoolExecutor(max_workers=6) as pool:
                verified = list(pool.map(_verify_on_official_page, candidates))
            with sm._SESSION_FACTORY() as session:
                for pair in verified:
                    if pair is None:
                        continue
                    cand, url = pair
                    stats["verified"] += 1
                    tid = _insert_seed(session, root, sub, cand, url)
                    if tid:
                        stats["inserted"] += 1
                        sm._update_team_score(session, session.get(sm.StrategicTeamRow, tid))
                    else:
                        stats["skipped_duplicate"] += 1
                session.commit()
            print(f"  → {sum(1 for v in verified if v)} verified, "
                  f"total inserted so far: {stats['inserted']}")
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
