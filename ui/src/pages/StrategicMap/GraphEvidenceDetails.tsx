import type { StrategicGraphElement } from "@/services/strategicMap";
import type { RecommendationCitation } from "@/services/strategicRecommendations";
import ClaimSourceDetails from "./ClaimSourceDetails";

const STATUS: Record<string, string> = { input: "已确认的需求", inference: "系统推荐关系", source: "有来源记录",
  supported: "引文匹配 · 尚非交付承诺", conditional: "有条件支持", insufficient: "依据不足", unconfirmed: "归属引文未保存",
  not_met: "未满足", not_observed: "未观察到排除项 · 尚非全面排查" };

export default function GraphEvidenceDetails({ element, onOpenTeam }: { element: StrategicGraphElement; onOpenTeam?: (id: string) => void }) {
  const raw = element.data?.raw || {};
  const citations = Array.isArray(raw.citations) ? raw.citations as RecommendationCitation[] : [];
  return <div className="mt-3 space-y-3 break-words text-xs leading-5" data-testid="graph-evidence-details">
    {typeof raw.status === "string" && <p className="font-semibold text-slate-800">{STATUS[raw.status] || "需查看依据"}</p>}
    {typeof raw.basis === "string" && <p className="text-slate-600">{raw.basis}</p>}
    {citations.length > 0 ? <div className="space-y-3"><h3 className="font-semibold text-slate-800">本研判保存的依据</h3>
      {citations.map((cite, index) => <details key={cite.id || index} className="rounded-lg border border-slate-200 bg-white p-3" open={citations.length === 1}>
        <summary className="cursor-pointer text-blue-700">{cite.text || "来源引文"}</summary>
        <blockquote className="my-2 border-l-2 border-slate-300 pl-2 text-slate-700">{cite.quote}</blockquote>
        {/^https?:\/\//i.test(cite.url) && <a className="inline-flex min-h-11 items-center text-blue-700 underline" href={cite.url} target="_blank" rel="noreferrer">查看原文依据 ↗</a>}
        {cite.id && <p className="break-all text-slate-500">引文编号：{cite.id}</p>}
        <ClaimSourceDetails citation={cite} />
      </details>)}
    </div> : element.source && <p className="text-slate-500">此关系未附外部引文，请按上方依据与状态理解。</p>}
    {typeof raw.teamId === "string" && onOpenTeam && <button type="button" className="min-h-11 rounded-lg border border-blue-200 px-3 text-blue-700" onClick={() => onOpenTeam(raw.teamId as string)}>打开团队当前档案</button>}
  </div>;
}
