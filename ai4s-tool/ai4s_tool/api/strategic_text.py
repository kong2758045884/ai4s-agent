"""Pure local normalization shared by catalogue search and task matching.

Aliases expand vocabulary, never eligibility or ownership of achievements.
There are no providers, database writes or model calls in this module.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

VERSION = "task-elements-v4"
ALIASES = {
    "中科院": "中国科学院", "中国科大": "中国科学技术大学", "中科大": "中国科学技术大学",
    "哈工大": "哈尔滨工业大学", "北大": "北京大学", "浙大": "浙江大学",
    "上海交大": "上海交通大学", "复旦": "复旦大学", "清华": "清华大学",
    "quantum key distribution": "量子密钥分发", "quantum communication": "量子通信",
    "量子通讯": "量子通信", "quantum computation": "量子计算", "quantum computing": "量子计算",
    "quantum simulation": "量子模拟", "quantum sensing": "量子传感",
    "quantum metrology": "量子精密测量", "precision measurement": "精密测量",
    "quantum memory": "量子存储", "quantum teleportation": "量子远程传态",
    "entanglement": "纠缠", "earth system": "地球系统", "climate prediction": "气候预测",
    "protein structure prediction": "蛋白质结构预测", "weather forecasting": "天气预测",
    "预报": "预测", "embodied intelligence": "具身智能", "large language model": "大语言模型",
}
CONCEPTS = tuple(sorted(set(ALIASES.values()) | {
    "蛋白质结构预测", "蛋白质设计", "蛋白质", "药物研发", "药物发现", "药物筛选", "免疫治疗",
    "医学影像", "基因编辑", "肿瘤", "生物计算", "分子动力学", "结构生物学", "单细胞",
    "量子计算", "量子模拟", "量子通信", "量子精密测量", "量子传感", "量子材料", "量子光学",
    "高能物理", "粒子探测", "对撞机", "地球系统", "气候预测", "天气预测", "暴雨预测",
    "深地探测", "地震", "地质", "海洋", "遥感", "碳循环", "智能导钻", "cas-esm",
    "具身智能", "机器人", "智能体", "人工智能", "大模型", "大语言模型", "自然语言处理",
    "计算机视觉", "机器学习", "深度学习", "强化学习", "多模态", "图像超分辨率",
    "催化", "能源材料", "合金", "电池", "材料设计", "材料计算", "材料", "储能",
    "科学计算", "科学数据", "知识图谱", "高性能计算", "数值模拟", "仿真", "开放科学",
    "论文", "专利", "开源代码", "开源", "数据集", "实验验证", "临床试验", "系统", "模型",
}, key=lambda x: (-len(x), x)))
_FILLER = re.compile(r"请(?:帮我)?|帮我|推荐|寻找|找到|哪些|一个|一些|国内|科研团队|研究团队|团队|课题组|实验室|"
                     r"能承担|能够|可以|希望|需要|必须|要求|用于|开发|开展|进行|相关|任务|能力|研究|具备|支持|的|吗")
_NEGATIVE = re.compile(r"(?:不考虑|排除|不要|不包括|不涉及|不需要|不含)([^，。；;！？!?]+)")


@lru_cache(maxsize=4096)
def normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    for alias, canonical in sorted(ALIASES.items(), key=lambda item: -len(item[0])):
        # Do not turn 清华大学 into 清华大学大学 or 北大 into a substring of 西北大学.
        pattern = re.escape(alias)
        if alias in {"清华", "复旦"}:
            pattern += r"(?!大学)"
        if alias == "北大":
            pattern = r"(?<!西)北大(?!学)"
        text = re.sub(pattern, canonical, text)
    text = text.replace("量子计算与模拟", "量子计算 量子模拟").replace("量子计算和模拟", "量子计算 量子模拟")
    return re.sub(r"\s+", " ", text).strip()


def _concepts(value: str) -> list[str]:
    text = normalize(value)
    found = []
    for concept in CONCEPTS:
        if concept in text:
            found.append(concept)
            text = text.replace(concept, " ")
    return found


def query_terms(value: str) -> list[str]:
    text = normalize(value)
    known = _concepts(text)
    for term in known:
        text = text.replace(term, " ")
    rest = _FILLER.sub(" ", text)
    # Keep unknown noun phrases instead of turning one matching character into a hit.
    rest = re.sub(r"[与和及或]", " ", rest)
    terms = re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9][a-z0-9.+-]{1,}", rest)
    return list(dict.fromkeys([*known, *terms]))


def search_match(query: str, value: str) -> bool:
    haystack = normalize(value)
    phrase = normalize(query)
    if phrase in haystack:
        return True
    terms = query_terms(query)
    return bool(terms) and all(term in haystack for term in terms)


def snippet(query: str, value: str, limit: int = 260) -> str:
    """Keep the original wording. Never display normalized text as an original quote."""
    text = str(value or "")
    probes = [query.casefold(), *query_terms(query)]
    positions = [text.casefold().find(term) for term in probes if term]
    positions = [pos for pos in positions if pos >= 0]
    start = max(0, min(positions) - 50) if positions else 0
    return ("…" if start else "") + text[start:start + limit] + ("…" if start + limit < len(text) else "")


def parse_task(value: str) -> dict:
    text = normalize(value)
    excluded = list(dict.fromkeys(term for match in _NEGATIVE.finditer(text) for term in query_terms(match[1])))
    positive = _NEGATIVE.sub(" ", text)
    terms = query_terms(positive)
    outcome_terms = {"论文", "专利", "开源", "开源代码", "数据集", "实验验证", "临床试验"}
    goals = [term for term in terms if term not in outcome_terms]
    outcomes = [term for term in terms if term in outcome_terms]
    # Generic output words should not dilute a recognized scientific objective.
    if any(t not in {"系统", "模型", "人工智能"} for t in goals):
        goals = [t for t in goals if t not in {"系统", "模型"}]
    any_of = "或者" in positive or "或" in positive
    unknown = [t for t in goals if t not in CONCEPTS]
    return {"version": VERSION, "goals": goals, "required": outcomes,
            "excluded": excluded, "mode": "any" if any_of else "all",
            "unresolved": unknown, "notice": "本地任务解析；未列出的交付周期、预算等条件需进一步确认"}


def evaluate_task(parsed: dict, team: dict, claims: list[tuple]) -> tuple[int, list[dict], list[dict]]:
    goals, required = parsed["goals"], parsed["required"]
    wanted = list(dict.fromkeys(goals + required))
    if not wanted:
        return 0, [], []
    fields = " ".join([team.get("domainName", ""), team.get("subdomainName", ""),
                       *(team.get("researchDirections") or [])])
    context = " ".join([team.get("teamName", ""), team.get("institutionName", ""), team.get("description", ""), fields,
                        *(str(c[4]) + " " + str(c[5]) for c in claims)])
    if any(term in normalize(context) for term in parsed["excluded"]):
        return 0, [], []
    # A profile or research-direction citation is a recall lead, not proof that
    # the team achieved a required task capability. Formal matches use outcomes.
    matched = {term: [c for c in claims if c[3] == "outcome" and
                term in normalize(str(c[4]) + " " + str(c[5]))] for term in wanted}
    groups = parsed.get("groups")
    if groups:
        if any(not (any(matched[t] for t in g["terms"]) if g["operator"] == "any" else all(matched[t] for t in g["terms"])) for g in groups):
            return 0, [], []
    else:
        if any(not matched[t] for t in required):
            return 0, [], []
        if goals and not (any(matched[t] for t in goals) if parsed["mode"] == "any" else all(matched[t] for t in goals)):
            return 0, [], []
    ranks = sorted([(sum(c in matched[t] for t in wanted) / len(wanted), c) for c in claims],
                   key=lambda pair: (-pair[0], pair[1][0]))
    outcomes = [(fit, c) for fit, c in ranks if c[3] == "outcome" and fit > 0
                and (not goals or any(c in matched[t] for t in goals))]
    effective = ([t for t in goals if matched[t]][:1] + required) if parsed["mode"] == "any" else wanted
    if groups:
        effective = list(dict.fromkeys(t for group in groups for t in
            ([t for t in group["terms"] if matched[t]][:1] if group["operator"] == "any" else group["terms"])))
    evidence_fit = sum(bool(matched[t]) for t in effective) / max(1, len(effective))
    if evidence_fit < .5 or not outcomes:
        return 0, [], []
    direction_fit = sum(term in normalize(fields) for term in effective) / max(1, len(effective))
    coverage = sum(any(c in matched[t] for _, c in outcomes) for t in effective) / max(1, len(effective))
    score = round(100 * (.60 * evidence_fit + .25 * direction_fit + .15 * coverage))
    # Every matched requirement keeps a reference, even on tasks with many clauses.
    chosen = {outcomes[0][1][0]: outcomes[0][1]}
    for refs in matched.values():
        if refs:
            chosen.setdefault(refs[0][0], refs[0])
    for fit, claim in ranks:
        if fit > 0 and len(chosen) < 4:
            chosen.setdefault(claim[0], claim)
    citations = [{"id": c[0], "kind": c[3], "text": c[4], "quote": c[5], "url": c[6]} for c in chosen.values()]
    criteria = [{"requirement": term, "matched": bool(refs), "citations": [c[0] for c in refs if c[0] in {v["id"] for v in citations}]} for term, refs in matched.items()]
    return score, citations, criteria
