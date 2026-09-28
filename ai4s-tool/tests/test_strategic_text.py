import pytest
from ai4s_tool.api import strategic_text as text


@pytest.mark.parametrize("query,original", [
    ("中科院", "中国科学院生物物理研究所"), ("中国科学院", "中科院大气物理研究所"),
    ("暴雨预测", "团队开展暴雨预报"), ("量子通讯", "quantum communication"),
    ("CAS-ESM", "CAS-ESM 地球系统模式"),
])
def test_aliases_are_symmetric_without_changing_originals(query, original):
    assert text.search_match(query, original)
    assert text.snippet(query, original) == original
    assert text.normalize(text.normalize(original)) == text.normalize(original)


def result(task, source="团队发表量子计算和量子模拟论文，并开放代码", **extra):
    team = {"teamName": "量子组", "domainName": "量子科技", "researchDirections": ["量子计算", "量子模拟"], **extra}
    claim = ("c", "t", "r", "outcome", source, source, "https://uni.edu.cn/paper", "")
    return text.evaluate_task(text.parse_task(task), team, [claim])


def test_polite_long_query_preserves_scientific_requirements():
    short = result("量子计算与模拟")
    long = result("请推荐能承担量子计算与模拟任务的国内团队")
    assert short[0] > 0 and short == long
    assert {x["requirement"] for x in long[2]} == {"量子计算", "量子模拟"}
    assert all(x["citations"] for x in long[2])


def test_required_capabilities_exclusions_and_unproved_outcomes():
    assert result("量子计算与模拟", "量子计算论文")[0] == 0
    assert result("量子计算，不考虑量子模拟")[0] == 0
    assert result("量子计算，具备临床试验")[0] == 0
    assert result("蛋白质结构预测")[0] == 0
    assert not text.search_match("量子计算", "神经计算论文")


def test_unknown_constraints_are_not_claimed_as_satisfied():
    parsed = text.parse_task("量子计算，三个月交付")
    assert "三个月交付" in parsed["unresolved"]
    assert result("量子计算，三个月交付")[0] == 0


def test_profile_description_cannot_complete_missing_outcome_capability():
    team = {"teamName": "量子组", "domainName": "量子科技", "researchDirections": ["量子计算", "量子模拟"]}
    claims = [
        ("o1", "t1", "r1", "outcome", "量子计算论文", "团队发表量子计算论文", "https://example.org/paper", ""),
        ("d1", "t1", "r1", "description", "量子模拟方向", "团队介绍提及量子模拟", "https://example.org/profile", ""),
    ]
    assert text.evaluate_task(text.parse_task("量子计算与模拟"), team, claims)[0] == 0
    score, citations, criteria = text.evaluate_task(text.parse_task("量子计算"), team, claims)
    assert score > 0
    assert [c["id"] for c in citations] == ["o1"]
    assert criteria == [{"requirement": "量子计算", "matched": True, "citations": ["o1"]}]
