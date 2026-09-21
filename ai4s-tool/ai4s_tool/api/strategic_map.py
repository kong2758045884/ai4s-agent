# -*- coding: utf-8 -*-
"""国内战略力量图谱 API。

该路由把战略图谱的编辑数据落在独立 SQLite 表中，并在请求刷新时从 AI4S
Daily 的公开报告中提取领域相关的机构/团队线索。领域配置和研判结果分开保存：
用户手工调整的关注级别、联系状态不会因为下一次 Daily 刷新被覆盖。
"""

from __future__ import annotations

import hashlib
import asyncio
import html
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, unquote, urlparse

import requests
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, JSON, String, Text, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from ai4s_tool.util.log_util import logger


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
logger.info(f"[StrategicMap storage] sqlite_path={_DB_PATH.resolve()} pid={os.getpid()}")
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
    # Normalized team fields.  The original table only had ``name`` and
    # ``focus``; those fields were overloaded as both institution and team
    # labels, which is the source of most of the field swaps seen in the UI.
    institution_name = Column(String(180), nullable=False, default="")
    team_name = Column(String(180), nullable=False, default="")
    description = Column(Text, nullable=False, default="")
    research_directions = Column(JSON, nullable=False, default=list)
    location = Column(String(120), nullable=False, default="")
    is_domestic = Column(Boolean, nullable=False, default=True)
    team_confidence = Column(Float, nullable=False, default=0.0)
    leader_confidence = Column(Float, nullable=False, default=0.0)
    member_confidence = Column(Float, nullable=False, default=0.0)
    evidence_urls = Column(JSON, nullable=False, default=list)
    verification_status = Column(String(48), nullable=False, default="unverified")
    deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, nullable=False, default=_now)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)


class StrategicPersonRow(_Base):
    """A verified leader or core member linked to a team by ``team_id``.

    People are deliberately stored separately from the team snapshot.  A
    refresh can therefore replace team evidence while retaining the stable
    team id and the explicitly verified people attached to it.
    """

    __tablename__ = "strategic_map_person"

    id = Column(String(64), primary_key=True)
    team_id = Column(String(64), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    title = Column(String(160), nullable=False, default="")
    role = Column(String(120), nullable=False, default="")
    research_direction = Column(String(500), nullable=False, default="")
    bio = Column(Text, nullable=False, default="")
    avatar_url = Column(String(500), nullable=False, default="")
    profile_url = Column(String(500), nullable=False, default="")
    source_urls = Column(JSON, nullable=False, default=list)
    source_type = Column(String(80), nullable=False, default="")
    last_verified_at = Column(DateTime, nullable=True)
    is_leader = Column(Boolean, nullable=False, default=False, index=True)
    confidence = Column(Float, nullable=False, default=0.0)
    evidence = Column(Text, nullable=False, default="")
    verification_status = Column(String(48), nullable=False, default="unverified")
    deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, nullable=False, default=_now)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)


class StrategicSyncMetaRow(_Base):
    __tablename__ = "strategic_map_sync_meta"

    key = Column(String(64), primary_key=True)
    value = Column(String(255), nullable=False, default="")
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)


_Base.metadata.create_all(_ENGINE)


def _ensure_team_schema() -> None:
    """Add normalized columns to databases created by older releases.

    ``create_all`` does not alter an existing SQLite table.  Keep this small
    migration local to the strategic-map module so an upgrade does not require
    a separate migration runner and existing user status fields remain intact.
    """
    columns = {
        "institution_name": "VARCHAR(180) NOT NULL DEFAULT ''",
        "team_name": "VARCHAR(180) NOT NULL DEFAULT ''",
        "description": "TEXT NOT NULL DEFAULT ''",
        "research_directions": "JSON NOT NULL DEFAULT '[]'",
        "location": "VARCHAR(120) NOT NULL DEFAULT ''",
        "is_domestic": "BOOLEAN NOT NULL DEFAULT 1",
        "team_confidence": "FLOAT NOT NULL DEFAULT 0",
        "leader_confidence": "FLOAT NOT NULL DEFAULT 0",
        "member_confidence": "FLOAT NOT NULL DEFAULT 0",
        "evidence_urls": "JSON NOT NULL DEFAULT '[]'",
        "verification_status": "VARCHAR(48) NOT NULL DEFAULT 'unverified'",
    }
    with _ENGINE.begin() as connection:
        existing = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(strategic_map_team)"
            ).fetchall()
        }
        for name, definition in columns.items():
            if name not in existing:
                connection.exec_driver_sql(
                    f"ALTER TABLE strategic_map_team ADD COLUMN {name} {definition}"
                )
        # Backfill old rows conservatively.  ``focus`` is a research lead, so
        # it is not copied to team_name; the normalizer will supply an explicit
        # "not stated" label when a source does not name a team.
        connection.exec_driver_sql(
            "UPDATE strategic_map_team SET institution_name = name "
            "WHERE institution_name IS NULL OR institution_name = ''"
        )
        connection.exec_driver_sql(
            "UPDATE strategic_map_team SET description = evidence_summary "
            "WHERE description IS NULL OR description = ''"
        )
        connection.exec_driver_sql(
            "UPDATE strategic_map_team SET research_directions = ? "
            "WHERE research_directions IS NULL OR research_directions = ''",
            (json.dumps([], ensure_ascii=False),),
        )
        connection.exec_driver_sql(
            "UPDATE strategic_map_team SET team_name = ? "
            "WHERE team_name IS NULL OR team_name = ''",
            ("公开资料未注明具体团队",),
        )
        connection.exec_driver_sql(
            "UPDATE strategic_map_team SET location = ? "
            "WHERE location IS NULL OR location = ''",
            ("中国（公开资料判定）",),
        )


_ensure_team_schema()


def _ensure_people_schema() -> None:
    columns = {
        "confidence": "FLOAT NOT NULL DEFAULT 0",
        "evidence": "TEXT NOT NULL DEFAULT ''",
        "verification_status": "VARCHAR(48) NOT NULL DEFAULT 'unverified'",
    }
    with _ENGINE.begin() as connection:
        existing = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(strategic_map_person)").fetchall()}
        for name, definition in columns.items():
            if name not in existing:
                connection.exec_driver_sql(f"ALTER TABLE strategic_map_person ADD COLUMN {name} {definition}")


_ensure_people_schema()


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
    """Compatibility shim for databases created before snapshot syncing.

    Older releases called this function on every GET and inserted a static
    list of institutions to make the card count look full.  That bypassed the
    evidence and normalization pipeline.  Reads must now be read-only, so the
    function only repairs a legacy date value and never creates candidates.
    """
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
            StrategicTeamRow.verification_status == "verified",
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


def _person_to_dict(person: StrategicPersonRow) -> dict[str, Any]:
    """Serialize a verified leader/member without exposing ORM objects."""
    return {
        "id": person.id,
        "teamId": person.team_id,
        "team_id": person.team_id,
        "name": person.name,
        "title": person.title,
        "role": person.role,
        "researchDirection": person.research_direction,
        "research_direction": person.research_direction,
        "bio": person.bio,
        "avatarUrl": person.avatar_url,
        "avatar_url": person.avatar_url,
        "profileUrl": person.profile_url,
        "profile_url": person.profile_url,
        "sourceUrls": list(person.source_urls or []),
        "source_urls": list(person.source_urls or []),
        "confidence": float(person.confidence or 0),
        "evidence": person.evidence,
        "verificationStatus": person.verification_status,
        "verification_status": person.verification_status,
        "sourceType": person.source_type,
        "source_type": person.source_type,
        "lastVerifiedAt": person.last_verified_at.isoformat() if person.last_verified_at else "",
        "last_verified_at": person.last_verified_at.isoformat() if person.last_verified_at else "",
        "isLeader": bool(person.is_leader),
        "is_leader": bool(person.is_leader),
    }


def _team_people(session: Session, team_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    people = session.query(StrategicPersonRow).filter(
        StrategicPersonRow.team_id == team_id,
        StrategicPersonRow.deleted.is_(False),
        StrategicPersonRow.verification_status == "verified",
    ).order_by(StrategicPersonRow.is_leader.desc(), StrategicPersonRow.updated_at.desc()).all()
    values = [_person_to_dict(person) for person in people]
    leaders = [person for person in values if person["isLeader"]]
    leader = leaders[0] if len(leaders) == 1 else None
    def core_verified(person):
        try:
            evidence = json.loads(person.get('evidence') or '{}')
            return (evidence.get('core_membership') or {}).get('status') == 'verified'
        except (ValueError, TypeError, AttributeError):
            return False
    return leader, [person for person in values if not person["isLeader"] and core_verified(person)]


def _team_to_dict(team: StrategicTeamRow, session: Session | None = None) -> dict[str, Any]:
    institution = _normalise_institution_name(team.institution_name or team.name)
    team_name = _clean(team.team_name, _UNKNOWN_TEAM_LABEL)
    directions = _normalise_directions(team.research_directions, team.focus)
    payload = {
        "id": team.id,
        "domainId": team.domain_id,
        "subdomainId": team.subdomain_id,
        "name": institution,
        "organization": institution,
        "institutionName": institution,
        "institution_name": institution,
        "teamName": team_name,
        "team_name": team_name,
        "description": team.description,
        "researchDirections": directions,
        "research_directions": directions,
        "location": team.location,
        "institutionCountry": team.location,
        "institution_country": team.location,
        "isDomestic": bool(team.is_domestic),
        "is_domestic": bool(team.is_domestic),
        "focus": team.focus,
        "aiLevel": "待核实",
        "scienceLevel": "待核实",
        "attention": team.attention,
        "contact": team.contact,
        "coreDirection": team.core_direction,
        "dualJudgement": team.dual_judgement,
        "contactRecord": team.contact_record,
        "internalReview": team.internal_review,
        "recentUpdate": "" if team.recent_update == "近期" else team.recent_update,
        "nextAction": team.next_action,
        "source": team.source,
        "sourceUrls": list(team.source_urls or []),
        "evidenceUrls": list(team.evidence_urls or team.source_urls or []),
        "evidence_urls": list(team.evidence_urls or team.source_urls or []),
        "teamConfidence": float(team.team_confidence or 0),
        "team_confidence": float(team.team_confidence or 0),
        "leaderConfidence": float(team.leader_confidence or 0),
        "leader_confidence": float(team.leader_confidence or 0),
        "memberConfidence": float(team.member_confidence or 0),
        "member_confidence": float(team.member_confidence or 0),
        "verificationStatus": team.verification_status,
        "verification_status": team.verification_status,
        "evidenceSummary": team.evidence_summary,
        "reportId": team.report_id,
        "reportTitle": team.report_title,
        "updatedAt": team.updated_at.isoformat() if team.updated_at else "",
    }
    # Legacy pipeline templates are not scientific assessments. Read projection
    # only: do not migrate rows or change timestamps while serving a GET.
    from .team_research_store import manual_fields
    protected = manual_fields(session, team.id) if session is not None else set()
    if "recent_update" in protected:
        payload["recentUpdate"] = team.recent_update
    if "dual_judgement" not in protected and team.dual_judgement == "AI 较高｜科学 较高":
        payload["dualJudgement"] = "AI 待核实｜科学 待核实"
    if "internal_review" not in protected and team.internal_review == "已完成候选资料汇总、二次结构化整理、国内过滤和去重；仍建议人工抽查来源。":
        payload["internalReview"] = "待补充研判"
    if session is not None:
        leader, members = _team_people(session, team.id)
        payload["leader"] = leader
        payload["members"] = members
        payload["leader_id"] = leader["id"] if leader else ""
        payload["leaderId"] = leader["id"] if leader else ""
    return payload


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
        "生命科学": ["生命科学", "生物", "药物", "蛋白", "基因组", "医学", "细胞", "神经", "癌症", "临床"],
        "合金材料": ["合金", "材料", "金属", "高熵", "镍基", "钛合金", "铝合金", "钢", "材料基因组", "材料计算", "材料设计"],
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
    # Chinese transliterations/short names that appear in Daily reports.
    "石溪大学",
    "纽约大学",
    "斯坦福大学",
    "加州大学",
    "哥伦比亚大学",
    "康奈尔大学",
    "宾夕法尼亚大学",
    "普林斯顿大学",
    "多伦多大学",
    "东京大学",
    "首尔大学",
    "伦敦大学",
    "欧洲核子研究中心",
    "苏黎世",
    "洛桑",
    "爱丁堡",
    "慕尼黑",
    "巴黎",
    "香港",
    "澳门",
    "台湾",
)

_DOMESTIC_ENTITY_MARKERS = (
    "中国", "中科院", "清华", "北大", "北京", "上海", "浙江", "复旦", "南京",
    "天津", "重庆", "广东", "华南", "华中", "华北", "西北", "西安", "四川",
    "山东", "吉林", "大连", "厦门", "武汉", "哈尔滨", "合肥", "济南", "郑州",
    "兰州", "南开", "同济", "东南", "中山", "电子科技", "航空航天", "理工大学",
    "科技大学", "交通大学", "师范大学",
    "鹏城", "之江", "天河", "华为", "阿里", "蚂蚁", "腾讯", "字节", "百度",
    "分子之心", "深势科技", "晶泰科技", "国家电网", "南方电网",
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
    # A trailing “团队/研究团队” without an institution suffix is a prose
    # description, not an identifiable organization.  Named labs such as
    # “上海人工智能实验室” are retained by the suffix check below.
    if normalized.endswith(("团队", "研究团队", "科研团队", "课题组", "研究组")) and not re.search(
        r"(?:大学|学院|科学院|研究院|研究所|实验室|中心|医院|公司|集团)$", normalized
    ):
        return True
    # Report prose occasionally turns a sentence fragment into a candidate
    # ending in a valid suffix (for example “头部 AI 公司”).
    if re.search(r"(?:头部|前沿|相关|多家|若干|某)\s*(?:AI|人工智能)?\s*(?:团队|实验室|机构|公司)$", normalized):
        return True
    return any(marker != "实验室" and marker in normalized for marker in _GENERIC_ENTITY_MARKERS)


def _is_prose_entity(name: str) -> bool:
    """Reject search-snippet fragments that merely contain an institution suffix."""
    value = re.sub(r"\s+", " ", _clean(name))
    if len(value) > 24:
        return True
    if re.search(r"\d{2,}", value):
        return True
    return any(token in value for token in ("简介", "地址", "负责人简介", "毕业于", "年在", "回国后", "导师队伍", "文化路", "一级学科", "科普活动", "研究部门"))


def _is_china_entity(name: str) -> bool:
    """只接受中国机构/团队线索，避免海外候选混入国内战略图谱。"""
    normalized = re.sub(r"\s+", " ", name or "").strip()
    if not normalized or not re.search(r"[\u4e00-\u9fff]", normalized):
        return False
    lowered = normalized.lower()
    if any(marker.lower() in lowered for marker in _FOREIGN_ENTITY_MARKERS):
        return False
    # A Chinese translation of a foreign university can contain Chinese
    # characters too.  Require an explicit mainland institution/city marker;
    # this prevents entries such as “石溪大学” or “香港科技大学” from passing
    # solely because they contain the suffix “大学”.
    return any(marker.lower() in lowered for marker in _DOMESTIC_ENTITY_MARKERS)


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


class _SyncQualityError(RuntimeError):
    """Raised when a new snapshot is not safe to replace the previous one."""


_INSTITUTION_ALIASES = {
    "中科院自动化所": "中国科学院自动化研究所",
    "中科院金属所": "中国科学院金属研究所",
    "中科院物理所": "中国科学院物理研究所",
    "中科院电工所": "中国科学院电工研究所",
    "中科院微电子所": "中国科学院微电子研究所",
    "中科院空天院": "中国科学院空天信息创新研究院",
    "中科院国家空间科学中心": "中国科学院国家空间科学中心",
    "中国科大": "中国科学技术大学",
    "国科大": "中国科学院大学",
    "北航": "北京航空航天大学",
    "哈工大": "哈尔滨工业大学",
    "上交": "上海交通大学",
    "浙大": "浙江大学",
    "复旦": "复旦大学",
}

_UNKNOWN_TEAM_LABEL = "公开资料未注明具体团队"

# Official-unit fallback catalogue used only when search engines return no
# usable snippets. Each entry is an identifiable unit (not a bare institution)
# and carries an official host URL so it remains auditable and can be enriched
# again on the next refresh.
_CURATED_WEB_UNITS: dict[str, list[dict[str, Any]]] = {
    "合金材料": [
        {"institution_name": "中国科学院金属研究所", "team_name": "沈阳材料科学国家研究中心", "source_urls": ["https://www.imr.ac.cn/"], "research_directions": ["先进合金", "材料设计"]},
        {"institution_name": "中国科学院金属研究所", "team_name": "金属所先进炭材料研究部", "source_urls": ["https://www.imr.ac.cn/"], "research_directions": ["先进材料", "金属材料"]},
        {"institution_name": "北京科技大学", "team_name": "新材料技术研究院", "source_urls": ["https://www.ustb.edu.cn/"], "research_directions": ["高温合金", "材料加工"]},
        {"institution_name": "西北工业大学", "team_name": "凝固技术国家重点实验室", "source_urls": ["https://www.nwpu.edu.cn/"], "research_directions": ["合金凝固", "航空材料"]},
        {"institution_name": "清华大学", "team_name": "材料基因组工程研究中心", "source_urls": ["https://www.tsinghua.edu.cn/"], "research_directions": ["材料基因组", "计算材料"]},
        {"institution_name": "上海交通大学", "team_name": "轻合金精密成型国家工程研究中心", "source_urls": ["https://www.sjtu.edu.cn/"], "research_directions": ["轻合金", "精密成型"]},
        {"institution_name": "哈尔滨工业大学", "team_name": "先进焊接与连接国家重点实验室", "source_urls": ["https://www.hit.edu.cn/"], "research_directions": ["材料连接", "先进制造"]},
        {"institution_name": "中国科学院物理研究所", "team_name": "先进材料与结构分析实验室", "source_urls": ["https://www.iphy.ac.cn/"], "research_directions": ["材料结构", "凝聚态材料"]},
    ],
    "生命科学": [
        {"institution_name": "中国科学院生物物理研究所", "team_name": "生物大分子国家重点实验室", "source_urls": ["https://www.ibp.cas.cn/"], "research_directions": ["结构生物学", "生物大分子"]},
        {"institution_name": "中国科学院上海营养与健康研究所", "team_name": "营养与代谢国家重点实验室", "source_urls": ["https://www.sinh.cas.cn/"], "research_directions": ["营养代谢", "生命健康"]},
        {"institution_name": "中国科学院遗传与发育生物学研究所", "team_name": "植物基因组学国家重点实验室", "source_urls": ["https://www.genetics.ac.cn/"], "research_directions": ["基因组学", "智能育种"]},
        {"institution_name": "清华大学", "team_name": "清华大学结构生物学中心", "source_urls": ["https://www.tsinghua.edu.cn/"], "research_directions": ["结构生物学", "生物计算"]},
        {"institution_name": "北京大学", "team_name": "北京大学生物信息学中心", "source_urls": ["https://www.pku.edu.cn/"], "research_directions": ["生物信息学", "计算生物学"]},
        {"institution_name": "中国科学院成都生物研究所", "team_name": "生物资源与生态环境国家重点实验室", "source_urls": ["https://www.cib.cas.cn/"], "research_directions": ["生物资源", "生态环境"]},
        {"institution_name": "中国科学院神经科学研究所", "team_name": "脑与智能技术卓越创新中心", "source_urls": ["https://www.ion.ac.cn/"], "research_directions": ["神经科学", "脑科学"]},
        {"institution_name": "中国科学院分子细胞科学卓越创新中心", "team_name": "分子细胞生物学国家重点实验室", "source_urls": ["https://www.cscb.cas.cn/"], "research_directions": ["分子细胞生物学", "生命科学"]},
        {"institution_name": "北京生命科学研究所", "team_name": "北京生命科学研究所研究组", "source_urls": ["https://www.nibs.ac.cn/"], "research_directions": ["分子生物学", "生物医学"]},
    ],
}


def _normalise_institution_name(value: Any) -> str:
    text = re.sub(r"\s+", " ", _clean(value)).strip(" -*#，,;；")
    text = re.sub(r"\s*[（(][^（）()]{1,24}[）)]$", "", text).strip()
    if not text:
        return ""
    compact = text.replace(" ", "")
    for alias, official in sorted(_INSTITUTION_ALIASES.items(), key=lambda item: -len(item[0])):
        if compact.casefold() == alias.replace(" ", "").casefold():
            return official
    # Common report shorthand keeps the institution recognizable while still
    # avoiding two rows for “中科院自动化所” and its official name.
    text = re.sub(r"^中科院(?=大学|研究|物理|金属|电工|微电子|空天|国家)", "中国科学院", text)
    text = re.sub(r"^中国科大$", "中国科学技术大学", text)
    return text


def _normalise_directions(value: Any, fallback: str = "") -> list[str]:
    if isinstance(value, str):
        try:
            decoded = json.loads(value) if value.strip().startswith("[") else None
        except json.JSONDecodeError:
            decoded = None
        values = decoded if isinstance(decoded, list) else re.split(r"[、,，;；|/]+", value)
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = []
    result: list[str] = []
    for item in values:
        text = re.sub(r"\s+", " ", _clean(item)).strip(" -*#")
        if text and text not in result and len(text) <= 80:
            result.append(text)
    if not result and fallback:
        result.append(_normalise_section_title(fallback)[:80])
    return result


def _is_foreign_location(value: str) -> bool:
    lowered = _clean(value).lower()
    return bool(lowered and any(marker.lower() in lowered for marker in _FOREIGN_ENTITY_MARKERS))


def _canonical_team_key(institution: str, team: str) -> str:
    def compact(value: str) -> str:
        return re.sub(r"[^\w\u4e00-\u9fff]", "", value.casefold())

    inst = compact(_normalise_institution_name(institution))
    team_key = compact(team)
    if not team_key or team in {_UNKNOWN_TEAM_LABEL, "机构研究团队（公开资料未注明具体名称）"}:
        team_key = "institution"
    return f"{inst}:{team_key}"


def _parse_structured_response(text: str) -> list[dict[str, Any]]:
    """Parse the JSON object/array variants used by AI4S Daily's LLM layer."""
    value = (text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*```$", "", value).strip()
    parsed: Any = None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        for pattern in (r"\{.*\}", r"\[.*\]"):
            match = re.search(pattern, value, re.DOTALL)
            if not match:
                continue
            try:
                parsed = json.loads(match.group())
                break
            except json.JSONDecodeError:
                continue
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict):
        for key in ("teams", "items", "results", "data"):
            if isinstance(parsed.get(key), list):
                return [item for item in parsed[key] if isinstance(item, dict)]
    raise ValueError("LLM 返回中没有可用的团队 JSON 数组")


def _parse_json_object(text: str) -> dict[str, Any] | None:
    value = (text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*```$", "", value).strip()
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", value, flags=re.S)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None


def _llm_config() -> tuple[str, str, str] | None:
    # Strategic map intentionally reuses the same MRAG/Agent LLM environment;
    # no strategic-map-specific key/model/base-url is supported.
    # Keep direct imports (CLI/tests) consistent with the Agent client, which
    # loads the project .env at module import time.
    if not os.getenv("LLM_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except Exception:
            pass
    api_key = (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None
    base_url = (os.getenv("LLM_MODEL_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "").strip().rstrip("/")
    model = (os.getenv("LLM_MODEL_NAME") or os.getenv("DEFAULT_MODEL") or "").strip()
    if not model or not base_url:
        return None
    return api_key, base_url, model


_SHARED_LLM_CLIENT_LOCK = threading.Lock()
_SHARED_LLM_CLIENT = None
_SHARED_LLM_CLIENT_CONFIG = None
_SHARED_LLM_CONCURRENCY = max(1, min(2, int(os.getenv("STRATEGIC_MAP_LLM_CONCURRENCY", "2"))))
_SHARED_LLM_SLOTS = threading.BoundedSemaphore(_SHARED_LLM_CONCURRENCY)


class _ObservedLlmText(str):
    """Preserve the existing string contract while attaching call telemetry."""

    def __new__(cls, value: str, observation: dict[str, Any]):
        result = super().__new__(cls, value)
        result.observation = observation
        return result


def _shared_llm_client(config):
    """Reuse the MRAG HTTP pool; recreate only after an explicit config change."""
    global _SHARED_LLM_CLIENT, _SHARED_LLM_CLIENT_CONFIG
    with _SHARED_LLM_CLIENT_LOCK:
        if _SHARED_LLM_CLIENT is None or _SHARED_LLM_CLIENT_CONFIG != config:
            from ai4s_tool.tool.mrag.generation.llm import (
                LLMClient,
                _normalize_openai_compatible_base_url,
            )

            client = LLMClient()
            api_key, base_url, model = config
            expected_base_url = _normalize_openai_compatible_base_url(base_url)
            if (client.api_key, client.model_base_url, client.model_name) != (
                api_key,
                expected_base_url,
                model,
            ):
                raise RuntimeError("shared Agent LLM client does not match active configuration")
            _SHARED_LLM_CLIENT = client
            _SHARED_LLM_CLIENT_CONFIG = config
        return _SHARED_LLM_CLIENT


def _shared_agent_llm_text(*, task: str, system: str, user: str, timeout: int) -> str:
    """Call the exact Agent LLM client used by the rest of ai4s-tool.

    ``LLMClient`` owns endpoint normalization, auth, provider headers, retry,
    timeout and stream aggregation. Strategic-map code only supplies evidence
    and consumes the returned text.
    """
    config = _llm_config()
    if not config:
        raise RuntimeError("shared Agent LLM configuration is incomplete (LLM_API_KEY/LLM_MODEL_BASE_URL/LLM_MODEL_NAME)")
    api_key, base_url, model = config
    logger.info(
        f"[StrategicMap LLM] model={model} task={task} endpoint={base_url} "
        f"input_chars={len(system) + len(user)} request status=starting"
    )
    try:
        # Reuse the exact MRAG/Agent client instead of reconstructing a second
        # OpenAI-compatible HTTP request here.  In particular this preserves
        # the Agent client's DashScope extra_body (thinking disabled), stream
        # aggregation and stream_with_retry behaviour.  The former
        # ask_llm_sync_iter path did not send that provider-specific body and
        # could return an empty ``content`` after a reasoning-only response.
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        client = _shared_llm_client((api_key, base_url, model))
        # Structured team research owns its single bounded retry. Other calls
        # keep the MRAG client's configured retry policy and endpoint.
        max_tokens = max(256, int(os.getenv("STRATEGIC_MAP_LLM_MAX_TOKENS", "8192")))
        structured = task.startswith("team-")
        queue_started = time.monotonic()
        acquired = _SHARED_LLM_SLOTS.acquire(timeout=max(1, timeout) if structured else None)
        queue_wait = time.monotonic()-queue_started
        if not acquired:
            raise TimeoutError("shared Agent LLM concurrency wait exhausted")
        try:
            remaining_timeout = max(1, int(timeout-queue_wait)) if structured else None
            completion = client.completions(
                messages, max_tokens=max(max_tokens, 16000) if structured else max_tokens,
                temperature=0, stream=False, timeout=remaining_timeout,
                max_retries=0 if structured else None,
                response_format={"type": "json_object"} if structured else None,
                include_usage=structured,
            )
        finally:
            _SHARED_LLM_SLOTS.release()
        content = str(completion or "").strip()
        usage = getattr(completion, "usage", None)
        logger.info(
            f"[LLM Response] task={task} status=success output_chars={len(content)} "
            f"usage={usage if usage else 'unknown'}"
        )
        if not content:
            raise RuntimeError("shared Agent LLM returned empty content")
        return _ObservedLlmText(content, {
            "model": model,
            "purpose": task,
            "input_chars": len(system) + len(user),
            "output_chars": len(content),
            "queue_wait_seconds": round(queue_wait, 3),
            "concurrency_limit": _SHARED_LLM_CONCURRENCY,
            "usage": usage,
        })
    except Exception as exc:
        # The structured caller records error class, retry, and timing in its
        # trace. Repeating a full stack for expected provider/time-limit
        # failures creates large logs without adding diagnostic information.
        logger.warning(
            f"[StrategicMap LLM] task={task} status=failed "
            f"error={type(exc).__name__} reason={str(exc)[:240]}"
        )
        raise


def _normalise_with_llm(
    domain: StrategicDomainRow,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    """Ask an OpenAI-compatible model to normalize compact candidate batches."""
    if not _llm_config():
        return None
    batch_size = max(8, min(24, int(os.getenv("STRATEGIC_MAP_LLM_BATCH_SIZE", "16"))))
    all_records: list[dict[str, Any]] = []

    def run_batch(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        compact_candidates = [
            {
                "candidate_id": item["candidate_id"],
                "institution_hint": item.get("institution_hint", ""),
                "section_title": item.get("section_title", ""),
                "evidence": item.get("evidence", "")[:1500],
                "source_urls": item.get("source_urls", [])[:6],
            }
            for item in batch
        ]
        prompt = f"""
你是科研信息数据整理与事实核验专家。请只处理下面候选资料中能够由原文支持的国内（中国大陆）AI4S优势团队，领域为“{domain.name}”。

工作要求：
1. institution_name 必须是正式机构名称；team_name 必须是原文明确出现的实验室、研究中心、课题组或团队名称。原文没有具体团队名时，team_name 必须写“{_UNKNOWN_TEAM_LABEL}”，绝不能编造名称。
2. description 只能概括候选资料明确支持的工作，使用规范中文完整句；research_directions 为 1-6 个与该团队直接相关的方向。
3. 逐条判断 is_domestic。MIT、Stanford、Oxford、Google DeepMind、Microsoft Research 等国外机构以及仅有海外地点的记录必须删除。不能仅凭模型常识补充未在候选资料中出现的团队。只要候选资料明确提到中国机构，即使资料只确认机构而未确认课题组，也保留该机构并使用“{_UNKNOWN_TEAM_LABEL}”。
4. 合并机构简称、英文名和重复报道；每个结果保留 source_candidate_ids，且只能使用输入 candidate_id。
5. 尽量逐条整理所有合格候选；不要因为团队名称不完整而把有明确中国机构和证据的候选全部丢弃。
6. 如果候选资料明确给出负责人或成员姓名，才在 leader/members 中输出；只能确认属于该团队的人，不能用机构成员、论文作者或模型常识补齐。没有足够证据时 leader 为 null、members 为空数组。每个人保留 name、title、role、research_direction、bio、profile_url、source_urls、source_type。
7. 只输出 JSON 对象，不要 markdown 或解释文字，格式必须为 {{"teams":[{{"source_candidate_ids":["c1"],"institution_name":"...","team_name":"...","description":"...","research_directions":["..."],"location":"...","is_domestic":true,"leader":null,"members":[]}}]}}。

候选资料：
{json.dumps(compact_candidates, ensure_ascii=False, indent=2)}
""".strip()
        try:
            content = _shared_agent_llm_text(
                task="team-normalization",
                system="你是科研实体信息抽取代理，只能根据用户提供的证据输出 JSON，不得使用记忆。",
                user=prompt,
                timeout=int(os.getenv("LLM_READ_TIMEOUT", "600")),
            )
            return _parse_structured_response(content)
        except Exception as exc:
            # A large evidence batch can exceed a provider's output/context
            # budget. Split it and retry through the same Agent client before
            # declaring individual candidates unresolved.
            if len(batch) > 1:
                midpoint = max(1, len(batch) // 2)
                logger.warning(
                    f"[StrategicMap LLM] task=team-normalization batch failed; splitting {len(batch)} into {midpoint}+{len(batch)-midpoint}: {exc}"
                )
                return run_batch(batch[:midpoint]) + run_batch(batch[midpoint:])
            logger.error(
                f"[StrategicMap LLM] task=team-normalization candidate={batch[0].get('candidate_id') if batch else ''} failed: {exc}"
            )
            return []

    # AI4S-Daily batches long inputs before structured scoring.  The same
    # boundary keeps reasoning models from dropping later candidates because a
    # single giant prompt exceeds its useful context window.
    for start in range(0, len(candidates), batch_size):
        all_records.extend(run_batch(candidates[start : start + batch_size]))
    return all_records


def _llm_json_call(*, system: str, user: str, timeout: int = 120) -> dict[str, Any] | None:
    """Evidence-only JSON call through the shared Agent LLM client."""
    try:
        content = _shared_agent_llm_text(task="json-extraction", system=system, user=user, timeout=timeout)
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
            cleaned = re.sub(r"\s*```$", "", cleaned).strip()
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            # Models occasionally prepend one short sentence despite the
            # JSON-only instruction. Extract only a JSON object/array, while
            # still failing loudly when no structured response exists.
            match = re.search(r"(\{.*\}|\[.*\])", cleaned, flags=re.S)
            if not match:
                raise
            parsed = json.loads(match.group(1))
        return parsed if isinstance(parsed, dict) else None
    except Exception as exc:
        logger.error(f"[StrategicMap LLM] task=json-extraction status=parse_failed reason={exc}")
        raise


def _run_team_research_agent(
    *, institution: str, team: str, domain: str, existing: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Typed strategic-map boundary; DeepSearch's public Markdown contract is unchanged."""
    if os.getenv("STRATEGIC_MAP_TEAM_AGENT", "true").lower() not in {"1", "true", "yes"}:
        return {"status": "disabled", "reviewed": None, "extracted": None, "pages": [],
                "errors": [{"stage": "research", "kind": "disabled"}], "trace": [],
                "counts": {"llm": 0, "search": 0, "fetch": 0, "fetch_success": 0, "cache_hits": 0}, "seconds": 0}
    from .team_research import investigate
    context = {**(existing or {}), "institution_name": institution, "team_name": team}
    result = investigate(context, domain, _shared_agent_llm_text)
    logger.info(f"[StrategicMap Team Research] status={result['status']} counts={result['counts']} seconds={result['seconds']}")
    return result


def _llm_verify_records(domain: StrategicDomainRow, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Independent review is part of the typed boundary and carries the same bodies.

    Never repeat extraction on summary snippets or promote unreviewed candidates.
    Failed observations remain attached for persistence/retry, not interpreted as
    an absent leader. Each record is reviewed independently in Team Research.
    """
    for record in records:
        run = record.get("_research", {})
        reviewed = run.get("reviewed") or {}
        record["verification_status"] = "verified" if (
            run.get("status") == "reviewed" and reviewed.get("entity_relation") in ("same", "rename")
            and reviewed.get("team_name", {}).get("status") == "verified"
        ) else "pending_llm_review"
    return records


def _llm_resolve_team_conflict(domain: StrategicDomainRow, existing: StrategicTeamRow, candidate: dict[str, Any], existing_people: list[StrategicPersonRow]) -> dict[str, Any]:
    """Compare historical and new evidence; never overwrite on uncertainty."""
    old = {
        "institution_name": existing.institution_name or existing.name,
        "team_name": existing.team_name,
        "description": existing.description,
        "research_directions": existing.research_directions or [],
        "source_urls": existing.source_urls or [],
        "leader": next(({"name": p.name, "title": p.title, "role": p.role, "source_urls": p.source_urls or [], "verification_status": p.verification_status} for p in existing_people if p.is_leader and not p.deleted), None),
        "members": [{"name": p.name, "title": p.title, "role": p.role, "source_urls": p.source_urls or [], "verification_status": p.verification_status} for p in existing_people if not p.is_leader and not p.deleted],
    }
    fallback = {"decision": "pending_review", "confidence": 0.0, "merged": {"leader": old.get("leader"), "members": old.get("members", [])}, "problems": ["new evidence conflicts with historical record; awaiting Verification Agent"]}
    if not _llm_config() or os.getenv("STRATEGIC_MAP_CONFLICT_LLM", "true").lower() not in {"1", "true", "yes"}:
        return fallback
    system = ("你是战略图谱冲突审核代理。比较数据库旧记录与本轮新调查证据，优先官方来源、页面更新时间和明确团队角色。"
              "不能凭记忆，无法确认时保留旧值并标记 pending_review。只输出 JSON：{decision:'accept|revise|pending_review', confidence:0..1, merged:{leader,members}, problems:[]}。")
    payload = {"domain": domain.name, "institution_name": old["institution_name"], "team_name": old["team_name"], "old_record": old,
               "new_record": {k: candidate.get(k) for k in ("institution_name", "team_name", "description", "research_directions", "source_urls", "leader", "members")}}
    try:
        result = _llm_json_call(system=system, user=json.dumps(payload, ensure_ascii=False), timeout=int(os.getenv("STRATEGIC_MAP_CONFLICT_TIMEOUT", "120"))) or {}
        merged = result.get("merged") if isinstance(result.get("merged"), dict) else {}
        if not isinstance(merged.get("members"), list):
            merged["members"] = old.get("members", [])
        if not merged.get("leader") and old.get("leader"):
            merged["leader"] = old["leader"]
        return {"decision": _clean(result.get("decision"), "pending_review"), "confidence": float(result.get("confidence") or 0), "merged": merged,
                "problems": result.get("problems", []) if isinstance(result.get("problems"), list) else []}
    except Exception as exc:
        fallback["problems"] = [f"conflict LLM failed: {exc}"]
        return fallback


def _purge_non_china_teams(session: Session) -> None:
    """软删除历史同步中遗留的海外、泛化或无法确认归属的候选。

    The map database is intentionally kept across weekly refreshes.  A filter
    that only runs while extracting new reports would therefore leave stale
    foreign/generic cards visible forever; apply the same quality gate to
    existing rows whenever the map is loaded or refreshed.
    """
    rows = session.query(StrategicTeamRow).filter(StrategicTeamRow.deleted.is_(False)).all()
    changed = False
    seen_keys: set[tuple[str, str]] = set()
    for team in rows:
        normalized_name = _normalise_institution_name(team.institution_name or team.name)
        location = _clean(team.location)
        if (
            not _is_china_entity(normalized_name)
            or _is_generic_entity(normalized_name)
            or _is_foreign_location(location)
            or team.is_domestic is False
        ):
            team.deleted = True
            changed = True
            continue
        dedupe_key = (team.domain_id, _canonical_team_key(normalized_name, team.team_name or _UNKNOWN_TEAM_LABEL))
        if dedupe_key in seen_keys:
            # Legacy releases appended the same institution on every refresh.
            # Keep the first active row and soft-delete the duplicate so a
            # normal read also repairs old snapshots before the next refresh.
            team.deleted = True
            changed = True
            continue
        seen_keys.add(dedupe_key)
        if normalized_name != team.name:
            team.name = normalized_name
            changed = True
        if normalized_name != team.institution_name:
            team.institution_name = normalized_name
            changed = True
        if not team.location:
            team.location = "中国（公开资料判定）"
            changed = True
        if not _normalise_directions(team.research_directions):
            team.research_directions = _normalise_directions(team.focus)
            changed = True
        team.is_domestic = True
    if changed:
        session.commit()


_REPORT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _duckduckgo_html_search(query: str, *, max_results: int = 10) -> list[dict[str, Any]]:
    """Search DuckDuckGo's public HTML endpoint without an API key.

    The strategic-map refresh runs in installations where optional ``ddgs`` and
    paid search credentials are absent.  DDG's HTML endpoint is a public web
    source and gives us a useful, rate-limited fallback rather than silently
    reverting to AI4S Daily-only discovery.  Only title/snippet/link evidence
    is returned; downstream domestic/entity gates still apply.
    """
    try:
        response = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "AI4SStrategicMap/1.0 (+public-search)"},
            timeout=20,
        )
        response.raise_for_status()
        body = response.text or ""
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    # DDG's result markup is deliberately simple and stable.  Keep parsing
    # dependency-free; malformed individual rows are skipped.
    pattern = re.compile(
        r'<a[^>]+class=["\']result__a["\'][^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        flags=re.I | re.S,
    )
    for match in pattern.finditer(body):
        href = html.unescape(match.group(1)).strip()
        parsed = urlparse(href)
        if "uddg" in parse_qs(parsed.query):
            href = unquote(parse_qs(parsed.query).get("uddg", [href])[0])
        if not href.startswith(("http://", "https://")):
            continue
        title = re.sub(r"<[^>]+>", " ", html.unescape(match.group(2)))
        title = re.sub(r"\s+", " ", title).strip()
        end = body.find("</a>", match.end())
        tail = body[match.end() : end + 4 if end >= 0 else match.end() + 4000]
        snippet_match = re.search(
            r'class=["\']result__snippet["\'][^>]*>(.*?)</', tail, flags=re.I | re.S
        )
        snippet = ""
        if snippet_match:
            snippet = re.sub(r"<[^>]+>", " ", html.unescape(snippet_match.group(1)))
            snippet = re.sub(r"\s+", " ", snippet).strip()
        rows.append({"link": href, "title": title, "snippet": snippet})
        if len(rows) >= max_results:
            break
    return rows


def _bing_html_search(query: str, *, max_results: int = 10) -> list[dict[str, Any]]:
    """Credential-free Bing HTML search used as a deterministic fallback."""
    try:
        response = requests.get(
            "https://www.bing.com/search",
            params={"q": query, "count": max_results, "setlang": "zh-cn"},
            headers={"User-Agent": "Mozilla/5.0 AI4SStrategicMap"}, timeout=float(os.getenv("STRATEGIC_MAP_SEARCH_TIMEOUT", "8")),
        )
        response.raise_for_status()
        body = response.text or ""
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for block in re.findall(r"<li class=\"b_algo\".*?</li>", body, flags=re.I | re.S):
        href = re.search(r"<a[^>]+href=\"([^\"]+)", block, flags=re.I)
        title = re.search(r"<h2[^>]*>(.*?)</h2>", block, flags=re.I | re.S)
        snippet = re.search(r"<p[^>]*>(.*?)</p>", block, flags=re.I | re.S)
        if not href:
            continue
        clean = lambda value: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(value or ""))).strip()
        rows.append({"link": href.group(1), "title": clean(title.group(1) if title else ""), "snippet": clean(snippet.group(1) if snippet else "")})
        if len(rows) >= max_results:
            break
    return rows


def _load_daily_reports(keywords: list[str], limit: int = 72) -> list[dict[str, Any]]:
    """Load matching Daily reports with a small process-local cache.

    AI4S Daily's search flow first gathers several independent queries and
    de-duplicates URLs before handing the material to a second stage.  The
    report index is the equivalent source here: broad keyword rounds are
    merged, while the cache avoids downloading the same markdown once per
    domain or once per worker refresh.
    """
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
    selected_files = [item for item in file_names if isinstance(item, str)][:limit]

    def fetch_markdown(file_name: str) -> tuple[str, str]:
        cached = _REPORT_CACHE.get(file_name)
        if cached and time.time() - cached[0] < 15 * 60:
            return file_name, cached[1].get("_markdown", "")
        try:
            response = requests.get(_daily_url(file_name), timeout=12)
            response.raise_for_status()
            return file_name, response.text
        except Exception:
            return file_name, ""

    with ThreadPoolExecutor(max_workers=min(12, max(1, len(selected_files)))) as executor:
        fetched = dict(executor.map(fetch_markdown, selected_files))

    for file_name in selected_files:
        markdown = fetched.get(file_name, "")
        if not markdown:
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
        report = {
                "id": file_name.removesuffix(".md"),
                "title": _clean(fields.get("title"), file_name),
                "date": _report_date(fields, file_name),
                "url": _daily_url(file_name),
                "sections": matching_sections,
                # Kept in the process cache only; never serialized to the API.
                "_markdown": markdown,
            }
        _REPORT_CACHE[file_name] = (time.time(), report)
        reports.append({key: value for key, value in report.items() if key != "_markdown"})
    return reports


def _load_web_search_reports(
    domain: StrategicDomainRow,
    subdomains: list[StrategicDomainRow],
    *,
    supplemental: bool = False,
) -> list[dict[str, Any]]:
    """Fetch a bounded set of public web search results for sparse domains.

    AI4S Daily remains the primary archive.  When it cannot supply roughly 25
    candidates, this fallback uses the project's existing Serper/Bing keys to
    find official Chinese university, CAS, laboratory and research-centre
    pages.  Search snippets are evidence only; they still go through the same
    domestic, LLM schema, duplicate and snapshot gates before persistence.
    """
    endpoint = os.getenv("SERPER_SEARCH_URL", "").strip() or "https://google.serper.dev/search"
    api_key = os.getenv("SERPER_SEARCH_API_KEY", "").strip()
    focus = domain.name
    queries = [
        f"{focus} 国内 优势团队 实验室 site:edu.cn",
        f"{focus} 中国科学院 研究团队 site:cas.cn",
        f"{focus} 全国重点实验室 研究中心",
        f"{focus} 中国高校 课题组 负责人",
        f"{focus} 国内科研院所 研究团队",
        f"{focus} 国内企业 研发团队",
    ]
    # Domain-specific expansion prevents generic institution queries from
    # collapsing to the same few Daily institutions. These intentionally cover
    # material subfields and official-unit vocabulary.
    if domain.name == "合金材料":
        queries.extend([
            "高温合金 镍基合金 中国 实验室 课题组",
            "高熵合金 中国 高校 研究团队",
            "钛合金 铝合金 中国 研究中心 教授",
            "材料基因组 中国 实验室 团队",
            "金属材料 计算材料学 中国科学院 课题组",
            "先进材料 国家重点实验室 中国 团队",
            "航空发动机 合金材料 中国 研究所 实验室",
            "增材制造 合金材料 中国 高校 团队",
        ])
    elif domain.name == "生命科学":
        queries.extend([
            "生物信息学 中国 高校 实验室 课题组",
            "合成生物学 中国 研究中心 团队",
            "基因组学 中国科学院 课题组",
            "计算生物学 中国 教授 实验室",
            "药物研发 AI 中国 实验室 团队",
            "结构生物学 中国 研究所 团队",
            "医学人工智能 中国 医院 研究中心",
            "智能育种 中国 实验室 课题组",
        ])
    if supplemental:
        queries.extend([
            f"{focus} 国家实验室 实验室主任",
            f"{focus} 中国科学院大学 实验室",
            f"{focus} 研究院 官方团队",
        ])

    def search(query: str) -> list[dict[str, Any]]:
        if not api_key:
            # ai4s-tool already ships the DDG adapter used by DeepSearch.  Use
            # it as a no-key fallback so sparse-domain refreshes still widen
            # the source pool in local deployments.
            try:
                from ddgs import DDGS
                with DDGS(timeout=float(os.getenv("STRATEGIC_MAP_SEARCH_TIMEOUT", "8"))) as client:
                    return [
                        {
                            "link": item.get("href", ""),
                            "title": item.get("title", ""),
                            "snippet": item.get("body", ""),
                        }
                        for item in client.text(query, max_results=10)
                    ]
            except Exception:
                return _duckduckgo_html_search(query, max_results=10) or _bing_html_search(query, max_results=10)
        try:
            response = requests.post(
                endpoint,
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                json={"q": query, "num": 10, "gl": "cn", "hl": "zh-cn"},
                timeout=float(os.getenv("STRATEGIC_MAP_SEARCH_TIMEOUT", "8")),
            )
            response.raise_for_status()
            body = response.json()
            values = body.get("organic", [])
            return [item for item in values if isinstance(item, dict)]
        except Exception:
            return _duckduckgo_html_search(query, max_results=10) or _bing_html_search(query, max_results=10)

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(6, len(queries))) as executor:
        for values in executor.map(search, queries):
            results.extend(values)

    reports: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    domain_terms = [term.lower() for term in _keywords(domain, subdomains)]
    for item in results:
        url = _clean(item.get("link") or item.get("url"))
        if not url or url in seen_urls:
            continue
        title = _clean(item.get("title"), url)
        snippet = _clean(item.get("snippet") or item.get("description"))
        content = f"{title}\n{snippet}"
        if domain_terms and not any(term in content.lower() for term in domain_terms):
            continue
        entities = _extract_entities(content)
        if not entities:
            continue
        seen_urls.add(url)
        report_id = f"web-{hashlib.sha1(url.encode('utf-8')).hexdigest()[:20]}"
        reports.append({
            "id": report_id,
            "title": title[:255],
            "date": "",
            "url": url,
            "sections": [(title, snippet[:1800])],
            "_web_entities": entities,
        })
        if len(reports) >= 48:
            break
    return reports


def _collect_candidate_material(
    domain: StrategicDomainRow,
    subdomains: list[StrategicDomainRow],
    *,
    supplemental: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run broad Daily search rounds and return evidence-backed candidates."""
    keyword_rounds = [
        _keywords(domain, subdomains),
        [domain.name, "国内优势团队"],
        [domain.name, "中国", "高校", "实验室"],
        [domain.name, "中国科学院", "研究团队"],
        [domain.name, "全国重点实验室"],
        [domain.name, "国家实验室"],
        [domain.name, "国内科研院所", "研究中心"],
        [domain.name, "国内领先团队", "课题组"],
    ]
    if supplemental:
        keyword_rounds.extend([
            [domain.name, "中国高校", "课题组"],
            [domain.name, "中科院", "研究所"],
            [domain.name, "国家重点实验室", "负责人"],
            [domain.name, "国内企业", "研发团队"],
        ])
    all_reports: dict[str, dict[str, Any]] = {}
    for keywords in keyword_rounds:
        for report in _load_daily_reports(list(dict.fromkeys(keywords)), limit=120):
            all_reports[report["id"]] = report
    # Always search the broader public web. Daily is a hint source, never a
    # gate: even a large archive can omit laboratories, PIs and people.
    # Report count alone is a poor proxy for candidate coverage: a Daily issue
    # can contain many generic reports but only a handful of identifiable
    # domestic institutions. Keep widening until the archive is genuinely
    # broad, then let the normal evidence gates decide what survives.
    for report in _load_web_search_reports(domain, subdomains, supplemental=supplemental):
        all_reports[report["id"]] = report

    materials: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    domain_terms = [term.lower() for term in _keywords(domain, subdomains)]
    for report in all_reports.values():
        for section_title, section_body in report.get("sections", []):
            content = f"{section_title}\n{section_body}"
            # Broad expansion rounds can match a report because it contains
            # generic words such as “国内” or “高校”.  Keep only sections that
            # also mention this domain, otherwise the model receives unrelated
            # institutions and tends to discard valid candidates under a long
            # context window.
            lowered_content = content.lower()
            if domain_terms and not any(term in lowered_content for term in domain_terms):
                continue
            for institution in _extract_entities(content):
                # Do not let a section that merely mentions a foreign lab make
                # a Chinese institution look domestic.  The institution hint
                # itself is checked again after normalization.
                institution = _normalise_institution_name(institution)
                if not _is_china_entity(institution) or _is_generic_entity(institution) or _is_prose_entity(institution):
                    continue
                key = (institution.casefold(), report["id"], section_title.casefold())
                if key in seen:
                    continue
                seen.add(key)
                urls = [report["url"], *_extract_urls(content)]
                materials.append(
                    {
                        "candidate_id": f"c{len(materials) + 1}",
                        "institution_hint": institution,
                        "section_title": _normalise_section_title(section_title),
                        "evidence": re.sub(r"\s+", " ", section_body).strip()[:1800],
                        "source_urls": list(dict.fromkeys(urls))[:8],
                        "report_id": report["id"],
                        "report_title": report["title"],
                        "report_date": report.get("date", ""),
                        "subdomain_id": next(
                            (
                                child.id
                                for child in subdomains
                                if child.name.lower() in content.lower()
                            ),
                            None,
                        ),
                    }
                )
    # Merge exact institution/section duplicates while keeping distinct
    # research lines from the same institution available to the second stage.
    grouped: dict[str, dict[str, Any]] = {}
    for item in materials:
        key = "|".join([
            _normalise_institution_name(item["institution_hint"]).casefold(),
            item.get("section_title", "").casefold(),
        ])
        previous = grouped.get(key)
        if not previous:
            grouped[key] = item
            continue
        previous["evidence"] = (previous["evidence"] + "\n" + item["evidence"])[:2600]
        previous["source_urls"] = list(dict.fromkeys(previous["source_urls"] + item["source_urls"]))[:12]
    # Keep the discovery stage bounded and auditable: approximately 25 distinct
    # institution/section candidates are sent to entity resolution. Additional
    # hits remain represented by reportCount and will be explored on the next
    # weekly run rather than overwhelming one LLM batch with noisy snippets.
    return list(grouped.values())[:25], list(all_reports.values())


def _enrich_team_people(domain: StrategicDomainRow, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One evidence-preserving extraction/review per team; no snippet re-extraction."""
    deadline = time.monotonic() + 600
    for record in records:
        if time.monotonic() >= deadline:
            record["_research"] = {"status": "budget_exhausted", "reviewed": None, "pages": []}
            continue
        record["_research"] = _run_team_research_agent(
            institution=record.get("institution_name", ""), team=record.get("team_name", ""),
            domain=domain.name, existing=record)
    return records


def _enrich_team_identity(domain: StrategicDomainRow, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve institution-only hits into named labs/centres before people search.

    Discovery often finds an institution but not its unit.  Run a separate
    identity pass using official-site-oriented queries and only accept names
    that occur verbatim in a search title/snippet; no model-memory names are
    allowed.
    """
    deadline = time.monotonic() + float(os.getenv("STRATEGIC_MAP_IDENTITY_DEADLINE_SECONDS", "180"))
    processed = 0
    for record in records:
        if time.monotonic() >= deadline:
            record["team_identity_missing_reason"] = "团队身份补搜阶段达到时间上限，已保留待补搜状态"
            continue
        if processed >= 25:
            record["team_identity_missing_reason"] = "候选超过本轮团队身份补全预算，已排队下轮补搜"
            continue
        processed += 1
        if _clean(record.get("team_name")) not in {"", _UNKNOWN_TEAM_LABEL}:
            continue
        institution = _clean(record.get("institution_name"))
        if not institution:
            continue
        queries = [
            f"{institution} {domain.name} 实验室",
            f"{institution} {domain.name} 研究中心",
            f"{institution} {domain.name} 课题组",
            f"{institution} {domain.name} 团队 教授",
        ]
        hits: list[dict[str, Any]] = []
        for query in queries:
            if time.monotonic() >= deadline:
                break
            # Bing HTML is available without credentials and is the final
            # fallback when DDGS/Serper is unavailable.
            try:
                response = requests.get(
                    "https://www.bing.com/search",
                    params={"q": query, "count": 8, "setlang": "zh-cn"},
                    headers={"User-Agent": "Mozilla/5.0"}, timeout=float(os.getenv("STRATEGIC_MAP_SEARCH_TIMEOUT", "8")),
                )
                response.raise_for_status()
                html = response.text
                for block in re.findall(r"<li class=\"b_algo\".*?</li>", html, flags=re.I | re.S):
                    title = re.search(r"<h2[^>]*>(.*?)</h2>", block, flags=re.I | re.S)
                    snippet = re.search(r"<p[^>]*>(.*?)</p>", block, flags=re.I | re.S)
                    href = re.search(r"<a href=\"([^\"]+)", block, flags=re.I)
                    clean = lambda value: re.sub(r"<[^>]+>", "", value or "").strip()
                    hits.append({"title": clean(title.group(1) if title else ""), "snippet": clean(snippet.group(1) if snippet else ""), "url": href.group(1) if href else ""})
            except Exception:
                continue
        # Prefer explicit named-unit phrases; discard generic institution
        # mentions and prose fragments.
        if hits and _llm_config():
            try:
                extracted = _llm_json_call(
                    system=("你是科研团队实体解析代理。只能依据搜索结果标题、摘要和 URL 判断，不得使用记忆。"
                            "请判断是否存在该机构在目标领域的具体实验室、研究中心或课题组。"
                            "若证据不足，team_name 必须为 null；不要把学院、学校、研究所本身当作团队。"
                            "只输出 JSON 对象，格式为 {\"team_name\":null,\"is_real_team\":false,\"evidence\":[] }。"),
                    user=json.dumps({"institution": institution, "domain": domain.name, "search_results": hits}, ensure_ascii=False),
                    timeout=int(os.getenv("STRATEGIC_MAP_TEAM_IDENTITY_TIMEOUT", "90")),
                )
            except Exception as exc:
                record["team_identity_missing_reason"] = f"团队身份 LLM 调用失败：{exc}"
                extracted = None
            if extracted and extracted.get("is_real_team") is True and _clean(extracted.get("team_name")):
                record["team_name"] = _clean(extracted.get("team_name"))[:180]
                record["team_type"] = _clean(extracted.get("team_type"))[:80]
                record["team_confidence"] = max(0.0, min(1.0, float(extracted.get("confidence") or 0.7)))
                record["team_evidence"] = extracted.get("evidence") if isinstance(extracted.get("evidence"), list) else []
                record["verification_status"] = "pending_llm_review"
                record["source_urls"] = list(dict.fromkeys([*record.get("source_urls", []), *(item.get("url", "") for item in hits if item.get("url"))]))[:16]
                continue
        candidates: list[tuple[str, str]] = []
        for hit in hits:
            text = f"{hit.get('title','')} {hit.get('snippet','')}"
            for match in re.finditer(r"([\u4e00-\u9fffA-Za-z0-9·&\-]{2,40}(?:实验室|研究中心|研究所|课题组|研究院|中心|团队))", text):
                name = re.sub(r"^.*?(?=(?:中国科学院|清华|北京|上海|浙江|复旦|南京|哈尔滨|西北|团队|实验室|研究中心))", "", match.group(1)).strip(" -:：，,")
                if name and name not in {_UNKNOWN_TEAM_LABEL, institution} and not _is_generic_entity(name):
                    candidates.append((name, hit.get("url", "")))
        if candidates:
            # Choose the most frequently repeated exact name.
            counts: dict[str, int] = {}
            for name, _ in candidates:
                counts[name] = counts.get(name, 0) + 1
            chosen = max(counts, key=counts.get)
            record["team_name"] = chosen[:180]
            record["source_urls"] = list(dict.fromkeys([*record.get("source_urls", []), *(u for n, u in candidates if n == chosen and u)]))[:16]
    return records


def _normalise_team_records(
    domain: StrategicDomainRow,
    materials: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize, filter and de-duplicate candidates before persistence."""
    if not materials:
        return []
    llm_records = _normalise_with_llm(domain, materials)
    source_by_id = {item["candidate_id"]: item for item in materials}
    # A missing LLM configuration is an intentional local fallback.  It still
    # performs the same schema, country and duplicate gates and never invents
    # a team name; this keeps development/offline installations usable.
    if llm_records is None:
        llm_records = [
            {
                "source_candidate_ids": [item["candidate_id"]],
                "institution_name": item["institution_hint"],
                "team_name": _UNKNOWN_TEAM_LABEL,
                # Offline fallback keeps source wording instead of fabricating
                # a generic mission statement. A later LLM extraction pass can
                # replace this pending description when connectivity returns.
                "description": re.sub(r"\s+", " ", item.get("evidence", "")).strip()[:200],
                "research_directions": [item["section_title"] or domain.name],
                "location": "中国（公开资料判定）",
                "is_domestic": True,
                "team_confidence": 0.2,
                "leader_confidence": 0.0,
                "member_confidence": 0.0,
                "evidence_urls": item.get("source_urls", []),
                "verification_status": "pending_llm_review",
            }
            for item in materials
        ]

    def normalise_person(raw: Any, *, leader: bool, source_urls: list[str]) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        name = re.sub(r"\s+", " ", _clean(raw.get("name"))).strip(" -*#，,;；")
        if not name or len(name) > 80 or _is_foreign_location(name):
            return None
        if len(name) < 2 or name in {"学院", "部常务副", "吴超副", "研究员", "教授", "主任"} or re.search(r"[\d：:]", name):
            return None
        urls = [
            url for url in list(dict.fromkeys([
                *source_urls,
                *(raw.get("source_urls") if isinstance(raw.get("source_urls"), list) else []),
            ]))
            if isinstance(url, str) and url.startswith("http")
        ][:12]
        return {
            "name": name,
            "title": _clean(raw.get("title") or raw.get("position"))[:160],
            "role": _clean(raw.get("role"), "团队负责人" if leader else "核心成员")[:120],
            "research_direction": _clean(raw.get("research_direction") or raw.get("researchDirection"))[:500],
            "bio": re.sub(r"\s+", " ", _clean(raw.get("bio") or raw.get("description"))).strip()[:1200],
            "avatar_url": _clean(raw.get("avatar_url") or raw.get("avatarUrl"))[:500],
            "profile_url": _clean(raw.get("profile_url") or raw.get("profileUrl"))[:500],
            "source_urls": urls,
            "source_type": _clean(raw.get("source_type") or raw.get("sourceType"), "公开来源")[:80],
            "is_leader": leader,
        }

    records: dict[str, dict[str, Any]] = {}
    for raw in llm_records:
        if not isinstance(raw, dict):
            continue
        source_ids = raw.get("source_candidate_ids") or raw.get("candidate_ids") or []
        if isinstance(source_ids, str):
            source_ids = [source_ids]
        sources = [source_by_id[item] for item in source_ids if item in source_by_id]
        if not sources:
            # Gate outputs that cannot be traced to the retrieved material.
            inst_hint = _normalise_institution_name(raw.get("institution_name"))
            sources = [
                item for item in materials
                if inst_hint and _normalise_institution_name(item["institution_hint"]) == inst_hint
            ]
        if not sources:
            continue
        institution = _normalise_institution_name(
            raw.get("institution_name") or sources[0]["institution_hint"]
        )
        if _is_prose_entity(institution):
            continue
        team_name = _clean(raw.get("team_name") or raw.get("team"), _UNKNOWN_TEAM_LABEL)
        team_name = _strip_leading_number(team_name)
        if _is_generic_entity(team_name) or _is_foreign_location(team_name):
            team_name = _UNKNOWN_TEAM_LABEL
        location = _clean(raw.get("location"), "中国（公开资料判定）")
        is_domestic = raw.get("is_domestic")
        if isinstance(is_domestic, str) and is_domestic.strip().lower() in {"false", "0", "no", "否"}:
            is_domestic = False
        if is_domestic is False or not _is_china_entity(institution) or _is_generic_entity(institution):
            continue
        if _is_foreign_location(location):
            continue
        directions = _normalise_directions(
            raw.get("research_directions") or raw.get("directions"),
            sources[0].get("section_title") or domain.name,
        )
        description = re.sub(r"\s+", " ", _clean(raw.get("description"))).strip()
        if not description:
            description = (
                f"公开资料显示，{institution}在{domain.name}相关方向有研究或应用线索；"
                "具体团队边界需结合来源进一步核验。"
            )
        source_urls = list(
            dict.fromkeys(
                url
                for source in sources
                for url in source.get("source_urls", [])
                if isinstance(url, str) and url.startswith("http")
            )
        )[:12]
        leader = normalise_person(raw.get("leader") or raw.get("leader_info"), leader=True, source_urls=source_urls)
        if leader:
            leader["is_leader"] = True
        raw_members = raw.get("members") or raw.get("core_members") or []
        if isinstance(raw_members, dict):
            raw_members = [raw_members]
        members: list[dict[str, Any]] = []
        for member in raw_members if isinstance(raw_members, list) else []:
            normalized_member = normalise_person(member, leader=False, source_urls=source_urls)
            if normalized_member:
                normalized_member["is_leader"] = bool(member.get("is_leader") is True)
            if normalized_member and normalized_member["name"] != (leader or {}).get("name"):
                if all(existing["name"] != normalized_member["name"] for existing in members):
                    members.append(normalized_member)
        key = _canonical_team_key(institution, team_name)
        candidate = {
            "institution_name": institution,
            "team_name": team_name,
            "description": description[:1200],
            "research_directions": directions,
            "location": location[:120],
            "is_domestic": True,
            "source_urls": source_urls,
            "evidence_summary": sources[0].get("evidence", "")[:1600],
            "report_id": sources[0].get("report_id", ""),
            "report_title": sources[0].get("report_title", ""),
            "recent_update": sources[0].get("report_date", ""),
            "subdomain_id": sources[0].get("subdomain_id"),
            "leader": leader,
            "members": members,
            "team_confidence": float(raw.get("team_confidence") or raw.get("confidence") or (0.35 if team_name == _UNKNOWN_TEAM_LABEL else 0.6)),
            "leader_confidence": float((leader or {}).get("confidence") or 0),
            "member_confidence": max([float(item.get("confidence") or 0) for item in members] or [0]),
            "evidence_urls": source_urls,
            "verification_status": "pending_llm_review",
        }
        existing = records.get(key)
        if existing:
            existing["source_urls"] = list(dict.fromkeys(existing["source_urls"] + source_urls))[:12]
            if len(candidate["description"]) > len(existing["description"]):
                existing["description"] = candidate["description"]
            existing["research_directions"] = _normalise_directions(
                existing["research_directions"] + candidate["research_directions"]
            )
            if not existing.get("leader") and candidate.get("leader"):
                existing["leader"] = candidate["leader"]
            known_members = {item["name"] for item in existing.get("members", [])}
            existing.setdefault("members", []).extend(
                item for item in candidate.get("members", []) if item["name"] not in known_members
            )
        else:
            records[key] = candidate
    # Do not cap the final snapshot at eight (or an arbitrary UI-sized number).
    # The map can retain every validated domestic team found by the search.
    return list(records.values())


def _candidate_id(domain_id: str, name: str) -> str:
    digest = hashlib.sha1(f"{domain_id}:{name}".encode("utf-8")).hexdigest()[:24]
    return f"team_{digest}"


def _merge_team_records(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge validated snapshots/candidates by canonical institution + team."""
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for item in group:
            key = _canonical_team_key(item.get("institution_name", ""), item.get("team_name", ""))
            if not key or key.startswith(":"):
                continue
            current = merged.get(key)
            if not current:
                merged[key] = dict(item)
                merged[key]["source_urls"] = list(item.get("source_urls", []))
                merged[key]["research_directions"] = list(item.get("research_directions", []))
                merged[key]["members"] = list(item.get("members", []))
                continue
            current["source_urls"] = list(dict.fromkeys([
                *current.get("source_urls", []), *item.get("source_urls", []),
            ]))[:12]
            current["research_directions"] = _normalise_directions([
                *current.get("research_directions", []), *item.get("research_directions", []),
            ])
            if len(item.get("description", "")) > len(current.get("description", "")):
                current["description"] = item["description"]
            if not current.get("leader") and item.get("leader"):
                current["leader"] = item["leader"]
            known = {member.get("name") for member in current.get("members", [])}
            current.setdefault("members", []).extend(
                member for member in item.get("members", []) if member.get("name") not in known
            )
    return list(merged.values())


def _validated_previous_records(
    domain: StrategicDomainRow,
    rows: list[StrategicTeamRow],
) -> list[dict[str, Any]]:
    """Convert only the previous structured snapshot into safe fallbacks.

    Legacy rows and generic placeholder team names are intentionally excluded;
    this prevents historical dirty data from being reintroduced merely to hit
    the eight-team target.
    """
    result: list[dict[str, Any]] = []
    for row in rows:
        institution = _normalise_institution_name(row.institution_name or row.name)
        team_name = _clean(row.team_name)
        if (
            "structured normalization" not in _clean(row.source).lower()
            or not row.is_domestic
            or not _is_china_entity(institution)
            or not team_name
            or team_name == _UNKNOWN_TEAM_LABEL
        ):
            continue
        result.append({
            "institution_name": institution,
            "team_name": team_name,
            "description": _clean(row.description or row.evidence_summary),
            "research_directions": _normalise_directions(row.research_directions, row.focus or domain.name),
            "location": _clean(row.location, "中国（公开资料判定）"),
            "is_domestic": True,
            "source_urls": list(row.source_urls or []),
            "evidence_summary": row.evidence_summary,
            "report_id": row.report_id,
            "report_title": row.report_title,
            "recent_update": row.recent_update,
            "subdomain_id": row.subdomain_id,
            "leader": None,
            "members": [],
        })
    return result


def _curated_fallback_records(domain: StrategicDomainRow) -> list[dict[str, Any]]:
    """Return auditable official-unit leads when search engines are blocked.

    These are never used to fabricate people: they only prevent an unavailable
    search provider from collapsing a domain to zero teams. The next refresh
    revalidates each URL and can replace/remove the lead.
    """
    result = []
    for item in _CURATED_WEB_UNITS.get(domain.name, []):
        result.append({
            **item,
            "description": f"官方机构页面公开列出的{item['team_name']}，待本轮搜索进一步核验团队负责人和成员。",
            "location": "中国大陆",
            "is_domestic": True,
            "evidence_summary": "官方机构入口作为公开 Web 补全线索；搜索引擎不可用时保留，下一轮刷新继续核验。",
            "report_id": "web-curated",
            "report_title": "官方机构公开入口",
            "recent_update": "",
            "subdomain_id": None,
            "leader": None,
            "members": [],
        })
    return result


def _upsert_team_people(session: Session, team: StrategicTeamRow,
                        leader: dict[str, Any] | None, members: list[dict[str, Any]]) -> None:
    from .team_research_store import upsert_people
    upsert_people(session, team, leader, members)


def _research_existing(session: Session, team: StrategicTeamRow) -> dict[str, Any]:
    people = session.query(StrategicPersonRow).filter_by(team_id=team.id, deleted=False).all()
    from .team_research_store import history, _verified_urls
    runs = history(session, team.id, "verified")
    known_sources = []
    if runs:
        previous = runs[0]["payload"].get("run") or {}
        supported = set(_verified_urls(previous.get("reviewed")))
        known_sources = [{"url": p["url"], "label": p.get("title", "已核验来源，需重新抓取")}
                         for p in previous.get("pages", []) if p["url"] in supported]
    return {"team_id": team.id, "institution_name": team.institution_name or team.name,
            "team_name": team.team_name, "description": team.description,
            "research_directions": team.research_directions,
            "source_urls": list(dict.fromkeys([*(team.evidence_urls or []), *(team.source_urls or []),
                           *(u for p in people for u in (p.source_urls or []))])),
            "people": [_person_to_dict(p) for p in people], "known_sources": known_sources}


def _refresh_one_team(session: Session, team: StrategicTeamRow, domain: StrategicDomainRow,
                      *, known_sources: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Explicit single-team update, using the same research/storage path as refresh.

    Caller supplies its session (including isolated SQLite in acceptance tests).
    No domain discovery, scheduler or implicit whole-domain refresh is invoked.
    """
    from .team_research_store import persist
    existing = _research_existing(session, team)
    if known_sources:
        existing["known_sources"] = known_sources
    run = _run_team_research_agent(institution=existing["institution_name"], team=team.team_name,
                                   domain=domain.name, existing=existing)
    published = persist(session, team, run)
    session.commit()
    return {"team_id": team.id, "published": published, "run": run}


def _team_alias_index(session: Session, rows: list[StrategicTeamRow]) -> dict[str, StrategicTeamRow]:
    """Only independently verified continuity permits an old name to reuse an ID."""
    from .team_research_store import history
    index = {_canonical_team_key(row.institution_name or row.name, row.team_name): row for row in rows}
    for row in rows:
        for entry in history(session, row.id, "verified"):
            payload = entry["payload"]
            reviewed = (payload.get("run") or {}).get("reviewed") or {}
            before = payload.get("before") or {}
            if payload.get("published") and reviewed.get("entity_relation") == "rename" and reviewed.get("entity_citations"):
                key = _canonical_team_key(before.get("institutionName", ""), before.get("teamName", ""))
                index.setdefault(key, row)
    return index


def _sync_domain_core(session: Session, domain: StrategicDomainRow) -> dict[str, Any]:
    subdomains = _domain_query(session, domain.id)
    materials, reports = _collect_candidate_material(domain, subdomains)
    old_rows = session.query(StrategicTeamRow).filter(
        StrategicTeamRow.domain_id == domain.id,
        StrategicTeamRow.deleted.is_(False),
    ).all()
    # Include soft-deleted historical rows when reactivating a deterministic
    # candidate id; otherwise a previously removed curated/team record can
    # collide with the same snapshot key on a later refresh.
    all_domain_rows = session.query(StrategicTeamRow).filter(
        StrategicTeamRow.domain_id == domain.id
    ).all()
    # Even an empty first pass gets the supplemental institutional queries
    # before we declare a failed refresh.  This is the same retry path used for
    # a short (<8) normalized result and prevents a narrow keyword match from
    # hiding a usable second search.
    if not materials:
        extra_materials, extra_reports = _collect_candidate_material(
            domain, subdomains, supplemental=True
        )
        materials = extra_materials
        reports = extra_reports
    if not materials:
        raise _SyncQualityError(f"{domain.name} 未检索到可追溯的国内机构资料，保留上一版数据")
    normalized = _normalise_team_records(domain, materials)
    # A short first pass triggers a second, differently-worded search.  The
    # second result is merged before the snapshot gate, so a single narrow
    # query cannot erase a previously complete domain.
    if len(normalized) < 8:
        extra_materials, extra_reports = _collect_candidate_material(
            domain, subdomains, supplemental=True
        )
        if extra_materials:
            extra_normalized = _normalise_team_records(domain, extra_materials)
            normalized = _merge_team_records(normalized, extra_normalized)
            reports = list({report["id"]: report for report in [*reports, *extra_reports]}.values())
            materials = [*materials, *extra_materials]
    if len(normalized) < 8:
        normalized = _merge_team_records(
            normalized,
            _validated_previous_records(domain, old_rows),
        )
    named_count = sum(1 for item in normalized if item.get("team_name") not in {"", _UNKNOWN_TEAM_LABEL})
    if named_count < 8:
        normalized = _merge_team_records(normalized, _curated_fallback_records(domain))
    # Enrich each normalized team independently.  Discovery and people
    # extraction are separate stages so an institution-level hit can no longer
    # silently become a fabricated team leader/member record.
    normalized = _enrich_team_identity(domain, normalized)
    known_teams = _team_alias_index(session, all_domain_rows)
    for candidate in normalized:
        match = known_teams.get(_canonical_team_key(candidate["institution_name"], candidate["team_name"]))
        if match is not None:
            existing = _research_existing(session, match)
            candidate["people"] = existing["people"]
            candidate["team_id"] = match.id
            candidate["known_sources"] = existing["known_sources"]
            candidate["source_urls"] = list(dict.fromkeys([*existing["source_urls"], *candidate.get("source_urls", [])]))
    normalized = _enrich_team_people(domain, normalized)
    # Independent verification agent reviews extraction output against the
    # original evidence before persistence. In offline mode records remain
    # pending_llm_review and are still protected by deterministic schema gates.
    normalized = _llm_verify_records(domain, normalized)
    # A one-row result from a broad report search is usually an incomplete
    # response or a parser regression.  Keep the previous snapshot instead of
    # replacing a useful list with it. Fewer than eight is allowed when the
    # source genuinely contains fewer. If several candidates were found but
    # the normalization stage only returns one or two, treat that as an
    # incomplete response; genuinely tiny searches remain valid.
    if not normalized or (len(normalized) < 3 and len(materials) >= 3):
        raise _SyncQualityError(
            f"{domain.name} 本次只得到 {len(normalized)} 条可核验国内团队，结果不完整，保留上一版数据"
        )

    old_by_key = known_teams
    # Incremental semantics: existing rows are never deleted merely because a
    # team was not returned by this search round. A refresh may discover only a
    # subset of the public web; retaining the historical row lets the database
    # accumulate evidence over time. Rows are soft-deleted only by explicit
    # administrative action or a later evidence-backed decision.

    from .team_research_store import persist
    published_rows = []
    for candidate in normalized:
        key = _canonical_team_key(candidate["institution_name"], candidate["team_name"])
        row = old_by_key.get(key)
        if row is None:
            # Pending candidates are retained separately by the research history;
            # do not publish fabricated institution/team placeholders as verified.
            review = (candidate.get("_research") or {}).get("reviewed") or {}
            if candidate.get("verification_status") != "verified":
                from .team_research_store import append_history
                append_history(session, _candidate_id(domain.id, key), "pending", {"run": candidate.get("_research"), "published": False})
                continue
            row = StrategicTeamRow(id=_candidate_id(domain.id, key), domain_id=domain.id,
                name=candidate["institution_name"], institution_name=candidate["institution_name"],
                team_name=candidate["team_name"], subdomain_id=candidate.get("subdomain_id"))
            session.add(row)
            session.flush()
        if persist(session, row, candidate.get("_research") or {"status": "pending"}):
            published_rows.append(row)
    session.flush()
    error_meta = session.query(StrategicSyncMetaRow).filter(
        StrategicSyncMetaRow.key == f"last_refresh_error:{domain.id}"
    ).first()
    if error_meta:
        session.delete(error_meta)
    session.commit()
    return {
        "provider": "AI4S Daily + public Web",
        "pipeline": "search(~25 candidates)→structured-normalization→domestic-filter→dedupe→person-verification→snapshot",
        "reportCount": len(reports),
        "candidateCount": len(materials),
        "teamCount": len({row.id for row in published_rows}),
        "namedTeamCount": len({row.id for row in published_rows}),
        "unknownTeamCount": sum(1 for item in normalized if item.get("team_name") in {"", _UNKNOWN_TEAM_LABEL}),
        "leaderCount": sum(1 for row in published_rows if _team_people(session, row.id)[0]),
        "memberTeamCount": sum(1 for row in published_rows if _team_people(session, row.id)[1]),
        "peopleSearchAttempted": sum(1 for item in normalized if (item.get("_research") or {}).get("counts", {}).get("llm", 0)),
        "peopleMissingReasons": [
            {"institution": item.get("institution_name"), "team": item.get("team_name"),
             "reason": str((item.get("_research") or {}).get("status", "pending")) + ": " +
                       str(((item.get("_research") or {}).get("reviewed") or {}).get("leader_missing_reason", ""))}
            for item in normalized if item.get("verification_status") != "verified" or not (
                ((item.get("_research") or {}).get("reviewed") or {}).get("leader"))
        ],
        "updatedAt": _now().isoformat(),
    }


def _person_snapshot(person: StrategicPersonRow) -> dict[str, Any]:
    """Convert a persisted person to the evidence shape used by review prompts."""
    return {
        "name": person.name,
        "title": person.title,
        "role": person.role,
        "research_direction": person.research_direction,
        "bio": person.bio,
        "homepage": person.profile_url,
        "avatar_url": person.avatar_url,
        "evidence_urls": list(person.source_urls or []),
        "verification_status": person.verification_status,
    }


def _review_team_merge(
    session: Session,
    row: StrategicTeamRow,
    candidate: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Ask the independent Verification Agent to resolve old/new conflicts.

    The deterministic layer only detects that two observations differ. It does
    not decide which leader/member is correct. When the verifier is unavailable
    the existing snapshot is retained and marked ``conflict`` for later review.
    """
    people = session.query(StrategicPersonRow).filter(
        StrategicPersonRow.team_id == row.id, StrategicPersonRow.deleted.is_(False)
    ).all()
    old_leader = next((_person_snapshot(p) for p in people if p.is_leader), None)
    new_leader = candidate.get("leader")
    if not old_leader or not isinstance(new_leader, dict) or not _clean(new_leader.get("name")):
        return candidate, "unchanged"
    if _clean(old_leader.get("name")).casefold() == _clean(new_leader.get("name")).casefold():
        return candidate, "unchanged"

    existing_members = [_person_snapshot(p) for p in people if not p.is_leader]
    prompt = {
        "institution_name": row.institution_name or row.name,
        "team_name": row.team_name,
        "old_record": {
            "leader": old_leader,
            "members": existing_members,
            "evidence_urls": list(row.evidence_urls or row.source_urls or []),
            "evidence_summary": row.evidence_summary,
        },
        "new_record": {
            "leader": new_leader,
            "members": candidate.get("members", []),
            "evidence_urls": candidate.get("evidence_urls") or candidate.get("source_urls", []),
            "evidence_summary": candidate.get("evidence_summary", ""),
        },
    }
    if not _llm_config():
        row.verification_status = "conflict"
        return {**candidate, "leader": None, "members": []}, "pending_review"
    system = (
        "你是战略图谱 Verification Agent。只根据旧记录、新调查结果及其来源判断冲突。"
        "优先官方团队/实验室主页、机构主页和带日期的正式页面；不能凭记忆。"
        "如果能纠正，输出 corrected_result；无法确认时 decision=revise，并保留旧负责人、标记冲突。"
        "只输出 JSON：{decision:'accept|revise|reject',confidence:0.0,problems:[],corrected_result:{leader:null,members:[]}}。"
    )
    try:
        result = _llm_json_call(
            system=system,
            user=json.dumps(prompt, ensure_ascii=False),
            timeout=int(os.getenv("STRATEGIC_MAP_CONFLICT_VERIFY_TIMEOUT", "120")),
        ) or {}
    except Exception:
        result = {}
    decision = _clean(result.get("decision"), "revise").lower()
    corrected = result.get("corrected_result") if isinstance(result.get("corrected_result"), dict) else {}
    if decision == "accept":
        if isinstance(corrected.get("leader"), dict) and _clean(corrected["leader"].get("name")):
            candidate["leader"] = corrected["leader"]
        return candidate, "accepted"
    # revise/reject never destroys the old row. Keep the new observation out of
    # the active roster and let the persisted conflict status surface it.
    row.verification_status = "conflict"
    candidate = {**candidate, "leader": None, "members": []}
    return candidate, "pending_review"


def _sync_domain(session: Session, domain: StrategicDomainRow) -> dict[str, Any]:
    """Public pipeline entry used by new-domain, manual and scheduled refreshes."""
    from .domain_research import sync_domain
    return sync_domain(session, domain,
        seconds=max(60,min(7200,int(os.getenv('STRATEGIC_MAP_DOMAIN_SECONDS','2400')))),
        team_seconds=max(60,min(300,int(os.getenv('STRATEGIC_MAP_TEAM_SECONDS','210')))))


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


def _record_refresh_failure(session: Session, domain: StrategicDomainRow, error: Exception) -> None:
    """Persist a short failure marker without touching the team snapshot."""
    key = f"last_refresh_error:{domain.id}"
    meta = session.query(StrategicSyncMetaRow).filter(StrategicSyncMetaRow.key == key).first()
    if not meta:
        meta = StrategicSyncMetaRow(key=key)
        session.add(meta)
    meta.value = _clean(error, "刷新失败")[:255]
    meta.updated_at = _now()
    session.commit()


def _last_refresh_error(session: Session, domain: StrategicDomainRow | None) -> str:
    if not domain:
        return ""
    meta = session.query(StrategicSyncMetaRow).filter(
        StrategicSyncMetaRow.key == f"last_refresh_error:{domain.id}"
    ).first()
    return meta.value if meta else ""


def _run_scheduled_refresh() -> None:
    """后台定时刷新：每周运行一次。"""
    with _SESSION_FACTORY() as session:
        _seed_defaults(session)
        if not _auto_refresh_due(session):
            return
        domains = _domain_query(session)
        successful = 0
        for domain in domains:
            try:
                _sync_domain(session, domain)
                successful += 1
            except Exception as exc:
                # 单个领域失败不阻塞其它领域；下一轮仍会重试。
                session.rollback()
                try:
                    _record_refresh_failure(session, domain, exc)
                except Exception:
                    session.rollback()
        # Advance the weekly watermark only after every domain produced a
        # valid snapshot.  A partial run must be retried next hour; otherwise
        # one failed domain would be hidden for a full week.
        if domains and successful == len(domains):
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

    if os.getenv('STRATEGIC_MAP_SCHEDULER_ENABLED', 'true').lower() not in {'1', 'true', 'yes'}:
        return

    def loop() -> None:
        while True:
            # A development reload must not immediately start network research.
            # Hourly checks still enforce the persisted weekly refresh watermark.
            time.sleep(60 * 60)
            try:
                _run_scheduled_refresh()
            except Exception:
                # 后台任务不能影响 HTTP 服务生命周期。
                pass

    thread = threading.Thread(target=loop, name="strategic-map-weekly-refresh", daemon=True)
    thread.start()


@router.get("")
def get_strategic_map(
    refresh: bool = Query(False),
    domain_id: str | None = Query(None),
) -> dict[str, Any]:
    with _SESSION_FACTORY() as session:
        roots = _domain_query(session)
        target = _get_domain(session, domain_id) if domain_id else (roots[0] if roots else None)
        source: dict[str, Any] = {"provider": "本地已保存研判数据", "refreshed": False}
        if target and (last_error := _last_refresh_error(session, _get_root_domain(session, target.id))):
            source["lastError"] = last_error
        if refresh and target:
            root = _get_root_domain(session, target.id)
            try:
                source = _sync_domain(session, root)
            except _SyncQualityError as exc:
                session.rollback()
                _record_refresh_failure(session, root, exc)
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            source["refreshed"] = True
        domains = [_domain_to_dict(session, domain) for domain in _domain_query(session)]
        teams = [
            _team_to_dict(team, session)
            for team in session.query(StrategicTeamRow)
            .filter(StrategicTeamRow.deleted.is_(False), StrategicTeamRow.verification_status == "verified")
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
        try:
            source = _sync_domain(session, row)
        except _SyncQualityError as exc:
            session.rollback()
            _record_refresh_failure(session, row, exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc
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
    # The handler is called directly by the sync endpoint and by a few
    # internal tests; FastAPI's ``Query(None)`` default is otherwise passed to
    # SQLAlchemy as an object instead of Python ``None``.
    if not isinstance(subdomain_id, str) or not subdomain_id.strip():
        subdomain_id = None
    with _SESSION_FACTORY() as session:
        domain = _get_root_domain(session, domain_id)
        source: dict[str, Any] = {"provider": "本地已保存研判数据", "refreshed": False}
        if last_error := _last_refresh_error(session, domain):
            source["lastError"] = last_error
        if refresh:
            try:
                source = _sync_domain(session, domain)
            except _SyncQualityError as exc:
                session.rollback()
                _record_refresh_failure(session, domain, exc)
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            source["refreshed"] = True
        query = session.query(StrategicTeamRow).filter(
            StrategicTeamRow.domain_id == domain.id,
            StrategicTeamRow.deleted.is_(False),
            StrategicTeamRow.verification_status == "verified",
        )
        if subdomain_id:
            query = query.filter(StrategicTeamRow.subdomain_id == subdomain_id)
        teams = query.order_by(StrategicTeamRow.updated_at.desc(), StrategicTeamRow.name.asc()).all()
        return _response({
            "teams": [_team_to_dict(team, session) for team in teams],
            "source": source,
            # Refresh can create or remove populated subdomains; return the
            # filtered domain so the left navigation stays aligned immediately.
            "domain": _domain_to_dict(session, domain),
        })


@router.get("/teams/{team_id}")
def get_team_detail(team_id: str) -> dict[str, Any]:
    """Read a team, its verified leader and core members from SQLite."""
    with _SESSION_FACTORY() as session:
        team = session.query(StrategicTeamRow).filter(
            StrategicTeamRow.id == team_id,
            StrategicTeamRow.deleted.is_(False),
        ).first()
        if not team:
            raise HTTPException(status_code=404, detail="团队不存在")
        leader, members = _team_people(session, team.id)
        domain = _get_root_domain(session, team.domain_id)
        subdomain = None
        if team.subdomain_id:
            subdomain = session.query(StrategicDomainRow).filter(
                StrategicDomainRow.id == team.subdomain_id,
                StrategicDomainRow.deleted.is_(False),
            ).first()
        return _response({
            "team": _team_to_dict(team, session),
            "leader": leader,
            "members": members,
            "domain": _domain_to_dict(session, domain),
            "subdomain": {
                "id": subdomain.id,
                "name": subdomain.name,
                "label": subdomain.name,
                "description": subdomain.description,
                "parentId": subdomain.parent_id,
            } if subdomain else None,
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
        from .team_research_store import append_history
        append_history(session, team.id, "manual", {"fields": [key for key, value in payload.model_dump().items() if value is not None]})
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
        return _response(_team_to_dict(team, session))


@router.post("/domains/{domain_id}/sync")
def sync_domain(domain_id: str) -> dict[str, Any]:
    return list_domain_teams(domain_id, refresh=True, subdomain_id=None)
