"""Directory adapters for quantum and earth science; HTTP and exact quotes only.

An institution's directory establishes a unit's identity. Only the unit's own
profile/publication section supplies outcomes; navigation, aspirations and the
parent institution's news are never used as that unit's achievements.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

SOURCES = (
    {"url": "https://ioe.sxu.edu.cn/sys/index.htm", "institution": "山西大学光电研究所",
     "domains": ("高能物理与量子科技",), "adapter": "quantum_labs"},
    {"url": "https://igg.cas.cn/jgsz/kyxt/", "institution": "中国科学院地质与地球物理研究所",
     "domains": ("地球科学",), "adapter": "earth_centres"},
    {"url": "https://iap.cas.cn/gb/jgsz/kyxt/", "institution": "中国科学院大气物理研究所",
     "domains": ("地球科学",), "adapter": "atmosphere_units",
     "evidence_hosts": ("labesm.iap.ac.cn", "klaeem.ac.cn", "lapc.iap.ac.cn",
                        "lasgweb.iap.ac.cn", "www.icces.ac.cn", "www.tea.ac.cn",
                        "lacs.iap.ac.cn", "lageo.iap.ac.cn", "nzc.iap.ac.cn")},
    {"url": "https://quantum.ustc.edu.cn/web/node/1", "institution": "中国科学技术大学",
     "domains": ("高能物理与量子科技",), "adapter": "quantum_portal",
     "team_name": "量子物理与量子信息研究部",
     "intro_start": "量子物理与量子信息研究部成立于", "intro_stop": "研究部战略目标是",
     "result_pattern": r"研究部在多光子.+?已经成为国际著名的量子物理与量子信息研究组。"},
    {"url": "https://quantum-materials.ustc.edu.cn/main.htm", "institution": "中国科学技术大学",
     "domains": ("高能物理与量子科技",), "adapter": "quantum_portal",
     "team_name": "复杂量子材料及其微结构研究组",
     "intro_start": "我们致力于", "intro_stop": "长期招聘",
     "result_pattern": r"在本研究中，我们.+?我们的工作发表在了.+?。"},
)


def text_of(page):
    return " ".join(page.get("text", "").split())


def _trim(text):
    return re.split(r"地址[：:]|版权所有|Copyright|附件下载|附件：|返回移动版", text, maxsplit=1)[0].strip()


def _directions(profile, name):
    # Compact verbatim topic labels for cards. Only labels present in the
    # unit's own text are emitted; no new taxonomy nodes are created here.
    labels = ("量子计算", "量子模拟", "量子通信", "量子通讯", "量子传感", "量子精密测量",
              "量子存储", "量子中继", "量子光学", "量子纠缠", "超冷原子", "非经典光场",
              "光量子器件", "单频激光", "量子材料", "电子结构", "里德堡原子",
              "地球系统", "数值模拟", "气候预测", "季风", "暴雨", "冰雹", "大气探测",
              "气候变化", "气溶胶", "地震", "碳循环", "岩石圈", "成矿", "地质灾害",
              "地热", "地下水", "油气", "深地", "电磁探测", "智能导钻", "行星", "空间环境")
    return [label for label in labels if label in profile][:8] or [name]


def _profile(page, adapter):
    text = text_of(page)
    if adapter == "quantum_labs":
        # Use the last heading: navigation can repeat it before the real body.
        if "实验室简介" in text:
            text = text.rsplit("实验室简介", 1)[1]
        elif "最新工作" in text:
            return _trim(text.split("最新工作", 1)[1].split("View PDF")[0])[:1800]
        elif "当前位置：" in text:
            text = text.split("当前位置：", 1)[1]
            text = re.split(r"欢迎|本实验室|实验室主要|本研究组", text, maxsplit=1)[-1]
        else:
            return ""
        return _trim(re.split(r"最新工作|最新动态|新闻动态|近期动态", text, maxsplit=1)[0])
    if adapter == "earth_centres":
        text = text.split("您现在的位置：")[-1]
        match = re.search(r"历史渊源[：:]|学科中心概况[：:]", text)
        return _trim(text[match.end():].split("——学科组信息——")[0]) if match else ""
    # Intro headings recur in the sidebar; the final instance precedes prose.
    markers = ("实验室简介", "中心简介", "研究室简介")
    matched = [m for m in markers if m in text]
    if matched:
        text = text[max(text.rfind(m) + len(m) for m in matched):]
    else:
        text = text.split("您现在的位置：")[-1]
        text = text.split("您当前的位置：")[-1]
    return _trim(text)


def _facts(page, profile, adapter):
    """Extract explicit completed work; return quotes, not generated claims."""
    values = []
    # These sections explicitly attribute work to this laboratory. Historical
    # outcomes retain their original dates; they are never presented as new.
    text = text_of(page)
    if adapter == "quantum_labs" and "最新工作" in text:
        latest = _trim(text.split("最新工作", 1)[1])
        for item in re.split(r"View\s*PDF", latest, flags=re.I)[:4]:
            if re.search(r"\b(?:19|20)\d{2}\b", item) and re.search(
                    r"Authors|作者|Phys\.|Optica|Nature|Science|Letters|Review", item, re.I):
                values.append(item.strip()[:2200])
    # Accept concrete completed results, never general '成果丰硕', people
    # awards, parent-university news or objectives promising future progress.
    scoped = profile
    if adapter == "atmosphere_units" and "LACS自2004年成立以来" in scoped:
        scoped = scoped.split("LACS自2004年成立以来", 1)[1]
    for sentence in re.split(r"(?<=[。；])", scoped):
        sentence = re.sub(r"(?:中长期目标|总体定位)[：:]", "", sentence)
        if re.search(r"将(?:开展|聚焦|研究|建设|成为|进一步)|拟(?:开展|建设|研发)|有望|目标|"
                     r"计划(?:在|于|开展|建设|实现|提出|研发)|力争|旨在|希望|可望|未来|国际上.*研究组", sentence):
            continue
        if re.search(r"建立了一支|建立了战略合作|代表性成果发表在《|共发表论文|研制了一批", sentence):
            continue
        if re.search(r"研制了|研制成功|成功研制|成功应用|实现了|成功演示|成功观测|"
                     r"构建了|编制了|提出了|建立了|发展了|发布了|完成了|发表在|工作发表|已在[^。]{1,60}大学应用|建成了|"
                     r"中心在以.{10,150}取得了重要突破", sentence):
            if len(sentence.strip()) >= 30:
                values.append(sentence.strip()[:2200])
    return list(dict.fromkeys(values))[:8]


def _record(source, directory, link, page):
    from .official_team_directory import citation, compact, person
    adapter = source["adapter"]
    name = re.sub(r"[（(][A-Za-z][A-Za-z .-]*[）)]$", "", link["label"]).strip()
    name = re.sub(r"\s+", "", name)
    text = text_of(page)
    # Labels in the institution's directory are authoritative, but the
    # destination must still describe that unit. Never accept a redirected
    # university home page or a similarly named foreign laboratory.
    def identity(value):
        return compact(value).replace("全国", "").replace("重点", "").replace("—", "-").replace("和", "与")
    if identity(name) not in identity(text):
        return None
    profile = _profile(page, adapter)
    if len(profile) < 45:
        return None
    if adapter == "earth_centres":
        description = re.split(r"人才队伍[：:]|中长期目标[：:]|主\s*任[：:]", profile.split("总体定位：")[-1])[0]
        head = re.search(r"(?<!副)主\s*任[：:]\s*([\u4e00-\u9fff](?:\s*[\u4e00-\u9fff]){1,3})(?=\s+副|$)", profile)
    else:
        description, head = profile[:1800], None
    # Direction text is verbatim; no keyword-invented field classification.
    directions = _directions(profile, name)
    basis = [citation(directory, link["label"]), citation(page, description)]
    facts = [{"kind": "description", "text": description, "citations": [basis[1]]}]
    for quote in _facts(page, profile, adapter):
        cite = citation(page, quote)
        facts.append({"kind": "outcome", "text": quote, "citations": [cite]})
        basis.append(cite)
    return {"team_name": name, "institution_name": source["institution"],
            "domain": source["domains"][0], "description": description,
            "directions": directions, "leader": person(page, compact(head.group(1)), "主任", head.group()) if head else None,
            "members": [], "citations": basis, "facts": facts,
            "source_urls": list(dict.fromkeys([source["url"], link["url"], page["url"]])),
            "identity_basis": "official_research_unit_with_scoped_facts",
            "observed_at": page.get("fetched_at", "")}


def discover_source(source, directory, fetch):
    adapter = source["adapter"]
    if adapter == "quantum_portal":
        from .official_team_directory import citation, person
        text = text_of(directory)
        if source["team_name"] not in text or source["intro_start"] not in text:
            return [], [{"url": source["url"], "reason": "unit_identity_or_body_mismatch"}]
        intro = text[text.index(source["intro_start"]):].split(source["intro_stop"])[0].strip()
        identity = citation(directory, source["team_name"])
        facts = [{"kind": "description", "text": intro, "citations": [citation(directory, intro)]}]
        outcome = re.search(source["result_pattern"], text)
        if outcome:
            facts.append({"kind": "outcome", "text": outcome.group(), "citations": [citation(directory, outcome.group())]})
        head = re.search(r"研究部主任为([\u4e00-\u9fff]{2,4})院士", intro)
        return [{"team_name": source["team_name"], "institution_name": source["institution"],
                 "domain": source["domains"][0], "description": intro, "directions": _directions(intro, source["team_name"]),
                 "leader": person(directory, head.group(1), "研究部主任", head.group()) if head else None,
                 "members": [], "citations": [identity] + [c for f in facts for c in f["citations"]],
                 "facts": facts, "source_urls": [source["url"]],
                 "identity_basis": "official_research_unit_with_scoped_facts",
                 "observed_at": directory.get("fetched_at", "")}], []
    root_host = urlsplit(source["url"]).hostname
    links = []
    seen = set()
    for link in directory.get("links", []):
        url, label = link["url"], link["label"]
        host, path = urlsplit(url).hostname, urlsplit(url).path
        if adapter == "quantum_labs":
            eligible = host == root_host and "实验室" in label and ("/sys/" in path or "/newlab" in path)
        elif adapter == "earth_centres":
            eligible = host == root_host and path.endswith("_center/") and "中心" in label
        else:
            eligible = (host == root_host or host in source["evidence_hosts"]) and bool(re.search(r"实验室|研究中心|科学中心|分中心", label))
        if eligible and url not in seen and (host, path) != (root_host, urlsplit(source["url"]).path):
            links.append(link)
            seen.add(url)
    records, errors = [], []
    for link in links:
        page = fetch(link["url"])
        if page.get("status") != "ok":
            errors.append({"url": link["url"], "reason": "unit_fetch_failed"})
            continue
        if adapter == "atmosphere_units" and urlsplit(link["url"]).hostname != root_host:
            # Follow the unit's own About/Introduction link, not its general
            # news feed (which may discuss another research institution).
            about = next((l for l in page.get("links", []) if re.search(r"实验室简介|中心简介", l["label"])
                          and urlsplit(l["url"]).hostname == urlsplit(link["url"]).hostname), None)
            if not about:
                errors.append({"url": link["url"], "reason": "unit_intro_missing"})
                continue
            page = fetch(about["url"])
            if page.get("status") != "ok":
                errors.append({"url": about["url"], "reason": "intro_fetch_failed"})
                continue
        try:
            record = _record(source, directory, link, page)
        except ValueError:
            record = None
        if record:
            records.append(record)
        else:
            errors.append({"url": page.get("url", link["url"]), "reason": "unit_identity_or_body_mismatch"})
    return records, errors
