import type { AssessmentItem, AssessmentRun, Criterion } from "@/services/strategicAssessments";

export function suggestedRole(item: AssessmentItem, criteria: Criterion[]) {
  const supported = criteria.flatMap(criterion => {
    if (criterion.necessity !== "required" || !["goal", "capability"].includes(criterion.kind)) return [];
    const row = item.criteriaMatrix.find(value => value.criterionId === criterion.id);
    if (row?.status !== "supported") return [];
    const claimIds = row.claimIds.filter(id => item.citations.some(c => c.id === id && c.kind === "outcome"));
    return claimIds.length ? [{ text: criterion.text, claimIds }] : [];
  });
  if (!supported.length) return null;
  return { text: `${supported.slice(0, 2).map(value => value.text).join("、")}相关环节（待团队确认）`,
    claimIds: [...new Set(supported.slice(0, 2).flatMap(value => value.claimIds))] };
}

export function shortageStages(run: AssessmentRun) {
  const coverage = run.coverage;
  if (!coverage) return [];
  const { scopeTeamCount, outcomeBackedTeamCount, conditionMatchedTeamCount } = coverage;
  const stages = [];
  if (scopeTeamCount < run.requestedLimit) stages.push({ title: "范围收录不足",
    detail: `当前范围收录 ${scopeTeamCount} 支具体科研单元，少于希望的 ${run.requestedLimit} 支。可检查领域范围。` });
  if (outcomeBackedTeamCount < Math.min(scopeTeamCount, run.requestedLimit)) stages.push({ title: "团队成果依据不足",
    detail: `其中 ${outcomeBackedTeamCount} 支有可用于推荐的团队级成果依据；其余仍需补充或核验原文。` });
  if (conditionMatchedTeamCount < Math.min(outcomeBackedTeamCount, run.requestedLimit)) stages.push({ title: "条件尚未获充分支持",
    detail: `已有成果的团队中，${conditionMatchedTeamCount} 支通过已确认条件的当前证据规则；其余未获逐项支持，不等于科研能力为零。` });
  return stages;
}
