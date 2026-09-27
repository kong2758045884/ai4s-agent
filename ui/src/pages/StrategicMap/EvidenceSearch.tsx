import { useEffect, useRef, useState } from "react";
import { ExternalLink } from "lucide-react";
import { recommendationApi, type IntelligenceSearchResult } from "@/services/strategicRecommendations";

type Props = { domainId: string; subdomainId: string; domainName: string; subdomainName: string; onOpenTeam: (id: string) => void };
export default function EvidenceSearch({ domainId, subdomainId, domainName, subdomainName, onOpenTeam }: Props) {
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState("current");
  const [items, setItems] = useState<IntelligenceSearchResult[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);
  const sequence = useRef(0);
  const version = useRef("");
  const invalidate = () => {
    sequence.current++; active.current?.abort(); version.current = "";
    setItems([]); setTotal(null); setPage(1); setBusy(false); setError("");
  };
  useEffect(() => { invalidate(); return () => { sequence.current++; active.current?.abort(); }; }, [domainId, subdomainId]);
  const load = async (requestedPage = 1) => {
    if (query.trim().length < 2) return;
    active.current?.abort();
    const controller = new AbortController(); active.current = controller;
    const id = ++sequence.current;
    setBusy(true); setError("");
    try {
      const data = await recommendationApi.search(query.trim(), scope === "current" ? domainId : "", requestedPage,
        { subdomainId: scope === "current" ? subdomainId : "", signal: controller.signal });
      if (controller.signal.aborted || sequence.current !== id) return;
      if (requestedPage > 1 && version.current && version.current !== data.dataVersion) {
        invalidate(); setError("资料已更新，请重新检索以查看完整结果。"); return;
      }
      version.current = data.dataVersion; setItems(data.items); setTotal(data.total); setPage(requestedPage);
    } catch (reason) {
      if (!controller.signal.aborted && sequence.current === id) setError(reason instanceof Error ? reason.message : String(reason));
    } finally { if (sequence.current === id) setBusy(false); }
  };
  return <section aria-label="团队资料与原文检索" className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
    <h3 className="font-semibold text-slate-900">检索团队资料与成果原文</h3>
    <p className="mt-1 text-xs leading-5 text-slate-500">查询已入库、通过来源校验的团队资料。当前范围：{scope === "all" ? "全部领域" : subdomainName || domainName || "全部领域"}。</p>
    <form onSubmit={e => { e.preventDefault(); void load(); }} className="mt-3 flex flex-wrap gap-2">
      <select aria-label="证据检索范围" value={scope} onChange={e => { invalidate(); setScope(e.target.value); }} className="rounded-lg border border-slate-200 px-2 py-2 text-sm"><option value="current">当前领域</option><option value="all">全部领域</option></select>
      <input aria-label="证据检索" maxLength={100} value={query} onChange={e => { invalidate(); setQuery(e.target.value); }} placeholder="团队、机构、成果或论文关键词（至少两个字）" className="min-w-0 flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm" />
      <button disabled={busy || query.trim().length < 2} className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">{busy ? "检索中" : "检索"}</button>
    </form>
    {error && <div role="alert" className="mt-3 text-sm text-red-700">{error}<button type="button" onClick={() => void load(page)} className="ml-3 underline">重新检索</button></div>}
    {total !== null && <p role="status" className="mt-3 text-xs text-slate-500">找到 {total} 条证据{total > 0 ? ` · 第 ${(page - 1) * 20 + 1}–${Math.min(page * 20, total)} 条` : "，可调整关键词或切换全部领域"}</p>}
    <div className="mt-3 space-y-2" aria-busy={busy}>{items.map(item => <article key={item.id} className="rounded-lg border border-slate-100 p-3">
      <p className="text-[11px] text-slate-500">{item.verificationMethod || "来源与引文校验"}</p>
      <button type="button" onClick={() => item.teamId && onOpenTeam(item.teamId)} className="mt-1 text-left text-sm font-semibold text-slate-900 hover:text-blue-700">{item.title}</button>
      <p className="mt-1 text-xs leading-6 text-slate-600">{item.snippet}</p>
      {item.url && <a href={item.url} target="_blank" rel="noopener noreferrer" className="mt-2 inline-flex items-center gap-1 text-xs text-blue-700">打开原文 <ExternalLink className="size-3" /></a>}
    </article>)}</div>
    {total !== null && total > 20 && <nav aria-label="证据分页" className="mt-4 flex items-center justify-between gap-3 text-sm">
      <button disabled={busy || page === 1} onClick={() => void load(page - 1)} className="rounded-lg border px-3 py-2 disabled:opacity-40">上一页</button>
      <span className="text-xs text-slate-500">{page} / {Math.ceil(total / 20)}</span>
      <button disabled={busy || page * 20 >= total} onClick={() => void load(page + 1)} className="rounded-lg border px-3 py-2 disabled:opacity-40">下一页</button>
    </nav>}
  </section>;
}
