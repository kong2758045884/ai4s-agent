package org.wwz.ai.test.domain;

import org.junit.Assert;
import org.junit.Test;
import org.wwz.ai.domain.agent.runtime.prompt.AgentPrompt;
import org.wwz.ai.domain.agent.runtime.prompt.PlanSolvePrompt;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

/** AI4S 正式研判必须加载运行时 skill，并保持老师认可的九章契约。 */
public class Ai4sReportPromptContractTest {

    @Test
    public void reactAndPlanSolveShouldRequireReportSkill() {
        Assert.assertTrue(AgentPrompt.REACT_SYSTEM_PROMPT.contains("ai4s-report-analysis"));
        Assert.assertTrue(AgentPrompt.REACT_SYSTEM_PROMPT.contains("skill_tool"));
        Assert.assertTrue(AgentPrompt.REACT_SYSTEM_PROMPT.contains("九章契约"));
        Assert.assertTrue(PlanSolvePrompt.ORCHESTRATION.contains("ai4s-report-analysis"));
        Assert.assertTrue(PlanSolvePrompt.ORCHESTRATION.contains("正式交付为已打开验证的 HTML"));
    }

    @Test
    public void runtimeSkillShouldContainExactlyTheNineRequiredChapterNames() throws Exception {
        Path skill = Path.of("runtime", "skills", "ai4s-report-analysis", "SKILL.md");
        if (!Files.isRegularFile(skill)) {
            skill = Path.of("..", "runtime", "skills", "ai4s-report-analysis", "SKILL.md").normalize();
        }
        Assert.assertTrue("runtime skill missing: " + skill.toAbsolutePath(), Files.isRegularFile(skill));
        String content = Files.readString(skill, StandardCharsets.UTF_8);
        List<String> chapters = List.of(
                "事件概览", "技术路线", "主要创新", "论文团队", "前序工作",
                "竞争路线", "AI4S意义", "待观察问题", "来源证据"
        );
        for (String chapter : chapters) {
            Assert.assertTrue("missing chapter " + chapter, content.contains("**" + chapter + "**"));
        }
        Assert.assertTrue(content.contains("科学问题、输入输出和评价指标写入“技术路线”"));
        Assert.assertFalse(content.contains("输出十个独立章节"));
    }
}
