import type { StrategicTeam } from "@/services/strategicMap";

const LEVELS = ["待核实", "较低", "一般", "较高"] as const;
export type JudgementLevel = typeof LEVELS[number];
export const capabilitySourceLabel = (source?: string) =>
  source === "manual" ? "人工修改" : source === "ai" ? "AI 初判" : "未初评";
export const capabilityLevelLabel = (value?: string) =>
  !value || value === "待核实" ? "未评价" : value;

export default function TeamJudgementEditor({ aiLevel, scienceLevel, assessments, editing, disabled, onChange }: {
  aiLevel: string;
  scienceLevel: string;
  assessments?: StrategicTeam["capabilityAssessments"];
  editing?: boolean;
  disabled?: boolean;
  onChange?: (field: "aiLevel" | "scienceLevel", level: JudgementLevel) => void;
}) {
  return <div className="strategic-map-capabilities min-w-0 flex-1">
    <div className="grid grid-cols-2 gap-3">
      {([ ["aiLevel", "ai", "AI 能力", aiLevel], ["scienceLevel", "science", "科学能力", scienceLevel] ] as const).map(([field, dimension, label, value]) => {
        const assessment = assessments?.[dimension];
        return <div key={field} className="min-w-0">
          <label className="flex min-w-0 items-center justify-between gap-2 text-[12px] font-medium text-[#546b7d]">
            <span className="shrink-0">{label}</span>
            {editing ? <select aria-label={label} value={value} disabled={disabled}
              onChange={(event) => onChange?.(field, event.target.value as JudgementLevel)}
              className="min-w-0 flex-1 rounded-md border border-[#ccd9e4] bg-white px-1 py-1 text-[12px] text-[#304b61] outline-none focus:border-[#4c91bd] disabled:opacity-60">
              {LEVELS.map((level) => <option key={level} value={level}>{capabilityLevelLabel(level)}</option>)}
            </select> : <span className="text-[13px] font-semibold text-[#2e668e]" data-capability={dimension}>{capabilityLevelLabel(value)}</span>}
          </label>
          {assessment?.source !== "none" && assessment?.source && <span className="mt-0.5 block text-right text-[10px] text-slate-400" title={assessment?.reason}>
            {editing && value !== assessment?.level ? "保存后标记人工修改" : capabilitySourceLabel(assessment?.source)}
          </span>}
        </div>;
      })}
    </div>
    {Object.values(assessments ?? {}).some((value) => value.source === "ai") && <details className="mt-2 text-[12px] text-slate-500">
      <summary className="cursor-pointer">AI 初判依据</summary>
      {([ ["ai", "AI"], ["science", "科学"] ] as const).map(([key, label]) => {
        const value = assessments?.[key];
        return value?.source === "ai" ? <div key={key} className="mt-2 leading-5">
          <p>{label}：{value.reason}</p>
          {value.citations.map((cite, index) => <a key={`${cite.url}-${index}`} href={cite.url} target="_blank" rel="noreferrer" className="mr-2 text-blue-700 hover:underline">原文 {index + 1}</a>)}
        </div> : null;
      })}
    </details>}
  </div>;
}
