import unittest
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ai4s_tool.api import strategic_map as sm
from ai4s_tool.api import domain_research as dr


class StrategicMapProductRulesTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        sm._Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self):
        self.engine.dispose()

    def test_paid_refresh_and_startup_snapshot_require_explicit_opt_in(self):
        with patch.dict(sm.os.environ, {}, clear=True):
            self.assertFalse(sm._daily_scheduler_enabled())
            self.assertFalse(sm._startup_snapshot_sync_enabled())
            with patch.dict(sm.os.environ, {
                "STRATEGIC_MAP_SCHEDULER_ENABLED": "true",
                "STRATEGIC_MAP_SKIP_STARTUP_SYNC": "false",
            }):
                self.assertTrue(sm._daily_scheduler_enabled())
                self.assertTrue(sm._startup_snapshot_sync_enabled())

    def test_scheduled_refresh_lease_is_cross_session_and_owner_safe(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = create_engine(f"sqlite:///{Path(folder) / 'scheduler.db'}")
            sm._Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            with patch.object(sm, "_SESSION_FACTORY", factory):
                self.assertTrue(sm._acquire_scheduler_lease("first"))
                self.assertFalse(sm._acquire_scheduler_lease("second"))
                self.assertTrue(sm._renew_scheduler_lease("first"))
                self.assertFalse(sm._renew_scheduler_lease("second"))
                sm._release_scheduler_lease("second")
                self.assertFalse(sm._acquire_scheduler_lease("second"))
                sm._release_scheduler_lease("first")
                self.assertTrue(sm._acquire_scheduler_lease("second"))
                sm._release_scheduler_lease("second")
            engine.dispose()

    def test_scheduled_refresh_lease_allows_only_one_concurrent_claim(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = create_engine(f"sqlite:///{Path(folder) / 'scheduler.db'}")
            sm._Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            with patch.object(sm, "_SESSION_FACTORY", factory):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    claims = list(executor.map(sm._acquire_scheduler_lease, ("first", "second")))
                self.assertEqual([False, True], sorted(claims))
                with factory() as session:
                    row = session.get(sm.StrategicSyncMetaRow, sm._SCHEDULER_LEASE_KEY)
                    owner, _ = sm._scheduler_lease_state(row)
                    row.value = sm._scheduler_lease_value(
                        owner, sm._now() - timedelta(seconds=1)
                    )
                    session.commit()
                self.assertTrue(sm._acquire_scheduler_lease("recovered"))
                sm._release_scheduler_lease("recovered")
            engine.dispose()

    def test_scheduled_refresh_rechecks_due_after_claim_and_retries_partial_run(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = create_engine(f"sqlite:///{Path(folder) / 'scheduler.db'}")
            sm._Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            with factory() as session:
                session.add(sm.StrategicDomainRow(id="domain", name="通用 AI"))
                session.commit()

            calls = []

            def fail_once(session, domain):
                calls.append(domain.id)
                if len(calls) == 1:
                    # A competing worker sees the lease, not a duplicate run.
                    sm._run_scheduled_refresh()
                    raise RuntimeError("source temporarily unavailable")
                return {"teamCount": 1}

            with (
                patch.object(sm, "_SESSION_FACTORY", factory),
                patch.object(sm, "_seed_defaults"),
                patch.object(sm, "_sync_domain", side_effect=fail_once),
            ):
                sm._run_scheduled_refresh()
                with factory() as session:
                    self.assertTrue(sm._auto_refresh_due(session))
                    self.assertTrue(sm._auto_domain_attempt_recent(session, "domain"))
                    self.assertIsNone(session.get(sm.StrategicSyncMetaRow, "last_auto_refresh"))
                sm._run_scheduled_refresh()
                self.assertEqual(["domain"], calls)
                with factory() as session:
                    attempt = session.get(
                        sm.StrategicSyncMetaRow,
                        f"{sm._SCHEDULER_DOMAIN_ATTEMPT_PREFIX}domain",
                    )
                    attempt.value = (sm._now() - timedelta(days=2)).isoformat()
                    session.commit()
                sm._run_scheduled_refresh()
                with factory() as session:
                    self.assertFalse(sm._auto_refresh_due(session))
                sm._run_scheduled_refresh()

            self.assertEqual(["domain", "domain"], calls)
            engine.dispose()

    def test_scheduled_refresh_retries_only_failed_domains(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = create_engine(f"sqlite:///{Path(folder) / 'scheduler.db'}")
            sm._Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            with factory() as session:
                session.add_all((
                    sm.StrategicDomainRow(id="first", name="通用 AI", sort_order=0),
                    sm.StrategicDomainRow(id="second", name="化学与材料", sort_order=1),
                ))
                session.commit()

            calls = []

            def fail_second_once(session, domain):
                calls.append(domain.id)
                if domain.id == "second" and calls.count("second") == 1:
                    raise RuntimeError("temporary source failure")
                return {"teamCount": 1}

            with (
                patch.object(sm, "_SESSION_FACTORY", factory),
                patch.object(sm, "_seed_defaults"),
                patch.object(sm, "_sync_domain", side_effect=fail_second_once),
            ):
                sm._run_scheduled_refresh()
                with factory() as session:
                    self.assertTrue(sm._auto_domain_refresh_recent(session, "first"))
                    self.assertFalse(sm._auto_domain_refresh_recent(session, "second"))
                    self.assertTrue(sm._auto_domain_attempt_recent(session, "second"))
                    self.assertTrue(sm._auto_refresh_due(session))
                sm._run_scheduled_refresh()
                self.assertEqual(1, calls.count("second"))
                with factory() as session:
                    attempt = session.get(
                        sm.StrategicSyncMetaRow,
                        f"{sm._SCHEDULER_DOMAIN_ATTEMPT_PREFIX}second",
                    )
                    attempt.value = (sm._now() - timedelta(days=2)).isoformat()
                    session.commit()
                sm._run_scheduled_refresh()
                with factory() as session:
                    self.assertFalse(sm._auto_refresh_due(session))

            self.assertEqual(1, calls.count("first"))
            self.assertEqual(2, calls.count("second"))
            engine.dispose()

    def test_scheduled_refresh_honors_recent_legacy_domain_job_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = create_engine(f"sqlite:///{Path(folder) / 'scheduler.db'}")
            sm._Base.metadata.create_all(engine)
            dr.JOBS.create(engine, checkfirst=True)
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            with factory() as session:
                session.add(sm.StrategicDomainRow(id="domain", name="通用 AI"))
                session.execute(dr.JOBS.insert().values(
                    id="domain:domain", domain_id="domain", team_id=None,
                    state="incomplete", payload={}, updated=time.time(),
                ))
                session.commit()
            with (
                patch.object(sm, "_SESSION_FACTORY", factory),
                patch.object(sm, "_seed_defaults"),
                patch.object(sm, "_sync_domain") as sync,
            ):
                sm._run_scheduled_refresh()
                sync.assert_not_called()
                with factory() as session:
                    self.assertTrue(sm._auto_refresh_due(session))
                    self.assertTrue(sm._auto_domain_attempt_recent(session, "domain"))
            engine.dispose()

    def test_legacy_roots_migrate_without_losing_team_identity_or_manual_fields(self):
        with self.factory() as session:
            legacy = sm.StrategicDomainRow(id="legacy-life", name="生命科学")
            child = sm.StrategicDomainRow(
                id="legacy-protein",
                name="蛋白质结构",
                parent_id=legacy.id,
            )
            team = sm.StrategicTeamRow(
                id="stable-team",
                domain_id=legacy.id,
                subdomain_id=child.id,
                name="测试机构",
                institution_name="测试机构",
                team_name="测试团队",
                attention="重点关注",
                contact_record="人工联系记录",
            )
            session.add_all((legacy, child, team))
            session.commit()
            sm._ensure_fixed_domain_taxonomy(session)

            roots = session.query(sm.StrategicDomainRow).filter_by(
                parent_id=None,
                deleted=False,
            ).all()
            migrated = session.get(sm.StrategicTeamRow, "stable-team")
            target = session.get(sm.StrategicDomainRow, migrated.domain_id)
            target_children = {
                row.name for row in session.query(sm.StrategicDomainRow).filter_by(
                    parent_id=target.id, deleted=False,
                ).all()
            }

        self.assertEqual(sm._FIXED_DOMAIN_NAMES, {row.name for row in roots})
        self.assertEqual("生命科学与医学", target.name)
        self.assertEqual("stable-team", migrated.id)
        self.assertEqual("重点关注", migrated.attention)
        self.assertEqual("人工联系记录", migrated.contact_record)
        self.assertEqual({"蛋白质结构"}, target_children)

    def test_deleted_curated_subdomain_stays_deleted_after_reseeding(self):
        with self.factory() as session:
            sm._seed_defaults(session)
            root = session.query(sm.StrategicDomainRow).filter_by(
                name="通用 AI", parent_id=None, deleted=False,
            ).one()
            child = session.query(sm.StrategicDomainRow).filter_by(
                name="推理与智能体", parent_id=root.id, deleted=False,
            ).one()
            root_id, deleted_id = root.id, child.id

        with patch.object(sm, "_SESSION_FACTORY", self.factory):
            sm.delete_subdomain(deleted_id)

        with self.factory() as session:
            sm._seed_defaults(session)
            matching = session.query(sm.StrategicDomainRow).filter_by(
                name="推理与智能体", parent_id=root_id,
            ).all()
            self.assertEqual([deleted_id], [row.id for row in matching])
            self.assertTrue(matching[0].deleted)
            self.assertEqual(0, session.query(sm.StrategicDomainRow).filter_by(
                name="推理与智能体", parent_id=root_id, deleted=False,
            ).count())

        with patch.object(sm, "_SESSION_FACTORY", self.factory):
            recreated = sm.create_subdomain(
                root_id,
                sm.SubdomainPayload(name="推理与智能体", description="人工重新添加"),
            )["data"]
        with self.factory() as session:
            sm._seed_defaults(session)
            active = session.query(sm.StrategicDomainRow).filter_by(
                name="推理与智能体", parent_id=root_id, deleted=False,
            ).all()
            self.assertEqual([recreated["id"]], [row.id for row in active])

    def test_renamed_curated_subdomain_does_not_recreate_old_name(self):
        with self.factory() as session:
            sm._seed_defaults(session)
            root = session.query(sm.StrategicDomainRow).filter_by(
                name="通用 AI", parent_id=None, deleted=False,
            ).one()
            child = session.query(sm.StrategicDomainRow).filter_by(
                name="推理与智能体", parent_id=root.id, deleted=False,
            ).one()
            child_id = child.id
            child.name = "自定义推理方向"
            session.commit()
            sm._seed_defaults(session)
            self.assertEqual(0, session.query(sm.StrategicDomainRow).filter_by(
                name="推理与智能体", parent_id=root.id, deleted=False,
            ).count())
            self.assertEqual(child_id, session.query(sm.StrategicDomainRow).filter_by(
                name="自定义推理方向", parent_id=root.id, deleted=False,
            ).one().id)

    def test_refresh_task_migration_adds_subdomain_scope(self):
        engine = create_engine("sqlite://")
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE strategic_map_refresh_task "
                "(id TEXT PRIMARY KEY, domain_id TEXT NOT NULL, state TEXT NOT NULL)"
            )
        with patch.object(sm, "_ENGINE", engine):
            sm._ensure_refresh_task_schema()
            sm._ensure_refresh_task_schema()
        with engine.connect() as connection:
            columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info(strategic_map_refresh_task)"
                ).fetchall()
            }
        self.assertIn("subdomain_id", columns)
        engine.dispose()

    def test_finishing_nationwide_task_preserves_batch_identity(self):
        with self.factory() as session:
            task = sm.StrategicRefreshTaskRow(
                id="refresh-test",
                domain_id="domain",
                state="running",
                result={"nationwideBatchId": "nationwide-test"},
            )
            session.add(task)
            session.flush()

            sm._finish_refresh_task(
                session,
                task,
                state="succeeded",
                message="完成",
                result={"teamCount": 12},
            )

            self.assertEqual("nationwide-test", task.result["nationwideBatchId"])
            self.assertEqual(12, task.result["teamCount"])

    def test_hybrid_score_is_zero_until_all_evidence_gates_pass(self):
        with self.factory() as session:
            pending = sm.StrategicTeamRow(
                id="pending",
                domain_id="domain",
                name="测试机构",
                institution_name="测试机构",
                team_name="测试团队",
                research_directions=["科学计算"],
                location="中国",
                verification_status="collected",
                evidence_summary="公开来源",
                evidence_urls=["https://example.org/team"],
                team_confidence=0.8,
            )
            verified = sm.StrategicTeamRow(
                id="verified",
                domain_id="domain",
                name="验证机构",
                institution_name="验证机构",
                team_name="验证团队",
                research_directions=["科学计算"],
                location="中国",
                verification_status="verified",
                evidence_summary="官方页面明确给出团队与成果",
                evidence_urls=["https://example.org/verified"],
                team_confidence=0.8,
                recent_update="2026-09-20",
            )
            session.add_all((pending, verified))
            session.flush()
            pending_score = sm._update_team_score(session, pending)
            verified_without_leader = sm._update_team_score(session, verified)
            session.add(
                sm.StrategicPersonRow(
                    id="leader",
                    team_id=verified.id,
                    name="测试负责人",
                    role="团队负责人",
                    is_leader=True,
                    verification_status="verified",
                    source_urls=["https://example.org/leader"],
                )
            )
            session.flush()
            verified_score = sm._update_team_score(session, verified)

        self.assertEqual(0, pending_score["total"])
        self.assertFalse(pending_score["eligibility"]["eligible"])
        self.assertGreater(verified_without_leader["total"], 0)
        self.assertFalse(verified_without_leader["eligibility"]["leaderEvidence"])
        self.assertGreater(verified_score["total"], 0)
        self.assertTrue(verified_score["eligibility"]["eligible"])
        self.assertTrue(verified_score["eligibility"]["leaderEvidence"])
        self.assertEqual("hybrid-evidence-v3", verified_score["version"])

    def test_candidate_pool_requires_verified_roster_and_score_threshold(self):
        with self.factory() as session:
            domain = sm.StrategicDomainRow(id="domain", name="通用 AI")
            teams = [
                sm.StrategicTeamRow(
                    id=team_id,
                    domain_id=domain.id,
                    name=f"机构 {team_id}",
                    team_name=f"团队 {team_id}",
                    description="该团队持续开展人工智能科学研究，并有公开的项目、人员和成果证据，研究方向涉及模型设计、实验验证与应用评估。",
                    eligibility={"eligible": True},
                    score_total=score,
                )
                for team_id, score in (("qualified", 62.0), ("low-score", 59.9), ("missing-roster", 80.0))
            ]
            session.add_all((domain, *teams))
            for team_id in ("qualified", "low-score"):
                for name, is_leader in (("负责人", True), ("成员甲", False), ("成员乙", False)):
                    session.add(sm.StrategicPersonRow(
                        id=f"{team_id}-{name}",
                        team_id=team_id,
                        name=name,
                        is_leader=is_leader,
                        verification_status="verified",
                        source_urls=["https://example.org/roster"],
                    ))
            session.commit()

        with patch.object(sm, "_SESSION_FACTORY", self.factory):
            all_data = sm.get_strategic_map(
                refresh=False, domain_id=None, include_legacy=False,
                include_incomplete=True,
            )["data"]
            admitted = sm.get_strategic_map(
                refresh=False, domain_id=None, include_legacy=False,
                include_incomplete=False,
            )["data"]

        self.assertEqual(3, len(all_data["teams"]))
        self.assertEqual({"qualified"}, {team["id"] for team in admitted["teams"]})
        self.assertEqual(1, all_data["source"]["candidateQualified"])
        self.assertEqual(60.0, all_data["source"]["candidateThreshold"])
        self.assertFalse(next(team for team in all_data["teams"] if team["id"] == "missing-roster")["candidateQualified"])

    def test_graph_score_survives_leader_removal_without_http_sources(self):
        with self.factory() as session:
            team = sm.StrategicTeamRow(
                id="graph-team",
                domain_id="domain",
                name="测试实验室",
                institution_name="测试实验室",
                team_name="测试实验室",
                research_directions=["科学计算"],
                location="中国（Hyper 国内图谱）",
                verification_status="graph_verified",
                evidence_summary="Hyper 图谱事件证明该团队发布研究成果。",
                score_evidence_ids=["hyper-graph:zn", "hyper-event:test"],
                team_confidence=0.85,
            )
            session.add(team)
            session.flush()
            score = sm._update_team_score(session, team)

        self.assertGreater(score["total"], 0)
        self.assertTrue(score["eligibility"]["eligible"])
        self.assertFalse(score["eligibility"]["leaderEvidence"])
        self.assertIn("hyper-event:test", score["evidenceIds"])

    def test_explicit_pi_is_promoted_when_team_has_no_leader(self):
        with self.factory() as session:
            team = sm.StrategicTeamRow(
                id="team",
                domain_id="domain",
                name="测试机构",
                institution_name="测试机构",
                team_name="测试实验室",
            )
            person = sm.StrategicPersonRow(
                id="person",
                team_id=team.id,
                name="测试负责人",
                role="PI",
                is_leader=False,
                verification_status="verified",
                source_urls=["https://example.org/team"],
            )
            session.add_all((team, person))
            session.flush()
            self.assertEqual(1, sm._promote_explicit_team_heads(session))
            self.assertTrue(person.is_leader)

    def test_subdomain_refresh_falls_back_to_web_when_graph_roster_is_incomplete(self):
        domain = SimpleNamespace(id="root", name="通用 AI")
        subdomain = SimpleNamespace(id="child", name="多模态基础模型")
        graph_result = {
            "candidateCount": 1,
            "teamCount": 1,
            "leaderCount": 0,
            "memberTeamCount": 0,
            "memberCount": 0,
        }
        with (
            self.factory() as session,
            patch(
                "ai4s_tool.api.strategic_graph.sync_hyper_snapshot",
                return_value=graph_result,
            ),
            patch(
                "ai4s_tool.api.domain_research.sync_domain",
                return_value={"teamCount": 2, "leaderCount": 1},
            ) as web_sync,
        ):
            result = sm._sync_domain(session, domain, subdomain=subdomain)

        web_sync.assert_called_once()
        self.assertEqual(
            "Hyper-Extract graph → neighborhood → public Web fallback",
            result["provider"],
        )
        self.assertEqual(1, result["graphCandidateCount"])
        self.assertEqual(0, result["graphLeaderCount"])

    def test_subdomain_refresh_skips_web_for_complete_graph_rosters(self):
        domain = SimpleNamespace(id="root", name="通用 AI")
        subdomain = SimpleNamespace(id="child", name="多模态基础模型")
        graph_result = {
            "candidateCount": 2,
            "teamCount": 2,
            "leaderCount": 2,
            "memberTeamCount": 2,
            "memberCount": 5,
        }
        with (
            self.factory() as session,
            patch(
                "ai4s_tool.api.strategic_graph.sync_hyper_snapshot",
                return_value=graph_result,
            ),
            patch("ai4s_tool.api.domain_research.sync_domain") as web_sync,
        ):
            result = sm._sync_domain(session, domain, subdomain=subdomain)

        web_sync.assert_not_called()
        self.assertEqual("Hyper-Extract graph → neighborhood", result["provider"])
        self.assertEqual(2, result["graphMemberTeamCount"])

    def test_nationwide_seed_populates_every_fixed_domain_without_bypassing_gate(self):
        with self.factory() as session:
            result = sm._seed_nationwide_scan_candidates(session)
            roots = session.query(sm.StrategicDomainRow).filter_by(
                parent_id=None,
                deleted=False,
            ).all()
            counts = {
                domain.name: session.query(sm.StrategicTeamRow).filter_by(
                    domain_id=domain.id,
                    deleted=False,
                ).count()
                for domain in roots
            }
            sample = session.query(sm.StrategicTeamRow).filter_by(
                team_name="高性能计算机研究中心"
            ).one()

        self.assertEqual(48, result["total"])
        self.assertEqual({name: 8 for name in sm._FIXED_DOMAIN_NAMES}, counts)
        self.assertEqual("graph_verified", sample.verification_status)
        self.assertGreater(sample.score_total, 0)
        self.assertTrue(sample.eligibility["concreteTeam"])
        self.assertTrue(sample.eligibility["geography"])
        self.assertTrue(sample.eligibility["domainRelevance"])
        self.assertTrue(sample.eligibility["advantageEvidence"])

    def test_nationwide_refresh_queues_one_sequential_batch(self):
        with (
            patch.object(sm, "_SESSION_FACTORY", self.factory),
            patch.object(sm, "_launch_refresh_batch") as launch,
        ):
            response = sm.start_nationwide_refresh()["data"]

        self.assertGreaterEqual(response["seeded"]["total"], 48)
        self.assertTrue(all(count >= 8 for count in response["seeded"]["byDomain"].values()))
        self.assertEqual(6, len(response["tasks"]))
        self.assertTrue(all(task["state"] == "accepted" for task in response["tasks"]))
        launch.assert_called_once()
        self.assertEqual(6, len(launch.call_args.args[0]))


if __name__ == "__main__":
    unittest.main()
