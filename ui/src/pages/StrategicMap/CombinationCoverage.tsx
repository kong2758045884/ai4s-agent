import type { AssessmentItem, Criterion } from "@/services/strategicAssessments";

export function combinationCoverage(criteria: Criterion[], teams: AssessmentItem[]) {
  return criteria.filter(c => c.necessity === "required").map(c => {
    const supported = teams.filter(t => t.criteriaMatrix.some(row => row.criterionId === c.id && row.status === "supported"));
    const conditional = teams.filter(t => t.criteriaMatrix.some(row => row.criterionId === c.id && row.status === "conditional"));
    return { criterion: c, supported, conditional };
  });
}

export default function CombinationCoverage({ criteria, teams }: { criteria: Criterion[]; teams: AssessmentItem[] }) {
  const rows = combinationCoverage(criteria, teams);
  const covered = rows.filter(row => row.supported.length > 0).length;
  return <section aria-label="候选组合能力覆盖" className="mt-3 rounded-xl border border-blue-100 bg-blue-50/40 p-4 text-sm">
    <h5 className="font-semibold text-slate-900">组合仍需确认什么</h5>
    <p className="mt-2 leading-6">{rows.length ? `所选团队的保存证据覆盖 ${covered} / ${rows.length} 项必要条件。` : "当前未设置必要条件，请先核对任务要求。"}这是证据覆盖汇总，联合交付能力仍需确认。</p>
    <ul className="mt-2 space-y-2">{rows.map(row => <li key={row.criterion.id} className="rounded-lg bg-white p-3 text-xs leading-6">
      <p className="font-medium">{row.criterion.text}</p>
      <p>{row.supported.length ? `有依据：${row.supported.map(t => t.teamName).join("、")}` : row.conditional.length ? `条件支持，尚未计入覆盖：${row.conditional.map(t => t.teamName).join("、")}` : "尚缺依据：需补充原文或由团队确认"}</p>
    </li>)}</ul>
    <p className="mt-3 text-xs leading-6 text-slate-600">协作接口、数据使用许可、可投入人员、设备与合作意愿均待确认；建议在分工依据或跟进事项中记录责任人和验证办法。</p>
  </section>;
}
