"""Official directory contracts; synthetic sources and isolated SQLite only."""
import copy
import json
import unittest
from unittest.mock import patch

from tests import test_team_research_pipeline as fixtures
from ai4s_tool.api import official_team_directory as directory

sm, store = fixtures.sm, fixtures.store


def record():
    page = {"url": "https://test.edu.cn/group", "text": "甲研究组 PI 张甲 研究方向 基础模型 成员 李乙",
            "fetched_at": "2026-09-23T01:00:00Z"}
    return {
        "team_name": "甲研究组", "institution_name": "测试所", "domain": "测试领域",
        "description": "基础模型研究", "directions": ["基础模型"],
        "leader": directory.person(page, "张甲", "PI", "甲研究组 PI 张甲"),
        "members": [directory.person(page, "李乙", "课题组成员", "成员 李乙")],
        "source_urls": [page["url"]], "citations": [directory.citation(page, page["text"])],
        "identity_basis": "official_named_group_card",
    }


class DirectoryTest(unittest.TestCase):
    def setUp(self):
        fixtures.PipelineTest.setUp(self)

    def test_refetch_preserves_identity_manual_fields_and_deduplicates_history(self):
        value = record()
        with sm._SESSION_FACTORY() as session:
            row = session.get(sm.StrategicTeamRow, "t")
            row.description = "人工维护简介"
            store.append_history(session, row.id, "manual", {"fields": ["description"]})
            session.commit()
            with patch.object(directory, "discover", return_value=([value], [])):
                first = directory.sync(session, session.get(sm.StrategicDomainRow, "d"))
            refreshed = copy.deepcopy(value)
            for cite in refreshed["citations"]:
                cite["fetched_at"] = "2026-09-24T01:00:00Z"
            for p in [refreshed["leader"], *refreshed["members"]]:
                evidence = json.loads(p["evidence"])
                evidence["fetched_at"] = "2026-09-24T01:00:00Z"
                p["evidence"] = json.dumps(evidence)
            with patch.object(directory, "discover", return_value=([refreshed], [])):
                second = directory.sync(session, session.get(sm.StrategicDomainRow, "d"))
            self.assertEqual((0, 0), (first["directoryCreated"], second["directoryCreated"]))
            self.assertEqual(1, session.query(sm.StrategicTeamRow).count())
            self.assertEqual(2, session.query(sm.StrategicPersonRow).count())
            self.assertEqual(1, len(store.history(session, "t", "official_directory")))
            self.assertEqual("人工维护简介", row.description)
            self.assertGreater(row.score_total, 0)
            self.assertEqual(["张甲"], [p["name"] for p in sm._team_people(session, "t")[0]])

    def test_conflicting_head_retains_published_team_and_score(self):
        with sm._SESSION_FACTORY() as session:
            row = session.get(sm.StrategicTeamRow, "t")
            row.description, row.score_total = "旧简介", 68
            row.verification_status = "verified"
            store.upsert_people(session, row, {**record()["leader"], "name": "王丙"}, [])
            session.commit()
            with patch.object(directory, "discover", return_value=([record()], [])):
                result = directory.sync(session, session.get(sm.StrategicDomainRow, "d"))
            self.assertEqual(1, result["directoryConflicts"])
            self.assertEqual(("旧简介", 68, "verified"), (row.description, row.score_total, row.verification_status))
            self.assertEqual(["王丙"], [p["name"] for p in sm._team_people(session, "t")[0]])
            self.assertFalse(store.history(session, "t", "official_directory_conflict")[0]["payload"]["published"])

    def test_directory_without_head_keeps_existing_people_and_refetch_changes_nothing(self):
        value = record()
        value["leader"], value["members"] = None, []
        with sm._SESSION_FACTORY() as session:
            row = session.get(sm.StrategicTeamRow, "t")
            store.upsert_people(session, row, record()["leader"], record()["members"])
            session.commit()
            domain = session.get(sm.StrategicDomainRow, "d")
            first = directory.sync(session, domain, records=[value])
            stamp = row.updated_at
            second = directory.sync(session, domain, records=[value])
            self.assertEqual(1, first["directoryUpdated"])
            self.assertEqual(0, second["directoryUpdated"])
            self.assertEqual(stamp, row.updated_at)
            self.assertEqual(2, session.query(sm.StrategicPersonRow).count())

    def test_invalid_quote_is_rejected_and_acronym_is_same_team(self):
        with self.assertRaises(ValueError):
            directory.citation({"url": "https://test.edu.cn", "text": "副主任张甲"}, "主任李乙")
        self.assertEqual(directory._directory_key("测试所", "机器智能实验室（MIFA Lab）"),
                         directory._directory_key("测试所", "机器智能实验室"))

    def test_dynamic_subdomain_needs_full_subject_not_shared_characters(self):
        self.assertEqual(0, directory._subdomain_score("研究细胞周期与蛋白质", "细胞内吞"))
        self.assertGreater(directory._subdomain_score("研究细胞内吞过程", "细胞内吞"), 0)

    def test_roster_excludes_former_future_and_cosupervisor(self):
        source = directory.DIRECTORIES[3]
        text = (
            "About MIFA Lab 机器智能基础与应用实验室 上海交通大学 pre-training and foundation models "
            "data-efficient fine-tuning continual learning multimodal learning "
            "News 2026 paper accepted Current Members Principal Investigator First Last ( 张甲 ) "
            "PhD Students Student One ( 李乙 ), co-supervised with Dr. Someone ( 王丙 ), 2025–present "
            "Student Two ( 赵丁 ), starting 2026 MS Students Student Three ( 钱戊 ), 2024–present "
            "Former Members Student Old ( 孙己 ), 2023–present"
        )
        page = {"url": source["url"], "text": text}
        result = directory._current_members_record(source, page, {}, page)
        self.assertEqual("张甲", result["leader"]["name"])
        self.assertEqual(["李乙", "钱戊"], [p["name"] for p in result["members"]])
        page["text"] = "团队成员 张甲，博士 现为课题组副研究员。 李乙 李乙正在研究细胞。 以往成员 王丙 王丙已离职。"
        self.assertEqual(["张甲", "李乙"], [p["name"] for p in directory._profile_members(page)])

    def test_partial_source_failure_keeps_successful_groups(self):
        source = directory.DIRECTORIES[3]
        broken = {**source, "url": "https://unavailable.edu.cn"}
        page = {"url": source["url"], "status": "ok", "text": "test"}
        with patch.object(directory, "DIRECTORIES", (broken, source)), \
             patch.object(directory, "_current_members_record", return_value=record()):
            records, errors = directory.discover("通用 AI", fetch=lambda url: page if url == source["url"] else {"status": "fetch_failed"})
        self.assertEqual(1, len(records))
        self.assertEqual([{"url": broken["url"], "reason": "directory_fetch_failed"}], errors)

    def test_department_requires_team_results_and_retains_acting_role(self):
        source = directory.DIRECTORIES[4]
        page = {"url": "https://test.edu.cn/department", "title": "陶瓷材料研究部--测试所",
                "text": "研究部简介：研究陶瓷材料。研究部副主任（主持工作）：张甲 研究方向：陶瓷；复合材料 研究成果：研制新型陶瓷。课题组：甲组"}
        value = directory._department_record(source, page, {}, page)
        self.assertEqual(("张甲", "副主任（主持工作）"), (value["leader"]["name"], value["leader"]["role"]))
        self.assertEqual(["陶瓷", "复合材料"], value["directions"])
        page["text"] = page["text"].replace("研究成果：研制新型陶瓷。", "")
        self.assertIsNone(directory._department_record(source, page, {}, page))

    def test_directory_fetch_keeps_image_map_links_and_roster_after_bibliography(self):
        tr = fixtures.tr
        response = tr.requests.Response()
        response.status_code = 200
        response.url = "https://test.edu.cn"
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        response._content = ('<meta charset="utf-8"><area href="/department.html">'
                             + "论文 " * 9000 + "<p>团队成员 张甲</p>").encode()
        response._content_consumed = True
        with patch.object(tr, "_public_url", return_value=response.url), patch.object(tr.requests, "Session") as factory:
            factory.return_value.__enter__.return_value.get.return_value = response
            page = directory.fetch_directory_page(response.url)
        self.assertFalse(page["truncated"])
        self.assertIn("团队成员 张甲", page["text"])
        self.assertIn({"url": "https://test.edu.cn/department.html", "label": ""}, page["links"])

    def test_missing_model_still_commits_directory_and_records_partial_result(self):
        from ai4s_tool.api import domain_research as dr
        dr.META.create_all(sm._ENGINE)
        with sm._SESSION_FACTORY() as session, \
             patch.object(directory, "discover", return_value=([record()], [])), \
             patch.object(sm, "_llm_config", return_value=None), \
             patch("ai4s_tool.api.strategic_graph.graph_institution_seeds", return_value=[]), \
             patch.object(sm, "_shared_agent_llm_text") as model:
            with self.assertRaisesRegex(sm._SyncQualityError, "模型服务未配置"):
                dr.sync_domain(session, session.get(sm.StrategicDomainRow, "d"))
            model.assert_not_called()
            job = session.execute(dr.JOBS.select().where(dr.JOBS.c.id == "domain:d")).mappings().one()
            self.assertEqual("incomplete", job["state"])
            self.assertEqual((1, 1, 1), tuple(job["payload"][key] for key in ("teamCount", "leaderCount", "memberCount")))
            self.assertEqual(1, len(store.history(session, "t", "official_directory")))
