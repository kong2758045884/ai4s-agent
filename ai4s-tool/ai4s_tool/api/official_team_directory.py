"""Discover named research groups from current official directories.

Adapters describe page structure, never a fixed list of researchers. Each field
retains a verbatim citation. Paper authors and former members are not rosters.
"""
from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlsplit

from . import team_research as tr
from .official_research_catalogues import SOURCES as RESEARCH_DIRECTORIES


DIRECTORIES = (
    {
        "url": "https://collegeai.tsinghua.edu.cn/kxyj/ktzjs.htm",
        "institution": "清华大学人工智能学院",
        "domains": ("通用 AI", "科学通用底座"),
        "adapter": "pi_cards",
        "detail_path": "/kxyj/ktzjs/",
    },
    {
        "url": "https://www.ibp.cas.cn/swdfz/",
        "institution": "中国科学院生物物理研究所",
        "domains": ("生命科学与医学",),
        "adapter": "group_profiles",
        "detail_path": "/rc/",
    },
    {
        "url": "https://www.genetics.ac.cn/jgsh/yjzx/",
        "institution": "中国科学院遗传与发育生物学研究所",
        "domains": ("生命科学与医学",),
        "adapter": "research_units",
        "detail_path": "/jgsh/yjzx/",
    },
    {
        "url": "https://mifa.sjtu.edu.cn/",
        "institution": "上海交通大学",
        "domains": ("通用 AI",),
        "adapter": "current_members",
        "team_name": "机器智能基础与应用实验室",
    },
    {
        "url": "https://imr.cas.cn/jgsz/index.html",
        "institution": "中国科学院金属研究所",
        "domains": ("化学与材料",),
        "adapter": "department",
        "detail_path": "/jgsz/synl/",
    },
) + RESEARCH_DIRECTORIES


def fetch_directory_page(url: str) -> dict:
    # The network body still has fetch_page's size/time limits. Do not cut off
    # directory links or rosters that follow a long bibliography.
    return tr.fetch_page(url, max_text_chars=None, max_links=None)


def compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def citation(page: dict, quote: str) -> dict:
    if not quote.strip() or compact(quote) not in compact(page.get("text", "")):
        raise ValueError("引用不在抓取的正文中")
    return {
        "url": page["url"],
        "quote": quote,
        "fetched_at": page.get("fetched_at", ""),
        "source_type": "official_institution",
    }


def section(text: str, start: str, stops: tuple[str, ...]) -> str:
    index = text.find(start)
    if index < 0:
        return ""
    tail = text[index + len(start):]
    end = min((tail.find(stop) for stop in stops if stop in tail), default=len(tail))
    return tail[:end].strip(" ：:")


def person(page: dict, name: str, role: str, quote: str) -> dict:
    return {
        "name": name,
        "role": role,
        "profile_url": page["url"],
        "source_urls": [page["url"]],
        "source_type": "官方课题组目录·正文核验",
        "verification_status": "verified",
        "confidence": 0.95,
        "evidence": json.dumps({
            **citation(page, quote),
            "team_relation": "exact_team",
            "leadership_recency": "current_official_listing",
        }, ensure_ascii=False),
    }


def _card_record(source: dict, directory: dict, link: dict, page: dict) -> dict | None:
    match = re.fullmatch(r"(.+?)\s+研究方向\s+(.+?)\s+PI\s+(.+)", link["label"])
    if not match:
        return None
    team, direction, pi = match.groups()
    name = re.match(r"[\u4e00-\u9fff]{2,4}", pi)
    leader_name = name.group() if name else pi.strip()
    text = " ".join(page["text"].split())
    if compact(team) not in compact(page.get("title", "")) or "PI" not in text:
        return None
    body = text[text.find(" PI"):]
    description = section(body, "课题组简介", ("主要成果", "研究成果", "代表性论文", "课题组成员", "新闻动态"))
    if not description:
        description = section(body, "研究方向", ("主要成果", "代表性论文", "课题组成员"))
    achievement = section(body, "主要成果", ("代表性论文", "课题组成员", "新闻动态"))
    if not achievement:
        achievement = section(body, "研究成果", ("课题组风格", "课题组文章列表", "课题组成员", "新闻动态"))
    if not achievement:
        achievement = section(body, "代表性论文", ("课题组成员", "新闻动态", "contacts"))
    if not description or not achievement:
        return None
    members_text = section(body, "课题组成员", ("新闻动态", "contacts", "地址："))
    members = []
    # The roster explicitly pairs names with email addresses. Emails are only
    # delimiters; no contact addresses are copied into the product roster.
    for member in re.finditer(r"([\u4e00-\u9fff]{2,4})\s+[\w.+-]+@[\w.-]+", members_text):
        members.append(person(page, member.group(1), "课题组成员", member.group()))
    domain = (
        "科学通用底座"
        if any(term in direction for term in ("科学智能", "AI+Science", "科学观测", "物理测量"))
        else "通用 AI"
    )
    return {
        "team_name": team,
        "institution_name": source["institution"],
        "domain": domain,
        "description": description[:2400],
        "directions": [direction],
        "leader": person(directory, leader_name, "PI", link["label"]),
        "members": members,
        "citations": [
            citation(directory, link["label"]),
            citation(page, description[:2400]),
            citation(page, achievement[:1600]),
        ],
        "source_urls": [directory["url"], page["url"]],
        "identity_basis": "official_named_group_card",
    }


def _profile_record(source: dict, directory: dict, link: dict, page: dict) -> dict | None:
    name = link["label"].strip()
    if not re.fullmatch(r"[\u4e00-\u9fff]{2,4}", name):
        return None
    text = " ".join(page["text"].split())
    # Profile headings belong to the named person. Navigation occurrences of
    # “研究组长” cannot confer a position on someone elsewhere in the page.
    header = re.search(rf"{re.escape(name)}\s*/(.+?)电子邮件", text)
    if not header or "研究组长" not in header.group():
        return None
    if source["institution"] not in header.group():
        return None
    direction = section(header.group(), "研究方向", ("电子邮件",))
    if not direction:
        return None
    description = section(text, "承担项目情况", (
        "代表论著", "发表论文", "其他论著", "工作人员", "研究生",
    ))
    if not description:
        return None
    # This label is a normalization of an explicitly listed research-group
    # head, not the assertion that the person directs the parent laboratory.
    team = f"{name}研究组"
    members = _profile_members(page)
    return {
        "team_name": team,
        "institution_name": source["institution"],
        "domain": source["domains"][0],
        "description": description[:2400],
        "directions": [direction],
        "leader": person(page, name, "研究组长", header.group()),
        "members": members,
        "citations": [
            citation(page, header.group()),
            citation(page, description[:2400]),
        ],
        "source_urls": [directory["url"], page["url"]],
        "identity_basis": "current_group_head_directory_and_personal_group_profile",
    }


def _profile_members(page: dict) -> list[dict]:
    text = " ".join(page["text"].split())
    starts = [text.find(label) for label in ("团队成员", "工作人员") if label in text]
    if not starts:
        return []
    roster = text[min(starts):]
    roster = re.split(r"以往成员|离职人员|毕业学生|所况简介", roster, maxsplit=1)[0]
    # The official biographies either repeat a standalone name before its
    # paragraph or label a named PhD/staff member. Neither pattern reads papers.
    pattern = r"(?<![\u4e00-\u9fff])([\u4e00-\u9fff]{2,4})(?=，(?:博士|科研秘书)|\s+\1)"
    matches = list(re.finditer(pattern, roster))
    people = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(roster)
        bio = roster[match.start():end].strip()
        if re.search(r"已离|离职|毕业后|曾为课题组|曾任", bio):
            continue
        role = re.search(r"现为课题组(副研究员|助理研究员|研究员)|，(科研秘书)", bio)
        value = person(page, match.group(1), next(filter(None, role.groups())) if role else "课题组成员", bio)
        value["bio"] = bio[:1600]
        people.append(value)
    return people


def _current_members_record(source: dict, directory: dict, link: dict, page: dict) -> dict | None:
    text = " ".join(page["text"].split())
    if source["team_name"] not in text:
        return None
    roster = section(text, "Current Members", ("Former Members", "Lab Memories"))
    head = re.search(r"Principal Investigator\s+.+?\(\s*([\u4e00-\u9fff]{2,4})\s*\)", roster)
    if not head:
        return None
    members = []
    for label, role, stops in (
        ("PhD Students", "博士研究生", ("MS Students",)),
        ("MS Students", "硕士研究生", ()),
    ):
        body = section(roster, label, stops)
        # Co-supervisors are inside the student record, never separate members.
        for entry in re.finditer(
            r"([A-Z][A-Za-z .-]+)\(\s*([\u4e00-\u9fff]{2,4})\s*\)\s*,\s*"
            r"(?:co-supervised with .+?,\s*)?\d{4}[–-]present", body
        ):
            members.append(person(page, entry.group(2), role, entry.group()))
    description = section(text, "About MIFA Lab", ("News",))
    achievement = section(text, "News", ("Current Members",))
    if not description or not achievement:
        return None
    return {
        "team_name": source["team_name"], "institution_name": source["institution"],
        "domain": source["domains"][0], "description": description[:2400],
        "directions": ["pre-training and foundation models", "data-efficient fine-tuning",
                       "continual learning", "multimodal learning"],
        "leader": person(page, head.group(1), "PI", head.group()),
        "members": members,
        "citations": [citation(page, description), citation(page, achievement)],
        "source_urls": [page["url"]],
        "identity_basis": "official_named_lab_current_roster",
    }


def _unit_record(source: dict, directory: dict, link: dict, page: dict) -> dict | None:
    team = link["label"].strip()
    text = " ".join(page["text"].split())
    if compact(team) not in compact(page.get("title", "")):
        return None
    # Explicit current leadership section, confined to this unit's body.
    head = re.search(r"现任领导\s+主任[：:]\s*([\u4e00-\u9fff]{2,4})(?=\s|$)", text)
    if not head:
        return None
    before = text[:head.start()]
    start = before.find(team, before.find("内设科研单元", before.find("首页 >")))
    body = before[start:] if start >= 0 else ""
    direction = section(body, "研究方向", ())
    description = body.split("研究方向")[0].removeprefix(team).strip()
    if not direction or len(description) < 60:
        return None
    # Only independently operating research units, not the institute as a whole.
    if not re.search(r"实验室|中心", team):
        return None
    deputies = re.search(r"副主任[：:]\s*([\u4e00-\u9fff\s]+?)(?:附件下载|科研进展|联系我们)", text[head.end():])
    members = [
        person(page, name, "副主任", deputies.group())
        for name in (deputies.group(1).split() if deputies else [])
        if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", name)
    ]
    return {
        "team_name": team, "institution_name": source["institution"],
        "domain": source["domains"][0], "description": description[:2400],
        "directions": [direction[:500]],
        "leader": person(page, head.group(1), "主任", head.group()),
        "members": members,
        "citations": [citation(page, head.group()), citation(page, body[:3500])],
        "source_urls": [directory["url"], page["url"]],
        "identity_basis": "current_research_unit_directory",
    }


def _department_record(source: dict, directory: dict, link: dict, page: dict) -> dict | None:
    text = " ".join(page["text"].split())
    team = page.get("title", "").split("--")[0].strip()
    if not team.endswith("研究部"):
        return None
    head = re.search(r"研究部(主任|副主任[（(]主持工作[）)])[：:]\s*([\u4e00-\u9fff]{2,4})(?=\s|$)", text)
    if not head:
        return None
    description = section(text, "研究部简介", ("研究部主任", "研究部副主任"))
    direction = section(text[head.end():], "研究方向", ("研究成果", "课题组", "附件"))
    achievements = section(text[head.end():], "研究成果", ("课题组", "附件"))
    if not description or not direction or not achievements:
        return None
    return {
        "team_name": team, "institution_name": source["institution"],
        "domain": source["domains"][0], "description": description[:2400],
        "directions": [part.strip() for part in re.split(r"[；;]", direction) if part.strip()],
        "leader": person(page, head.group(2), head.group(1), head.group()),
        "members": [], "source_urls": [directory["url"], page["url"]],
        "citations": [citation(page, description[:2400]), citation(page, direction),
                      citation(page, achievements[:2400]), citation(page, head.group())],
        "identity_basis": "official_research_department_with_current_head_and_results",
    }


def discover(domain_name: str, *, fetch=fetch_directory_page) -> tuple[list[dict], list[dict]]:
    """Walk all matching directory entries; a worker bound is not a result cap."""
    parsers = {"pi_cards": _card_record, "group_profiles": _profile_record,
               "research_units": _unit_record, "current_members": _current_members_record,
               "department": _department_record}
    records, errors = [], []
    for source in DIRECTORIES:
        if domain_name not in source["domains"]:
            continue
        directory = fetch(source["url"])
        if directory.get("status") != "ok":
            errors.append({"url": source["url"], "reason": "directory_fetch_failed"})
            continue
        if source["adapter"] in {"quantum_labs", "earth_centres", "atmosphere_units", "quantum_portal"}:
            from .official_research_catalogues import discover_source
            found, failed = discover_source(source, directory, fetch)
            records.extend(found)
            errors.extend(failed)
            continue
        if source["adapter"] == "current_members":
            record = _current_members_record(source, directory, {}, directory)
            if record:
                record["observed_at"] = directory.get("fetched_at", "")
                records.append(record)
            continue
        host = urlsplit(source["url"]).hostname
        links = [
            link for link in directory.get("links", [])
            if urlsplit(link["url"]).hostname == host
            and urlsplit(link["url"]).path.startswith(source["detail_path"])
            and urlsplit(link["url"]).path.endswith((".htm", ".html"))
            and link["url"] != source["url"]
        ]
        with ThreadPoolExecutor(max_workers=4) as pool:
            pages = list(pool.map(fetch, [link["url"] for link in links]))
        for link, page in zip(links, pages):
            if page.get("status") != "ok":
                errors.append({"url": link["url"], "reason": "profile_fetch_failed"})
                continue
            try:
                record = parsers[source["adapter"]](source, directory, link, page)
            except ValueError:
                record = None
                errors.append({"url": link["url"], "reason": "citation_validation_failed"})
            if record and record["domain"] == domain_name:
                record["observed_at"] = page.get("fetched_at", "")
                records.append(record)
    return records, errors


def _signature(record: dict) -> str:
    def stable(value):
        if isinstance(value, dict):
            return {key: stable(item) for key, item in value.items() if key not in ("observed_at", "fetched_at")}
        if isinstance(value, list):
            return [stable(item) for item in value]
        if isinstance(value, str) and value.startswith("{"):
            try:
                return stable(json.loads(value))
            except ValueError:
                pass
        return value
    return hashlib.sha256(json.dumps(stable(record), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _directory_key(institution: str, team: str) -> str:
    from .strategic_map import _canonical_team_key
    # A trailing parenthesized Latin acronym does not create a second entity.
    team = re.sub(r"[（(][A-Za-z][A-Za-z .-]*[）)]$", "", team).strip()
    return _canonical_team_key(institution, team)


def _subdomain_score(text: str, name: str) -> int:
    from .strategic_graph import _SUBDOMAIN_HINTS, _normalized_name, _subdomain_match_score
    # Character n-grams are useful discovery hints, not a qualification rule:
    # “细胞” alone cannot put a cell-cycle group into “细胞内吞”.
    hints = tuple(_normalized_name(value) for value in (name, *_SUBDOMAIN_HINTS.get(name, ())))
    return _subdomain_match_score(text, hints)


def sync(session, domain, subdomain=None, *, fetch=fetch_directory_page, records=None) -> dict[str, Any]:
    """Persist source-backed directory records, retaining manual data and history."""
    from . import strategic_map as sm
    from .strategic_graph import _normalized_name
    from .team_research_store import append_history, history, manual_fields, upsert_people

    records, errors = discover(domain.name, fetch=fetch) if records is None else (records, [])
    records = [record for record in records if record["domain"] == domain.name]
    existing = {
        _directory_key(row.institution_name or row.name, row.team_name): row
        for row in session.query(sm.StrategicTeamRow).filter_by(domain_id=domain.id)
    }
    children = session.query(sm.StrategicDomainRow).filter_by(parent_id=domain.id, deleted=False).all()
    created = updated = members_count = conflicts = 0
    for record in records:
        searchable = _normalized_name(" ".join([
            record["team_name"], *record["directions"], record["description"],
        ]))
        if subdomain is not None and not _subdomain_score(searchable, subdomain.name):
            continue
        key = _directory_key(record["institution_name"], record["team_name"])
        row = existing.get(key)
        if row is not None and row.deleted:
            continue
        incoming = record.get("leader")
        leaders, _ = sm._team_people(session, row.id) if row is not None else ([], [])
        conflict = bool(incoming) and any(p["name"] != incoming["name"] for p in leaders)
        signature = _signature(record)
        old = history(session, row.id, "official_directory") if row is not None else []
        if old and old[0]["payload"].get("signature") == signature:
            continue
        if conflict:
            old = history(session, row.id, "official_directory_conflict")
            if not old or old[0]["payload"].get("signature") != signature:
                append_history(session, row.id, "official_directory_conflict", {
                    "signature": signature, "record": record, "published": False,
                })
            conflicts += 1
            session.commit()
            continue
        if row is None:
            row = sm.StrategicTeamRow(
                id=sm._candidate_id(domain.id, key), domain_id=domain.id,
                name=record["institution_name"], institution_name=record["institution_name"],
                team_name=record["team_name"],
            )
            session.add(row)
            session.flush()
            existing[key] = row
            created += 1
        protected = manual_fields(session, row.id)
        for field, value in (
            ("description", record["description"]),
            ("research_directions", record["directions"]),
            ("focus", "、".join(record["directions"])[:255]),
        ):
            if field not in protected:
                setattr(row, field, value)
        if subdomain is not None:
            row.subdomain_id = subdomain.id
        elif not row.subdomain_id and children:
            ranked = sorted(children, key=lambda child: _subdomain_score(searchable, child.name), reverse=True)
            if _subdomain_score(searchable, ranked[0].name):
                row.subdomain_id = ranked[0].id
        if "location" not in protected and not row.location:
            row.location = "中国（官方科研单位名录）"
        row.is_domestic = True
        row.source = "官方课题组目录"
        row.source_urls = list(dict.fromkeys([*(row.source_urls or []), *record["source_urls"]]))
        row.evidence_urls = list(dict.fromkeys([*(row.evidence_urls or []), *record["source_urls"]]))
        row.evidence_summary = "\n".join(cite["quote"] for cite in record["citations"])
        row.report_title = f"{record['institution_name']}官方课题组资料"
        row.team_confidence = 0.95
        row.verification_status = "verified"
        deleted_names = {
            p.name for p in session.query(sm.StrategicPersonRow).filter_by(team_id=row.id, deleted=True)
        }
        members = [p for p in record["members"]
                   if (not incoming or p["name"] != incoming["name"]) and p["name"] not in deleted_names]
        upsert_people(session, row, incoming if incoming and incoming["name"] not in deleted_names else None, members)
        session.flush()
        if incoming:
            row.leader_confidence = 0.95
        row.member_confidence = 0.95 if members else row.member_confidence or 0
        sm._update_team_score(session, row)
        row.updated_at = sm._now()
        # Identical re-fetches do not create duplicate research histories.
        old = history(session, row.id, "official_directory")
        if not old or old[0]["payload"].get("signature") != signature:
            append_history(session, row.id, "official_directory", {
                "signature": signature, "record": record, "published": True,
                "rule": record["identity_basis"],
            })
        updated += 1
        members_count += len(members)
        # Checkpoint each group so a later network/SQLite failure loses no batch.
        session.commit()
    return {
        "directoryCreated": created, "directoryUpdated": updated,
        "directoryMembers": members_count, "directoryConflicts": conflicts,
        "directoryErrors": errors,
    }
