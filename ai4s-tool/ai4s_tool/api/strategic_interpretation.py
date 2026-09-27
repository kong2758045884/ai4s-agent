"""Local task interpretation with original spans and explicitly unknown conditions."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from . import strategic_text as text

VERSION = "assessment-criteria-v1"
_EXCLUDE = re.compile(r"不考虑|排除|不要|不包括|不涉及|不需要|不含")
_PREFER = re.compile(r"优先|最好|偏好|尽量|倾向")
_MUST = re.compile(r"必须|务必|至少|不得|只接受|仅限|要求|需要|具备|支持|能够|能承担")
_CONSTRAINT = re.compile(r"(?:\d+(?:\.\d+)?\s*(?:%|％|天|周|个月|月|年|万元|万|元|人|小时|分钟|秒|ms|nm|mpa|k|°c|℃))|期限|预算|交付|精度|准确率|样本量|可投入|设备|算力", re.I)
_ORGANIZATION = re.compile(r"牵头|联合|协作|独立承担|单一团队|多个团队|分工")
_SPLIT = re.compile(r"[^，,。；;！？!?\n]+?(?=并且|同时|而且|并具备|并支持|且具备|且支持|[，,。；;！？!?\n]|$)")
_EXTRA_FILLER = re.compile(r"我想|我们想|我要|我需要|请您|您|帮忙|谢谢|一下|想要|完成|能够开展|能不能|成果原文|成果支撑|有对应|有相关|对应|相关|科学目标|研究目标|必要能力|并且|同时|而且|并具备|并支持|且具备|且支持|采用|包括|含有|至少|必须|务必")
_DOMAIN_TERMS = {
    "科学通用底座": r"科学计算|科学数据|高性能计算|知识图谱|开放科学|数值模拟",
    "通用 AI": r"具身智能|机器人|大模型|大语言模型|自然语言处理|多模态|计算机视觉|智能体",
    "高能物理与量子科技": r"量子|高能物理|粒子|对撞机",
    "化学与材料": r"催化|材料|合金|电池|储能|化学",
    "生命科学与医学": r"蛋白质|肿瘤|药物|基因|免疫|单细胞|医学|临床|生物",
    "地球科学": r"气候|天气|海洋|遥感|地震|地质|地球|碳循环|深地",
}


class Scope(BaseModel):
    mode: Literal["auto", "selected"] = "auto"
    domainIds: list[str] = Field(default_factory=list, max_length=6)
    subdomainId: str | None = Field(default=None, max_length=64)
    domesticOnly: Literal[True] = True


class InterpretationRequest(BaseModel):
    taskText: str = Field(min_length=2, max_length=2000)
    scope: Scope = Field(default_factory=Scope)


class Criterion(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    kind: Literal["goal", "capability", "outcome", "constraint", "preference", "exclusion", "organization", "unresolved"]
    text: str = Field(min_length=1, max_length=500)
    necessity: Literal["required", "preferred", "excluded", "informational"]
    sourceSpan: dict | None = None
    operator: Literal["all", "any"] = "all"


def residual_terms(value: str) -> list[str]:
    return [t for t in text.query_terms(_EXTRA_FILLER.sub(" ", value)) if t not in text.CONCEPTS]


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def interpret(body: InterpretationRequest, domains: list[dict]) -> dict:
    original = body.taskText.strip()
    criteria = []
    for index, match in enumerate(_SPLIT.finditer(original)):
        clause = match[0].strip()
        if not clause:
            continue
        normalized = text.normalize(clause)
        known = text._concepts(clause)
        if _EXCLUDE.search(clause):
            kind, necessity = "exclusion", "excluded"
        elif _PREFER.search(clause):
            kind, necessity = "preference", "preferred"
        elif _CONSTRAINT.search(clause):
            kind, necessity = "constraint", "required"
        elif _ORGANIZATION.search(clause):
            kind, necessity = "organization", "required" if _MUST.search(clause) else "informational"
        elif known:
            kind, necessity = ("goal" if not criteria else "capability"), "required"
        else:
            kind, necessity = "unresolved", "required" if _MUST.search(clause) else "informational"
        start = match.start() + len(match[0]) - len(match[0].lstrip())
        criteria.append({"id": f"criterion-{index + 1}", "kind": kind, "text": clause,
            "necessity": necessity, "sourceSpan": {"start": start, "end": start + len(clause), "text": clause},
            "terms": known, "evaluable": kind in {"goal", "capability", "preference", "exclusion"} and bool(known),
            "operator": "any" if "或" in clause else "all", "unresolvedTerms": residual_terms(clause),
            "status": "本地规则提取，待确认"})
    normalized = text.normalize(original)
    proposed = [d["id"] for d in domains if any(
        text.normalize(d["name"]) == text.normalize(name) and re.search(pattern, normalized)
        for name, pattern in _DOMAIN_TERMS.items())]
    selected = list(dict.fromkeys(body.scope.domainIds))
    conflict = body.scope.mode == "selected" and bool(proposed) and bool(set(proposed) - set(selected))
    resolved = selected if body.scope.mode == "selected" else proposed
    result = {"parserVersion": VERSION, "taskText": original, "criteria": criteria,
        "scope": {**body.scope.model_dump(), "domainIds": selected},
        "proposedDomainIds": proposed, "resolvedDomainIds": resolved,
        "scopeConflict": conflict, "scopeChoices": ["keep", "switch", "expand"] if conflict else [],
        "unknowns": [label for label, pattern in (("具体研究对象或材料", r"针对|研究对象|材料为"),
            ("验收指标", r"精度|准确率|指标|验收"), ("交付时间", r"\d+.*[天周月年]|期限|交付时间"),
            ("人员、资源与合作条件", r"人员|可投入|资源|合作条件")) if not re.search(pattern, original)],
        "notice": "本地规则解析，不调用模型；请确认必要条件。未识别的限制不会被视为已满足。"}
    result["interpretationId"] = "interpret-" + digest(result)[:24]
    return result


def matching_criteria(criteria: list[dict]) -> tuple[dict, list[dict]]:
    """Compile confirmed fields, keeping hard constraints that need additional proof."""
    goals, required, excluded, unresolved, groups = [], [], [], [], []
    outcomes = {"论文", "专利", "开源", "开源代码", "数据集", "实验验证", "临床试验"}
    blockers = []
    for criterion in criteria:
        known = text._concepts(criterion["text"])
        necessity, kind = criterion["necessity"], criterion["kind"]
        if necessity == "excluded":
            terms = text.query_terms(_EXCLUDE.sub("", criterion["text"]))
            excluded.extend(terms)
        elif necessity == "required":
            residual = residual_terms(criterion["text"])
            if kind in {"constraint", "organization", "unresolved"} or not known or residual:
                blockers.append({"criterionId": criterion["id"], "text": criterion["text"],
                    "status": "insufficient", "reason": "现有成果匹配不能证明该必要条件，需补充直接依据"})
            else:
                goals.extend(t for t in known if t not in outcomes)
                required.extend(t for t in known if t in outcomes)
                groups.append({"terms": known, "operator": criterion.get("operator", "all")})
        if not known:
            unresolved.append(criterion["text"])
    return {"version": VERSION, "goals": list(dict.fromkeys(goals)), "required": list(dict.fromkeys(required)),
        "excluded": list(dict.fromkeys(excluded)), "unresolved": unresolved, "mode": "all",
        "groups": groups,
        "notice": "按已确认的必要条件匹配；未知约束不会自动通过"}, blockers
