# -*- coding: utf-8 -*-
# =====================
#
# Author: liumin.423
# Date:   2025/7/9
# =====================
"""DeepSearch 查询拆解与章节搜索词。"""

import os
import re
import time
import json

from loguru import logger

from ai4s_tool.util.llm_util import ask_llm
from ai4s_tool.util.llm_util import resolve_openai_compat_env
from ai4s_tool.util.prompt_util import get_prompt
from ai4s_tool.model.context import RequestIdCtx
from ai4s_tool.util.log_util import timer


# AI4S 研判报告的固定章节契约。只对明显的研判/研究报告查询启用，避免改变
# 普通问答的章节拆解行为；缺失的章节会带着专门的检索任务补入搜索计划。
AI4S_REPORT_CHAPTERS = (
    ("事件概览", "梳理事件背景、触发时间线、参与机构、公开数字和当前状态。"),
    ("科学问题", "说明研究对象、科学假设、评价指标、边界条件和待解决的核心问题。"),
    ("技术路线", "拆解模型或方法、数据、训练/实验流程、输入输出和工程实现。"),
    ("主要创新", "逐条比较相对前序工作的变化，说明每项创新的证据和适用边界。"),
    ("论文团队与机构", "核对论文、作者、研究机构、研究团队、代表成果及其关系。"),
    ("前序工作", "按时间顺序梳理继承、复用、演进和关键转折。"),
    ("竞争路线", "比较国内外相关路线的目标、方法、数据、公开结果、许可和部署门槛。"),
    ("AI4S意义", "只根据前文事实分析对科学研究、实验闭环、数据和产业流程的具体影响。"),
    ("待观察问题", "列出尚未解决的问题、可验证信号、观察方法、时间窗口和资料缺口。"),
    ("来源证据", "集中整理原始论文、机构官网、项目/代码仓库和其他来源的标题、作者、日期与 URL。"),
)


def _is_ai4s_report_query(query: str) -> bool:
    """判断查询是否要求 AI4S 研判报告，而不是普通事实问答。"""
    text = str(query or "").strip().lower()
    markers = (
        "ai4s", "研判报告", "研究报告", "技术路线", "竞争格局", "论文团队",
        "前序工作", "科学问题", "研究机构", "研究路线",
    )
    return any(marker in text for marker in markers)


def _chapter_category(title: str) -> str | None:
    """把模型返回的章节标题映射到固定章节职责。"""
    text = str(title or "").strip().lower()
    groups = {
        "事件概览": ("事件", "背景", "概览", "时间线", "发布", "进展"),
        "科学问题": ("科学问题", "研究问题", "研究对象", "任务定义", "评价指标"),
        "技术路线": ("技术路线", "架构", "模型", "方法", "算法", "数据流程"),
        "主要创新": ("主要创新", "创新", "贡献", "改进"),
        "论文团队与机构": ("论文", "团队", "机构", "作者", "研究人员", "组织"),
        "前序工作": ("前序", "沿革", "历史", "演进", "发展", "继承"),
        "竞争路线": ("竞争", "竞品", "竞争格局", "竞争路线", "国内外路线"),
        "AI4S意义": ("ai4s意义", "ai4s 价值", "意义", "影响", "价值"),
        "待观察问题": ("待观察", "未解决", "局限", "风险", "趋势", "问题"),
        "来源证据": ("来源", "证据", "参考文献", "参考资料"),
    }
    # 先匹配更具体的短语，避免“问题”把“科学问题”误判到待观察问题。
    for category, markers in groups.items():
        if any(marker in text for marker in markers):
            return category
    return None


def ensure_ai4s_report_structure(chapters: list[dict], query: str) -> list[dict]:
    """为 AI4S 研判补齐固定章节，同时保留模型已规划的搜索词和内容。"""
    if not _is_ai4s_report_query(query):
        return chapters

    assigned: dict[str, dict] = {}
    extras: list[dict] = []
    for chapter in chapters or []:
        category = _chapter_category(chapter.get("title", ""))
        if category and category not in assigned:
            assigned[category] = dict(chapter)
        else:
            extras.append(dict(chapter))

    result: list[dict] = []
    for title, research_task in AI4S_REPORT_CHAPTERS:
        item = assigned.get(title)
        if item is None:
            # 每个补齐章节只发一个聚焦查询，避免固定章节把检索量无界放大。
            item = {
                "title": title,
                "content": research_task,
                "search_queries": [f"{query} {title} {research_task}"],
            }
        else:
            item["title"] = title
            item["content"] = str(item.get("content") or research_task).strip()
            item["search_queries"] = normalize_search_queries(item.get("search_queries"))
            if not item["search_queries"]:
                item["search_queries"] = [f"{query} {title}"]
        result.append(item)

    # 保留模型提出的额外专题，但放在固定章节之后；通常不会超过一个。
    result.extend(extras)
    return result


@timer()
async def query_decompose(query: str, **kwargs):
    """将复杂问题拆成章节，并为每个章节生成搜索关键词。"""
    llm_config = resolve_openai_compat_env("DEEPSEARCH")
    model = (
        os.getenv("QUERY_DECOMPOSE_MODEL") or os.getenv("DEFAULT_MODEL") or "gpt-4.1"
    )
    current_date = time.strftime("%Y-%m-%d", time.localtime())
    prompt = get_prompt("deepsearch")["chapter_structure_prompt"].format(
        query=query,
        current_date=current_date,
        max_queries=os.getenv("QUERY_DECOMPOSE_MAX_SIZE", 5),
        max_search_queries=os.getenv("CHAPTER_SEARCH_QUERY_MAX_SIZE", 3),
    )
    messages = [
        {
            "role": "system",
            "content": prompt,
        },
        {"role": "user", "content": "请只输出符合要求的 JSON 数组。"},
    ]
    extend_queries = ""
    async for chunk in ask_llm(
        messages=messages,
        model=model,
        stream=True,
        only_content=True,  # 只返回内容
        api_base=llm_config["api_base"],
        api_key=llm_config["api_key"],
    ):
        if chunk:
            extend_queries += chunk

    chapters = parse_report_structure(extend_queries, fallback_query=query)
    chapters = ensure_ai4s_report_structure(chapters, query)
    logger.debug(
        f"{RequestIdCtx.request_id} query_decompose queries completed, "
        f"chars={len(extend_queries)} chapters={len(chapters)}"
    )
    return chapters


def normalize_search_queries(value) -> list[str]:
    """将模型返回的搜索词统一为去重后的字符串列表。"""
    values = value if isinstance(value, list) else [value]
    normalized = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def parse_report_structure(content: str, fallback_query: str = "") -> list[dict]:
    """解析章节和每章搜索词，并兼容旧版 markdown 查询列表。"""
    text = (content or "").strip()
    if text:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                parsed = parsed.get("chapters") or parsed.get("items") or []
            if isinstance(parsed, list):
                chapters = []
                for item in parsed:
                    if isinstance(item, str) and item.strip():
                        value = item.strip()
                        chapters.append(
                            {
                                "title": value,
                                "content": value,
                                "search_queries": [value],
                            }
                        )
                    elif isinstance(item, dict):
                        title = str(item.get("title") or item.get("name") or "").strip()
                        content_text = str(
                            item.get("content") or item.get("description") or title
                        ).strip()
                        if title:
                            search_queries = normalize_search_queries(
                                item.get("search_queries") or item.get("queries")
                            )
                            if not search_queries and content_text:
                                search_queries = [content_text]
                            chapters.append(
                                {
                                    "title": title,
                                    "content": content_text or title,
                                    "search_queries": search_queries,
                                }
                            )
                if chapters:
                    return chapters
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

    legacy_queries = re.findall(r"^- (.+)$", text, re.MULTILINE)
    if legacy_queries:
        return [
            {
                "title": item.strip(),
                "content": item.strip(),
                "search_queries": [item.strip()],
            }
            for item in legacy_queries
            if item.strip()
        ]

    fallback = (fallback_query or "").strip()
    return (
        [{"title": fallback, "content": fallback, "search_queries": [fallback]}]
        if fallback
        else []
    )


if __name__ == "__main__":
    pass
