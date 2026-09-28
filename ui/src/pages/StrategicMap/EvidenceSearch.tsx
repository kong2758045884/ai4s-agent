import { useEffect, useRef, useState } from "react";
import { ArrowRight, ExternalLink, FileText, LoaderCircle, Search } from "lucide-react";
import { recommendationApi, type IntelligenceSearchOptions, type IntelligenceSearchResult } from "@/services/strategicRecommendations";

type Props = { domainId: string; subdomainId: string; domainName: string; subdomainName: string; onOpenTeam: (id: string) => void;
  initialQuery?: string; initialEntityType?: "all" | "team_profile" | "team_claim" };
type EntityType = NonNullable<IntelligenceSearchOptions["entityType"]>;
type SourceStatus = NonNullable<IntelligenceSearchOptions["sourceStatus"]>;
const types: { value: EntityType; label: string }[] = [
  { value: "all", label: "全部资料" }, { value: "team_profile", label: "团队档案" }, { value: "team_claim", label: "成果依据" },
];

export default function EvidenceSearch({ domainId, subdomainId, domainName, subdomainName, onOpenTeam, initialQuery = "", initialEntityType = "all" }: Props) {
  const [query, setQuery] = useState(initialQuery.slice(0, 100));
  const [scope, setScope] = useState("current");
  const [entityType, setEntityType] = useState<EntityType>(initialEntityType);
  const [sourceStatus, setSourceStatus] = useState<SourceStatus>("all");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [items, setItems] = useState<IntelligenceSearchResult[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);
  const sequence = useRef(0);
  const snapshot = useRef("");

  const invalidate = () => {
    sequence.current++;
    active.current?.abort();
    snapshot.current = "";
    setItems([]); setTotal(null); setPage(1); setBusy(false); setError("");
  };
  useEffect(() => { invalidate(); return () => { sequence.current++; active.current?.abort(); }; }, [domainId, subdomainId]);

  const load = async (requestedPage = 1) => {
    if (query.trim().length < 2) return;
    if (dateFrom && dateTo && dateFrom > dateTo) { setError("开始日期不能晚于结束日期。"); return; }
    active.current?.abort();
    const controller = new AbortController(); active.current = controller;
    const id = ++sequence.current;
    setBusy(true); setError("");
    try {
      const data = await recommendationApi.search(query.trim(), scope === "current" ? domainId : "", requestedPage, {
        subdomainId: scope === "current" ? subdomainId : "", entityType, sourceStatus, dateFrom, dateTo,
        snapshotId: requestedPage > 1 ? snapshot.current : "", signal: controller.signal,
      });
      if (controller.signal.aborted || sequence.current !== id) return;
      if (requestedPage > 1 && snapshot.current && snapshot.current !== data.snapshotId) {
        invalidate(); setError("资料已更新，请重新检索以查看完整结果。"); return;
      }
      snapshot.current = data.snapshotId;
      setItems(data.items); setTotal(data.total); setPage(requestedPage);
    } catch (reason) {
      if (!controller.signal.aborted && sequence.current === id) {
        const message = reason instanceof Error ? reason.message : String(reason);
        if (message.includes("资料已更新")) { invalidate(); setError(message); }
        else setError(message);
      }
    } finally { if (sequence.current === id) setBusy(false); }
  };

  return <section aria-label="团队资料与原文检索" className="min-w-0 overflow-hidden rounded-[22px] border border-[#d4e4ef] bg-white shadow-[0_12px_32px_-26px_rgba(20,61,99,0.65)]">
    <div className="bg-gradient-to-br from-[#eaf6ff] via-[#f6fbff] to-white p-4 sm:p-5">
      <div className="flex items-start gap-3">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-[#d4ecfc] text-[#1768a2]"><Search className="size-5" aria-hidden="true" /></span>
        <div className="min-w-0">
          <p className="text-[11px] font-semibold tracking-[0.08em] text-[#2c6f9e]">团队资料</p>
          <h3 className="mt-0.5 text-lg font-semibold text-[#102d47]">检索团队资料与成果原文</h3>
          <p className="mt-1 text-xs leading-5 text-[#59758c]">查团队、机构、研究方向和已入库引文。当前范围：{scope === "all" ? "全部领域" : subdomainName || domainName || "当前领域"}。</p>
        </div>
      </div>
      <form onSubmit={event => { event.preventDefault(); void load(); }} className="mt-4 space-y-3">
        <div className="flex flex-col gap-2 sm:flex-row">
          <label className="relative min-w-0 flex-1">
            <span className="sr-only">证据检索</span>
            <Search className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-[#7392a9]" aria-hidden="true" />
            <input aria-label="证据检索" maxLength={100} value={query} onChange={event => { invalidate(); setQuery(event.target.value); }}
              placeholder="输入团队、机构、方向或成果关键词" className="min-h-12 w-full rounded-xl border border-[#bed3e4] bg-white py-2.5 pl-10 pr-3 text-sm text-[#18344a] shadow-sm outline-none placeholder:text-[#8ba0af] focus-visible:border-[#2b78b5] focus-visible:ring-2 focus-visible:ring-[#2b78b5]/20" />
          </label>
          <button type="submit" disabled={busy || query.trim().length < 2}
            className="inline-flex min-h-12 items-center justify-center gap-2 rounded-xl bg-[#1768a2] px-6 text-sm font-semibold text-white shadow-[0_6px_14px_-8px_rgba(23,104,162,0.9)] transition hover:bg-[#125886] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#1768a2] disabled:cursor-not-allowed disabled:bg-[#dce7ee] disabled:text-[#4a6274] disabled:shadow-none">
            {busy && <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />}{busy ? "检索中" : "检索"}
          </button>
        </div>
        <div role="group" aria-label="检索资料类型" className="flex max-w-full flex-wrap gap-1 rounded-xl border border-[#dbe7f0] bg-white/80 p-1">
          {types.map(type => <button key={type.value} type="button" aria-pressed={entityType === type.value}
            onClick={() => { invalidate(); setEntityType(type.value); }}
            className={entityType === type.value ? "min-h-9 rounded-lg bg-[#1768a2] px-3 text-xs font-semibold text-white shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#1768a2]" : "min-h-9 rounded-lg px-3 text-xs font-semibold text-[#526e81] hover:bg-[#e9f3fa] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#1768a2]"}>
            {type.label}
          </button>)}
        </div>
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          <label className="text-xs font-medium text-[#49667b]">范围
            <select aria-label="证据检索范围" value={scope} onChange={event => { invalidate(); setScope(event.target.value); }}
              className="mt-1 min-h-10 w-full rounded-lg border border-[#ccdeeb] bg-white px-3 text-sm text-[#18344a] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#1768a2]"><option value="current">当前领域</option><option value="all">全部领域</option></select>
          </label>
          <label className="text-xs font-medium text-[#49667b]">来源状态
            <select aria-label="来源状态" value={sourceStatus} onChange={event => { invalidate(); setSourceStatus(event.target.value as SourceStatus); }}
              className="mt-1 min-h-10 w-full rounded-lg border border-[#ccdeeb] bg-white px-3 text-sm text-[#18344a] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#1768a2]"><option value="all">全部已入库来源</option><option value="official">机构官网来源</option><option value="human_reviewed">人工签署依据</option></select>
          </label>
          <label className="text-xs font-medium text-[#49667b]">起始发表日期
            <input aria-label="起始发表日期" type="date" value={dateFrom} onChange={event => { invalidate(); setDateFrom(event.target.value); }}
              className="mt-1 min-h-10 w-full rounded-lg border border-[#ccdeeb] bg-white px-3 text-sm text-[#18344a] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#1768a2]" />
          </label>
          <label className="text-xs font-medium text-[#49667b]">结束发表日期
            <input aria-label="结束发表日期" type="date" value={dateTo} onChange={event => { invalidate(); setDateTo(event.target.value); }}
              className="mt-1 min-h-10 w-full rounded-lg border border-[#ccdeeb] bg-white px-3 text-sm text-[#18344a] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#1768a2]" />
          </label>
        </div>
        <p className="text-[11px] leading-5 text-[#6a8192]">日期仅筛选有发表时间的成果依据；来源校验不等于专家审核。</p>
      </form>
    </div>
    <div className="p-4 sm:p-5">
      {error && <div role="alert" className="rounded-xl border border-[#f0c6c0] bg-[#fff8f6] p-3 text-sm text-[#9a4538]">{error}<button type="button" onClick={() => void load(page)} className="ml-2 font-semibold underline">重新检索</button></div>}
      {total === null && !error && <div className="flex items-center gap-3 py-2 text-sm text-[#6b8395]"><FileText className="size-5 text-[#89a9c1]" aria-hidden="true" />输入至少两个字，按下“检索”查看有来源的资料。</div>}
      {total !== null && <p role="status" className="mb-3 text-xs font-medium text-[#58758b]">找到 {total} 条资料{total > 0 ? " · 第 " + ((page - 1) * 20 + 1) + "–" + Math.min(page * 20, total) + " 条" : "；试试其他关键词、资料类型或全部领域"}</p>}
      <div className="space-y-2" aria-busy={busy}>{items.map(item => <article key={item.type + ":" + item.id} className="rounded-xl border border-[#e0eaf1] bg-white p-3.5 transition hover:border-[#a8cde7] hover:shadow-[0_5px_18px_-13px_rgba(23,104,162,0.65)]">
        <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
          <span className={item.type === "team_profile" ? "rounded-md bg-[#ecf5fc] px-2 py-0.5 font-semibold text-[#236991]" : "rounded-md bg-[#eef7f2] px-2 py-0.5 font-semibold text-[#26734d]"}>{item.type === "team_profile" ? "团队档案" : "成果依据"}</span>
          {item.sourceStatus && <span className="rounded-md bg-[#f1f5fa] px-2 py-0.5 text-[#496d85]">{item.sourceStatus === "official" ? "机构官网来源" : "来源已校验"}</span>}
          <span className="rounded-md bg-[#f2f5f7] px-2 py-0.5 text-[#526b7b]">{item.humanReviewStatus === "reviewed" ? "已有人审" : "未有人审"}</span>
          {item.matchReason && <span className="text-[#678195]">命中：{item.matchReason}</span>}
          {item.matchedAlias && <span className="max-w-full break-words rounded-md border border-[#cfe3f1] bg-[#f1f8fd] px-2 py-0.5 font-medium text-[#276b96]">关联名称：{item.matchedAlias}</span>}
          {item.date && <span className="text-[#7890a0]">{item.date}</span>}
        </div>
        <button type="button" onClick={() => item.teamId && onOpenTeam(item.teamId)} className="mt-2 inline-flex min-h-8 max-w-full items-center gap-1 text-left text-sm font-semibold text-[#15364e] hover:text-[#1768a2] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#1768a2]">
          <span className="truncate">{item.title}</span><ArrowRight className="size-3.5 shrink-0" aria-hidden="true" />
        </button>
        <p className="mt-1 line-clamp-3 text-xs leading-6 text-[#5a7182]">{item.snippet || item.reviewNotice}</p>
        {item.type === "team_profile" && <p className="mt-1 text-[11px] text-[#7890a0]">身份有来源，成果仍须逐条查看</p>}
        {item.url && <a href={item.url} target="_blank" rel="noopener noreferrer" className="mt-2 inline-flex min-h-8 items-center gap-1 text-xs font-semibold text-[#1768a2] hover:underline">打开原文 <ExternalLink className="size-3.5" aria-hidden="true" /></a>}
      </article>)}</div>
      {total !== null && total > 20 && <nav aria-label="证据分页" className="mt-4 flex items-center justify-between gap-3 border-t border-[#edf2f6] pt-4 text-sm">
        <button type="button" disabled={busy || page === 1} onClick={() => void load(page - 1)} className="min-h-10 rounded-lg border border-[#cbdeeb] px-3 font-medium text-[#1768a2] hover:bg-[#eef7fc] disabled:cursor-not-allowed disabled:text-[#94a8b7]">上一页</button>
        <span className="text-xs text-[#647f92]">{page} / {Math.ceil(total / 20)}</span>
        <button type="button" disabled={busy || page * 20 >= total} onClick={() => void load(page + 1)} className="min-h-10 rounded-lg border border-[#cbdeeb] px-3 font-medium text-[#1768a2] hover:bg-[#eef7fc] disabled:cursor-not-allowed disabled:text-[#94a8b7]">下一页</button>
      </nav>}
    </div>
  </section>;
}
