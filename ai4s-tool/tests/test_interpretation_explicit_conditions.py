from ai4s_tool.api.strategic_interpretation import InterpretationRequest, interpret, matching_criteria


def test_coordinated_required_actions_are_separate_editable_criteria_with_original_spans():
    task = ("寻找能开展催化材料智能筛选并进行实验验证的国内团队，要求有论文原文，"
            "优先开源代码，排除临床试验，6个月内交付")
    result = interpret(InterpretationRequest(taskText=task), [])
    criteria = result["criteria"]
    assert [c["text"] for c in criteria] == [
        "寻找能开展催化材料智能筛选", "并进行实验验证的国内团队", "要求有论文原文",
        "优先开源代码", "排除临床试验", "6个月内交付",
    ]
    assert all(task[c["sourceSpan"]["start"]:c["sourceSpan"]["end"]] == c["text"] for c in criteria)
    assert [c["necessity"] for c in criteria] == [
        "required", "required", "required", "preferred", "excluded", "required",
    ]
    parsed, blockers = matching_criteria(criteria)
    assert "实验验证" in parsed["required"]
    # A paper claim alone does not establish access to its full original text.
    assert {b["criterionId"] for b in blockers} == {criteria[0]["id"], criteria[2]["id"], criteria[-1]["id"]}
    assert "人员、资源与合作条件" in result["unknowns"]
    assert "验收指标" in result["unknowns"]
