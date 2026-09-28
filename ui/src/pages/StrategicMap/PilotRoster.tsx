import { useEffect, useMemo, useState } from "react";
import { ArrowRight, CheckCircle2, ExternalLink, ListFilter, LoaderCircle, Search } from "lucide-react";
import { normalizeToolBaseUrlForBrowser } from "@/utils/fileUrl";

type RosterItem = {
  teamId: string; teamName: string; institutionName: string; subdomainId: string | null;
  subdomainName: string; identitySourceUrl: string | null; claimCount: number;
  outcomeCount: number; humanReviewedClaimCount: number;
};
type Roster = {
  dataVersion: string; target: number; totalUnits: number; outcomeBackedUnits: number;
  unclassifiedUnits: number; shortfall: number; items: RosterItem[];
};
type Filter = "all" | "outcome" | "missing" | "unclassified";

export default function PilotRoster({ domainId, subdomainId, domainName, subdomainName, onOpenTeam }: {
  domainId: string; subdomainId: string; domainName: string; subdomainName: string; onOpenTeam: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<Roster | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setData(null); setError("");
    const params = new URLSearchParams({ domain_id: domainId, subdomain_id: subdomainId });
    void fetch(`${normalizeToolBaseUrlForBrowser("")}/v1/strategic-map/intelligence/pilot-roster?${params}`, { signal: controller.signal })
      .then(async response => { if (!response.ok) throw Error("试点清单暂时不可用"); return response.json() as Promise<Roster>; })
      .then(value => { if (!controller.signal.aborted) setData(value); })
      .catch(reason => { if (!controller.signal.aborted) setError(reason.message || "试点清单读取失败"); });
    return () => controller.abort();
  }, [open, domainId, subdomainId, attempt]);
  const items = useMemo(() => data?.items.filter(item => {
    const matches = `${item.teamName} ${item.institutionName} ${item.subdomainName}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
    return matches && (filter === "all" || filter === "outcome" && item.outcomeCount > 0
      || filter === "missing" && item.outcomeCount === 0 || filter === "unclassified" && !item.subdomainId);
  }) || [], [data, query, filter]);
  const scopeName = subdomainName || domainName || "全部领域";
  return <section aria-label="试点团队与证据缺口" className="min-w-0 overflow-hidden rounded-[22px] border border-[#d7e5dc] bg-gradient-to-br from-[#f4faf6] via-white to-[#f6fbff] shadow-[0_10px_28px_-25px_rgba(20,79,62,0.55)]">
    <div className="flex flex-wrap items-center justify-between gap-3 p-4 sm:p-5">
      <div className="flex min-w-0 items-start gap-3">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-[#dceee3] text-[#207254]"><ListFilter className="size-5" aria-hidden="true" /></span>
        <div className="min-w-0">
          <p className="text-[11px] font-semibold tracking-[0.12em] text-[#327d61]">PILOT ROSTER</p>
          <h3 className="mt-0.5 text-base font-semibold text-[#173a2d]">试点团队与证据缺口</h3>
          <p className="mt-1 text-xs leading-5 text-[#60786b]">当前范围：{scopeName} · 按公开资料核对团队身份与成果，目标 20 支</p>
        </div>
      </div>
      <button type="button" aria-expanded={open} onClick={() => setOpen(value => !value)}
        className="inline-flex min-h-11 shrink-0 items-center justify-center gap-2 rounded-xl bg-[#18765a] px-4 text-sm font-semibold text-white shadow-[0_6px_15px_-9px_rgba(24,118,90,0.9)] hover:bg-[#115e47] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#18765a]">
        {open ? "收起清单" : "查看试点清单"}<ArrowRight className={`size-4 transition-transform ${open ? "rotate-90" : ""}`} aria-hidden="true" />
      </button>
    </div>
    {open && <div className="border-t border-[#dcece2] bg-white/85 p-4 sm:p-5">
      {!subdomainId && <p className="mb-3 rounded-xl border border-[#eedfba] bg-[#fffaf0] p-3 text-xs leading-5 text-[#82612f]">当前为大类范围。20 支试点验收需要先选择一个细分领域；未归类单位不能自动计入任一细分领域。</p>}
      {error && <p role="alert" className="text-sm text-[#a14b3f]">{error}。<button type="button" onClick={() => setAttempt(value => value + 1)} className="min-h-10 font-semibold underline">重试</button></p>}
      {!data && !error && <p role="status" className="flex items-center gap-2 text-sm text-[#638373]"><LoaderCircle className="size-4 animate-spin" aria-hidden="true" />正在核对已发布团队…</p>}
      {data && <>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">{[
          ["已收录单元", data.totalUnits], ["有成果依据", data.outcomeBackedUnits],
          ["成果待补", data.totalUnits - data.outcomeBackedUnits], ["距 20 支", data.shortfall],
        ].map(([label, count]) => <div key={label} className="rounded-xl border border-[#dce9e0] bg-[#f7fbf8] p-3"><p className="text-xs text-[#5d7869]">{label}</p><p className="mt-1 text-xl font-bold text-[#19543f]">{count}</p></div>)}</div>
        <p className="mt-3 text-xs leading-5 text-[#65786c]">{subdomainId ? `当前细分领域已收录 ${data.totalUnits} 支。` : `当前大类有 ${data.unclassifiedUnits} 支尚未归入细分领域。`}身份和成果统计来自当前已发布资料；仅有来源校验不代表专家签署。</p>
        <div className="mt-4 flex flex-col gap-2 sm:flex-row">
          <label className="relative min-w-0 flex-1"><span className="sr-only">在试点清单中查找团队</span><Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[#78998a]" aria-hidden="true" />
            <input value={query} onChange={event => setQuery(event.target.value)} placeholder="查找团队、机构或细分领域" aria-label="在试点清单中查找团队" className="min-h-11 w-full rounded-xl border border-[#c9ded1] bg-white py-2 pl-9 pr-3 text-sm text-[#244839] outline-none focus-visible:border-[#18765a] focus-visible:ring-2 focus-visible:ring-[#18765a]/20" /></label>
          <select value={filter} onChange={event => setFilter(event.target.value as Filter)} aria-label="试点清单状态" className="min-h-11 rounded-xl border border-[#c9ded1] bg-white px-3 text-sm text-[#244839] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#18765a]">
            <option value="all">全部团队</option><option value="outcome">有成果依据</option><option value="missing">成果待补</option><option value="unclassified">细分领域待确认</option>
          </select>
        </div>
        <p role="status" className="mt-3 text-xs text-[#698071]">当前筛选显示 {items.length} 支 · 资料版本 {data.dataVersion}</p>
        <div className="mt-2 max-h-[520px] space-y-2 overflow-y-auto pr-1">{items.map(item => <article key={item.teamId} className="rounded-xl border border-[#e1ebe4] bg-white p-3.5">
          <div className="flex flex-wrap items-start justify-between gap-2"><div className="min-w-0 flex-1"><button type="button" onClick={() => onOpenTeam(item.teamId)} className="inline-flex min-h-9 max-w-full items-center gap-1 text-left text-sm font-semibold text-[#174f3b] hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#18765a]"><span className="break-words">{item.teamName}</span><ArrowRight className="size-3.5 shrink-0" aria-hidden="true" /></button><p className="text-xs leading-5 text-[#698071]">{item.institutionName} · {item.subdomainName || "细分领域待确认"}</p></div>
            <span className={item.outcomeCount ? "rounded-lg bg-[#e8f6ed] px-2 py-1 text-xs font-semibold text-[#28714c]" : "rounded-lg bg-[#fff4df] px-2 py-1 text-xs font-semibold text-[#8c621f]"}>{item.outcomeCount ? `${item.outcomeCount} 条成果依据` : "成果待补"}</span></div>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-[#6d8176]"><span>公开引文 {item.claimCount} 条</span><span>人工审核 {item.humanReviewedClaimCount} 条</span>{item.identitySourceUrl && <a href={item.identitySourceUrl} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-8 items-center gap-1 font-semibold text-[#176e50] hover:underline">身份来源 <ExternalLink className="size-3" aria-hidden="true" /></a>}</div>
        </article>)}{!items.length && <p className="rounded-xl border border-dashed border-[#c9ded1] p-5 text-center text-sm text-[#688071]">当前筛选没有团队；可调整关键词或状态。</p>}</div>
        <p className="mt-3 flex items-start gap-1.5 text-xs leading-5 text-[#6b8173]"><CheckCircle2 className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />清单仅说明已收录资料的覆盖程度；团队归属、成果适用性与 20 支试点结论仍须人工逐支复核。</p>
      </>}
    </div>}
  </section>;
}
