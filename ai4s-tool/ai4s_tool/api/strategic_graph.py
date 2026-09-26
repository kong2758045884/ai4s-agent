"""Hyper-Extract graph adapter for the Strategic Map workspace.

The browser talks only to ai4s-tool. Hyper's OntoSight server remains an
optional sidecar for semantic search, chat and the original scan pipeline;
graph browsing also has a read-only snapshot fallback for local development.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import requests
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Integer, JSON, String, Text

from . import strategic_map as _strategic_map


router = APIRouter(prefix="/strategic-map/graph", tags=["strategic_graph"])

GraphScope = Literal["domestic", "international"]
GraphCluster = Literal["高峰", "高原"]

_HYPER_BASE_URL = os.getenv("HYPEREXTRACT_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
_HYPER_TIMEOUT = max(2.0, float(os.getenv("HYPEREXTRACT_HTTP_TIMEOUT", "20")))
_SIDE_CAR_LOCK = threading.RLock()
_SCOPE_TO_GRAPH = {"domestic": "zn", "international": "gw"}
_ROOT_CLUSTER = {
    "科学通用底座": "高原",
    "通用 AI": "高原",
    "高能物理与量子科技": "高峰",
    "化学与材料": "高峰",
    "生命科学与医学": "高峰",
    "地球科学": "高峰",
}
_SUBDOMAIN_HINTS = {
    "科研数据与知识工程": ("科研数据", "知识工程", "知识图谱", "数据库", "数据平台"),
    "科学计算与仿真": ("科学计算", "仿真", "数值模拟", "求解器", "计算平台"),
    "科学基础模型": ("科学基础模型", "科学大模型", "预训练模型", "基础模型"),
    "自主实验室与科研Agent": ("自主实验室", "自动化实验室", "科研agent", "科研智能体", "机器人科学家"),
    "基础模型与多模态": ("基础模型", "大模型", "多模态", "视觉语言"),
    "推理与智能体": ("推理", "智能体", "agent", "规划"),
    "AI系统与算力": ("ai系统", "算力", "芯片", "训练系统", "推理系统"),
    "安全评测与治理": ("安全", "评测", "治理", "可信", "对齐"),
    "高能粒子物理": ("高能物理", "粒子物理", "对撞机", "粒子"),
    "核物理与加速器": ("核物理", "加速器", "核科学"),
    "量子计算与模拟": ("量子计算", "量子模拟", "量子算法"),
    "量子通信与精密测量": ("量子通信", "精密测量", "量子测量", "量子传感"),
    "计算化学与分子设计": ("计算化学", "分子设计", "分子模拟", "化学智能"),
    "材料发现与材料基因组": ("材料发现", "材料基因组", "计算材料"),
    "催化与能源材料": ("催化", "能源材料", "电池", "光伏"),
    "合金与结构材料": ("合金", "结构材料", "金属材料"),
    "蛋白质结构与设计": ("蛋白质结构", "蛋白设计", "结构生物"),
    "药物研发": ("药物研发", "药物发现", "制药", "靶点"),
    "基因组与单细胞": ("基因组", "单细胞", "转录组"),
    "医学影像与临床AI": ("医学影像", "临床ai", "诊断", "医疗"),
    "合成生物与智能育种": ("合成生物", "智能育种", "农业生物"),
    "天气与气候": ("天气", "气候", "气象"),
    "遥感与地理空间": ("遥感", "地理空间", "卫星"),
    "海洋与水文": ("海洋", "水文", "水资源"),
    "地质资源与灾害": ("地质", "资源勘探", "灾害", "地震"),
}


class GraphQuery(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    scope: GraphScope = "domestic"
    cluster: GraphCluster = "高峰"
    domain_id: str | None = None
    subdomain_id: str | None = None


class GraphScanRequest(BaseModel):
    keyword: str = Field(min_length=1, max_length=120)
    scope: GraphScope = "domestic"
    max_candidates: int = Field(default=100, ge=1, le=200)
    domain_id: str | None = Field(default=None, max_length=64)
    subdomain_id: str | None = Field(default=None, max_length=64)


class StrategicGraphScanTaskRow(_strategic_map._Base):
    """Durable copy of a Hyper scan and its evidence-scored candidates."""

    __tablename__ = "strategic_map_graph_scan_task"

    id = Column(String(64), primary_key=True)
    hyper_job_id = Column(String(64), nullable=False, default="")
    scope = Column(String(24), nullable=False, index=True)
    keyword = Column(String(120), nullable=False, index=True)
    domain_id = Column(String(64), nullable=True, index=True)
    subdomain_id = Column(String(64), nullable=True, index=True)
    max_candidates = Column(Integer, nullable=False, default=100)
    state = Column(String(24), nullable=False, default="accepted", index=True)
    stage = Column(String(80), nullable=False, default="排队中")
    progress = Column(JSON, nullable=False, default=dict)
    result = Column(JSON, nullable=False, default=dict)
    error = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=_strategic_map._now)
    started_at = Column(DateTime, nullable=True)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=_strategic_map._now,
        onupdate=_strategic_map._now,
    )
    finished_at = Column(DateTime, nullable=True)


_strategic_map._Base.metadata.create_all(_strategic_map._ENGINE)
_SCAN_SESSION_FACTORY = _strategic_map._SESSION_FACTORY
_SCAN_SCORE_VERSION = "hybrid-evidence-v4"
_SCAN_POLL_INTERVAL = max(0.2, float(os.getenv("STRATEGIC_GRAPH_SCAN_POLL_INTERVAL", "1")))
_SCAN_TIMEOUT = max(60.0, float(os.getenv("STRATEGIC_GRAPH_SCAN_TIMEOUT", "900")))
_TEAM_MARKERS = (
    "团队",
    "实验室",
    "研究组",
    "课题组",
    "研究中心",
    "laboratory",
    " lab",
    " group",
    " team",
)
_QUALITY_MARKERS = (
    "nature",
    "science",
    "cell",
    "顶刊",
    "开源",
    "发布",
    "突破",
    "首个",
    "领先",
    "一等奖",
    "特等奖",
)
_GENERIC_SUBDOMAIN_HINTS = frozenset(
    {"研究", "方向", "领域", "技术", "应用", "系统", "平台", "基础", "模型", "智能"}
)


def _response(data: Any) -> dict[str, Any]:
    return {"code": 200, "data": data}


def _request_json(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> Any:
    try:
        with requests.Session() as client:
            client.trust_env = False
            response = client.request(
                method,
                f"{_HYPER_BASE_URL}{path}",
                params=params,
                json=body,
                timeout=timeout or _HYPER_TIMEOUT,
            )
            response.raise_for_status()
            return response.json()
    except (requests.RequestException, ValueError) as exc:
        detail = ""
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                detail = str(response.json().get("detail") or response.json().get("error") or "")
            except (ValueError, AttributeError):
                detail = response.text[:300]
        message = detail or f"Hyper-Extract 服务不可用：{type(exc).__name__}"
        raise HTTPException(status_code=503, detail=message) from exc


def _activate(scope: GraphScope, cluster: GraphCluster) -> None:
    graph = _SCOPE_TO_GRAPH[scope]
    _request_json("GET", "/api/graph", params={"name": graph})
    _request_json("GET", "/api/cluster", params={"name": cluster})


def _all_remote_items(path: str) -> list[dict[str, Any]]:
    page = 0
    result: list[dict[str, Any]] = []
    while page < 10:
        payload = _request_json(
            "GET",
            path,
            params={"page": page, "page_size": 2000},
        )
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            break
        result.extend(item for item in items if isinstance(item, dict))
        if not payload.get("has_next"):
            break
        page += 1
    return result


def _remote_graph(scope: GraphScope, cluster: GraphCluster) -> dict[str, Any]:
    with _SIDE_CAR_LOCK:
        _activate(scope, cluster)
        meta = _request_json("GET", "/api/meta")
        nodes = _all_remote_items("/api/nodes_paginated")
        edges = _all_remote_items("/api/edges_paginated")
    return {"nodes": nodes, "edges": edges, "meta": meta, "provider": "Hyper-Extract"}


def _snapshot_root() -> Path | None:
    configured = os.getenv("STRATEGIC_MAP_HYPER_SNAPSHOT_DIR", "").strip()
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path(__file__).resolve().parents[3] / "runtime" / "hyper-snapshot",
        Path(__file__).resolve().parents[4] / "Hyper-Extract-1" / "Hyper-Extract",
        Path(__file__).resolve().parents[4] / "Hyper-Extract",
    ]
    for candidate in candidates:
        if candidate and (candidate / "ZN" / "data.json").is_file():
            return candidate
    return None


def _snapshot_updated_at(root: Path, scope: GraphScope) -> str:
    folder = "ZN" if scope == "domestic" else "GW"
    try:
        payload = json.loads((root / folder / "metadata.json").read_text(encoding="utf-8"))
        value = str(payload.get("updated_at") or "").strip()
        return datetime.fromisoformat(value).isoformat() if value else ""
    except (OSError, ValueError, AttributeError, TypeError):
        return ""


def _remote_scan_available() -> bool:
    """The stock Hyper graph API need not include our optional scan extension."""
    try:
        schema = _request_json("GET", "/openapi.json", timeout=3)
    except HTTPException:
        return False
    paths = schema.get("paths") if isinstance(schema, dict) else None
    scan = paths.get("/api/scan") if isinstance(paths, dict) else None
    return isinstance(scan, dict) and "post" in scan and "get" in scan


def _snapshot_graph(scope: GraphScope, cluster: GraphCluster) -> dict[str, Any]:
    root = _snapshot_root()
    if root is None:
        raise HTTPException(
            status_code=503,
            detail="Hyper-Extract 服务未启动，且未配置图谱快照目录",
        )
    folder = "ZN" if scope == "domestic" else "GW"
    try:
        payload = json.loads((root / folder / "data.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Hyper-Extract 图谱快照读取失败") from exc
    raw_nodes = [item for item in payload.get("nodes", []) if isinstance(item, dict) and item.get("name")]
    raw_edges = [
        item
        for item in payload.get("edges", [])
        if isinstance(item, dict) and item.get("source") and item.get("target")
    ]
    descendants: set[str] = {cluster}
    changed = True
    while changed:
        changed = False
        for edge in raw_edges:
            if edge.get("type") == "隶属" and edge["target"] in descendants and edge["source"] not in descendants:
                descendants.add(edge["source"])
                changed = True
    nodes = [
        {
            "id": item["name"],
            "label": f"{item['name']} ({item.get('level') or '节点'})",
            "type": "node",
            "data": {
                "label": f"{item['name']} ({item.get('level') or '节点'})",
                "raw": item,
            },
        }
        for item in raw_nodes
        if item["name"] in descendants
    ]
    edges = []
    for item in raw_edges:
        if item["source"] not in descendants or item["target"] not in descendants:
            continue
        edge_id = hashlib.sha1(
            f"{item['source']}:{item['target']}:{item.get('type', '')}".encode("utf-8")
        ).hexdigest()[:12]
        edges.append(
            {
                "id": edge_id,
                "source": item["source"],
                "target": item["target"],
                "label": item.get("type") or "相关",
                "type": "edge",
                "data": {
                    "label": item.get("type") or "相关",
                    "raw": item,
                },
            }
        )
    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "type": "graph",
            "features": {"search": True, "chat": True, "scan": False},
            "stats": {"Nodes": len(nodes), "Edges": len(edges)},
        },
        "provider": "Hyper-Extract snapshot",
    }


def _snapshot_all(scope: GraphScope) -> dict[str, Any]:
    root = _snapshot_root()
    if root is None:
        raise HTTPException(
            status_code=503,
            detail="未配置 Hyper-Extract 图谱快照目录",
        )
    folder = "ZN" if scope == "domestic" else "GW"
    try:
        payload = json.loads((root / folder / "data.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Hyper-Extract 图谱快照读取失败") from exc
    raw_nodes = [
        item
        for item in payload.get("nodes", [])
        if isinstance(item, dict) and item.get("name")
    ]
    raw_edges = [
        item
        for item in payload.get("edges", [])
        if isinstance(item, dict) and item.get("source") and item.get("target")
    ]
    nodes = [
        {
            "id": item["name"],
            "label": f"{item['name']} ({item.get('level') or '节点'})",
            "type": "node",
            "data": {
                "label": f"{item['name']} ({item.get('level') or '节点'})",
                "raw": item,
            },
        }
        for item in raw_nodes
    ]
    edges = []
    for item in raw_edges:
        edge_id = hashlib.sha1(
            f"{item['source']}:{item['target']}:{item.get('type', '')}".encode("utf-8")
        ).hexdigest()[:12]
        edges.append(
            {
                "id": edge_id,
                "source": item["source"],
                "target": item["target"],
                "label": item.get("type") or "相关",
                "type": "edge",
                "data": {
                    "label": item.get("type") or "相关",
                    "raw": item,
                },
            }
        )
    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "type": "graph",
            "features": {"search": True, "chat": True, "scan": False},
            "stats": {"Nodes": len(nodes), "Edges": len(edges)},
        },
        "provider": "Hyper-Extract snapshot",
    }


def _graph_base(scope: GraphScope, cluster: GraphCluster) -> dict[str, Any]:
    try:
        return _remote_graph(scope, cluster)
    except HTTPException:
        try:
            return _snapshot_graph(scope, cluster)
        except HTTPException:
            # Saved strategic-map teams still form a useful overlay when the
            # optional Hyper sidecar and snapshot directory are both absent.
            return {
                "nodes": [],
                "edges": [],
                "meta": {"type": "graph", "features": {}, "stats": {"Nodes": 0, "Edges": 0}},
                "provider": "strategic-map",
            }


def _context_graph_base(
    scope: GraphScope,
    cluster: GraphCluster,
    domain_name: str,
) -> dict[str, Any]:
    if domain_name:
        try:
            return _snapshot_all(scope)
        except HTTPException:
            pass
    return _graph_base(scope, cluster)


def _context_names(
    domain_id: str | None,
    subdomain_id: str | None,
) -> tuple[str, str]:
    if subdomain_id and not domain_id:
        raise HTTPException(status_code=400, detail="选择子领域时必须提供所属领域")
    if not domain_id:
        return "", ""
    from . import strategic_map as sm

    with sm._SESSION_FACTORY() as session:
        domain = session.query(sm.StrategicDomainRow).filter(
            sm.StrategicDomainRow.id == domain_id,
            sm.StrategicDomainRow.parent_id.is_(None),
            sm.StrategicDomainRow.deleted.is_(False),
        ).first()
        if domain is None:
            raise HTTPException(status_code=404, detail="领域不存在")
        subdomain = None
        if subdomain_id:
            subdomain = session.query(sm.StrategicDomainRow).filter(
                sm.StrategicDomainRow.id == subdomain_id,
                sm.StrategicDomainRow.parent_id == domain.id,
                sm.StrategicDomainRow.deleted.is_(False),
            ).first()
            if subdomain is None:
                raise HTTPException(status_code=404, detail="子领域不存在或不属于当前领域")
        return domain.name, subdomain.name if subdomain else ""


def _element_raw(item: dict[str, Any]) -> dict[str, Any]:
    data = item.get("data")
    if not isinstance(data, dict):
        return {}
    raw = data.get("raw")
    return raw if isinstance(raw, dict) else {}


def _element_name(item: dict[str, Any]) -> str:
    return str(
        _element_raw(item).get("name")
        or (item.get("data") or {}).get("label")
        or item.get("label")
        or item.get("id")
        or ""
    ).strip()


def _normalized_name(value: str) -> str:
    return re.sub(r"[\s·•_\-]+", "", value).casefold()


def _subdomain_hints(name: str) -> tuple[str, ...]:
    """Expand an unregistered child domain into deterministic graph-search hints."""
    configured = _SUBDOMAIN_HINTS.get(name)
    if configured:
        return tuple(dict.fromkeys((name, *configured)))

    hints: list[str] = []

    def add(value: str) -> None:
        normalized = _normalized_name(value)
        if len(normalized) >= 2 and normalized not in hints:
            hints.append(normalized)

    add(name)
    for token in re.findall(r"[a-zA-Z][a-zA-Z0-9.+#-]*", name):
        add(token)
    for run in re.findall(r"[\u3400-\u9fff]{2,}", name):
        add(run)
        for width in range(min(4, len(run)), 1, -1):
            for start in range(len(run) - width + 1):
                hint = run[start : start + width]
                if hint not in _GENERIC_SUBDOMAIN_HINTS:
                    add(hint)
    return tuple(hints[:40])


def _subdomain_match_score(text: str, hints: tuple[str, ...]) -> int:
    normalized = _normalized_name(text)
    return sum(
        max(1, min(len(hint), 6) - 1)
        for hint in hints
        if hint and hint in normalized
    )


def _explicit_team_identity(name: str, profile: str = "") -> bool:
    """Accept an entity only when its name or opening definition identifies a team."""
    name_text = f" {name.casefold()} "
    if any(marker.casefold() in name_text for marker in _TEAM_MARKERS):
        return True
    opening = re.split(r"[,，。；;：:\n]", profile.strip(), maxsplit=1)[0].casefold()
    return bool(opening) and any(
        marker.casefold() in f" {opening} " for marker in _TEAM_MARKERS
    )


def _relation_name(edge: dict[str, Any]) -> str:
    return str(
        _element_raw(edge).get("type")
        or (edge.get("data") or {}).get("label")
        or edge.get("label")
        or ""
    ).strip()


def _find_node_id(nodes: list[dict[str, Any]], name: str) -> str:
    wanted = _normalized_name(name)
    for node in nodes:
        if _normalized_name(_element_name(node)) == wanted:
            return str(node.get("id") or "")
    return ""


def _induced_graph(
    base: dict[str, Any],
    allowed: set[str],
    *,
    domain_name: str,
    subdomain_name: str,
    original_count: int,
) -> dict[str, Any]:
    nodes = [
        item
        for item in base.get("nodes", [])
        if isinstance(item, dict) and str(item.get("id")) in allowed
    ]
    edges = [
        item
        for item in base.get("edges", [])
        if isinstance(item, dict)
        and str(item.get("source")) in allowed
        and str(item.get("target")) in allowed
    ]
    meta = dict(base.get("meta") or {})
    stats = dict(meta.get("stats") or {})
    stats.update({"Nodes": len(nodes), "Edges": len(edges)})
    meta["stats"] = stats
    meta["filter"] = {
        "domain": domain_name,
        "subdomain": subdomain_name,
        "originalNodes": original_count,
        "filteredNodes": len(nodes),
    }
    return {
        **base,
        "nodes": nodes,
        "edges": edges,
        "meta": meta,
    }


def _filter_graph_context(
    base: dict[str, Any],
    domain_name: str,
    subdomain_name: str,
    cluster: GraphCluster | None = None,
) -> dict[str, Any]:
    """Return the selected domain branch and an evidence-matched subdomain view."""
    nodes = [item for item in base.get("nodes", []) if isinstance(item, dict)]
    edges = [item for item in base.get("edges", []) if isinstance(item, dict)]
    original_count = len(nodes)
    if not domain_name or not nodes:
        return base
    if cluster and _ROOT_CLUSTER.get(domain_name) != cluster:
        return _induced_graph(
            base,
            set(),
            domain_name=domain_name,
            subdomain_name=subdomain_name,
            original_count=original_count,
        )

    domain_id = _find_node_id(nodes, domain_name)
    if not domain_id:
        return _induced_graph(
            base,
            set(),
            domain_name=domain_name,
            subdomain_name=subdomain_name,
            original_count=original_count,
        )

    membership_edges = [edge for edge in edges if _relation_name(edge) == "隶属"]
    fixed_root_ids = {
        node_id
        for name in _ROOT_CLUSTER
        if (node_id := _find_node_id(nodes, name))
    }
    branch = {domain_id}
    changed = True
    while changed:
        changed = False
        for edge in membership_edges:
            source = str(edge.get("source") or "")
            target = str(edge.get("target") or "")
            if source in fixed_root_ids and source != domain_id:
                continue
            if target in branch and source not in branch:
                branch.add(source)
                changed = True
    for edge in membership_edges:
        if str(edge.get("source") or "") == domain_id:
            branch.add(str(edge.get("target") or ""))

    domain_graph = _induced_graph(
        base,
        branch,
        domain_name=domain_name,
        subdomain_name="",
        original_count=original_count,
    )
    if not subdomain_name:
        return domain_graph

    normalized_hints = _subdomain_hints(subdomain_name)
    scored: list[tuple[int, str]] = []
    for node in domain_graph["nodes"]:
        node_id = str(node.get("id") or "")
        if node_id == domain_id:
            continue
        score = _subdomain_match_score(
            json.dumps(node, ensure_ascii=False),
            normalized_hints,
        )
        if score:
            scored.append((score, node_id))
    scored.sort(key=lambda item: (-item[0], item[1]))
    seeds = {node_id for _, node_id in scored}
    if not seeds:
        return _induced_graph(
            domain_graph,
            {domain_id},
            domain_name=domain_name,
            subdomain_name=subdomain_name,
            original_count=original_count,
        )

    allowed = {domain_id, *seeds}
    # Keep each matched node's direct evidence/organization context.
    for edge in domain_graph["edges"]:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source in seeds or target in seeds:
            allowed.update((source, target))
    # Preserve the hierarchy path from every selected node back to the root.
    changed = True
    while changed:
        changed = False
        for edge in membership_edges:
            source = str(edge.get("source") or "")
            target = str(edge.get("target") or "")
            if source in allowed and target in branch and target not in allowed:
                allowed.add(target)
                changed = True
    return _induced_graph(
        domain_graph,
        allowed,
        domain_name=domain_name,
        subdomain_name=subdomain_name,
        original_count=original_count,
    )


def _filter_persisted_graph_scope(
    base: dict[str, Any],
    *,
    domain_name: str,
    subdomain_name: str,
    subdomain_id: str | None,
    cluster: GraphCluster,
) -> dict[str, Any]:
    """Only explicit subdomain IDs may place imported nodes in a child view.

    Older Hyper snapshots contain names and free text but no child taxonomy ID.
    Matching those names can pull neighboring fields into the selected child.
    Such unclassified nodes remain visible at the parent level until their
    source is assigned a persisted subdomain ID.
    """
    domain_graph = _filter_graph_context(base, domain_name, "", cluster)
    if not subdomain_id:
        return domain_graph
    allowed = {
        str(node.get("id") or "")
        for node in domain_graph.get("nodes", [])
        if isinstance(node, dict)
        and str(
            _element_raw(node).get("subdomainId")
            or _element_raw(node).get("subdomain_id")
            or ""
        ) == subdomain_id
    }
    return _induced_graph(
        domain_graph,
        allowed,
        domain_name=domain_name,
        subdomain_name=subdomain_name,
        original_count=len(base.get("nodes", [])),
    )


def _node(node_id: str, label: str, raw: dict[str, Any], *, highlighted: bool = False) -> dict[str, Any]:
    return {
        "id": node_id,
        "label": label,
        "type": "node",
        "highlighted": highlighted,
        "data": {"label": label, "raw": raw},
    }


def _edge(source: str, target: str, relation: str) -> dict[str, Any]:
    edge_id = hashlib.sha1(f"{source}:{target}:{relation}".encode("utf-8")).hexdigest()[:12]
    raw = {"source": source, "target": target, "type": relation}
    return {
        "id": edge_id,
        "source": source,
        "target": target,
        "label": relation,
        "type": "edge",
        "data": {"label": relation, "raw": raw},
    }


def _strategic_overlay(
    *,
    scope: GraphScope,
    cluster: GraphCluster,
    domain_id: str | None,
    subdomain_id: str | None,
) -> dict[str, list[dict[str, Any]]]:
    from . import strategic_map as sm

    _context_names(domain_id, subdomain_id)
    with sm._SESSION_FACTORY() as session:
        query = session.query(sm.StrategicTeamRow).filter(
            sm.StrategicTeamRow.deleted.is_(False),
            sm.StrategicTeamRow.is_domestic.is_(scope == "domestic"),
        )
        domain = None
        subdomain = None
        subdomains: list[Any] = []
        if domain_id:
            try:
                domain = sm._get_root_domain(session, domain_id)
            except HTTPException:
                domain = None
            if domain:
                query = query.filter(sm.StrategicTeamRow.domain_id == domain.id)
                subdomain_query = session.query(sm.StrategicDomainRow).filter(
                    sm.StrategicDomainRow.parent_id == domain.id,
                    sm.StrategicDomainRow.deleted.is_(False),
                )
                if subdomain_id:
                    subdomain_query = subdomain_query.filter(
                        sm.StrategicDomainRow.id == subdomain_id,
                    )
                subdomains = subdomain_query.order_by(
                    sm.StrategicDomainRow.sort_order,
                    sm.StrategicDomainRow.name,
                ).all()
                if subdomain_id:
                    subdomain = subdomains[0] if subdomains else None
                    if subdomain:
                        query = query.filter(
                            sm.StrategicTeamRow.subdomain_id == subdomain.id,
                        )
        if domain and _ROOT_CLUSTER.get(domain.name) != cluster:
            return {"nodes": [], "edges": []}

        rows = query.order_by(sm.StrategicTeamRow.score_total.desc()).limit(150).all()
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        if domain:
            nodes.append(_node(cluster, f"{cluster} (领域大类)", {"name": cluster, "level": "领域大类"}))
            nodes.append(
                _node(
                    domain.name,
                    f"{domain.name} (领域方向)",
                    {"name": domain.name, "level": "领域方向", "description": domain.description},
                )
            )
            edges.append(_edge(domain.name, cluster, "隶属"))
        for child in subdomains:
            nodes.append(
                _node(
                    child.name,
                    f"{child.name} (子领域)",
                    {
                        "name": child.name,
                        "level": "子领域",
                        "description": child.description,
                        "subdomainId": child.id,
                    },
                )
            )
            if domain:
                edges.append(_edge(child.name, domain.name, "隶属"))

        subdomain_names = {child.id: child.name for child in subdomains}

        for team in rows:
            institution = team.institution_name or team.name
            team_node_id = f"team:{team.id}"
            target = (
                subdomain_names.get(team.subdomain_id)
                or (domain.name if domain else cluster)
            )
            nodes.append(
                _node(
                    institution,
                    f"{institution} (机构)",
                    {
                        "name": institution,
                        "level": "机构",
                        "description": team.description or team.evidence_summary,
                    },
                )
            )
            nodes.append(
                _node(
                    team_node_id,
                    f"{team.team_name} (科研团队)",
                    {
                        "name": team.team_name,
                        "level": "科研团队",
                        "description": team.description,
                        "institution": institution,
                        "domainId": team.domain_id,
                        "subdomainId": team.subdomain_id,
                        "score": float(team.score_total or 0),
                        "eligibility": dict(team.eligibility or {}),
                        "scoreBreakdown": dict(team.score_breakdown or {}),
                        "evidenceUrls": list(team.score_evidence_ids or []),
                    },
                )
            )
            edges.append(_edge(team_node_id, institution, "所属机构"))
            edges.append(_edge(team_node_id, target, "研究方向"))
            for index, url in enumerate(list(team.score_evidence_ids or [])[:3]):
                host = urlsplit(url).hostname or "公开来源"
                evidence_id = f"evidence:{team.id}:{index}"
                nodes.append(
                    _node(
                        evidence_id,
                        f"{host} (证据)",
                        {
                            "name": team.report_title or host,
                            "level": "证据",
                            "description": team.evidence_summary,
                            "url": url,
                            "domainId": team.domain_id,
                            "subdomainId": team.subdomain_id,
                        },
                    )
                )
                edges.append(_edge(evidence_id, team_node_id, "证据支持"))
    return {"nodes": nodes, "edges": edges}


def _merge_graph(base: dict[str, Any], overlay: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    nodes = {
        str(item.get("id")): item
        for item in base.get("nodes", [])
        if isinstance(item, dict) and item.get("id")
    }
    for item in overlay["nodes"]:
        nodes[item["id"]] = item
    edges = {
        str(item.get("id")): item
        for item in base.get("edges", [])
        if isinstance(item, dict) and item.get("id")
    }
    for item in overlay["edges"]:
        edges[item["id"]] = item
    meta = dict(base.get("meta") or {})
    stats = dict(meta.get("stats") or {})
    stats.update({"Nodes": len(nodes), "Edges": len(edges)})
    meta["stats"] = stats
    return {
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "meta": meta,
        "provider": base.get("provider", "Hyper-Extract"),
    }


def _matching_overlay(
    overlay: dict[str, list[dict[str, Any]]],
    query: str,
) -> dict[str, list[dict[str, Any]]]:
    terms = [item.casefold() for item in re.split(r"[\s,，/]+", query) if item.strip()]
    if not terms:
        return {"nodes": [], "edges": []}
    query_text = query.casefold()
    compact_query = re.sub(r"\s+", "", query_text)
    named_matches = {
        item["id"]
        for item in overlay["nodes"]
        if (
            name := str(
                ((item.get("data") or {}).get("raw") or {}).get("name") or ""
            ).strip()
        )
        and len(name) >= 2
        and re.sub(r"\s+", "", name.casefold()) in compact_query
    }
    strict_matches = {
        item["id"]
        for item in overlay["nodes"]
        if all(term in json.dumps(item, ensure_ascii=False).casefold() for term in terms)
    }
    direct_matches = named_matches or strict_matches
    if not direct_matches:
        useful_terms = [term for term in terms if len(term) >= 2]
        direct_matches = {
            item["id"]
            for item in overlay["nodes"]
            if any(
                term in json.dumps(item, ensure_ascii=False).casefold()
                for term in useful_terms
            )
        }
    included = set(direct_matches)
    context_edges = [
        edge
        for edge in overlay["edges"]
        if edge["source"] in direct_matches or edge["target"] in direct_matches
    ][:60]
    for edge in context_edges:
        if edge["source"] in direct_matches or edge["target"] in direct_matches:
            included.update((edge["source"], edge["target"]))
    return {
        "nodes": [
            {**item, "highlighted": item["id"] in direct_matches}
            for item in overlay["nodes"]
            if item["id"] in included
        ],
        "edges": [
            item
            for item in overlay["edges"]
            if item["source"] in included and item["target"] in included
        ],
    }


def _rank_search_results(data: dict[str, Any], query: str) -> list[dict[str, Any]]:
    degree: dict[str, int] = {}
    for edge in data.get("edges", []):
        for key in ("source", "target"):
            node_id = str(edge.get(key) or "")
            degree[node_id] = degree.get(node_id, 0) + 1
    terms = [
        _normalized_name(item)
        for item in re.split(r"[\s,，、/]+", query)
        if len(_normalized_name(item)) >= 2
    ]
    rows = []
    for node in data.get("nodes", []):
        node_id = str(node.get("id") or "")
        raw = _element_raw(node)
        text = _normalized_name(json.dumps(node, ensure_ascii=False))
        term_hits = sum(1 for term in terms if term in text)
        evidence_score = float(raw.get("score") or 0)
        relevance = (
            (100 if node.get("highlighted") else 0)
            + term_hits * 20
            + min(degree.get(node_id, 0), 20)
            + min(evidence_score / 5, 20)
        )
        rows.append(
            {
                "id": node_id,
                "label": _element_name(node),
                "level": str(raw.get("level") or node.get("type") or "节点"),
                "description": str(raw.get("description") or ""),
                "relevance": round(relevance, 1),
                "degree": degree.get(node_id, 0),
                "evidenceScore": evidence_score,
                "highlighted": bool(node.get("highlighted")),
            }
        )
    rows.sort(
        key=lambda item: (
            not item["highlighted"],
            -item["relevance"],
            -item["degree"],
            item["label"],
        )
    )
    return rows[:80]


def graph_institution_seeds(
    domain_name: str,
    subdomain_name: str = "",
    limit: int | None = 16,
) -> list[str]:
    """Return high-connectivity domestic institutions for web-team discovery."""
    cluster = _ROOT_CLUSTER.get(domain_name, "高峰")
    base = _filter_graph_context(
        _context_graph_base("domestic", cluster, domain_name),
        domain_name,
        subdomain_name,
        cluster,
    )
    degree: dict[str, int] = {}
    for edge in base.get("edges", []):
        if not isinstance(edge, dict):
            continue
        for key in ("source", "target"):
            node_id = str(edge.get(key) or "")
            degree[node_id] = degree.get(node_id, 0) + 1
    candidates = []
    for node in base.get("nodes", []):
        if not isinstance(node, dict):
            continue
        raw = ((node.get("data") or {}).get("raw") or {})
        if str(raw.get("level") or "") != "机构":
            continue
        name = str(raw.get("name") or node.get("id") or "").strip()
        if name:
            candidates.append((name, degree.get(str(node.get("id") or ""), 0)))
    candidates.sort(key=lambda item: (-item[1], item[0]))
    names = list(dict.fromkeys(name for name, _ in candidates))
    return names if limit is None else names[: max(1, limit)]


def _hyper_keyword_variants(
    domain_name: str,
    subdomain_name: str = "",
) -> list[str]:
    """Return an ordered list of keywords to fan out against ``/api/candidates``.

    Hyper's keyword index does not tokenize on whitespace: ``通用 AI`` misses
    while ``通用AI`` returns 258 hits.  It also indexes narrow sub-topics that
    the wide domain label never matches (``材料`` finds 5 hits absent from
    ``化学与材料``). We therefore try:

      1. The subdomain / domain as given (users may have hand-tuned it),
      2. A whitespace-stripped version,
      3. Curated fallbacks for the six locked root domains.

    Deduplicated in-order; the caller iterates until enough candidates arrive.
    """
    # Curated fallbacks reflect the empirical Hyper index probes above.
    # We keep them narrow — a broad synonym would drag in unrelated hits.
    root_fallbacks: dict[str, list[str]] = {
        "科学通用底座": ["科学通用底座"],
        "通用 AI": ["通用AI", "通用人工智能"],
        "通用AI": ["通用AI", "通用人工智能"],
        "高能物理与量子科技": ["高能物理与量子科技", "高能物理", "量子科技", "粒子物理"],
        "化学与材料": ["化学与材料", "材料", "化学"],
        "生命科学与医学": ["生命科学与医学"],
        "地球科学": ["地球科学", "气候"],
    }
    ordered: list[str] = []
    seen: set[str] = set()
    def push(candidate: str) -> None:
        value = str(candidate or "").strip()
        if value and value not in seen:
            seen.add(value)
            ordered.append(value)
    # A subdomain scan must stay within that scope; querying its parent domain
    # here leaks unrelated sibling teams back into the result set.
    selected_names = (subdomain_name,) if subdomain_name else (domain_name,)
    for raw in selected_names:
        if not raw:
            continue
        push(raw)
        stripped = raw.replace(" ", "")
        if stripped != raw:
            push(stripped)
    # Only expand to curated fallbacks when caller asked for the whole domain;
    # a subdomain scan should not accidentally pull in unrelated peers.
    if not subdomain_name and domain_name in root_fallbacks:
        for fb in root_fallbacks[domain_name]:
            push(fb)
    return ordered


def _expand_umbrella_institutions(
    institutions: list[dict[str, Any]],
    *,
    domain_name: str,
    subdomain_name: str,
    keyword: str,
    max_teams_per_institution: int = 6,
) -> list[dict[str, Any]]:
    """Turn umbrella-institution rows (清华大学, 腾讯, ...) into concrete teams.

    Hyper's ``/api/candidates`` reliably surfaces the parent institutions for
    a domain (~200 for 通用AI, ~50 for 生命科学与医学). ``_explicit_team_identity``
    then drops them because their name lacks a "实验室/团队" marker. To keep the
    signal instead of throwing 90%+ of the pool away, we let the shared Agent
    LLM read the institution's Hyper profile/events/aliases and enumerate the
    concrete labs it explicitly mentions. Each expanded team keeps a citation
    to the Hyper evidence id that supports it, matching the auditability rule
    the strategic map applies everywhere else.
    """
    if not institutions:
        return []
    if not _strategic_map._llm_config():
        return []
    # Cap fanout so a single scan does not burn hundreds of LLM calls.
    expandable = sorted(institutions, key=lambda r: -int(r.get("event_count") or 0))
    fanout_ceiling = int(os.getenv("STRATEGIC_MAP_UMBRELLA_MAX", "60"))
    expandable = expandable[:fanout_ceiling]
    expanded: list[dict[str, Any]] = []
    for row in expandable:
        try:
            teams = _llm_extract_umbrella_teams(
                row,
                domain_name=domain_name,
                subdomain_name=subdomain_name,
                keyword=keyword,
                max_teams=max_teams_per_institution,
            )
        except Exception:
            continue
        for team in teams:
            expanded.append(team)
    return expanded


def _llm_extract_umbrella_teams(
    institution: dict[str, Any],
    *,
    domain_name: str,
    subdomain_name: str,
    keyword: str,
    max_teams: int,
) -> list[dict[str, Any]]:
    """One structured LLM call per institution; every accepted team cites Hyper evidence.

    Hyper's institution evidence is news-shaped, so lab names rarely appear
    verbatim. We hand the LLM two evidence surfaces and ask it to synthesise
    at most one **PI-anchored team** per PI it sees mentioned by name:

      - ``related_authors`` — Hyper-linked PI candidates, already tied to
        this institution via co-affiliation edges.
      - ``evidence`` — 15 recent news items where the PI or their
        subgroup is likely quoted.

    Rules the LLM must follow (or the row is discarded):
      1. ``team_name = "{institution} {leader}团队"`` (or "组"/"课题组") —
         a pattern every Chinese research site follows.
      2. Every ``leader`` must actually appear in the input
         ``related_authors`` list (case-insensitive substring).
      3. Every citation must resolve to a Hyper evidence id whose text
         contains the ``quote``.
      4. No leader → no team. Do not invent PIs.
    """
    import json as _json

    related_authors = [
        str(c.get("name") or "").strip()
        for c in institution.get("person_clues") or []
        if str(c.get("name") or "").strip()
    ][:20]
    if not related_authors:
        return []
    author_lookup = {name.casefold(): name for name in related_authors}

    system = (
        "你是知识图谱分析师。从「机构 Hyper 画像 + 事件 + 相关作者」里，"
        "识别在此机构领导具体 AI4S 研究团队的 PI，生成结构化团队记录。硬规则：\n"
        "1. leader 必须严格来自 related_authors 输入列表；\n"
        "2. 只有当 leader 姓名在 profile 或某条 evidence 的 title/description 中"
        "**至少出现一次**，才为其生成团队记录；\n"
        "3. team_name 使用格式：`<机构>·<leader>团队`（或"
        "`<机构>·<leader>课题组` / `<机构>·<leader>研究组`）；\n"
        "4. description ≥ 30 字，从 profile/events 原文中总结该 PI 或机构的研究方向，"
        "禁止杜撰不在原文中的机构、项目或方向；\n"
        "5. 每个团队至少 1 条 evidence_id + quote：evidence_id 来自 evidence 列表的 id 字段，"
        "quote ≥ 6 字，是该 evidence title/description 的**子串**；\n"
        "6. 若 related_authors 里没有任何 PI 在 profile/evidence 中出现，返回 teams=[]。\n"
        "7. 与当前领域「" + (subdomain_name or domain_name) + "」明显无关时，返回 teams=[]。\n"
        "输出**只允许**严格 JSON（不要 markdown 包裹）："
        "{\"teams\":[{\"team_name\":..., \"leader\":..., "
        "\"description\":..., \"evidence_ids\":[...], \"quote\":...}]}。"
    )
    payload = {
        "institution_name": institution["name"],
        "domain": domain_name,
        "subdomain": subdomain_name,
        "keyword": keyword,
        "aliases": institution.get("aliases", [])[:40],
        "directions": institution.get("directions", []),
        "profile": institution.get("profile", "")[:1500],
        "evidence": institution.get("evidence", [])[:15],
        "related_authors": related_authors,
        "max_teams": max_teams,
    }
    user = _json.dumps(payload, ensure_ascii=False)
    raw = _strategic_map._shared_agent_llm_text(
        task="umbrella-expand", system=system, user=user, timeout=45,
    )
    # DeepSeek/Qwen frequently wrap JSON in ```json ... ``` blocks; strip.
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    try:
        parsed = _json.loads(cleaned)
    except Exception:
        return []
    proposals = parsed.get("teams") if isinstance(parsed, dict) else None
    if not isinstance(proposals, list):
        return []
    evidence_by_id = {str(e.get("id") or ""): e for e in institution.get("evidence", [])}
    profile_text = " ".join(str(institution.get("profile") or "").split())
    all_evidence_text = " ".join(
        f"{ev.get('title','')} {ev.get('description','')}"
        for ev in institution.get("evidence") or []
    )
    accepted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for prop in proposals[:max_teams]:
        if not isinstance(prop, dict):
            continue
        leader = str(prop.get("leader") or "").strip()
        if not leader:
            continue
        leader_match = author_lookup.get(leader.casefold())
        if not leader_match:
            # Reject PIs the LLM invented that were not in the input list.
            continue
        team_name = str(prop.get("team_name") or "").strip()
        if not team_name:
            team_name = f"{institution['name']}·{leader_match}团队"
        if team_name.casefold() in seen:
            continue
        if not _explicit_team_identity(team_name, str(prop.get("description") or "")):
            continue
        description = " ".join(str(prop.get("description") or "").split())
        if len(description) < 30:
            continue
        quote = " ".join(str(prop.get("quote") or "").split())
        if len(quote) < 6:
            continue
        ev_ids = [str(e).strip() for e in (prop.get("evidence_ids") or []) if str(e).strip()]
        supporting: list[dict[str, Any]] = []
        for eid in ev_ids:
            ev = evidence_by_id.get(eid)
            if not ev:
                continue
            combined = f"{ev.get('title','')} {ev.get('description','')}"
            if quote in combined or "".join(quote.split()) in "".join(combined.split()):
                supporting.append(ev)
        if not supporting:
            continue
        # The PI must appear somewhere in the institution's Hyper payload
        # (profile OR any evidence text) — otherwise it's a co-affiliation
        # ghost the LLM hallucinated onto this team.
        if leader_match not in profile_text and leader_match not in all_evidence_text:
            continue
        seen.add(team_name.casefold())
        accepted.append({
            "institution_name": institution["name"],
            "team_name": team_name,
            "description": description[:400],
            "profile": " ".join([
                description,
                institution.get("profile", "")[:400],
            ]).strip(),
            "aliases": [],
            "directions": institution.get("directions", []),
            "event_count": len(supporting),
            "direction_overlap": institution.get("direction_overlap", 0),
            "rank": institution.get("rank"),
            "evidence": supporting,
            "personClues": [
                {"name": leader_match, "role": "PI", "relation": "team_lead"}
            ],
        })
    return accepted


def _structured_hyper_team_candidates(
    domain_name: str,
    subdomain_name: str = "",
) -> list[dict[str, Any]] | None:
    """Use Hyper's native ``find_candidates`` result when the sidecar supports it.

    Hyper's index is fuzzy: literal user-facing labels such as ``通用 AI`` time
    out on 60s while ``通用AI`` returns 210 hits in under a second, and some
    domains only surface useful pools via a sub-topic (``材料`` finds 5 hits
    that ``化学与材料`` misses). We fan out over a curated variant list per
    domain, merge by canonical name, and keep the highest-evidence copy.
    """
    keywords = _hyper_keyword_variants(domain_name, subdomain_name)
    if not keywords:
        return None
    merge_aliases = bool(_strategic_map._llm_config())
    raw_rows: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    keyword = keywords[0]
    for kw in keywords:
        try:
            payload = _request_json(
                "POST",
                "/api/candidates",
                body={
                    "keyword": kw,
                    "graph": _SCOPE_TO_GRAPH["domestic"],
                    "max_candidates": 200,
                    "merge_aliases": merge_aliases,
                    "include_all": True,
                    "semantic_search": merge_aliases,
                },
                # A single variant timing out (常见于含空格的原始领域名)
                # must not stop the fanout: keep the retry budget short and
                # move on to the next keyword.
                timeout=max(_HYPER_TIMEOUT, 45),
            )
        except HTTPException:
            continue
        rows = payload.get("candidates") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            continue
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            key = str(raw.get("canonical_name") or raw.get("name") or "").strip().casefold()
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            raw_rows.append(raw)
    if not raw_rows:
        return None

    result: list[dict[str, Any]] = []
    institution_rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        candidate_type = str(raw.get("type") or raw.get("level") or "")
        if candidate_type != "机构":
            continue
        name = str(raw.get("canonical_name") or raw.get("name") or "").strip()
        aliases = [
            str(value).strip()
            for value in raw.get("aliases") or []
            if str(value).strip()
        ]
        directions = [
            str(value).strip()
            for value in raw.get("directions") or []
            if str(value).strip()
        ]
        profile = " ".join(
            (
                str(raw.get("profile") or raw.get("description") or ""),
                *aliases,
                *directions,
            )
        ).strip()
        if not name:
            continue
        # Hyper's ``type=机构`` covers both concrete lab-level entities
        # (whose name already carries "实验室 / 团队 / 研究组") and umbrella
        # organizations (清华大学, 腾讯, ...). The former can be scored
        # directly; the latter is stashed for LLM-driven team expansion so
        # a 200-institution candidate pool does not collapse to 18 rows.
        is_concrete = _explicit_team_identity(name, profile)
        evidence = [
            {
                "id": str(item.get("id") or ""),
                "type": str(item.get("type") or "event"),
                "title": str(item.get("title") or ""),
                "description": str(item.get("description") or ""),
            }
            for item in raw.get("evidence") or []
            if isinstance(item, dict)
            and (item.get("title") or item.get("description"))
        ]
        if not evidence:
            continue
        person_clues = [
            {
                "name": str(item.get("name") or "").strip(),
                "profile": str(item.get("profile") or ""),
                "relation": str(item.get("relation") or ""),
                "relation_description": str(item.get("relation_description") or ""),
            }
            for item in raw.get("related_authors") or []
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]
        if not is_concrete:
            institution_rows.append({
                "name": name,
                "aliases": aliases,
                "directions": directions,
                "profile": profile,
                "evidence": evidence,
                "person_clues": person_clues,
                "event_count": int(raw.get("event_count") or len(evidence)),
                "direction_overlap": int(raw.get("direction_overlap") or 0),
                "rank": raw.get("rank"),
            })
            continue
        candidate = _scan_candidate_score(
            {
                "rank": raw.get("rank"),
                "name": name,
                "type": candidate_type,
                "profile": profile,
                "aliases": aliases,
                "directions": directions,
                "event_count": int(raw.get("event_count") or len(evidence)),
                "direction_overlap": int(raw.get("direction_overlap") or 0),
                "evidence": evidence,
            },
            keyword=keyword,
            scope="domestic",
        )
        if not candidate["eligibility"]["eligible"]:
            continue
        candidate["eligibility"]["leaderEvidence"] = False
        result.append(
            {
                **candidate,
                "institution_name": name,
                "team_name": name,
                "description": str(raw.get("description") or raw.get("profile") or ""),
                "leaders": [],
                "members": [],
                "personClues": person_clues,
                "_hyper_provider": "Hyper-Extract find_candidates structured API",
            }
        )

    # Expand umbrella institutions into concrete teams via LLM. The evidence
    # payload from Hyper (profile + 15 events + related_authors + aliases)
    # is dense enough to let a small model list the labs it explicitly
    # mentions; every team we accept must cite the Hyper evidence id that
    # supports it, so nothing gets fabricated.
    if institution_rows:
        expanded = _expand_umbrella_institutions(
            institution_rows,
            domain_name=domain_name,
            subdomain_name=subdomain_name,
            keyword=keyword,
        )
        for row in expanded:
            candidate = _scan_candidate_score(
                {
                    "rank": row.get("rank"),
                    "name": row["team_name"],
                    "type": "机构",
                    "profile": row["profile"],
                    "aliases": row.get("aliases", []),
                    "directions": row.get("directions", []),
                    "event_count": row.get("event_count", 1),
                    "direction_overlap": row.get("direction_overlap", 0),
                    "evidence": row["evidence"],
                },
                keyword=keyword,
                scope="domestic",
            )
            if not candidate["eligibility"]["eligible"]:
                continue
            candidate["eligibility"]["leaderEvidence"] = False
            result.append(
                {
                    **candidate,
                    "institution_name": row["institution_name"],
                    "team_name": row["team_name"],
                    "description": row.get("description", ""),
                    "leaders": [],
                    "members": [],
                    "personClues": row.get("personClues", []),
                    "_hyper_provider": "Hyper-Extract 机构展开·LLM 抽取",
                }
            )
    result.sort(
        key=lambda item: (
            -float(item.get("total") or 0),
            -int(item.get("event_count") or 0),
            str(item.get("team_name") or ""),
        )
    )
    return result


def _hyper_team_candidates(
    domain_name: str,
    subdomain_name: str = "",
) -> list[dict[str, Any]]:
    """Build scored team records from Hyper's native candidate discovery.

    New Hyper versions expose ``find_candidates`` directly. Older sidecars and
    offline development retain the local hierarchy/snapshot implementation.
    Only names that are themselves research units are promoted.
    """
    structured = _structured_hyper_team_candidates(domain_name, subdomain_name)
    cluster = _ROOT_CLUSTER.get(domain_name, "高峰")
    try:
        base = _filter_graph_context(
            _snapshot_all("domestic"),
            domain_name,
            subdomain_name,
            cluster,
        )
    except HTTPException:
        return structured or []
    # Native top-k and unavailable embeddings must not discard local branch
    # matches. Merge both sources, including when the native API returns [].
    if subdomain_name and structured is None and _strategic_map._llm_config():
        try:
            with _SIDE_CAR_LOCK:
                _activate("domestic", cluster)
                semantic = _request_json(
                    "POST",
                    "/api/search",
                    body={"query": subdomain_name},
                    timeout=max(_HYPER_TIMEOUT, 30),
                )
            base = _merge_graph(
                base,
                {
                    "nodes": [
                        item
                        for item in semantic.get("nodes", [])
                        if isinstance(item, dict)
                    ],
                    "edges": [
                        item
                        for item in semantic.get("edges", [])
                        if isinstance(item, dict)
                    ],
                },
            )
        except HTTPException:
            pass
    nodes = {
        str(item.get("id") or ""): _element_raw(item)
        for item in base.get("nodes", [])
        if isinstance(item, dict) and item.get("id")
    }
    edges = [
        item
        for item in base.get("edges", [])
        if isinstance(item, dict)
    ]
    result: list[dict[str, Any]] = list(structured or [])
    known_names = {
        _normalized_name(name)
        for candidate in result
        for name in [candidate["team_name"], *(candidate.get("aliases") or [])]
    }
    keyword = subdomain_name or domain_name

    for node_id, raw in nodes.items():
        if str(raw.get("level") or "") != "机构":
            continue
        name = str(raw.get("name") or node_id).strip()
        if _normalized_name(name) in known_names:
            continue
        profile = str(raw.get("description") or "")
        if not _explicit_team_identity(name, profile):
            continue

        evidence: list[dict[str, str]] = []
        directions: set[str] = set()
        related_people: list[dict[str, Any]] = []
        for edge in edges:
            source = str(edge.get("source") or "")
            target = str(edge.get("target") or "")
            relation = _relation_name(edge)
            edge_raw = _element_raw(edge)
            if (
                relation == "隶属"
                and target == node_id
                and str((nodes.get(source) or {}).get("level") or "") == "事件"
            ):
                event = nodes[source]
                digest = hashlib.sha1(
                    f"{source}:{event.get('description') or ''}".encode("utf-8")
                ).hexdigest()[:16]
                evidence.append(
                    {
                        "id": f"hyper-event:{digest}",
                        "type": "event",
                        "title": str(event.get("name") or source),
                        "description": str(event.get("description") or ""),
                    }
                )
            if (
                source == node_id
                and str((nodes.get(target) or {}).get("level") or "") == "领域方向"
            ):
                directions.add(str((nodes[target]).get("name") or target))
            if node_id not in (source, target):
                continue
            person_id = target if source == node_id else source
            person = nodes.get(person_id) or {}
            if str(person.get("level") or "") != "作者":
                continue
            related_people.append(
                {
                    "name": str(person.get("name") or person_id),
                    "title": "",
                    "role": "关联作者",
                    "research_direction": "",
                    "bio": "；".join(
                        value
                        for value in (
                            str(person.get("description") or ""),
                            str(edge_raw.get("description") or ""),
                            f"图谱关系：{relation}" if relation else "",
                        )
                        if value
                    ),
                    "profile_url": "",
                    "source_urls": [
                        value
                        for value in (f"hyper-node:{person_id}", str(edge.get("id") or ""))
                        if value
                    ],
                    "source_type": "Hyper-Extract 图谱关系",
                    "verification_status": "collected",
                    "confidence": 0.6,
                }
            )

        if not evidence:
            continue
        candidate = _scan_candidate_score(
            {
                "name": name,
                "type": "机构",
                "profile": " ".join((profile, *sorted(directions))),
                "directions": sorted(directions),
                "event_count": len(evidence),
                "direction_overlap": 1,
                "evidence": evidence,
            },
            keyword=keyword,
            scope="domestic",
        )
        if not candidate["eligibility"]["eligible"]:
            continue
        candidate["eligibility"]["leaderEvidence"] = False
        result.append(
            {
                **candidate,
                "institution_name": name,
                "team_name": name,
                "description": profile,
                "leaders": [],
                "members": [],
                "personClues": related_people,
                "_hyper_provider": "Hyper-Extract snapshot fallback",
            }
        )
    result.sort(
        key=lambda item: (
            -float(item.get("total") or 0),
            -int(item.get("event_count") or 0),
            str(item.get("team_name") or ""),
        )
    )
    return result


def sync_hyper_snapshot(
    session,
    domain,
    subdomain=None,
) -> dict[str, Any]:
    """Incrementally merge Hyper graph teams into the durable strategic map."""
    sm = _strategic_map
    subdomain_name = subdomain.name if subdomain is not None else ""
    candidates = _hyper_team_candidates(domain.name, subdomain_name)
    # Include soft-deleted rows too, so a previously purged placeholder is
    # updated in place instead of triggering a PRIMARY KEY conflict on reinsert.
    existing = {
        sm._canonical_team_key(row.institution_name or row.name, row.team_name): row
        for row in session.query(sm.StrategicTeamRow).filter(
            sm.StrategicTeamRow.domain_id == domain.id,
        )
    }
    children = session.query(sm.StrategicDomainRow).filter(
        sm.StrategicDomainRow.parent_id == domain.id,
        sm.StrategicDomainRow.deleted.is_(False),
    ).all()
    created = 0
    updated = 0
    leader_count = 0
    member_team_count = 0
    member_count = 0

    def child_for(candidate: dict[str, Any]):
        if subdomain is not None:
            return subdomain
        haystack = _normalized_name(
            " ".join(
                (
                    str(candidate.get("team_name") or ""),
                    str(candidate.get("description") or ""),
                    *[str(value) for value in candidate.get("directions") or []],
                )
            )
        )
        ranked = []
        for child in children:
            score = _subdomain_match_score(haystack, _subdomain_hints(child.name))
            ranked.append((score, child.sort_order, child))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return ranked[0][2] if ranked and ranked[0][0] else None

    for candidate in candidates:
        institution = str(candidate["institution_name"])
        team_name = str(candidate["team_name"])
        # Baseline gate at ingestion: a Hyper node whose team name equals its
        # institution name and carries no verifiable URL is a brand placeholder
        # ("集智俱乐部", "李飞飞团队"), not a scientific research team. It never
        # meets the leader/members baseline, so we reject it up front.
        candidate_urls = [str(u) for u in (candidate.get("source_urls") or []) if isinstance(u, str)]
        key = sm._canonical_team_key(institution, team_name)
        row = existing.get(key)
        if (
            institution.strip() == team_name.strip()
            and not any(u.startswith(("http://", "https://")) for u in candidate_urls)
        ):
            # Rejected graph placeholders must not retain a graph-derived
            # leader claim. Hyper author relations are discovery clues, not
            # evidence of current leadership.
            if row is not None:
                for person in session.query(sm.StrategicPersonRow).filter(
                    sm.StrategicPersonRow.team_id == row.id,
                    sm.StrategicPersonRow.source_type.like("Hyper-Extract%"),
                    sm.StrategicPersonRow.is_leader.is_(True),
                ):
                    person.deleted = True
            continue
        if row is None:
            row = sm.StrategicTeamRow(
                id=sm._candidate_id(domain.id, key),
                domain_id=domain.id,
                name=institution,
                institution_name=institution,
                team_name=team_name,
            )
            session.add(row)
            existing[key] = row
            created += 1
        else:
            # A previously-purged placeholder must stay purged; otherwise a
            # daily snapshot would revive it and violate the baseline rule.
            if row.deleted:
                continue
            updated += 1
        web_verified = row.verification_status == "verified"
        child = child_for(candidate)
        evidence = list(candidate.get("evidence") or [])
        evidence_ids = list(candidate.get("evidenceIds") or [])
        row.deleted = False
        row.subdomain_id = child.id if child else row.subdomain_id
        if not web_verified:
            row.description = str(candidate.get("description") or row.description or "")
        row.research_directions = list(
            dict.fromkeys(
                [
                    *(row.research_directions or []),
                    *[str(value) for value in candidate.get("directions") or [] if value],
                ]
            )
        )
        row.focus = "、".join(row.research_directions)[:255]
        row.location = "中国（Hyper 国内图谱）"
        row.is_domestic = True
        row.team_confidence = max(float(row.team_confidence or 0), 0.85)
        graph_summary = "\n".join(
            dict.fromkeys(
                str(item.get("title") or "") + "：" + str(item.get("description") or "")
                for item in evidence
                if item.get("title") or item.get("description")
            )
        )[:6000]
        if not web_verified:
            row.source = "Hyper-Extract 图谱快照"
            row.evidence_summary = graph_summary
            row.report_id = f"hyper-snapshot:{hashlib.sha1(team_name.encode('utf-8')).hexdigest()[:16]}"
            row.report_title = f"{domain.name} Hyper 图谱增量快照"
            row.recent_update = sm._now().date().isoformat()
            row.verification_status = "graph_verified"
        row.eligibility = dict(candidate.get("eligibility") or {})
        row.score_breakdown = dict(candidate.get("scoreBreakdown") or {})
        row.score_total = float(candidate.get("total") or 0)
        row.score_evidence_ids = evidence_ids
        row.score_version = sm._SCORE_VERSION
        row.scored_at = sm._now()
        row.updated_at = sm._now()
        leaders = list(candidate.get("leaders") or [])
        members = list(candidate.get("members") or [])
        sm._upsert_team_people(session, row, leaders[0] if leaders else None, members)
        expected_people = {
            str(person.get("name") or "")
            for person in [*leaders, *members]
            if person.get("name")
        }
        for person in session.query(sm.StrategicPersonRow).filter(
            sm.StrategicPersonRow.team_id == row.id,
            sm.StrategicPersonRow.source_type.like("Hyper-Extract%"),
        ):
            if person.name not in expected_people:
                person.deleted = True
            elif person.is_leader:
                person.is_leader = False
                person.role = "关联作者"
                person.verification_status = "collected"
                person.confidence = min(float(person.confidence or 0), 0.6)
                person.last_verified_at = None
        leader_count += int(bool(leaders))
        member_team_count += int(bool(members))
        member_count += len(members)
    session.flush()
    return {
        "provider": (
            str(candidates[0].get("_hyper_provider"))
            if candidates
            else "Hyper-Extract find_candidates structured API"
        ),
        "candidateCount": len(candidates),
        "teamCount": len(candidates),
        "leaderCount": leader_count,
        "memberTeamCount": member_team_count,
        "memberCount": member_count,
        "created": created,
        "updated": updated,
        "subdomainId": subdomain.id if subdomain is not None else None,
        "updatedAt": sm._now().isoformat(),
    }


@router.get("/status")
def graph_status() -> dict[str, Any]:
    root = _snapshot_root()
    try:
        meta = _request_json("GET", "/api/meta", timeout=3)
        hyper_available = True
        features = dict(meta.get("features") or {})
        features.update({"browsing": True, "scan": _remote_scan_available()})
    except HTTPException:
        # Persisted domains, teams and evidence still form a usable local
        # graph even when the optional Hyper sidecar/snapshot is unavailable.
        hyper_available = False
        features = {"browsing": True, "search": True, "chat": True, "scan": False}
    provider = (
        "Hyper-Extract snapshot" if root else
        "Hyper-Extract" if hyper_available else
        "strategic-map local graph"
    )
    return _response(
        {
            "available": True,
            "hyperAvailable": hyper_available,
            "provider": provider,
            "scopes": ["domestic", "international"],
            "clusters": ["高峰", "高原"],
            "features": features,
            "snapshotUpdatedAt": {
                scope: _snapshot_updated_at(root, scope) if root else ""
                for scope in ("domestic", "international")
            },
        }
    )


@router.get("/data")
def graph_data(
    scope: GraphScope = Query("domestic"),
    cluster: GraphCluster = Query("高峰"),
    domain_id: str | None = Query(None),
    subdomain_id: str | None = Query(None),
) -> dict[str, Any]:
    domain_name, subdomain_name = _context_names(domain_id, subdomain_id)
    base = _filter_persisted_graph_scope(
        _context_graph_base(scope, cluster, domain_name),
        domain_name=domain_name,
        subdomain_name=subdomain_name,
        subdomain_id=subdomain_id,
        cluster=cluster,
    )
    overlay = _strategic_overlay(
        scope=scope,
        cluster=cluster,
        domain_id=domain_id,
        subdomain_id=subdomain_id,
    )
    return _response(_merge_graph(base, overlay))


@router.get("/details/{element_id:path}")
def graph_details(
    element_id: str,
    scope: GraphScope = Query("domestic"),
    cluster: GraphCluster = Query("高峰"),
) -> dict[str, Any]:
    if not element_id.strip():
        raise HTTPException(status_code=400, detail="节点 ID 不能为空")
    try:
        with _SIDE_CAR_LOCK:
            _activate(scope, cluster)
            value = _request_json("GET", f"/api/details/{requests.utils.quote(element_id, safe='')}")
        return _response(value)
    except HTTPException:
        data = _snapshot_graph(scope, cluster)
        for item in [*data["nodes"], *data["edges"]]:
            if str(item.get("id")) == element_id:
                return _response(item)
        raise HTTPException(status_code=404, detail="图谱元素不存在")


@router.post("/search")
def graph_search(payload: GraphQuery) -> dict[str, Any]:
    domain_name, subdomain_name = _context_names(
        payload.domain_id,
        payload.subdomain_id,
    )
    context_base = _filter_persisted_graph_scope(
        _context_graph_base(payload.scope, payload.cluster, domain_name),
        domain_name=domain_name,
        subdomain_name=subdomain_name,
        subdomain_id=payload.subdomain_id,
        cluster=payload.cluster,
    )
    allowed_ids = {
        str(item.get("id") or "")
        for item in context_base.get("nodes", [])
        if isinstance(item, dict)
    }
    overlay = _strategic_overlay(
        scope=payload.scope,
        cluster=payload.cluster,
        domain_id=payload.domain_id,
        subdomain_id=payload.subdomain_id,
    )
    overlay_hits = _matching_overlay(overlay, payload.query)
    local_hits = _matching_overlay(
        {
            "nodes": context_base.get("nodes", []),
            "edges": context_base.get("edges", []),
        },
        payload.query,
    )
    base = {
        "nodes": [],
        "edges": [],
        "meta": context_base.get("meta") or {},
        "provider": "Hyper-Extract domain search",
    }
    try:
        with _SIDE_CAR_LOCK:
            _activate(payload.scope, payload.cluster)
            result = _request_json("POST", "/api/search", body={"query": payload.query})
        semantic_nodes = [
            {**item, "highlighted": True}
            for item in result.get("nodes", [])
            if isinstance(item, dict) and str(item.get("id") or "") in allowed_ids
        ]
        semantic_ids = {str(item.get("id") or "") for item in semantic_nodes}
        if semantic_nodes:
            semantic_edges = [
                item
                for item in result.get("edges", [])
                if isinstance(item, dict)
                and str(item.get("source") or "") in semantic_ids
                and str(item.get("target") or "") in semantic_ids
            ]
            base = {
                "nodes": semantic_nodes,
                "edges": semantic_edges,
                "meta": context_base.get("meta") or {},
                "provider": "Hyper-Extract semantic + domain search",
            }
    except HTTPException:
        pass
    merged = _merge_graph(_merge_graph(base, local_hits), overlay_hits)
    merged["searchResults"] = _rank_search_results(merged, payload.query)
    return _response(merged)


@router.post("/chat")
def graph_chat(payload: GraphQuery) -> dict[str, Any]:
    # A sidecar answer is generated against its global graph. Within a
    # selected domain it can cite unrelated nodes, so use the scoped search
    # and compose only from the returned evidence instead.
    if not payload.domain_id and not payload.subdomain_id:
        try:
            with _SIDE_CAR_LOCK:
                _activate(payload.scope, payload.cluster)
                result = _request_json(
                    "POST",
                    "/api/chat",
                    body={"query": payload.query},
                    timeout=max(_HYPER_TIMEOUT, 120),
                )
            evidence = result.get("data") if isinstance(result, dict) else None
            if isinstance(evidence, dict) and (
                evidence.get("nodes") or evidence.get("edges")
            ):
                return _response(result)
        except HTTPException:
            pass

    search_result = graph_search(payload)["data"]
    nodes = [item for item in search_result.get("nodes", []) if isinstance(item, dict)]
    edges = [item for item in search_result.get("edges", []) if isinstance(item, dict)]
    if not nodes:
        return _response(
            {
                "response": f"当前图谱未检索到足以回答“{payload.query}”的证据。",
                "data": search_result,
                "grounded": False,
            }
        )

    labels = {
        item.get("id"): str(
            ((item.get("data") or {}).get("raw") or {}).get("name")
            or (item.get("data") or {}).get("label")
            or item.get("label")
            or item.get("id")
        )
        for item in nodes
    }
    highlighted_ids = {
        item.get("id")
        for item in nodes
        if item.get("highlighted") and item.get("id") in labels
    }
    highlighted = [labels[item_id] for item_id in highlighted_ids]
    related = highlighted or list(labels.values())
    relation_lines = [
        f"{labels[edge.get('source')]} —{(edge.get('data') or {}).get('label') or edge.get('label') or '相关'}→ {labels[edge.get('target')]}"
        for edge in edges
        if edge.get("source") in labels and edge.get("target") in labels
        and (
            edge.get("source") in highlighted_ids
            or edge.get("target") in highlighted_ids
        )
    ]
    answer = f"图谱中与“{payload.query}”直接相关的节点包括：{'、'.join(related[:8])}。"
    if relation_lines:
        answer += f" 可核验关系：{'；'.join(relation_lines[:6])}。"
    return _response(
        {
            "response": answer,
            "data": search_result,
            "grounded": True,
        }
    )


def _scan_timestamp(value: datetime | None) -> str:
    return f"{value.isoformat()}Z" if value else ""


def _scan_task_dict(row: StrategicGraphScanTaskRow) -> dict[str, Any]:
    status = "done" if row.state == "done" else "error" if row.state == "error" else "running"
    return {
        "jobId": row.id,
        "hyperJobId": row.hyper_job_id,
        "keyword": row.keyword,
        "scope": row.scope,
        "domainId": row.domain_id,
        "subdomainId": row.subdomain_id,
        "maxCandidates": row.max_candidates,
        "status": status,
        "stage": row.stage,
        "progress": dict(row.progress or {"done": 0, "total": 0}),
        "result": dict(row.result or {}) or None,
        "error": row.error or None,
        "persistent": True,
        "createdAt": _scan_timestamp(row.created_at),
        "updatedAt": _scan_timestamp(row.updated_at),
        "finishedAt": _scan_timestamp(row.finished_at),
    }


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _scan_evidence(candidate: dict[str, Any]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for item in candidate.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "").strip()
        description = str(item.get("description") or "").strip()
        if not title and not description:
            continue
        digest = hashlib.sha1(
            f"{item.get('type', 'graph')}:{title}:{description}".encode("utf-8")
        ).hexdigest()[:16]
        records.append(
            {
                "id": str(item.get("id") or f"hyper-evidence:{digest}"),
                "type": str(item.get("type") or "graph"),
                "title": title,
                "description": description,
            }
        )
    if records:
        return records

    reasons = candidate.get("reasons")
    if not isinstance(reasons, dict):
        return []
    labels = {"achievement": "成果证据", "status": "现状证据", "future": "趋势证据"}
    for key, label in labels.items():
        text = str(reasons.get(key) or "").strip()
        if not text:
            continue
        digest = hashlib.sha1(f"{key}:{text}".encode("utf-8")).hexdigest()[:16]
        records.append(
            {
                "id": f"hyper-reason:{digest}",
                "type": "reviewed_reason",
                "title": label,
                "description": text,
            }
        )
    return records


def _scan_candidate_score(
    candidate: dict[str, Any],
    *,
    keyword: str,
    scope: GraphScope,
) -> dict[str, Any]:
    evidence = _scan_evidence(candidate)
    evidence_ids = [f"hyper-graph:{_SCOPE_TO_GRAPH[scope]}"]
    evidence_ids.extend(item["id"] for item in evidence)
    evidence_ids = list(dict.fromkeys(evidence_ids))
    event_count = max(0, int(_number(candidate.get("event_count"))))
    direction_overlap = max(0, int(_number(candidate.get("direction_overlap"))))
    profile = str(candidate.get("profile") or "")
    name = str(candidate.get("name") or "").strip()
    candidate_type = str(candidate.get("type") or "")
    evidence_text = " ".join(
        f"{item['title']} {item['description']}" for item in evidence
    )
    searchable = f"{name} {profile} {evidence_text}".casefold()
    concrete = candidate_type == "机构" and _explicit_team_identity(name, profile)
    geography = bool(evidence or event_count) and scope in ("domestic", "international")
    compact_keyword = re.sub(r"\s+", "", keyword.casefold())
    compact_searchable = re.sub(r"\s+", "", searchable)
    keyword_hit = bool(
        compact_keyword
        and (
            compact_keyword in compact_searchable
            or any(
                term in searchable
                for term in re.split(r"[\s,，/、]+", keyword.casefold())
                if len(term.strip()) >= 2
            )
        )
    )
    relevant = bool(evidence or event_count) and (direction_overlap > 0 or keyword_hit)
    quality_hits = sum(
        1 for marker in _QUALITY_MARKERS if marker in evidence_text.casefold()
    )
    advantage = bool(evidence or event_count)
    leader_evidence = False
    eligible = concrete and geography and relevant and advantage

    current_year = _strategic_map._now().year
    years = [
        int(value)
        for value in re.findall(r"(?<!\d)(20\d{2})(?!\d)", f"{profile} {evidence_text}")
        if 2000 <= int(value) <= current_year + 1
    ]
    latest_year = max(years, default=0)
    if not evidence and not event_count:
        recent_activity = 0.0
    elif latest_year >= current_year - 1:
        recent_activity = 15.0
    elif latest_year >= current_year - 3:
        recent_activity = 10.0
    elif latest_year:
        recent_activity = 6.0
    else:
        recent_activity = 4.0

    legacy = {
        key: (
            round(_number(candidate.get(key)), 1)
            if candidate.get(key) is not None
            else None
        )
        for key in ("achievement", "status", "future", "total")
    }
    legacy_values = [
        legacy["achievement"],
        legacy["status"],
        legacy["future"],
    ]
    ai_review = 0.0
    if all(value is not None for value in legacy_values):
        ai_review = min(
            20.0,
            (
                legacy_values[0] * 0.4
                + legacy_values[1] * 0.3
                + legacy_values[2] * 0.3
            )
            / 5.0,
        )

    aliases = candidate.get("aliases") or []
    breakdown = {
        "achievementQuality": min(
            25.0,
            event_count * 1.5 + quality_hits * 4.0,
        ),
        "domainRelevance": min(
            15.0,
            (8.0 if relevant else 0.0)
            + min(direction_overlap, 2) * 2.0
            + (3.0 if keyword_hit else 0.0),
        ),
        "recentActivity": recent_activity,
        "evidenceReliability": min(
            10.0,
            len(evidence) * 1.5 + (2.0 if event_count >= 3 else 0.0),
        ),
        "graphInfluence": min(
            10.0,
            event_count * 1.25 + min(direction_overlap, 2) * 1.5,
        ),
        "teamCompleteness": min(
            5.0,
            (2.0 if concrete else 0.0)
            + (2.0 if profile.strip() else 0.0)
            + (1.0 if aliases else 0.0),
        ),
        "aiEvidenceReview": ai_review,
    }
    breakdown = {key: round(value, 1) for key, value in breakdown.items()}
    total = round(sum(breakdown.values()), 1) if eligible else 0.0
    return {
        **candidate,
        "legacyRank": candidate.get("rank"),
        "legacyScores": legacy,
        "eligibility": {
            "concreteTeam": concrete,
            "geography": geography,
            "domainRelevance": relevant,
            "advantageEvidence": advantage,
            "leaderEvidence": leader_evidence,
            "eligible": eligible,
        },
        "eligibilityReasons": {
            "concreteTeam": "名称或画像明确为团队/实验室/研究组"
            if concrete
            else "未找到具体科研团队证据",
            "geography": f"候选来自{'国内' if scope == 'domestic' else '国外'}图谱且有关联证据"
            if geography
            else "缺少地域范围证据",
            "domainRelevance": "图谱方向或证据与扫描关键词直接匹配"
            if relevant
            else "缺少与扫描领域直接相关的证据",
            "advantageEvidence": "存在图谱事件或成果证据"
            if advantage
            else "缺少可证明优势能力的成果证据",
            "leaderEvidence": "已核验本团队现任负责人"
            if leader_evidence
            else "扫描候选尚未完成本团队现任负责人核验",
        },
        "scoreBreakdown": breakdown,
        "evidenceIds": evidence_ids,
        "evidence": evidence,
        "scoreVersion": _SCAN_SCORE_VERSION,
        "scoredAt": f"{_strategic_map._now().isoformat()}Z",
        "total": total,
    }


def _normalize_scan_result(
    raw_result: Any,
    *,
    keyword: str,
    scope: GraphScope,
) -> dict[str, Any]:
    source = raw_result if isinstance(raw_result, dict) else {}
    scored = [
        _scan_candidate_score(item, keyword=keyword, scope=scope)
        for item in source.get("candidates") or []
        if isinstance(item, dict)
    ]
    scored.sort(
        key=lambda item: (
            not bool((item.get("eligibility") or {}).get("eligible")),
            -_number(item.get("total")),
            -int(_number(item.get("event_count"))),
            str(item.get("name") or ""),
        )
    )
    for index, item in enumerate(scored, start=1):
        item["rank"] = index
    eligible_count = sum(
        1 for item in scored if (item.get("eligibility") or {}).get("eligible")
    )
    return {
        **source,
        "keyword": str(source.get("keyword") or keyword),
        "summary": str(source.get("summary") or "扫描完成"),
        "scoringSummary": (
            f"七维证据评分完成：{eligible_count}/{len(scored)} 个候选通过四项准入门槛。"
        ),
        "scoreVersion": _SCAN_SCORE_VERSION,
        "scoredAt": f"{_strategic_map._now().isoformat()}Z",
        "candidates": scored,
    }


def _scan_error_message(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    return str(exc) or type(exc).__name__


def _update_scan_task(task_id: str, **values: Any) -> None:
    with _SCAN_SESSION_FACTORY() as session:
        row = session.get(StrategicGraphScanTaskRow, task_id)
        if row is None:
            return
        for key, value in values.items():
            setattr(row, key, value)
        row.updated_at = _strategic_map._now()
        session.commit()


def _run_persistent_scan(task_id: str) -> None:
    with _SCAN_SESSION_FACTORY() as session:
        row = session.get(StrategicGraphScanTaskRow, task_id)
        if row is None:
            return
        scope = row.scope
        keyword = row.keyword
        max_candidates = row.max_candidates
    try:
        _update_scan_task(
            task_id,
            state="running",
            stage="提交 Hyper 扫描",
            started_at=_strategic_map._now(),
        )
        started = _request_json(
            "POST",
            "/api/scan",
            body={
                "keyword": keyword,
                "graph": _SCOPE_TO_GRAPH[scope],
                "max_candidates": max_candidates,
            },
            timeout=30,
        )
        if started.get("error"):
            raise RuntimeError(str(started["error"]))
        hyper_job_id = str(started.get("job_id") or "")
        if not re.fullmatch(r"scan-[a-f0-9]{8}", hyper_job_id):
            raise RuntimeError("Hyper-Extract 未返回有效扫描任务 ID")
        _update_scan_task(task_id, hyper_job_id=hyper_job_id, stage="排队中")

        deadline = time.monotonic() + _SCAN_TIMEOUT
        while time.monotonic() < deadline:
            remote = _request_json(
                "GET",
                "/api/scan",
                params={"job_id": hyper_job_id},
                timeout=10,
            )
            state = str(remote.get("status") or "error")
            _update_scan_task(
                task_id,
                stage=str(remote.get("stage") or ""),
                progress=remote.get("progress") or {"done": 0, "total": 0},
            )
            if state == "done":
                normalized = _normalize_scan_result(
                    remote.get("result"),
                    keyword=keyword,
                    scope=scope,
                )
                _update_scan_task(
                    task_id,
                    state="done",
                    stage="完成",
                    result=normalized,
                    error="",
                    finished_at=_strategic_map._now(),
                )
                return
            if state == "error":
                raise RuntimeError(str(remote.get("error") or "Hyper-Extract 扫描失败"))
            time.sleep(_SCAN_POLL_INTERVAL)
        raise TimeoutError(f"扫描超过 {int(_SCAN_TIMEOUT)} 秒")
    except Exception as exc:
        _update_scan_task(
            task_id,
            state="error",
            stage="失败",
            error=_scan_error_message(exc),
            finished_at=_strategic_map._now(),
        )


def _mark_interrupted_scan_tasks() -> None:
    with _SCAN_SESSION_FACTORY() as session:
        # Tests and embedders may replace the strategic-map session factory.
        # Ensure this table exists on the factory's actual bind.
        StrategicGraphScanTaskRow.__table__.create(
            session.get_bind(),
            checkfirst=True,
        )
        rows = session.query(StrategicGraphScanTaskRow).filter(
            StrategicGraphScanTaskRow.state.in_(("accepted", "running"))
        ).all()
        if not rows:
            return
        now = _strategic_map._now()
        for row in rows:
            row.state = "error"
            row.stage = "已中断"
            row.error = "ai4s-tool 服务重启，未完成的扫描已中断，请重新扫描"
            row.updated_at = now
            row.finished_at = now
        session.commit()


_mark_interrupted_scan_tasks()


@router.post("/scans", status_code=202)
def start_graph_scan(payload: GraphScanRequest) -> dict[str, Any]:
    _context_names(payload.domain_id, payload.subdomain_id)
    if not _remote_scan_available():
        raise HTTPException(status_code=503, detail="Hyper-Extract 实时扫描服务尚未连接")
    task_id = f"scan-{uuid.uuid4().hex}"
    with _SCAN_SESSION_FACTORY() as session:
        row = StrategicGraphScanTaskRow(
            id=task_id,
            scope=payload.scope,
            keyword=payload.keyword.strip(),
            domain_id=payload.domain_id,
            subdomain_id=payload.subdomain_id,
            max_candidates=payload.max_candidates,
            progress={"done": 0, "total": 0},
        )
        session.add(row)
        session.commit()
        response = _scan_task_dict(row)
    threading.Thread(
        target=_run_persistent_scan,
        args=(task_id,),
        daemon=True,
        name=f"strategic-graph-{task_id[-8:]}",
    ).start()
    return _response(response)


@router.get("/scans")
def list_graph_scans(
    scope: GraphScope = Query("domestic"),
    domain_id: str | None = Query(None),
    subdomain_id: str | None = Query(None),
    limit: int = Query(10, ge=1, le=50),
) -> dict[str, Any]:
    with _SCAN_SESSION_FACTORY() as session:
        query = session.query(StrategicGraphScanTaskRow).filter(
            StrategicGraphScanTaskRow.scope == scope
        )
        if domain_id:
            query = query.filter(StrategicGraphScanTaskRow.domain_id == domain_id)
        if subdomain_id:
            query = query.filter(StrategicGraphScanTaskRow.subdomain_id == subdomain_id)
        rows = query.order_by(
            StrategicGraphScanTaskRow.created_at.desc()
        ).limit(limit).all()
        return _response([_scan_task_dict(row) for row in rows])


@router.get("/scans/{job_id}")
def get_graph_scan(job_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"scan-[a-f0-9]{32}", job_id):
        raise HTTPException(status_code=400, detail="扫描任务 ID 无效")
    with _SCAN_SESSION_FACTORY() as session:
        row = session.get(StrategicGraphScanTaskRow, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="扫描任务不存在")
        return _response(_scan_task_dict(row))
