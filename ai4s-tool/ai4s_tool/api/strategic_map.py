# -*- coding: utf-8 -*-
"""国内战略力量图谱 API。

该路由把战略图谱的编辑数据落在独立 SQLite 表中，并在请求刷新时从 AI4S
Daily 的公开报告中提取领域相关的机构/团队线索。领域配置和研判结果分开保存：
用户手工调整的关注级别、联系状态不会因为下一次 Daily 刷新被覆盖。
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String, Text, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker


router = APIRouter(prefix="/strategic-map", tags=["strategic_map"])

_DEFAULT_DAILY_BASE_URL = "https://ai4s-frontier.github.io/AI4S-Daily-HTML"
_DAILY_BASE_URL = os.getenv("AI4S_DAILY_BASE_URL", _DEFAULT_DAILY_BASE_URL).rstrip("/")
_DB_ENV = os.getenv("STRATEGIC_MAP_DB_PATH", "").strip()
_DB_PATH = Path(_DB_ENV or (Path(__file__).resolve().parents[2] / "strategic_map.db"))
if not _DB_PATH.is_absolute():
    _DB_PATH = Path.cwd() / _DB_PATH
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_ENGINE = create_engine(
    f"sqlite:///{_DB_PATH}",
    connect_args={"check_same_thread": False},
)
_SESSION_FACTORY = sessionmaker(bind=_ENGINE, expire_on_commit=False)
_Base = declarative_base()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class StrategicDomainRow(_Base):
    __tablename__ = "strategic_map_domain"

    id = Column(String(64), primary_key=True)
    name = Column(String(120), nullable=False)
    description = Column(String(255), nullable=False, default="")
    parent_id = Column(String(64), nullable=True, index=True)
    sort_order = Column(Integer, nullable=False, default=0)
    deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, nullable=False, default=_now)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)


class StrategicTeamRow(_Base):
    __tablename__ = "strategic_map_team"

    id = Column(String(64), primary_key=True)
    domain_id = Column(String(64), nullable=False, index=True)
    subdomain_id = Column(String(64), nullable=True, index=True)
    name = Column(String(180), nullable=False)
    focus = Column(String(255), nullable=False, default="")
    ai_level = Column(String(32), nullable=False, default="待核实")
    science_level = Column(String(32), nullable=False, default="待核实")
    attention = Column(String(32), nullable=False, default="待核实")
    contact = Column(String(32), nullable=False, default="未接触")
    core_direction = Column(String(500), nullable=False, default="")
    dual_judgement = Column(String(120), nullable=False, default="AI 待核实｜科学 待核实")
    contact_record = Column(String(255), nullable=False, default="暂无联系记录")
    internal_review = Column(String(500), nullable=False, default="待补充研判")
    recent_update = Column(String(64), nullable=False, default="")
    next_action = Column(String(500), nullable=False, default="补充公开证据并确认团队信息")
    source = Column(String(64), nullable=False, default="AI4S Daily")
    source_urls = Column(JSON, nullable=False, default=list)
    evidence_summary = Column(Text, nullable=False, default="")
    report_id = Column(String(160), nullable=False, default="")
    report_title = Column(String(255), nullable=False, default="")
    deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, nullable=False, default=_now)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)


class StrategicSyncMetaRow(_Base):
    __tablename__ = "strategic_map_sync_meta"

    key = Column(String(64), primary_key=True)
    value = Column(String(255), nullable=False, default="")
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)


_Base.metadata.create_all(_ENGINE)


class DomainPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=255)


class SubdomainPayload(DomainPayload):
    pass


class TeamStatusPayload(BaseModel):
    """Fields users maintain from the map detail panel.

    The status fields remain required for backwards compatibility with the
    original editor. Detail fields are optional so older clients that only
    update attention/contact continue to work without clearing saved content.
    """

    attention: str = Field(min_length=1, max_length=32)
    contact: str = Field(min_length=1, max_length=32)
    core_direction: str | None = Field(default=None, max_length=500)
    dual_judgement: str | None = Field(default=None, max_length=120)
    contact_record: str | None = Field(default=None, max_length=255)
    internal_review: str | None = Field(default=None, max_length=500)
    recent_update: str | None = Field(default=None, max_length=64)
    next_action: str | None = Field(default=None, max_length=500)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _clean(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _seed_defaults(session: Session) -> None:
    if session.query(StrategicDomainRow).filter(StrategicDomainRow.deleted.is_(False)).count():
        return

    defaults = [
        ("生命科学", "药物、结构与生物计算", ["生物结构", "药物研发", "生物计算"]),
        ("合金材料", "先进材料、工艺与装备", ["高温合金", "轻量化材料", "材料基因组"]),
        ("集成电路", "器件、芯片与设计自动化", ["EDA 智能设计", "先进器件"]),
        ("电力求解器", "电网、能源与复杂系统计算", ["智能调度", "储能优化"]),
        ("航空航天", "飞行器、推进与空间任务", ["飞行器设计", "空间任务规划"]),
        ("其他重点领域", "待扩展的战略观察对象", ["交叉前沿探索"]),
    ]
    for index, (name, description, children) in enumerate(defaults):
        domain = StrategicDomainRow(
            id=_new_id("domain"),
            name=name,
            description=description,
            sort_order=index,
        )
        session.add(domain)
        session.flush()
        for child_index, child_name in enumerate(children):
            session.add(
                StrategicDomainRow(
                    id=_new_id("subdomain"),
                    name=child_name,
                    description="",
                    parent_id=domain.id,
                    sort_order=child_index,
                )
            )
    session.commit()


# A presentation must remain useful when the latest Daily issue contains only
# overseas or generic mentions. These rows are taken from named Chinese
# institutions that appear in public Daily issues; every label explicitly says
# it is a report lead awaiting verification, and each row keeps its source URL
# and evidence summary. They are only restored when a domain has no active
# candidates. Existing records are updated only with the source/evidence and
# classification fields; manually maintained status and detail fields remain
# untouched.
_PRESENTATION_CANDIDATES: dict[str, list[dict[str, str]]] = {
    "生命科学": [
        {
            "name": "清华大学",
            "focus": "计算神经科学与时序预测研究线（报告线索，待核实）",
            "subdomain": "生物计算与基础模型",
            "report": "push-2026-09-17-09-59-13.md",
            "title": "计算神经科学与时序预测：《自然·通讯》刊发预测编码启发的时间序列框架 FGL",
            "evidence": "AI4S Daily 报告提到清华大学相关研究成果；具体团队名称与合作边界仍需核验。",
        },
        {
            "name": "盛京医院",
            "focus": "肝癌 AI 诊断研究线（报告线索，待核实）",
            "subdomain": "医学影像与临床 AI",
            "report": "push-2026-08-25-08-01-17.md",
            "title": "达摩院联合盛京医院研发肝癌 AI 诊断模型 DAMO LiON",
            "evidence": "AI4S Daily 报告明确提到盛京医院参与 DAMO LiON 研究；具体团队和持续合作状态仍需核验。",
        },
        {
            "name": "阿里巴巴达摩院",
            "focus": "DAMO LiON 肝癌 AI 诊断研究线（报告线索，待核实）",
            "subdomain": "医学影像与临床 AI",
            "report": "push-2026-08-25-08-01-17.md",
            "title": "达摩院联合盛京医院研发肝癌 AI 诊断模型 DAMO LiON",
            "evidence": "AI4S Daily 报告明确写明阿里巴巴达摩院与中国医科大学附属盛京医院联合研发 DAMO LiON；具体研发团队边界和持续合作状态仍需核验。",
        },
        {
            "name": "蚂蚁集团 AI 安全实验室",
            "focus": "MedGuard 在线诊疗事实核查研究线（报告线索，待核实）",
            "subdomain": "医学影像与临床 AI",
            "report": "push-2026-08-25-08-01-17.md",
            "title": "蚂蚁集团携手厦门大学研发诊疗事实核查系统 MedGuard",
            "evidence": "AI4S Daily 报告明确提到蚂蚁 AI 安全实验室参与 MedGuard；团队公开组织架构和后续维护边界仍需核验。",
        },
        {
            "name": "厦门大学",
            "focus": "MedGuard 医疗事实核查研究线（报告线索，待核实）",
            "subdomain": "医学影像与临床 AI",
            "report": "push-2026-08-25-08-01-17.md",
            "title": "蚂蚁集团携手厦门大学研发诊疗事实核查系统 MedGuard",
            "evidence": "AI4S Daily 报告明确提到厦门大学参与 MedGuard；具体学院、课题组和合作作者仍需核验。",
        },
        {
            "name": "复旦大学 Neolab",
            "focus": "生命算子统一生命建模研究线（报告线索，待核实）",
            "subdomain": "生物计算与基础模型",
            "report": "push-2026-09-03-17-00-02-week.md",
            "title": "复旦 Neolab 提出生命算子，统一生命建模",
            "evidence": "AI4S Daily 周报明确提到复旦 Neolab 提出生命算子；具体实验室成员、论文署名和项目持续性仍需核验。",
        },
        {
            "name": "中国科学院遗传与发育生物学研究所",
            "focus": "智能育种机器人“吉儿”研究线（报告线索，待核实）",
            "subdomain": "智能育种与农业生物技术",
            "report": "push-2026-09-07-08-00-50.md",
            "title": "中科院发布具身智能育种机器人“吉儿”",
            "evidence": "AI4S Daily 报告明确提到中国科学院遗传与发育生物学研究所团队研发智能育种机器人“吉儿”；具体课题组和成果论文仍需核验。",
        },
        {
            "name": "分子之心",
            "focus": "QuantaMind 反应性机器学习力场研究线（报告线索，待核实）",
            "subdomain": "药物研发与计算化学",
            "report": "push-2026-09-15-08-02-28.md",
            "title": "分子之心反应性机器学习力场框架 QuantaMind 登上 Science Advances",
            "evidence": "AI4S Daily 报告明确提到分子之心团队及 QuantaMind；公司团队公开论文署名和与国内机构合作边界仍需核验。",
        },
    ],
    "合金材料": [
        {
            "name": "中国科学院金属研究所",
            "focus": "先进金属材料研究线（机构线索，待核实）",
            "subdomain": "先进合金与材料设计",
            "url": "https://www.imr.ac.cn/",
            "source": "机构官网（公开线索）",
            "title": "中国科学院金属研究所官方网站",
            "evidence": "机构官网可确认中国科学院金属研究所的机构身份；具体材料团队、项目名称和近期成果需进一步核验。",
        },
        {
            "name": "清华大学",
            "focus": "Galaxy VS 百亿级分子虚拟筛选研究线（报告线索，待核实）",
            "subdomain": "计算化学与分子材料",
            "report": "push-2026-09-09-08-00-57.md",
            "title": "清华大学联合天河超算推出 Galaxy VS",
            "evidence": "AI4S Daily 报告明确提到清华大学与天河超算团队联合发布 Galaxy VS；具体实验室和材料领域应用边界仍需核验。",
        },
        {
            "name": "天河超算团队",
            "focus": "Galaxy VS 分子对接与科学计算研究线（报告线索，待核实）",
            "subdomain": "计算化学与分子材料",
            "report": "push-2026-09-09-08-00-57.md",
            "title": "清华大学联合天河超算推出 Galaxy VS",
            "evidence": "AI4S Daily 报告明确提到天河超算团队参与 Galaxy VS；具体运行单位、团队名称和贡献边界仍需核验。",
        },
    ],
    "集成电路": [
        {
            "name": "字节跳动",
            "focus": "Seed 团队：科学智能闭环评测研究线（报告线索，待核实）",
            "subdomain": "科学智能与芯片系统",
            "report": "push-2026-09-16-08-01-19.md",
            "title": "科学智能通用底座：字节跳动联合高校推出自进化闭环评测三部曲",
            "evidence": "AI4S Daily 报告明确提到字节跳动 Seed 团队；与集成电路领域的直接关联仍需核验。",
        },
        {
            "name": "北京邮电大学",
            "focus": "Channel-Diff 物理引导无线信道预测研究线（报告线索，待核实）",
            "subdomain": "无线通信与芯片系统",
            "report": "push-2026-09-13-08-01-55.md",
            "title": "清华大学联合北邮提出 Channel-Diff",
            "evidence": "AI4S Daily 报告明确提到北京邮电大学团队参与 Channel-Diff；具体学院、课题组和与集成电路的应用边界仍需核验。",
        },
    ],
    "电力求解器": [
        {
            "name": "华为 2012 实验室",
            "focus": "科研智能体与复杂系统计算研究线：openJiuwen（报告线索，待核实）",
            "subdomain": "电力系统智能求解",
            "report": "push-2026-09-12-08-01-14.md",
            "title": "华为开源 openJiuwen 推出科研双维度 RSI 递归自我改进框架",
            "evidence": "AI4S Daily 报告明确提到华为 2012 实验室；电力系统应用场景和具体团队仍需核验。",
        },
    ],
    "航空航天": [
        {
            "name": "中国科学院",
            "focus": "天问三号智能遥感研究线（报告线索，待核实）",
            "subdomain": "空间遥感与飞行器系统",
            "report": "push-2026-09-04-08-01-01.md",
            "title": "我国推进全球首版智能化火星地质图编制",
            "evidence": "AI4S Daily 报告提到中国科学院参与相关火星地质图工作；具体研究所与团队仍需核验。",
        },
    ],
    "其他重点领域": [
        {
            "name": "上海人工智能实验室",
            "focus": "科学智能闭环评测研究线（报告线索，待核实）",
            "subdomain": "科学智能基础设施",
            "report": "push-2026-09-16-08-01-19.md",
            "title": "科学智能通用底座：字节跳动联合高校推出自进化闭环评测三部曲",
            "evidence": "AI4S Daily 报告提到上海人工智能实验室相关工作；具体团队与项目边界仍需核验。",
        },
        {
            "name": "中国科学技术大学",
            "focus": "420 公里光纤两端量子存储器纠缠研究线（报告线索，待核实）",
            "subdomain": "量子信息与量子计算",
            "report": "push-2026-08-20-08-01-09.md",
            "title": "中国科大实现 420 公里光纤两端量子存储器纠缠",
            "evidence": "AI4S Daily 报告明确提到中国科学技术大学团队完成 420 公里光纤两端量子存储器纠缠实验；具体实验室和论文署名仍需核验。",
        },
        {
            "name": "清华大学与北京邮电大学联合团队",
            "focus": "Channel-Diff 物理引导无线信道预测研究线（报告线索，待核实）",
            "subdomain": "无线通信与芯片系统",
            "report": "push-2026-09-13-08-01-55.md",
            "title": "清华大学联合北邮提出 Channel-Diff",
            "evidence": "AI4S Daily 报告明确提到清华大学与北京邮电大学团队提出 Channel-Diff；公开资料未给出统一团队名称，故保留联合团队线索并标注待核实。",
        },
    ],
}

# A strategic map is useful for comparison only when every domain has a
# visible Top 8. These are named domestic institutions/teams that are
# relevant to each domain; they are deliberately presented as public leads
# awaiting team-level verification rather than as asserted collaborations.
_MINIMUM_CANDIDATES: dict[str, list[tuple[str, str]]] = {
    "合金材料": [
        ("北京科技大学", "先进合金与材料设计"),
        ("哈尔滨工业大学", "极端环境与轻量化材料"),
        ("上海交通大学", "材料基因组与智能制造"),
        ("西北工业大学", "航空轻质材料"),
        ("中国科学院物理研究所", "材料计算与凝聚态"),
    ],
    "集成电路": [
        ("清华大学", "科学智能与芯片系统"),
        ("北京大学", "EDA 智能设计"),
        ("中国科学院微电子研究所", "先进器件与制造"),
        ("上海交通大学", "EDA 智能设计"),
        ("华中科技大学", "先进器件与制造"),
        ("中国电子科技集团第五十五研究所", "先进器件与制造"),
    ],
    "电力求解器": [
        ("中国电力科学研究院", "电网智能调度"),
        ("清华大学", "电力系统智能求解"),
        ("华北电力大学", "电网智能调度"),
        ("浙江大学", "储能优化"),
        ("中国科学院电工研究所", "储能优化"),
        ("南方电网科学研究院", "电网智能调度"),
        ("国家电网有限公司", "电网智能调度"),
    ],
    "航空航天": [
        ("北京航空航天大学", "飞行器设计"),
        ("哈尔滨工业大学", "空间任务规划"),
        ("西北工业大学", "飞行器设计"),
        ("中国航天科技集团", "飞行器设计"),
        ("中国航天科工集团", "空间任务规划"),
        ("中国科学院国家空间科学中心", "空间任务规划"),
        ("中国科学院空天信息创新研究院", "空间遥感与飞行器系统"),
    ],
    "其他重点领域": [
        ("北京大学", "交叉前沿探索"),
        ("浙江大学", "科学智能基础设施"),
        ("中国科学院自动化研究所", "AI 基础模型"),
        ("鹏城实验室", "科学智能基础设施"),
        ("之江实验室", "AI 基础模型"),
    ],
}

_OFFICIAL_SOURCE_URLS: dict[str, str] = {
    "北京科技大学": "https://www.ustb.edu.cn/",
    "哈尔滨工业大学": "https://www.hit.edu.cn/",
    "上海交通大学": "https://www.sjtu.edu.cn/",
    "西北工业大学": "https://www.nwpu.edu.cn/",
    "中国科学院物理研究所": "https://www.iop.cas.cn/",
    "清华大学": "https://www.tsinghua.edu.cn/",
    "北京大学": "https://www.pku.edu.cn/",
    "中国科学院微电子研究所": "https://ime.cas.cn/",
    "华中科技大学": "https://www.hust.edu.cn/",
    "中国电子科技集团第五十五研究所": "https://www.cetc.com.cn/",
    "中国电力科学研究院": "https://www.epri.sgcc.com.cn/",
    "华北电力大学": "https://www.ncepu.edu.cn/",
    "浙江大学": "https://www.zju.edu.cn/",
    "中国科学院电工研究所": "https://www.iee.cas.cn/",
    "南方电网科学研究院": "https://www.csg.cn/",
    "国家电网有限公司": "https://www.sgcc.com.cn/",
    "北京航空航天大学": "https://www.buaa.edu.cn/",
    "中国航天科技集团": "https://www.spacechina.com/",
    "中国航天科工集团": "https://www.casic.com.cn/",
    "中国科学院国家空间科学中心": "https://nssc.cas.cn/",
    "中国科学院空天信息创新研究院": "https://aircas.cas.cn/",
    "中国科学院自动化研究所": "https://www.ia.cas.cn/",
    "鹏城实验室": "https://www.pcl.ac.cn/",
    "之江实验室": "https://www.zhejianglab.com/",
}


def _presentation_candidates(domain_name: str) -> list[dict[str, str]]:
    """Return source-backed rows plus enough named leads for a Top 8 view."""
    candidates = [dict(item) for item in _PRESENTATION_CANDIDATES.get(domain_name, [])]
    names = {item.get("name", "") for item in candidates}
    minimum = _MINIMUM_CANDIDATES.get(domain_name)
    if minimum is None:
        generic_leads = [
            "清华大学", "北京大学", "中国科学院", "上海交通大学",
            "浙江大学", "复旦大学", "哈尔滨工业大学", "中国科学技术大学",
        ]
        minimum = [(name, f"{domain_name}研究") for name in generic_leads]
    for name, subdomain in minimum:
        if name in names:
            continue
        candidates.append({
            "name": name,
            "focus": f"{subdomain}机构研究线（机构公开线索，待核实）",
            "subdomain": subdomain,
            "url": _OFFICIAL_SOURCE_URLS.get(name, "https://ai4s-frontier.github.io/AI4S-Daily-HTML"),
            "source": "机构官网（公开线索）",
            "title": f"{name}公开科研方向与{domain_name}关联线索",
            "evidence": f"公开机构官网可确认{name}的机构身份；该条仅作为{domain_name}候选线索，具体团队、项目与近期成果需进一步核验。",
        })
    return candidates


def _seed_presentation_candidates(session: Session) -> None:
    """Ensure the auditable presentation candidates are present for each domain.

    The first implementation only seeded an empty domain. That made the map
    look sparse forever once a single candidate had been stored. We now add
    missing, source-backed rows alongside existing rows while preserving any
    user-maintained status/detail fields.
    """
    roots = _domain_query(session)
    changed = False
    for domain in roots:
        subdomains = _domain_query(session, domain.id)
        for candidate in _presentation_candidates(domain.name):
            requested_subdomain = _clean(candidate.get("subdomain"))
            subdomain = next(
                (item for item in subdomains if item.name.casefold() == requested_subdomain.casefold()),
                None,
            ) if requested_subdomain else None
            if requested_subdomain and subdomain is None:
                subdomain = StrategicDomainRow(
                    id=_new_id("subdomain"),
                    name=requested_subdomain,
                    description="由候选团队来源线索归类，待核实",
                    parent_id=domain.id,
                    sort_order=len(subdomains),
                )
                session.add(subdomain)
                session.flush()
                subdomains.append(subdomain)
            row = session.query(StrategicTeamRow).filter(
                StrategicTeamRow.domain_id == domain.id,
                StrategicTeamRow.name == candidate["name"],
            ).first()
            report_name = candidate.get("report", "")
            report_url = candidate.get("url") or _daily_url(report_name)
            report_date_match = re.search(r"20\d{2}-\d{2}-\d{2}", report_name)
            report_date = report_date_match.group(0) if report_date_match else ""
            if not row:
                row = StrategicTeamRow(
                    id=_candidate_id(domain.id, candidate["name"]),
                    domain_id=domain.id,
                    subdomain_id=subdomain.id if subdomain else None,
                    name=candidate["name"],
                    ai_level="待核实",
                    science_level="待核实",
                    attention="待核实",
                    contact="未接触",
                    contact_record="暂无联系记录",
                    core_direction=candidate["focus"],
                    dual_judgement="AI 待核实｜科学 待核实",
                    internal_review=candidate["evidence"],
                    recent_update=report_date,
                    next_action="核验具体团队、代表成果、依托单位与联系状态",
                )
                session.add(row)
            else:
                row.deleted = False
                row.focus = candidate["focus"]
                if subdomain:
                    row.subdomain_id = subdomain.id
                if not row.core_direction:
                    row.core_direction = candidate["focus"]
                if not row.recent_update:
                    row.recent_update = report_date
                if not row.internal_review or row.internal_review.startswith("AI4S Daily 公开报告命中"):
                    row.internal_review = candidate["evidence"]
            row.focus = candidate["focus"]
            if subdomain:
                row.subdomain_id = subdomain.id
            row.source = candidate.get("source") or "AI4S Daily"
            row.source_urls = [report_url]
            row.evidence_summary = candidate["evidence"]
            row.report_id = report_name.removesuffix(".md")
            row.report_title = candidate["title"]
            row.updated_at = _now()
            changed = True
    if changed:
        session.commit()
    _repair_incomplete_team_dates(session)


def _domain_query(session: Session, parent_id: str | None = None) -> list[StrategicDomainRow]:
    query = session.query(StrategicDomainRow).filter(
        StrategicDomainRow.deleted.is_(False),
        StrategicDomainRow.parent_id == parent_id,
    )
    return query.order_by(StrategicDomainRow.sort_order.asc(), StrategicDomainRow.created_at.asc()).all()


def _domain_to_dict(session: Session, domain: StrategicDomainRow) -> dict[str, Any]:
    # A selectable subdomain must have at least one current candidate. Empty
    # buckets make the map look artificial and can never produce a useful
    # filtered view, so hide them from the navigation payload.
    children = [
        child for child in _domain_query(session, domain.id)
        if session.query(StrategicTeamRow.id).filter(
            StrategicTeamRow.subdomain_id == child.id,
            StrategicTeamRow.deleted.is_(False),
        ).first()
    ]
    return {
        "id": domain.id,
        "name": domain.name,
        "label": domain.name,
        "description": domain.description,
        "parentId": domain.parent_id,
        "subdomains": [
            {
                "id": child.id,
                "name": child.name,
                "label": child.name,
                "description": child.description,
                "parentId": child.parent_id,
            }
            for child in children
        ],
    }


def _team_to_dict(team: StrategicTeamRow) -> dict[str, Any]:
    # 兼容已有数据库字段，同时提供更清晰的机构/团队字段给前端展示。
    return {
        "id": team.id,
        "domainId": team.domain_id,
        "subdomainId": team.subdomain_id,
        "name": team.name,
        "organization": team.name,
        "teamName": team.focus or "相关团队线索",
        "focus": team.focus,
        "aiLevel": team.ai_level,
        "scienceLevel": team.science_level,
        "attention": team.attention,
        "contact": team.contact,
        "coreDirection": team.core_direction,
        "dualJudgement": team.dual_judgement,
        "contactRecord": team.contact_record,
        "internalReview": team.internal_review,
        "recentUpdate": team.recent_update,
        "nextAction": team.next_action,
        "source": team.source,
        "sourceUrls": list(team.source_urls or []),
        "evidenceSummary": team.evidence_summary,
        "reportId": team.report_id,
        "reportTitle": team.report_title,
        "updatedAt": team.updated_at.isoformat() if team.updated_at else "",
    }


def _response(data: Any, **extra: Any) -> dict[str, Any]:
    return {"code": 200, "data": data, **extra}


def _get_domain(session: Session, domain_id: str) -> StrategicDomainRow:
    row = session.query(StrategicDomainRow).filter(
        StrategicDomainRow.id == domain_id,
        StrategicDomainRow.deleted.is_(False),
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="领域不存在")
    return row


def _get_root_domain(session: Session, domain_id: str) -> StrategicDomainRow:
    row = _get_domain(session, domain_id)
    if row.parent_id:
        row = _get_domain(session, row.parent_id)
    return row


def _daily_url(file_name: str) -> str:
    return f"{_DAILY_BASE_URL}/reports/{file_name}"


def _parse_frontmatter(markdown: str) -> dict[str, str]:
    if not markdown.startswith("---"):
        return {}
    end = markdown.find("\n---", 3)
    if end < 0:
        return {}
    values: dict[str, str] = {}
    for line in markdown[3:end].splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        value = value.strip().strip('"').strip("'")
        values[key.strip()] = value
    return values


def _report_date(fields: dict[str, str], file_name: str) -> str:
    value = fields.get("date") or fields.get("pushDate") or fields.get("pushTime")
    if value:
        return value[:10]
    match = re.search(r"(20\d{2}-\d{2}-\d{2})", file_name)
    return match.group(1) if match else ""


def _repair_incomplete_team_dates(session: Session) -> bool:
    """Repair dates truncated by an earlier seed without touching real edits.

    Older presentation rows were written with values such as ``2026-`` when
    the report filename parser only captured the year prefix.  Once a complete
    report id or source URL is available we can safely restore the full ISO
    date.  A normal user-entered date is left untouched.
    """
    changed = False
    incomplete = re.compile(r"^20\d{2}(?:-\d{0,2})?$")
    for team in session.query(StrategicTeamRow).filter(
        StrategicTeamRow.deleted.is_(False),
        StrategicTeamRow.recent_update.isnot(None),
    ).all():
        current = _clean(team.recent_update)
        if not current or not incomplete.fullmatch(current):
            continue
        candidates = [team.report_id or "", *(team.source_urls or [])]
        repaired = next(
            (match.group(1) for value in candidates
             if (match := re.search(r"(20\d{2}-\d{2}-\d{2})", str(value)))),
            "",
        )
        if repaired and repaired != current:
            team.recent_update = repaired
            team.updated_at = _now()
            changed = True
    if changed:
        session.commit()
    return changed


def _split_reports(markdown: str) -> Iterable[tuple[str, str]]:
    headings = list(re.finditer(r"^###\s+(.+?)\s*$", markdown, re.MULTILINE))
    for index, heading in enumerate(headings):
        start = heading.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        title = re.sub(r"^[^\w\u4e00-\u9fff]+", "", heading.group(1)).strip()
        body = markdown[start:end].strip()
        if title and body:
            yield title, body


def _keywords(domain: StrategicDomainRow, subdomains: list[StrategicDomainRow]) -> list[str]:
    values = [domain.name, *(child.name for child in subdomains)]
    aliases = {
        "生命科学": ["生命科学", "生物", "药物", "蛋白", "基因组", "医学"],
        "合金材料": ["合金", "材料", "高熵", "材料基因组"],
        "集成电路": ["集成电路", "芯片", "EDA", "器件", "半导体"],
        "电力求解器": ["电力", "电网", "储能", "能源", "调度"],
        "航空航天": ["航空", "航天", "飞行器", "空间任务", "推进"],
    }
    values.extend(aliases.get(domain.name, []))
    return list(dict.fromkeys(item.lower() for item in values if item.strip()))


def _extract_urls(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"https?://[^\s)\]>]+", text)))[:12]


_FOREIGN_ENTITY_MARKERS = (
    "google",
    "deepmind",
    "anthropic",
    "nvidia",
    "openai",
    "stanford",
    "mit",
    "nasa",
    "ibm",
    "berkeley",
    "brookhaven",
    "argonne",
    "fermi",
    "oak ridge",
    "university of",
    "美国",
    "英国",
    "加拿大",
    "澳大利亚",
    "新西兰",
    "日本",
    "韩国",
    "新加坡",
    "德国",
    "法国",
    "瑞士",
    "荷兰",
    "以色列",
    "伯克利",
    "布鲁克海文",
    "阿贡",
    "费米",
    "橡树岭",
    "斯坦福",
    "麻省理工",
    "哈佛",
    "剑桥",
    "牛津",
    # Daily reports frequently use Chinese transliterations or a short name
    # instead of the English institution name. Keep both forms here so these
    # records cannot pass the Chinese-character check below.
    "stowers",
    "斯托尔斯",
    "broad institute",
    "博德研究所",
    "exeter",
    "埃克塞特",
    "曼彻斯特",
    "manchester",
    "夏威夷大学",
    "麻省总医院",
    "洛斯阿拉莫斯",
    "慕尼黑",
    "巴塞尔大学",
    "rosalind",
    "罗斯林德",
    "加州大学",
    "密歇根大学",
    "普林斯顿",
    "康奈尔",
    "哥伦比亚大学",
    "华盛顿大学",
    "宾夕法尼亚大学",
    "宾夕法尼亚",
    "宾大",
    "芝加哥大学",
    "加州理工",
    "南加州大学",
    "纽约大学",
    "耶鲁",
    "威斯康星大学",
    "德州大学",
    "多伦多大学",
    "麦吉尔大学",
    "苏黎世联邦理工",
    "洛桑联邦理工",
    "爱丁堡大学",
    "帝国理工",
    "东京大学",
    "苹果公司",
    "苹果",
    "亚马逊",
    "脸书",
    "meta",
    "syensqo",
    "latent labs",
    "langchain",
    "deep life sci",
    "inherent",
    "hirsch",
    "microsoft",
    "微软",
    "英伟达",
    "特文特",
    "twente",
    "宾州",
    "宾夕法尼亚州立大学",
    "penn state",
    "anima",
    "understanding",
    "discovered materials",
    "materials",
    "laboratory",
    "laboratories",
    "ated understanding",
)


# A report section may mention a team or laboratory without naming it. Those
# phrases are useful evidence text but are not identifiable candidate records.
_GENERIC_ENTITY_MARKERS = (
    "ai4s daily",
    "研究团队",
    "科研团队",
    "研发团队",
    "计算生物学团队",
    "环境科学系团队",
    "初创团队",
    "相关团队",
    "团队线索",
    "学术机构",
    "科研机构",
    "头部机构",
    "前沿机构",
    "国家实验室",
    "国家级实验室",
    "家国家实验室",
    "多家国家实验室",
    "其他三家",
    "前沿闭源实验室",
    "头部实验室",
    "自主实验室",
    "湿实验室",
    "自动化实验室",
    "机器人实验室",
    "自驱实验室",
    "自驱动实验室",
    "改变了过去",
    "内部实验室",
    "顶尖医学机构",
    "实验室",
    "蛋白质从头设计公司",
    "ai 公司",
    "ai公司",
    "数据中心",
    "超算中心",
    "支持子智能体",
    "无中心",
    "并首度向",
    "通过衍生初创公司",
    "利用人形机器人",
    "在包含",
    "并在",
    "已通过",
    "加速",
    "大幅",
    "面对",
    "旨在",
    "牵头",
    "表明",
    "该进展",
    "为自动化",
    "发出可按需自主部署",
    "科研团队最新研发",
    "材料科学团队",
    "模型仍不擅长",
    "并指导实验室",
    "并指导",
)


def _strip_leading_number(value: str) -> str:
    """Remove report list numbering (including keycap emoji) from labels."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    # 1., 1)、1️⃣, ❶ and similar prefixes are formatting, not entity names.
    return re.sub(
        r"^\s*(?:(?:\d{1,3})(?:\ufe0f?\u20e3)?|[①②③④⑤⑥⑦⑧⑨⑩❶❷❸❹❺❻❼❽❾❿])"
        r"\s*(?:[.．、:：)）\-]\s*)?",
        "",
        text,
    ).strip()


def _normalise_entity_name(value: str) -> str:
    return _strip_leading_number(value).strip(" -*#\t")


def _normalise_section_title(value: str) -> str:
    """Keep the section title useful while dropping its list number."""
    return _strip_leading_number(value)


def _is_generic_entity(name: str) -> bool:
    normalized = re.sub(r"\s+", " ", name or "").strip().lower()
    if not normalized:
        return True
    # A bare suffix with a list number ("团队 1", "实验室 2") is a
    # placeholder, not an identifiable institution. Keep named entities such
    # as "上海人工智能实验室" because they contain a meaningful prefix.
    if re.fullmatch(r"(?:团队|研究团队|科研团队|机构|中心|实验室)(?:\s*[a-z0-9一二三四五六七八九十]+)?", normalized):
        return True
    if normalized == "华为":
        return True
    # ``实验室`` by itself is generic, while named labs such as 上海人工智能
    # 实验室、鹏城实验室 and 之江实验室 are valid institutions.
    if normalized == "实验室":
        return True
    return any(marker != "实验室" and marker in normalized for marker in _GENERIC_ENTITY_MARKERS)


def _is_china_entity(name: str) -> bool:
    """只接受中国机构/团队线索，避免海外候选混入国内战略图谱。"""
    normalized = re.sub(r"\s+", " ", name or "").strip()
    if not normalized or not re.search(r"[\u4e00-\u9fff]", normalized):
        return False
    lowered = normalized.lower()
    return not any(marker.lower() in lowered for marker in _FOREIGN_ENTITY_MARKERS)


def _extract_entities(text: str) -> list[str]:
    """从 Daily 原文提取中国机构/团队线索，保留原文可追溯性。"""
    candidates: list[str] = []
    segments = re.split(r"[。；;，,、|：:()（）\n]|联合|与|和|及|以及|携手|合作|共同", text)
    cn_suffix = r"(?:大学|学院|科学院|研究院|研究所|实验室|中心|医院|公司|集团|团队|研究组|课题组|机构|基地)"
    for segment in segments:
        value = segment.strip(" -*#\t")
        for match in re.finditer(rf"([\u4e00-\u9fffA-Za-z0-9][\u4e00-\u9fffA-Za-z0-9·&.'’\- ]{{1,18}}?{cn_suffix})", value):
            name = re.sub(r"^(?:联合|来自|由|与|和|等)\s*", "", match.group(1)).strip()
            name = _normalise_entity_name(name)
            if (
                2 <= len(name) <= 26
                and not _is_generic_entity(name)
                and not re.search(r"(?:利用|旨在|打造|实现|发布|推出|通过|开展|支持|自动化实验室$)", name)
            ):
                if _is_china_entity(name):
                    candidates.append(name)

    known = [
        "中国科学院",
        "中国工程院",
        "中国科学院大学",
        "清华大学",
        "北京大学",
        "上海交通大学",
        "浙江大学",
        "中国科学技术大学",
        "复旦大学",
        "南京大学",
        "哈尔滨工业大学",
        "华中科技大学",
        "西安交通大学",
        "北京航空航天大学",
        "北京理工大学",
        "上海人工智能实验室",
        "鹏城实验室",
        "之江实验室",
        "国家超级计算天津中心",
        "国家并行计算机工程技术研究中心",
    ]
    lowered = text.lower()
    candidates.extend(name for name in known if name.lower() in lowered)
    cleaned: list[str] = []
    for name in candidates:
        name = _normalise_entity_name(name)
        if (
            _is_china_entity(name)
            and not _is_generic_entity(name)
            and name not in cleaned
            and len(name) <= 36
        ):
            cleaned.append(name)
    return cleaned[:10]


def _purge_non_china_teams(session: Session) -> None:
    """软删除历史同步中遗留的海外、泛化或无法确认归属的候选。

    The map database is intentionally kept across weekly refreshes.  A filter
    that only runs while extracting new reports would therefore leave stale
    foreign/generic cards visible forever; apply the same quality gate to
    existing rows whenever the map is loaded or refreshed.
    """
    rows = session.query(StrategicTeamRow).filter(StrategicTeamRow.deleted.is_(False)).all()
    changed = False
    for team in rows:
        normalized_name = _normalise_entity_name(team.name)
        normalized_focus = _normalise_section_title(team.focus)
        if (
            not _is_china_entity(normalized_name)
            or _is_generic_entity(normalized_name)
            # A candidate extracted from a foreign-only report section is not
            # a domestic team even when its short name happens to contain a
            # Chinese word (for example “James Zou 团队”).
            or (normalized_focus and not _is_china_entity(normalized_focus))
        ):
            team.deleted = True
            changed = True
            continue
        if normalized_name != team.name:
            team.name = normalized_name
            changed = True
        if normalized_focus != team.focus:
            team.focus = normalized_focus
            changed = True
    if changed:
        session.commit()


def _load_daily_reports(keywords: list[str], limit: int = 24) -> list[dict[str, Any]]:
    try:
        response = requests.get(f"{_DAILY_BASE_URL}/reports/index.json", timeout=12)
        response.raise_for_status()
        file_names = response.json()
        if isinstance(file_names, dict):
            file_names = file_names.get("reports") or file_names.get("files") or []
        if not isinstance(file_names, list):
            return []
    except Exception:
        return []

    reports: list[dict[str, Any]] = []
    for file_name in [item for item in file_names if isinstance(item, str)][:limit]:
        try:
            response = requests.get(_daily_url(file_name), timeout=12)
            response.raise_for_status()
            markdown = response.text
        except Exception:
            continue
        fields = _parse_frontmatter(markdown)
        haystack = markdown.lower()
        if keywords and not any(keyword in haystack for keyword in keywords):
            continue
        sections = list(_split_reports(markdown))
        matching_sections = [
            (title, body)
            for title, body in sections
            if not keywords or any(keyword in f"{title}\n{body}".lower() for keyword in keywords)
        ]
        if not matching_sections:
            matching_sections = sections[:3]
        reports.append(
            {
                "id": file_name.removesuffix(".md"),
                "title": _clean(fields.get("title"), file_name),
                "date": _report_date(fields, file_name),
                "url": _daily_url(file_name),
                "sections": matching_sections,
            }
        )
    return reports


def _candidate_id(domain_id: str, name: str) -> str:
    digest = hashlib.sha1(f"{domain_id}:{name}".encode("utf-8")).hexdigest()[:24]
    return f"team_{digest}"


def _sync_domain(session: Session, domain: StrategicDomainRow) -> dict[str, Any]:
    subdomains = _domain_query(session, domain.id)
    reports = _load_daily_reports(_keywords(domain, subdomains))
    entities: dict[str, dict[str, Any]] = {}
    for report in reports:
        for section_title, section_body in report["sections"]:
            normalized_title = _normalise_section_title(section_title)
            content = f"{section_title}\n{section_body}"
            extracted = _extract_entities(content)
            # 章节标题经常描述技术方向，未必包含中国机构名称；只要正文
            # 能抽出明确的中国机构/团队，就保留该证据。候选本身仍经过
            # _is_china_entity/_is_generic_entity 双重过滤，海外或泛化线索
            # 不会因为同一章节出现中文而进入候选池。
            if not extracted:
                continue
            urls = [report["url"], *_extract_urls(content)]
            for name in extracted:
                key = name.lower()
                item = entities.setdefault(
                    key,
                    {
                        "name": name,
                        "focus": normalized_title,
                        "summary": re.sub(r"\s+", " ", section_body).strip()[:500],
                        "urls": list(dict.fromkeys(urls)),
                        "report": report,
                        "subdomain_id": next(
                            (
                                child.id
                                for child in subdomains
                                if child.name.lower() in content.lower()
                            ),
                            None,
                        ),
                    },
                )
                item["urls"] = list(dict.fromkeys([*item["urls"], *urls]))[:12]

    # 每次刷新只同步当前领域的真实 Daily 线索；用户在页面维护的状态字段保留。
    for candidate in list(entities.values())[:24]:
        name = candidate["name"]
        existing = session.query(StrategicTeamRow).filter(
            StrategicTeamRow.domain_id == domain.id,
            StrategicTeamRow.name == name,
        ).first()
        is_new = existing is None
        if not existing:
            existing = StrategicTeamRow(
                id=_candidate_id(domain.id, name),
                domain_id=domain.id,
                name=name,
                ai_level="较高",
                science_level="较高",
                attention="待核实",
                contact="未接触",
                contact_record="暂无联系记录",
            )
            session.add(existing)
        elif existing.deleted:
            # Candidate IDs are deterministic.  A previous weekly refresh may
            # have soft-deleted a low-quality row; if a later report contains
            # the same now-valid name, reuse and reactivate that row instead of
            # inserting the same primary key a second time.
            existing.deleted = False
        existing.focus = _clean(candidate["focus"], domain.name)
        existing.subdomain_id = candidate.get("subdomain_id")
        # Detail fields can be edited from the map. Keep saved values during
        # subsequent Daily refreshes; initialize them only for new candidates.
        if is_new:
            existing.core_direction = _clean(candidate["focus"], domain.name)
        if is_new:
            existing.dual_judgement = f"AI {existing.ai_level}｜科学 {existing.science_level}"
        if is_new:
            existing.internal_review = "AI4S Daily 公开报告命中，需补充团队代表成果和国内关联证据"
        if is_new:
            existing.recent_update = _clean(candidate["report"]["date"], "近期")
        if is_new:
            existing.next_action = "核验团队代表成果、依托单位与联系状态，确认是否纳入重点名单"
        existing.source = "AI4S Daily"
        existing.source_urls = candidate["urls"]
        existing.evidence_summary = candidate["summary"]
        existing.report_id = candidate["report"]["id"]
        existing.report_title = candidate["report"]["title"]
        existing.updated_at = _now()
    session.commit()
    return {
        "provider": "AI4S Daily",
        "reportCount": len(reports),
        "candidateCount": len(entities),
        "updatedAt": _now().isoformat(),
    }


# Strategic map data is refreshed once a week.  The scheduler still wakes up
# hourly so a restarted process can pick up the next due refresh promptly,
# while the persisted timestamp prevents duplicate refreshes across workers.
_AUTO_REFRESH_INTERVAL_SECONDS = 7 * 24 * 60 * 60


def _auto_refresh_due(session: Session) -> bool:
    meta = session.query(StrategicSyncMetaRow).filter(
        StrategicSyncMetaRow.key == "last_auto_refresh"
    ).first()
    if not meta or not meta.value:
        return True
    try:
        last = datetime.fromisoformat(meta.value)
    except ValueError:
        return True
    return (_now() - last).total_seconds() >= _AUTO_REFRESH_INTERVAL_SECONDS


def _run_scheduled_refresh() -> None:
    """后台定时刷新：每周运行一次。"""
    with _SESSION_FACTORY() as session:
        _seed_defaults(session)
        _seed_presentation_candidates(session)
        _purge_non_china_teams(session)
        if not _auto_refresh_due(session):
            return
        domains = _domain_query(session)
        successful = 0
        for domain in domains:
            try:
                _sync_domain(session, domain)
                successful += 1
            except Exception:
                # 单个领域失败不阻塞其它领域；下一轮仍会重试。
                session.rollback()
        if successful:
            meta = session.query(StrategicSyncMetaRow).filter(
                StrategicSyncMetaRow.key == "last_auto_refresh"
            ).first()
            if not meta:
                meta = StrategicSyncMetaRow(key="last_auto_refresh")
                session.add(meta)
            meta.value = _now().isoformat()
            meta.updated_at = _now()
            session.commit()


def start_strategic_map_scheduler() -> None:
    """启动单个守护线程；FastAPI 多 worker 时每个进程仍会安全地按 DB 时间戳退避。"""
    import threading
    import time

    def loop() -> None:
        while True:
            try:
                _run_scheduled_refresh()
            except Exception:
                # 后台任务不能影响 HTTP 服务生命周期。
                pass
            time.sleep(60 * 60)

    thread = threading.Thread(target=loop, name="strategic-map-weekly-refresh", daemon=True)
    thread.start()


@router.get("")
def get_strategic_map(
    refresh: bool = Query(False),
    domain_id: str | None = Query(None),
) -> dict[str, Any]:
    with _SESSION_FACTORY() as session:
        _seed_defaults(session)
        _seed_presentation_candidates(session)
        _purge_non_china_teams(session)
        roots = _domain_query(session)
        target = _get_domain(session, domain_id) if domain_id else (roots[0] if roots else None)
        source: dict[str, Any] = {"provider": "AI4S Daily", "refreshed": False}
        if refresh and target:
            source = _sync_domain(session, _get_root_domain(session, target.id))
            source["refreshed"] = True
            # A refresh can return fewer than eight auditable rows when the
            # latest reports are sparse. Keep the Top 8 floor deterministic.
            _seed_presentation_candidates(session)
        domains = [_domain_to_dict(session, domain) for domain in _domain_query(session)]
        teams = [
            _team_to_dict(team)
            for team in session.query(StrategicTeamRow)
            .filter(StrategicTeamRow.deleted.is_(False))
            .order_by(StrategicTeamRow.updated_at.desc())
            .all()
        ]
        return _response({"domains": domains, "teams": teams, "source": source})


@router.get("/domains")
def list_domains() -> dict[str, Any]:
    # This endpoint calls the handler directly rather than going through
    # FastAPI's dependency resolution.  Passing an explicit ``None`` for
    # ``domain_id`` is therefore important: the default value is
    # ``Query(None)`` and would otherwise be handed to SQLAlchemy as a query
    # object, producing ``Error binding parameter ... type 'Query'``.
    return get_strategic_map(refresh=False, domain_id=None)


@router.get("/domains/{domain_id}")
def get_domain(domain_id: str) -> dict[str, Any]:
    with _SESSION_FACTORY() as session:
        _seed_defaults(session)
        return _response(_domain_to_dict(session, _get_domain(session, domain_id)))


@router.post("/domains")
def create_domain(payload: DomainPayload) -> dict[str, Any]:
    with _SESSION_FACTORY() as session:
        _seed_defaults(session)
        if session.query(StrategicDomainRow).filter(
            StrategicDomainRow.parent_id.is_(None),
            StrategicDomainRow.name == payload.name.strip(),
            StrategicDomainRow.deleted.is_(False),
        ).first():
            raise HTTPException(status_code=409, detail="领域名称已存在")
        order = session.query(StrategicDomainRow).filter(
            StrategicDomainRow.parent_id.is_(None), StrategicDomainRow.deleted.is_(False)
        ).count()
        row = StrategicDomainRow(id=_new_id("domain"), name=payload.name.strip(), description=payload.description.strip(), sort_order=order)
        session.add(row)
        session.commit()
        # 新领域首次保存时立即完成一次研判，后续由页面按钮或后台周期任务更新。
        source = _sync_domain(session, row)
        _seed_presentation_candidates(session)
        return _response({"domain": _domain_to_dict(session, row), "source": source})


@router.put("/domains/{domain_id}")
def update_domain(domain_id: str, payload: DomainPayload) -> dict[str, Any]:
    with _SESSION_FACTORY() as session:
        row = _get_domain(session, domain_id)
        if row.parent_id:
            raise HTTPException(status_code=400, detail="子领域请使用子领域接口修改")
        duplicate = session.query(StrategicDomainRow).filter(
            StrategicDomainRow.id != row.id,
            StrategicDomainRow.parent_id.is_(None),
            StrategicDomainRow.name == payload.name.strip(),
            StrategicDomainRow.deleted.is_(False),
        ).first()
        if duplicate:
            raise HTTPException(status_code=409, detail="领域名称已存在")
        row.name = payload.name.strip()
        row.description = payload.description.strip()
        row.updated_at = _now()
        session.commit()
        return _response(_domain_to_dict(session, row))


@router.delete("/domains/{domain_id}")
def delete_domain(domain_id: str) -> dict[str, Any]:
    with _SESSION_FACTORY() as session:
        row = _get_domain(session, domain_id)
        row.deleted = True
        for child in _domain_query(session, row.id):
            child.deleted = True
        for team in session.query(StrategicTeamRow).filter(StrategicTeamRow.domain_id == row.id).all():
            team.deleted = True
        session.commit()
        return _response({"id": domain_id, "deleted": True})


@router.post("/domains/{domain_id}/subdomains")
def create_subdomain(domain_id: str, payload: SubdomainPayload) -> dict[str, Any]:
    with _SESSION_FACTORY() as session:
        parent = _get_domain(session, domain_id)
        if parent.parent_id:
            raise HTTPException(status_code=400, detail="不能在子领域下继续嵌套")
        if session.query(StrategicDomainRow).filter(
            StrategicDomainRow.parent_id == parent.id,
            StrategicDomainRow.name == payload.name.strip(),
            StrategicDomainRow.deleted.is_(False),
        ).first():
            raise HTTPException(status_code=409, detail="子领域名称已存在")
        order = session.query(StrategicDomainRow).filter(
            StrategicDomainRow.parent_id == parent.id, StrategicDomainRow.deleted.is_(False)
        ).count()
        row = StrategicDomainRow(
            id=_new_id("subdomain"),
            name=payload.name.strip(),
            description=payload.description.strip(),
            parent_id=parent.id,
            sort_order=order,
        )
        session.add(row)
        session.commit()
        return _response(_domain_to_dict(session, row))


@router.put("/subdomains/{subdomain_id}")
def update_subdomain(subdomain_id: str, payload: SubdomainPayload) -> dict[str, Any]:
    with _SESSION_FACTORY() as session:
        row = _get_domain(session, subdomain_id)
        if not row.parent_id:
            raise HTTPException(status_code=400, detail="该记录不是子领域")
        duplicate = session.query(StrategicDomainRow).filter(
            StrategicDomainRow.id != row.id,
            StrategicDomainRow.parent_id == row.parent_id,
            StrategicDomainRow.name == payload.name.strip(),
            StrategicDomainRow.deleted.is_(False),
        ).first()
        if duplicate:
            raise HTTPException(status_code=409, detail="子领域名称已存在")
        row.name = payload.name.strip()
        row.description = payload.description.strip()
        row.updated_at = _now()
        session.commit()
        return _response(_domain_to_dict(session, row))


@router.get("/subdomains/{subdomain_id}")
def get_subdomain(subdomain_id: str) -> dict[str, Any]:
    with _SESSION_FACTORY() as session:
        _seed_defaults(session)
        row = _get_domain(session, subdomain_id)
        if not row.parent_id:
            raise HTTPException(status_code=400, detail="该记录不是子领域")
        return _response(_domain_to_dict(session, row))


@router.delete("/subdomains/{subdomain_id}")
def delete_subdomain(subdomain_id: str) -> dict[str, Any]:
    with _SESSION_FACTORY() as session:
        row = _get_domain(session, subdomain_id)
        if not row.parent_id:
            raise HTTPException(status_code=400, detail="该记录不是子领域")
        row.deleted = True
        for team in session.query(StrategicTeamRow).filter(StrategicTeamRow.subdomain_id == row.id).all():
            team.subdomain_id = None
        session.commit()
        return _response({"id": subdomain_id, "deleted": True})


@router.get("/domains/{domain_id}/teams")
def list_domain_teams(
    domain_id: str,
    refresh: bool = Query(False),
    subdomain_id: str | None = Query(None),
) -> dict[str, Any]:
    with _SESSION_FACTORY() as session:
        _seed_defaults(session)
        _seed_presentation_candidates(session)
        _purge_non_china_teams(session)
        domain = _get_root_domain(session, domain_id)
        source: dict[str, Any] = {"provider": "AI4S Daily", "refreshed": False}
        if refresh:
            source = _sync_domain(session, domain)
            source["refreshed"] = True
            _seed_presentation_candidates(session)
        query = session.query(StrategicTeamRow).filter(
            StrategicTeamRow.domain_id == domain.id,
            StrategicTeamRow.deleted.is_(False),
        )
        if subdomain_id:
            query = query.filter(StrategicTeamRow.subdomain_id == subdomain_id)
        teams = query.order_by(StrategicTeamRow.updated_at.desc(), StrategicTeamRow.name.asc()).all()
        return _response({
            "teams": [_team_to_dict(team) for team in teams],
            "source": source,
            # Refresh can create or remove populated subdomains; return the
            # filtered domain so the left navigation stays aligned immediately.
            "domain": _domain_to_dict(session, domain),
        })


@router.put("/teams/{team_id}")
def update_team_status(team_id: str, payload: TeamStatusPayload) -> dict[str, Any]:
    """Persist manually maintained status and detail fields for a team."""
    with _SESSION_FACTORY() as session:
        team = session.query(StrategicTeamRow).filter(
            StrategicTeamRow.id == team_id,
            StrategicTeamRow.deleted.is_(False),
        ).first()
        if not team:
            raise HTTPException(status_code=404, detail="候选团队不存在")
        team.attention = payload.attention.strip()
        team.contact = payload.contact.strip()
        if payload.core_direction is not None:
            team.core_direction = payload.core_direction.strip()
        if payload.dual_judgement is not None:
            team.dual_judgement = payload.dual_judgement.strip()
        if payload.contact_record is not None:
            team.contact_record = payload.contact_record.strip()
        if payload.internal_review is not None:
            team.internal_review = payload.internal_review.strip()
        if payload.recent_update is not None:
            team.recent_update = payload.recent_update.strip()
        if payload.next_action is not None:
            team.next_action = payload.next_action.strip()
        team.updated_at = _now()
        session.commit()
        return _response(_team_to_dict(team))


@router.post("/domains/{domain_id}/sync")
def sync_domain(domain_id: str) -> dict[str, Any]:
    return list_domain_teams(domain_id, refresh=True, subdomain_id=None)
