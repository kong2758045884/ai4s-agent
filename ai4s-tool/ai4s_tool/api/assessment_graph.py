"""Versioned relationship projection of one saved assessment, with no live lookups.

Keep v1 deterministic for legacy reconstruction. Factual edges carry their saved
citations; requirements and recommendation edges describe an input or an inference,
not a verified affiliation, available resource, or cooperation agreement.
"""
from __future__ import annotations

import copy
import hashlib
import json

VERSION = "assessment-graph-v1"


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _refs(citations):
    return [{k: copy.deepcopy(c[k]) for k in ("id", "kind", "text", "quote", "url", "publishedAt", "provenance") if k in c}
            for c in citations if c.get("quote") and c.get("url", "").startswith(("https://", "http://"))]


def build_v1(run, *, legacy=False):
    """Accept only run fields. No DB, catalogue, network, selection or private notes."""
    nodes, edges = {}, {}

    def node(id_, label, type_, **raw):
        nodes.setdefault(id_, {"id": id_, "label": label, "type": type_,
                              "data": {"label": label, "raw": {"name": label, "level": type_, **raw}}})

    def edge(source, target, label, status, basis, citations=(), **raw):
        id_ = "relation:" + _digest([source, target, label])[:24]
        edges[id_] = {"id": id_, "source": source, "target": target, "label": label, "type": "关系",
                      "data": {"label": label, "raw": {"name": label, "level": "关系", "status": status,
                               "basis": basis, "citations": _refs(citations), **raw}}}

    domain_mode = run.get("mode") == "domain"
    root = ("observation:" if domain_mode else "task:") + run["taskId"]
    label = "、".join(run.get("scope", {}).get("domainNames", [])) + "领域观察" if domain_mode else run.get("taskText", "已保存任务")
    input_basis = f"已确认输入第 {run.get('inputVersion', 1)} 版；不是外部事实证明"
    node(root, label, "领域观察" if domain_mode else "任务", description=input_basis,
         taskId=run["taskId"], inputVersionId=run.get("inputVersionId"))
    criteria = {c["id"]: c for c in run.get("criteria", [])}
    for cid, c in criteria.items():
        kind = "能力要求" if c.get("kind") == "capability" else "任务条件"
        node("criterion:" + cid, c["text"], kind, description="需求条件，不代表团队已经具备", criterion=copy.deepcopy(c))
        edge(root, "criterion:" + cid, "提出条件", "input", input_basis)

    items = run.get("observation", {}).get("units", []) if domain_mode else run.get("items", [])
    for item in items:
        tid = "team:" + item["teamId"]
        citations = _refs(item.get("citations", []))
        node(tid, item["teamName"], "科研团队", teamId=item["teamId"], description=item.get("capability", item.get("reason", "")),
             institutionName=item.get("institutionName", ""), citations=citations)
        edge(root, tid, "纳入观察" if domain_mode else "推荐候选", "inference",
             "本次保存结果中的候选关系；不代表已经确认合作或可投入资源", citations,
             matchVersion=run.get("matchVersion"), taskMatchScore=item.get("taskMatchScore"))
        if item.get("institutionName"):
            # A name without a reviewed entity mapping remains team-local, never a fabricated canonical institution ID.
            iid = "institution:" + item["institutionId"] if item.get("institutionId") else "institution-record:" + item["teamId"]
            identity = _refs(item.get("identityEvidence", []))
            node(iid, item["institutionName"], "机构", institutionId=item.get("institutionId"),
                 description="已保存的机构映射" if item.get("institutionId") else "该团队档案中的机构名称，未据同名自动合并主体")
            edge(tid, iid, "档案所列归属", "source" if identity else "unconfirmed",
                 "当时保存的机构归属来源；不代表人员关系或成果归属" if identity else "旧研判未保存独立归属引文，不能视为已证实的归属关系", identity)
        by_id = {c["id"]: c for c in citations if c.get("id")}
        for cid, cite in by_id.items():
            claim_node = "claim:" + item["teamId"] + ":" + cid
            node(claim_node, cite.get("text") or cite["quote"], "成果" if cite.get("kind") == "outcome" else "证据",
                 claimId=cid, citations=[cite], description=cite["quote"])
            edge(tid, claim_node, "成果依据" if cite.get("kind") == "outcome" else "资料依据", "source",
                 "本次研判保存的团队引文；来源校验、AI 与人工结论见引文元数据", [cite])
        for row in item.get("criteriaMatrix", []):
            if row.get("criterionId") not in criteria:
                continue
            refs = [by_id[cid] for cid in row.get("claimIds", []) if cid in by_id]
            status = row.get("status", "insufficient")
            if status in {"supported", "conditional"} and not refs:
                status = "insufficient"
            text = {"supported": "引文匹配", "conditional": "条件支持", "not_observed": "未观察到排除项",
                    "not_met": "未满足", "insufficient": "依据不足"}.get(status, "依据不足")
            edge(tid, "criterion:" + row["criterionId"], text, status,
                 row.get("notice") or "按保存的条件匹配结果解释；匹配不等于已确认实际交付能力", refs,
                 criterionId=row["criterionId"], necessity=row.get("necessity"))
    graph = {"provider": "AI4S assessment snapshot", "nodes": list(nodes.values()), "edges": list(edges.values()),
             "searchResults": [], "meta": {"type": "assessment-snapshot", "features": {"scan": False, "chat": False},
                "stats": {"Nodes": len(nodes), "Edges": len(edges)}, "snapshot": {
                    "version": VERSION, "taskId": run["taskId"], "runId": run["runId"],
                    "scopeLabel": "、".join(run.get("scope", {}).get("domainNames", [])) or "按当时保存的任务范围",
                    "inputVersion": run.get("inputVersion"), "evidenceVersion": run.get("evidenceVersion", run.get("dataVersion")),
                    "frozenAt": run.get("createdAt"), "legacyReconstruction": legacy,
                    "notice": "仅按旧研判保存内容重建；未补入实时资料" if legacy else "随本次研判冻结；后续资料修改不改变此图",
                    "limitations": "未保存有据人员关系时不绘制人员；同机构、共同作者不推定团队合作。"}}}
    graph["meta"]["snapshot"]["hash"] = _digest(graph)
    return graph


def freeze(run):
    run["relationshipGraph"] = build_v1(run)


def read(run):
    return copy.deepcopy(run["relationshipGraph"]) if "relationshipGraph" in run else build_v1(run, legacy=True)
