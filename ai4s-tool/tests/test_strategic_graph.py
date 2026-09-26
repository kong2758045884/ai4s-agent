import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ai4s_tool.api import strategic_graph as graph
from ai4s_tool.api import strategic_map as sm


class StrategicGraphTest(unittest.TestCase):
    def test_status_reports_local_graph_when_hyper_is_unavailable(self):
        with (
            patch.object(graph, "_request_json", side_effect=HTTPException(status_code=503)),
            patch.object(graph, "_snapshot_root", return_value=None),
        ):
            result = graph.graph_status()["data"]
        self.assertTrue(result["available"])
        self.assertFalse(result["hyperAvailable"])
        self.assertTrue(result["features"]["browsing"])
        self.assertFalse(result["features"]["scan"])

    def test_scan_requires_both_live_hyper_routes(self):
        with patch.object(graph, "_request_json", return_value={"paths": {"/api/scan": {"post": {}}}}):
            self.assertFalse(graph._remote_scan_available())
        with patch.object(graph, "_request_json", return_value={"paths": {"/api/scan": {"post": {}, "get": {}}}}):
            self.assertTrue(graph._remote_scan_available())
        with (
            patch.object(graph, "_snapshot_root", return_value=None),
            patch.object(graph, "_request_json", side_effect=lambda method, path, **_: (
                {"features": {"search": True}} if path == "/api/meta" else
                {"paths": {"/api/scan": {"post": {}}}}
            )),
        ):
            status = graph.graph_status()["data"]
        self.assertTrue(status["hyperAvailable"])
        self.assertFalse(status["features"]["scan"])
        with (
            patch.object(graph, "_context_names", return_value=("", "")),
            patch.object(graph, "_remote_scan_available", return_value=False),
        ):
            with self.assertRaises(HTTPException) as raised:
                graph.start_graph_scan(graph.GraphScanRequest(keyword="量子计算"))
        self.assertEqual(503, raised.exception.status_code)

    def test_graph_base_allows_saved_overlay_without_hyper_or_snapshot(self):
        unavailable = HTTPException(status_code=503, detail="unavailable")
        with (
            patch.object(graph, "_remote_graph", side_effect=unavailable),
            patch.object(graph, "_snapshot_graph", side_effect=unavailable),
        ):
            result = graph._graph_base("domestic", "高峰")
        self.assertEqual([], result["nodes"])
        self.assertEqual([], result["edges"])
        self.assertEqual("strategic-map", result["provider"])

    def test_snapshot_preserves_domestic_and_foreign_cluster_views(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            payload = {
                "nodes": [
                    {"name": "高峰", "level": "领域大类"},
                    {"name": "高原", "level": "领域大类"},
                    {"name": "化学与材料", "level": "领域方向"},
                    {"name": "通用 AI", "level": "领域方向"},
                    {"name": "材料团队", "level": "机构"},
                    {"name": "模型团队", "level": "机构"},
                ],
                "edges": [
                    {"source": "化学与材料", "target": "高峰", "type": "隶属"},
                    {"source": "材料团队", "target": "化学与材料", "type": "隶属"},
                    {"source": "通用 AI", "target": "高原", "type": "隶属"},
                    {"source": "模型团队", "target": "通用 AI", "type": "隶属"},
                ],
            }
            for name in ("ZN", "GW"):
                target = root / name
                target.mkdir()
                (target / "data.json").write_text(json.dumps(payload), encoding="utf-8")
            with patch.dict(os.environ, {"STRATEGIC_MAP_HYPER_SNAPSHOT_DIR": folder}):
                peak = graph._snapshot_graph("domestic", "高峰")
                plateau = graph._snapshot_graph("international", "高原")
        self.assertEqual({"高峰", "化学与材料", "材料团队"}, {item["id"] for item in peak["nodes"]})
        self.assertEqual({"高原", "通用 AI", "模型团队"}, {item["id"] for item in plateau["nodes"]})

    def test_imported_snapshot_is_browsable_but_not_a_live_scan_service(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            payload = {
                "nodes": [{"name": "高原", "level": "领域大类"}],
                "edges": [{"source": "高原", "target": "高原", "type": "示例"}],
            }
            for name in ("ZN", "GW"):
                target = root / name
                target.mkdir()
                (target / "data.json").write_text(json.dumps(payload), encoding="utf-8")
                (target / "metadata.json").write_text(
                    json.dumps({"updated_at": "2026-09-22 00:17:40.561097"}),
                    encoding="utf-8",
                )
            with patch.dict(os.environ, {"STRATEGIC_MAP_HYPER_SNAPSHOT_DIR": folder}):
                imported = graph._snapshot_all("domestic")
                with (
                    patch.object(graph, "_request_json", side_effect=HTTPException(status_code=503)),
                ):
                    status = graph.graph_status()["data"]
        self.assertEqual("Hyper-Extract snapshot", imported["provider"])
        self.assertFalse(imported["meta"]["features"]["scan"])
        self.assertTrue(status["available"])
        self.assertFalse(status["hyperAvailable"])
        self.assertFalse(status["features"]["scan"])
        self.assertEqual(
            "2026-09-22T00:17:40.561097",
            status["snapshotUpdatedAt"]["domestic"],
        )

    def test_merge_keeps_hyper_nodes_and_adds_evidence_overlay(self):
        base = {
            "nodes": [{"id": "化学与材料"}],
            "edges": [],
            "meta": {"stats": {"Nodes": 1, "Edges": 0}},
            "provider": "Hyper-Extract",
        }
        overlay = {
            "nodes": [{"id": "team:1"}, {"id": "evidence:1"}],
            "edges": [
                {"id": "edge:1", "source": "evidence:1", "target": "team:1"}
            ],
        }
        result = graph._merge_graph(base, overlay)
        self.assertEqual(3, result["meta"]["stats"]["Nodes"])
        self.assertEqual(1, result["meta"]["stats"]["Edges"])
        self.assertEqual({"化学与材料", "team:1", "evidence:1"}, {item["id"] for item in result["nodes"]})

    def test_local_search_returns_only_direct_matches_and_one_hop_context(self):
        overlay = {
            "nodes": [
                graph._node("root", "高峰", {"name": "高峰"}),
                graph._node("domain", "化学与材料", {"name": "化学与材料"}),
                graph._node("team", "材料团队", {"name": "材料团队"}),
                graph._node("evidence", "论文证据", {"name": "论文证据"}),
            ],
            "edges": [
                graph._edge("domain", "root", "隶属"),
                graph._edge("team", "domain", "研究方向"),
                graph._edge("evidence", "team", "证据支持"),
            ],
        }
        result = graph._matching_overlay(overlay, "化学与材料")
        self.assertEqual({"root", "domain", "team"}, {item["id"] for item in result["nodes"]})
        self.assertEqual({"domain"}, {item["id"] for item in result["nodes"] if item["highlighted"]})
        self.assertNotIn("evidence", {item["id"] for item in result["nodes"]})
        natural = graph._matching_overlay(overlay, "化学与材料节点连接哪些团队？")
        self.assertIn("domain", {item["id"] for item in natural["nodes"] if item["highlighted"]})

    def test_chat_discards_ungrounded_sidecar_answer(self):
        search_result = {
            "nodes": [
                graph._node(
                    "domain",
                    "通用 AI",
                    {"name": "通用 AI", "level": "领域方向"},
                    highlighted=True,
                ),
                graph._node("root", "高原", {"name": "高原", "level": "领域大类"}),
            ],
            "edges": [graph._edge("domain", "root", "隶属")],
            "meta": {},
            "provider": "test",
        }
        with (
            patch.object(graph, "_activate"),
            patch.object(
                graph,
                "_request_json",
                return_value={"response": "无证据模型回答", "data": {"nodes": [], "edges": []}},
            ),
            patch.object(graph, "graph_search", return_value={"data": search_result}),
        ):
            result = graph.graph_chat(graph.GraphQuery(query="通用 AI"))
        self.assertTrue(result["data"]["grounded"])
        self.assertNotIn("无证据模型回答", result["data"]["response"])
        self.assertIn("通用 AI", result["data"]["response"])

    def test_scoped_chat_uses_scoped_evidence_only(self):
        scoped = {
            "nodes": [graph._node("selected", "选中领域", {"name": "选中领域"})],
            "edges": [],
            "meta": {},
            "provider": "test",
        }
        with (
            patch.object(graph, "_request_json") as sidecar,
            patch.object(graph, "graph_search", return_value={"data": scoped}),
        ):
            result = graph.graph_chat(graph.GraphQuery(query="介绍节点", domain_id="selected"))
        sidecar.assert_not_called()
        self.assertIn("选中领域", result["data"]["response"])

    def test_invalid_graph_context_never_falls_back_to_global_nodes(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = create_engine(
                f"sqlite:///{Path(folder) / 'scope.db'}",
                connect_args={"check_same_thread": False},
            )
            sm._Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            with factory() as session:
                session.add_all((
                    sm.StrategicDomainRow(id="root", name="通用 AI"),
                    sm.StrategicDomainRow(id="child", name="科研智能体", parent_id="root"),
                    sm.StrategicDomainRow(id="other", name="化学与材料"),
                ))
                session.commit()
            with patch.object(sm, "_SESSION_FACTORY", factory):
                self.assertEqual(("通用 AI", "科研智能体"), graph._context_names("root", "child"))
                for domain_id, subdomain_id, status in (
                    (None, "child", 400),
                    ("missing", None, 404),
                    ("other", "child", 404),
                    ("root", "missing", 404),
                ):
                    with self.subTest(domain_id=domain_id, subdomain_id=subdomain_id):
                        with self.assertRaises(HTTPException) as error:
                            graph._context_names(domain_id, subdomain_id)
                        self.assertEqual(status, error.exception.status_code)
            engine.dispose()

    def test_child_scope_excludes_unassigned_and_neighboring_imported_nodes(self):
        base = {
            "nodes": [
                graph._node("root", "高原", {"name": "高原", "level": "领域大类"}),
                graph._node("domain", "通用 AI", {"name": "通用 AI", "level": "领域方向"}),
                graph._node("own", "科研智能体团队", {"name": "科研智能体团队", "subdomainId": "agents"}),
                graph._node("neighbor", "其他科研智能体团队", {"name": "其他科研智能体团队", "subdomainId": "models"}),
                graph._node("legacy", "科研智能体旧节点", {"name": "科研智能体旧节点"}),
            ],
            "edges": [
                graph._edge("domain", "root", "隶属"),
                graph._edge("own", "domain", "隶属"),
                graph._edge("neighbor", "domain", "隶属"),
                graph._edge("legacy", "domain", "隶属"),
            ],
            "meta": {},
            "provider": "test",
        }
        parent = graph._filter_persisted_graph_scope(
            base, domain_name="通用 AI", subdomain_name="", subdomain_id=None, cluster="高原"
        )
        child = graph._filter_persisted_graph_scope(
            base, domain_name="通用 AI", subdomain_name="科研智能体", subdomain_id="agents", cluster="高原"
        )
        self.assertEqual({"root", "domain", "own", "neighbor", "legacy"}, {node["id"] for node in parent["nodes"]})
        self.assertEqual({"own"}, {node["id"] for node in child["nodes"]})

    def test_domain_and_subdomain_filters_return_different_subgraphs(self):
        nodes = [
            graph._node("高原", "高原", {"name": "高原", "level": "领域大类"}),
            graph._node("通用AI", "通用AI", {"name": "通用AI", "level": "领域方向"}),
            graph._node("科学通用底座", "科学通用底座", {"name": "科学通用底座", "level": "领域方向"}),
            graph._node("模型机构", "模型机构", {"name": "模型机构", "level": "机构", "description": "科学基础模型"}),
            graph._node("智能体机构", "智能体机构", {"name": "智能体机构", "level": "机构", "description": "科研智能体与推理"}),
            graph._node("计算平台", "计算平台", {"name": "计算平台", "level": "机构", "description": "科学计算与仿真"}),
        ]
        edges = [
            graph._edge("通用AI", "高原", "隶属"),
            graph._edge("科学通用底座", "高原", "隶属"),
            graph._edge("模型机构", "科学通用底座", "隶属"),
            graph._edge("智能体机构", "通用AI", "隶属"),
            graph._edge("计算平台", "科学通用底座", "隶属"),
        ]
        base = {"nodes": nodes, "edges": edges, "meta": {}, "provider": "test"}
        ai = graph._filter_graph_context(base, "通用 AI", "")
        foundation = graph._filter_graph_context(base, "科学通用底座", "")
        models = graph._filter_graph_context(base, "科学通用底座", "科学基础模型")

        self.assertEqual({"高原", "通用AI", "智能体机构"}, {item["id"] for item in ai["nodes"]})
        self.assertEqual(
            {"高原", "科学通用底座", "模型机构", "计算平台"},
            {item["id"] for item in foundation["nodes"]},
        )
        self.assertIn("模型机构", {item["id"] for item in models["nodes"]})
        self.assertNotIn("计算平台", {item["id"] for item in models["nodes"]})

    def test_saved_subdomains_are_graph_nodes_and_selection_is_an_exact_filter(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = create_engine(
                f"sqlite:///{Path(folder) / 'overlay.db'}",
                connect_args={"check_same_thread": False},
            )
            sm._Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            with factory() as session:
                domain = sm.StrategicDomainRow(id="domain", name="通用 AI")
                agents = sm.StrategicDomainRow(
                    id="agents",
                    name="科研智能体",
                    parent_id=domain.id,
                    sort_order=0,
                )
                models = sm.StrategicDomainRow(
                    id="models",
                    name="科学基础模型",
                    parent_id=domain.id,
                    sort_order=1,
                )
                session.add_all(
                    (
                        domain,
                        agents,
                        models,
                        sm.StrategicTeamRow(
                            id="agent-team",
                            domain_id=domain.id,
                            subdomain_id=agents.id,
                            name="智能体实验室",
                            institution_name="智能体实验室",
                            team_name="智能体团队",
                            score_evidence_ids=["https://example.org/agent-team"],
                        ),
                        sm.StrategicTeamRow(
                            id="model-team",
                            domain_id=domain.id,
                            subdomain_id=models.id,
                            name="基础模型实验室",
                            institution_name="基础模型实验室",
                            team_name="基础模型团队",
                        ),
                    )
                )
                session.commit()

            with patch.object(sm, "_SESSION_FACTORY", factory):
                domain_graph = graph._strategic_overlay(
                    scope="domestic",
                    cluster="高原",
                    domain_id=domain.id,
                    subdomain_id=None,
                )
                agents_graph = graph._strategic_overlay(
                    scope="domestic",
                    cluster="高原",
                    domain_id=domain.id,
                    subdomain_id=agents.id,
                )

            domain_node_ids = {item["id"] for item in domain_graph["nodes"]}
            agents_node_ids = {item["id"] for item in agents_graph["nodes"]}
            self.assertIn("科研智能体", domain_node_ids)
            self.assertIn("科学基础模型", domain_node_ids)
            self.assertIn("team:agent-team", domain_node_ids)
            self.assertIn("team:model-team", domain_node_ids)
            self.assertIn("科研智能体", agents_node_ids)
            self.assertIn("team:agent-team", agents_node_ids)
            self.assertNotIn("科学基础模型", agents_node_ids)
            self.assertNotIn("team:model-team", agents_node_ids)
            team_nodes = {
                item["id"]: item["data"]["raw"]
                for item in domain_graph["nodes"]
                if item["id"].startswith("team:")
            }
            self.assertEqual("agents", team_nodes["team:agent-team"]["subdomainId"])
            self.assertEqual("models", team_nodes["team:model-team"]["subdomainId"])
            evidence_nodes = [
                item["data"]["raw"]
                for item in domain_graph["nodes"]
                if item["id"].startswith("evidence:agent-team:")
            ]
            self.assertEqual(["agents"], [item["subdomainId"] for item in evidence_nodes])
            self.assertTrue(
                any(
                    edge["source"] == "team:agent-team"
                    and edge["target"] == "科研智能体"
                    for edge in domain_graph["edges"]
                )
            )
            engine.dispose()

    def test_dynamic_subdomain_expands_compound_name_into_graph_hints(self):
        base = {
            "nodes": [
                graph._node("高原", "高原", {"name": "高原", "level": "领域大类"}),
                graph._node("通用AI", "通用AI", {"name": "通用AI", "level": "领域方向"}),
                graph._node(
                    "千问研究团队",
                    "千问研究团队",
                    {
                        "name": "千问研究团队",
                        "level": "机构",
                        "description": "开展多模态模型研发",
                    },
                ),
                graph._node(
                    "千问成果",
                    "千问成果",
                    {
                        "name": "千问成果",
                        "level": "事件",
                        "description": "发布视觉语言基础模型",
                    },
                ),
                graph._node(
                    "智能体团队",
                    "智能体团队",
                    {
                        "name": "智能体团队",
                        "level": "机构",
                        "description": "开展智能体规划研究",
                    },
                ),
            ],
            "edges": [
                graph._edge("通用AI", "高原", "隶属"),
                graph._edge("千问研究团队", "通用AI", "隶属"),
                graph._edge("千问成果", "千问研究团队", "隶属"),
                graph._edge("智能体团队", "通用AI", "隶属"),
            ],
            "meta": {},
            "provider": "test",
        }

        result = graph._filter_graph_context(
            base,
            "通用 AI",
            "多模态基础模型",
            "高原",
        )

        self.assertIn("多模态", graph._subdomain_hints("多模态基础模型"))
        self.assertIn("基础模型", graph._subdomain_hints("多模态基础模型"))
        self.assertEqual(
            {"高原", "通用AI", "千问研究团队", "千问成果"},
            {item["id"] for item in result["nodes"]},
        )

    def test_team_identity_uses_the_entity_opening_definition_only(self):
        self.assertTrue(
            graph._explicit_team_identity(
                "阿里千问",
                "阿里巴巴旗下大模型团队，负责多模态模型研发。",
            )
        )
        self.assertFalse(
            graph._explicit_team_identity(
                "阿里巴巴",
                "中国互联网科技企业，旗下千问团队负责多模态模型研发。",
            )
        )

    def test_native_empty_result_still_recalls_dynamic_subdomain_from_snapshot(self):
        base = {
            "nodes": [
                graph._node("通用AI", "通用AI", {"name": "通用AI", "level": "领域方向"}),
                graph._node("模型实验室", "模型实验室", {
                    "name": "模型实验室", "level": "机构", "description": "多模态基础模型研究"
                }),
                graph._node("成果", "成果", {
                    "name": "成果", "level": "事件", "description": "发布多模态基础模型开源成果"
                }),
            ],
            "edges": [
                graph._edge("模型实验室", "通用AI", "隶属"),
                graph._edge("成果", "模型实验室", "隶属"),
            ],
        }
        with (
            patch.object(graph, "_snapshot_all", return_value=base),
            patch.object(graph, "_request_json", return_value={"candidates": []}) as remote,
            patch.object(graph._strategic_map, "_llm_config", return_value=None),
        ):
            result = graph._hyper_team_candidates("通用 AI", "多模态基础模型")
        self.assertEqual(["模型实验室"], [row["team_name"] for row in result])
        self.assertGreater(result[0]["total"], 0)
        self.assertEqual(["/api/candidates"], [call.args[1] for call in remote.call_args_list])

    def test_scan_score_uses_graph_evidence_without_waiting_for_leader_review(self):
        candidate = {
            "rank": 1,
            "name": "量子精密测量实验室",
            "type": "机构",
            "profile": "面向量子精密测量的科研团队，2026 年持续发布成果。",
            "event_count": 3,
            "direction_overlap": 1,
            "achievement": 80,
            "status": 70,
            "future": 60,
            "total": 72,
            "evidence": [
                {
                    "id": "hyper-event:a",
                    "type": "event",
                    "title": "实验室发布量子测量开源平台",
                    "description": "2026 年发布并开放实验数据。",
                },
                {
                    "id": "hyper-event:b",
                    "type": "event",
                    "title": "量子精密测量取得突破",
                    "description": "团队完成新一轮实验验证。",
                },
            ],
        }
        scored = graph._scan_candidate_score(
            candidate,
            keyword="量子精密测量",
            scope="domestic",
        )
        self.assertTrue(scored["eligibility"]["eligible"])
        self.assertFalse(scored["eligibility"]["leaderEvidence"])
        self.assertGreater(scored["total"], 0)
        self.assertEqual(15.0, scored["scoreBreakdown"]["recentActivity"])
        self.assertEqual(
            {
                "achievementQuality",
                "domainRelevance",
                "recentActivity",
                "evidenceReliability",
                "graphInfluence",
                "teamCompleteness",
                "aiEvidenceReview",
            },
            set(scored["scoreBreakdown"]),
        )
        self.assertIn("hyper-event:a", scored["evidenceIds"])
        self.assertEqual("hybrid-evidence-v4", scored["scoreVersion"])

        institution_only = graph._scan_candidate_score(
            {
                **candidate,
                "name": "某大学",
                "profile": "综合性大学",
            },
            keyword="量子精密测量",
            scope="domestic",
        )
        self.assertFalse(institution_only["eligibility"]["concreteTeam"])
        self.assertEqual(0, institution_only["total"])

    def test_native_hyper_candidates_keep_full_team_set_and_graph_people_as_clues(self):
        remote_candidates = [
            {
                "rank": index + 1,
                "name": f"量子科研团队{index + 1}",
                "canonical_name": f"量子科研团队{index + 1}",
                "type": "机构",
                "description": "面向量子计算的具体科研团队。",
                "profile": "面向量子计算的具体科研团队。",
                "aliases": [f"Quantum Team {index + 1}"] if index == 0 else [],
                "directions": ["量子计算与模拟"],
                "event_count": 1,
                "direction_overlap": 1,
                "evidence": [
                    {
                        "id": f"hyper-event:{index + 1}",
                        "type": "event",
                        "title": f"量子计算成果 {index + 1}",
                        "description": "团队发布量子计算研究成果。",
                    }
                ],
                "related_authors": [
                    {
                        "name": f"研究人员{index + 1}",
                        "profile": "论文作者",
                        "relation": "隶属",
                        "relation_description": "",
                    }
                ],
            }
            for index in range(12)
        ]
        with patch.object(graph, "_snapshot_all", return_value={"nodes": [], "edges": []}), patch.object(
            graph,
            "_request_json",
            return_value={
                "keyword": "量子计算",
                "total_found": 12,
                "returned_count": 12,
                "truncated": False,
                "candidates": remote_candidates,
            },
        ) as request, patch.object(graph._strategic_map, "_llm_config", return_value=None):
            candidates = graph._hyper_team_candidates(
                "高能物理与量子科技",
                "量子计算",
            )

        self.assertEqual(12, len(candidates))
        self.assertEqual(["Quantum Team 1"], candidates[0]["aliases"])
        self.assertEqual(
            "hyper-event:1",
            candidates[0]["evidence"][0]["id"],
        )
        self.assertEqual([], candidates[0]["leaders"])
        self.assertEqual([], candidates[0]["members"])
        self.assertEqual("研究人员1", candidates[0]["personClues"][0]["name"])
        self.assertEqual(
            "Hyper-Extract find_candidates structured API",
            candidates[0]["_hyper_provider"],
        )
        self.assertEqual(
            {
                "keyword": "量子计算",
                "graph": "zn",
                "max_candidates": 200,
                "merge_aliases": False,
                "include_all": True,
                "semantic_search": False,
            },
            request.call_args.kwargs["body"],
        )

    def test_snapshot_sync_removes_graph_only_leader_claims(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = create_engine(
                f"sqlite:///{Path(folder) / 'map.db'}",
                connect_args={"check_same_thread": False},
            )
            sm._Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            with factory() as session:
                domain = sm.StrategicDomainRow(id="domain", name="通用 AI")
                team = sm.StrategicTeamRow(
                    id="team",
                    domain_id=domain.id,
                    name="测试实验室",
                    institution_name="测试实验室",
                    team_name="测试实验室",
                )
                graph_leader = sm.StrategicPersonRow(
                    id="graph-leader",
                    team_id=team.id,
                    name="图谱作者",
                    role="负责人",
                    is_leader=True,
                    source_type="Hyper-Extract 图谱关系",
                    verification_status="verified",
                )
                official_leader = sm.StrategicPersonRow(
                    id="official-leader",
                    team_id=team.id,
                    name="官方负责人",
                    role="负责人",
                    is_leader=True,
                    source_type="官方团队页面·规则核验",
                    verification_status="verified",
                )
                session.add_all((domain, team, graph_leader, official_leader))
                session.commit()
                candidate = {
                    "institution_name": "测试实验室",
                    "team_name": "测试实验室",
                    "description": "具体科研团队",
                    "directions": ["通用 AI"],
                    "evidence": [
                        {
                            "id": "hyper-event:test",
                            "title": "测试成果",
                            "description": "公开发布研究成果。",
                        }
                    ],
                    "evidenceIds": ["hyper-event:test"],
                    "eligibility": {"eligible": True, "leaderEvidence": False},
                    "scoreBreakdown": {"domainRelevance": 20.0},
                    "total": 20.0,
                    "leaders": [],
                    "members": [],
                    "_hyper_provider": "Hyper-Extract find_candidates structured API",
                }
                with patch.object(
                    graph,
                    "_hyper_team_candidates",
                    return_value=[candidate],
                ):
                    result = graph.sync_hyper_snapshot(session, domain)
                session.commit()
                session.refresh(graph_leader)
                session.refresh(official_leader)

            self.assertEqual(0, result["leaderCount"])
            self.assertTrue(graph_leader.deleted)
            self.assertFalse(official_leader.deleted)
            self.assertTrue(official_leader.is_leader)
            engine.dispose()

    def test_completed_scan_is_read_from_sqlite_after_hyper_job_finishes(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = create_engine(
                f"sqlite:///{Path(folder) / 'scan.db'}",
                connect_args={"check_same_thread": False},
            )
            graph.StrategicGraphScanTaskRow.__table__.create(engine)
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            task_id = f"scan-{'a' * 32}"
            with factory() as session:
                session.add(
                    graph.StrategicGraphScanTaskRow(
                        id=task_id,
                        scope="domestic",
                        keyword="材料发现",
                        max_candidates=10,
                        progress={"done": 0, "total": 0},
                    )
                )
                session.commit()
            remote_result = {
                "status": "done",
                "stage": "完成",
                "progress": {"done": 1, "total": 1},
                "result": {
                    "keyword": "材料发现",
                    "summary": "完成",
                    "candidates": [
                        {
                            "rank": 1,
                            "name": "材料发现研究团队",
                            "type": "机构",
                            "profile": "2026 年材料发现研究团队",
                            "event_count": 2,
                            "direction_overlap": 1,
                            "achievement": 70,
                            "status": 60,
                            "future": 60,
                            "total": 64,
                            "evidence": [
                                {
                                    "id": "hyper-event:material",
                                    "type": "event",
                                    "title": "团队发布材料发现平台",
                                    "description": "2026 年公开发布。",
                                }
                            ],
                        }
                    ],
                },
            }
            with (
                patch.object(graph, "_SCAN_SESSION_FACTORY", factory),
                patch.object(
                    graph,
                    "_request_json",
                    side_effect=[
                        {"job_id": "scan-deadbeef"},
                        remote_result,
                    ],
                ),
            ):
                graph._run_persistent_scan(task_id)
                response = graph.get_graph_scan(task_id)["data"]

            self.assertEqual("done", response["status"])
            self.assertTrue(response["persistent"])
            self.assertEqual(
                "hybrid-evidence-v4",
                response["result"]["candidates"][0]["scoreVersion"],
            )
            self.assertEqual("hyper-event:material", response["result"]["candidates"][0]["evidence"][0]["id"])
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
