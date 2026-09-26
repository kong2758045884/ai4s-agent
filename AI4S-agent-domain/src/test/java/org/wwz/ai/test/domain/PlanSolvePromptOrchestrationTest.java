package org.wwz.ai.test.domain;

import org.junit.Assert;
import org.junit.Test;
import org.wwz.ai.domain.agent.runtime.prompt.AgentPrompt;
import org.wwz.ai.domain.agent.runtime.prompt.PlanSolvePrompt;
import org.wwz.ai.domain.agent.runtime.subagent.SubAgentDefinition;
import org.wwz.ai.domain.agent.runtime.subagent.SubAgentRegistry;

public class PlanSolvePromptOrchestrationTest {

    @Test
    public void ensureOrchestrationIsIdempotentAndWaitsForRequestedDeliverables() {
        String once = PlanSolvePrompt.ensureOrchestration("");
        String twice = PlanSolvePrompt.ensureOrchestration(once);
        Assert.assertEquals(once, twice);
        Assert.assertTrue(once.contains(PlanSolvePrompt.ORCHESTRATION_MARKER));
        Assert.assertTrue(once.contains("规模门控"));
        Assert.assertTrue(once.contains("## 7. Plan Mode"));
        Assert.assertTrue(once.contains("EnterPlanMode"));
        Assert.assertTrue(once.contains("运行中禁止 Agent(resume_agent_id)"));
        Assert.assertTrue(once.contains("禁止用 workspace_list 轮询子 Agent 是否完成"));
        Assert.assertTrue(once.contains("已结束/失败才 resume；运行中用 SendMessage"));
        Assert.assertTrue(once.contains("先 TaskOutput 等完成"));
        Assert.assertTrue(once.contains("不能只因已派工就结束本轮"));
        Assert.assertTrue(once.contains("workspace_append 按稳定 chunk_key 逐章串行追加"));
        Assert.assertTrue(once.contains("AI4S 正式研报写入 report/，正式海报写入 poster/"));
        Assert.assertTrue(once.contains("TaskOutput 等全部完成 → glob"));
        Assert.assertFalse(once.contains("启动 Agent 后短告知用户启动了什么，然后结束本轮"));
        Assert.assertEquals(1, once.split(PlanSolvePrompt.ORCHESTRATION_MARKER, -1).length - 1);
    }

    @Test
    public void ensureOrchestrationReplacesLegacyV3Block() {
        String legacy = "# Plan-Execute 主代理职责 (PLAN_SOLVE_ORCHESTRATION_V3)\n- old\n";
        String upgraded = PlanSolvePrompt.ensureOrchestration(legacy);
        Assert.assertTrue(upgraded.contains(PlanSolvePrompt.ORCHESTRATION_MARKER));
        Assert.assertFalse(upgraded.contains("PLAN_SOLVE_ORCHESTRATION_V3"));
        Assert.assertTrue(upgraded.contains("协调者"));
    }

    @Test
    public void ensureOrchestrationReplacesLegacyV5Block() {
        String legacy = "# Plan-Execute 主代理职责 (PLAN_SOLVE_ORCHESTRATION_V5)\n- 续跑用 resume_agent_id / SendMessage\n";
        String upgraded = PlanSolvePrompt.ensureOrchestration(legacy);
        Assert.assertTrue(upgraded.contains(PlanSolvePrompt.ORCHESTRATION_MARKER));
        Assert.assertFalse(upgraded.contains("PLAN_SOLVE_ORCHESTRATION_V5"));
        Assert.assertTrue(upgraded.contains("运行中禁止 Agent(resume_agent_id)"));
    }

    @Test
    public void ensureOrchestrationReplacesPrematureCompletionV6Block() {
        String legacy = "# Plan-Execute 主代理职责 (PLAN_SOLVE_ORCHESTRATION_V6)\n"
                + "- 启动 Agent 后短告知用户启动了什么，然后结束本轮\n";
        String upgraded = PlanSolvePrompt.ensureOrchestration(legacy);
        Assert.assertTrue(upgraded.contains(PlanSolvePrompt.ORCHESTRATION_MARKER));
        Assert.assertFalse(upgraded.contains("PLAN_SOLVE_ORCHESTRATION_V6"));
        Assert.assertFalse(upgraded.contains("启动 Agent 后短告知用户启动了什么，然后结束本轮"));
    }

    @Test
    public void ensureOrchestrationReplacesUnboundedWriterV7Block() {
        String legacy = "# Plan-Execute 主代理职责 (PLAN_SOLVE_ORCHESTRATION_V7)\n- old\n";
        String upgraded = PlanSolvePrompt.ensureOrchestration(legacy);
        Assert.assertTrue(upgraded.contains(PlanSolvePrompt.ORCHESTRATION_MARKER));
        Assert.assertFalse(upgraded.contains("PLAN_SOLVE_ORCHESTRATION_V7"));
        Assert.assertTrue(upgraded.contains("workspace_append 按稳定 chunk_key 逐章串行追加"));
    }

    @Test
    public void ensureOrchestrationReplacesReadStateDependentV8Block() {
        String legacy = "# Plan-Execute 主代理职责 (PLAN_SOLVE_ORCHESTRATION_V8)\n- old\n";
        String upgraded = PlanSolvePrompt.ensureOrchestration(legacy);
        Assert.assertTrue(upgraded.contains(PlanSolvePrompt.ORCHESTRATION_MARKER));
        Assert.assertFalse(upgraded.contains("PLAN_SOLVE_ORCHESTRATION_V8"));
        Assert.assertTrue(upgraded.contains("workspace_append 按稳定 chunk_key 逐章串行追加"));
    }

    @Test
    public void ensureOrchestrationReplacesV9WithValidatedReportPaths() {
        String legacy = "# Plan-Execute 主代理职责 (PLAN_SOLVE_ORCHESTRATION_V9)\n- Worker 自定报告路径\n";
        String upgraded = PlanSolvePrompt.ensureOrchestration(legacy);
        Assert.assertTrue(upgraded.contains(PlanSolvePrompt.ORCHESTRATION_MARKER));
        Assert.assertFalse(upgraded.contains("PLAN_SOLVE_ORCHESTRATION_V9"));
        Assert.assertTrue(upgraded.contains("AI4S 正式研报写入 report/，正式海报写入 poster/"));
    }

    @Test
    public void planSolveBaseIsOrchestrationNotReact() {
        String plan = AgentPrompt.composePlanSolveSystemPrompt();
        Assert.assertTrue(plan.contains(PlanSolvePrompt.ORCHESTRATION_MARKER));
        Assert.assertTrue(plan.contains(AgentPrompt.USER_FACING_REPLY_CONTRACT_MARKER));
        Assert.assertFalse(plan.contains("你是 AI4S，专注深度调研与数据分析"));
        Assert.assertTrue(plan.startsWith("# Plan-Execute 主代理职责")
                || plan.contains("# Plan-Execute 主代理职责"));
    }

    @Test
    public void subAgentSharesReactBaseWithCoordinatorFacing() {
        String sub = AgentPrompt.composeSubAgentSystemPrompt("专属指令");
        Assert.assertTrue(sub.contains("你是 AI4S 研判系统，面向用户提供智能研判服务"));
        Assert.assertTrue(sub.contains(AgentPrompt.COORDINATOR_FACING_REPLY_CONTRACT_MARKER));
        Assert.assertFalse(sub.contains(AgentPrompt.USER_FACING_REPLY_CONTRACT_MARKER));
        Assert.assertTrue(sub.contains("# Subagent directive"));
        Assert.assertTrue(sub.contains("专属指令"));
        Assert.assertFalse(sub.contains("执行型子代理（Worker）"));
    }

    @Test
    public void reactBaseKeepsUserFacingWhenEnsured() {
        String react = AgentPrompt.ensureUserFacingReplyContract(AgentPrompt.REACT_SYSTEM_PROMPT);
        Assert.assertTrue(react.contains(AgentPrompt.USER_FACING_REPLY_CONTRACT_MARKER));
        Assert.assertFalse(react.contains(AgentPrompt.COORDINATOR_FACING_REPLY_CONTRACT_MARKER));
        Assert.assertFalse(react.contains(PlanSolvePrompt.ORCHESTRATION_MARKER));
    }

    @Test
    public void generalPurposeDirectiveIsTypeSupplementOnly() {
        SubAgentRegistry registry = new SubAgentRegistry();
        SubAgentDefinition def = registry.require(SubAgentRegistry.TYPE_GENERAL_PURPOSE);
        Assert.assertTrue(def.getSystemPrompt().contains("通用调研/分析执行补充"));
        Assert.assertTrue(def.getWhenToUse().contains("Deep Search"));
    }
}
