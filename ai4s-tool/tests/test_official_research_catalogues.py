"""Regression tests for actual discovery/attribution risks, using synthetic pages."""
import copy
import importlib.util
from pathlib import Path
import sqlite3

import pytest

from ai4s_tool.api import official_research_catalogues as sources
from ai4s_tool.api import verified_team_catalogue as catalogue
from ai4s_tool.api.official_team_directory import _signature
from ai4s_tool.api import task_recommendations as task


def page(url, text, links=()):
    return {"url": url, "text": text, "status": "ok", "links": list(links)}


def test_only_named_lab_links_and_own_achievements_are_used():
    source = sources.SOURCES[0]
    link = {"label": "量子计算实验室", "url": "https://ioe.sxu.edu.cn/sys/labs42/index.htm"}
    directory = page(source["url"], "量子计算实验室 量子技术研发平台 外国实验室 全所取得量子计算重大突破", [
        link, {"label": "量子技术研发平台", "url": "https://ioe.sxu.edu.cn/sys/platform/index.htm"},
        {"label": "外国实验室", "url": "https://foreign.edu.cn/sys/labs/index.htm"}])
    lab = page(link["url"], "量子计算实验室 实验室简介 本实验室开展量子计算以及光量子信息和精密测量研究。"
               "我们拟建立量子计算网络，未来希望发表量子计算论文。"
               "我们研制了基于光子纠缠的量子逻辑门，并实现了多模式量子计算和量子纠缠交换实验。")
    records, errors = sources.discover_source(source, directory, lambda url: {link["url"]: lab}[url])
    assert not errors and len(records) == 1
    assert records[0]["leader"] is None and records[0]["members"] == []
    outcomes = [f["text"] for f in records[0]["facts"] if f["kind"] == "outcome"]
    assert len(outcomes) == 1 and "逻辑门" in outcomes[0]
    assert not any("未来" in f or "全所" in f for f in outcomes)


def test_redirected_home_page_cannot_verify_lab_and_parent_results_do_not_flow_to_child():
    source = sources.SOURCES[0]
    directory = page(source["url"], "甲实验室 全所研制了量子计算系统")
    link = {"label": "甲实验室", "url": "https://ioe.sxu.edu.cn/sys/labs42/index.htm"}
    assert sources._record(source, directory, link, page(link["url"], "山西大学新闻" * 40)) is None
    record = sources._record(source, directory, link, page(link["url"],
        "甲实验室 实验室简介 甲实验室主要研究量子相干和精密测量，我们希望在未来建立量子信息处理和量子计算的综合实验平台。"))
    assert record and not any(f["kind"] == "outcome" for f in record["facts"])


def test_partnership_and_staffing_are_not_research_outcomes():
    body = ("实验室建立了一支多学科协同创新的研究队伍。与国外某大学研究所建立了战略合作关系。"
            "实验室发布了自主可控的地球系统模式CAS-ESM，参与国际气候评估报告和比较计划。")
    assert sources._facts(page("https://x.cas.cn", body), body, "atmosphere_units") == [body.split("。", 2)[2]]


def test_delegated_official_host_is_scoped_and_unsigned_fact_cannot_be_injected():
    source = sources.SOURCES[2]
    url = "http://labesm.iap.ac.cn/gywm/sysjj/"
    link = {"label": "地球系统数值模拟与应用全国重点实验室", "url": url}
    directory = page(source["url"], link["label"])
    intro = link["label"] + " 实验室简介 " + link["label"] + "依托中国科学院大气物理研究所，在北京研究地球系统科学，发布了自主可控的地球系统模式CAS-ESM。"
    record = sources._record(source, directory, link, page(url, intro))
    payload = {"id": "x", "isDomestic": True, "institutionName": source["institution"], "teamName": link["label"]}
    entry = {"id": "r", "status": "official_directory", "payload": {"published": True, "record": record, "signature": _signature(record)}}
    assert catalogue.project(payload, [entry])
    bad = copy.deepcopy(entry)
    bad["payload"]["record"]["citations"][1]["url"] = "http://fake.ac.cn/report"
    bad["payload"]["signature"] = _signature(bad["payload"]["record"])
    assert catalogue.project(payload, [bad]) is None


def test_task_needs_related_outcome_and_always_shows_it():
    team = {"domainName": "地球科学", "researchDirections": ["暴雨预测"]}
    def claim(id, kind, text):
        return (id, "t", "r", kind, text, text, "https://x.cas.cn/result", "")
    unrelated = [claim("a", "description", "暴雨预测"), claim("b", "outcome", "发表量子计算论文")]
    assert task._score(task._terms("暴雨预测"), team, unrelated) == (0, [])
    related = [claim("b", "outcome", "研制了暴雨预测模型"),
               *[claim(str(i), "description", "暴雨预测") for i in range(8)]]
    score, cites = task._score(task._terms("暴雨预测"), team, related)
    assert score > 0 and cites[0]["kind"] == "outcome" and len(cites) == 4


def test_chinese_task_can_recall_original_english_publication_without_adding_claims():
    claim = ("id", "t", "r", "outcome", "Quantum communication over fiber",
             "Quantum communication over fiber", "https://lab.edu.cn/paper", "")
    score, cites = task._score(task._terms("寻找可以进行量子通信的国内团队"),
                               {"researchDirections": ["量子通讯"]}, [claim])
    assert score > 0 and cites[0]["quote"] == "Quantum communication over fiber"


def migration_module():
    spec = importlib.util.spec_from_file_location("expansion", Path(__file__).parents[1] / "scripts/expand_official_coverage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rollback_preserves_unrelated_rows_and_rejects_later_edits():
    migration = migration_module()
    with sqlite3.connect(":memory:") as conn:
        conn.row_factory = sqlite3.Row
        for table in migration.TABLES:
            conn.execute(f'CREATE TABLE {table}(id TEXT PRIMARY KEY,value TEXT)')
        conn.execute("INSERT INTO strategic_map_team VALUES ('old','original')")
        before = migration.snapshot(conn)
        conn.execute("UPDATE strategic_map_team SET value='import' WHERE id='old'")
        conn.execute("INSERT INTO strategic_map_team VALUES ('new','import')")
        conn.execute("INSERT INTO strategic_map_person VALUES ('member','import')")
        after = migration.snapshot(conn)
        manifest = {"changes": migration.changes(before, after)}
        conn.execute("INSERT INTO strategic_map_team VALUES ('other','user')")
        conn.execute("UPDATE strategic_map_team SET value='user edit' WHERE id='old'")
        conn.commit()
        with pytest.raises(RuntimeError, match="已有后续改动"):
            migration.undo(conn, manifest)
        conn.execute("UPDATE strategic_map_team SET value='import' WHERE id='old'")
        conn.commit()
        migration.undo(conn, manifest)
        assert [tuple(r) for r in conn.execute("SELECT * FROM strategic_map_team ORDER BY id")] == [('old', 'original'), ('other', 'user')]
        assert conn.execute("SELECT COUNT(*) FROM strategic_map_person").fetchone()[0] == 0
