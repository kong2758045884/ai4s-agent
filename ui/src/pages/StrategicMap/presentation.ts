// Keep stored/manual values intact; the map has no approval workflow.
export const attentionLabel = (value: string) => value === "待核实" ? "未标记" : value;
export const evaluationLabel = (value: string) => value === "待核实" || !value ? "暂无评价" : value;
export const judgementLabel = (value: string) =>
  /^(?:AI\s*待核实\s*[｜|]\s*科学\s*待核实)$/.test(value) ? "暂无评价" : value;

export function savedRosterPriority(team: { leader?: unknown; members?: unknown[]; teamName?: string }) {
  if (team.leader || team.members?.length) return 2;
  return team.teamName && team.teamName !== "公开资料未注明具体团队" ? 1 : 0;
}
