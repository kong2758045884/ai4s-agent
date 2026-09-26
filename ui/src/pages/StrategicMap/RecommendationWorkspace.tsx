import { useEffect, useState, type FormEvent } from "react";
import { ArrowRight, BookOpenText, Check, Clock3, ExternalLink, LoaderCircle, Search, Sparkles, UsersRound } from "lucide-react";
import {
  recommendationApi,
  type IntelligenceSearchResult,
  type RecommendationRun,
} from "@/services/strategicRecommendations";

type Props = {
  domainId: string;
  subdomainId: string;
  domainName: string;
  subdomainName: string;
  draft: string;
  onDraftChange: (value: string) => void;
  run: RecommendationRun | null;
  onRunChange: (value: RecommendationRun | null) => void;
  onOpenTeam: (id: string) => void;
};

const examples = ["蛋白质结构预测", "量子计算与模拟", "具身智能", "催化与能源材料"];

export default function RecommendationWorkspace({
  domainId, subdomainId, domainName, subdomainName, draft, onDraftChange,
  run, onRunChange, onOpenTeam,
}: Props) {
  const [limit, setLimit] = useState(10);
  const [busy, setBusy] = useState(false);
  const [expanding, setExpanding] = useState(false);
  const [error, setError] = useState("");
  const [compared, setCompared] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchResults, setSearchResults] = useState<IntelligenceSearchResult[]>([]);
  const [searchTotal, setSearchTotal] = useState<number | null>(null);
  const activeRun = (run?.domainId || "") === domainId && (run?.subdomainId || "") === subdomainId ? run : null;

  useEffect(() => {
    setCompared([]);
    setSearchResults([]);
    setSearchTotal(null);
  }, [domainId, subdomainId]);

  useEffect(() => {
    const job = activeRun?.expansion;
    if (!activeRun || !job || !["accepted", "running"].includes(job.state || job.status || "")) return;
    const timer = window.setInterval(() => {
      void recommendationApi.get(activeRun.runId).then(onRunChange).catch((reason) => setError(String(reason)));
    }, 3000);
    return () => window.clearInterval(timer);
  }, [activeRun, onRunChange]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (draft.trim().length < 2 || busy) return;
    setBusy(true);
    setError("");
    try {
      const next = await recommendationApi.create(draft.trim(), domainId, subdomainId, limit);
      onRunChange(next);
      setCompared([]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const expand = async () => {
    if (!activeRun || expanding) return;
    setExpanding(true);
    setError("");
    try { onRunChange(await recommendationApi.expand(activeRun.runId)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setExpanding(false); }
  };

  const doSearch = async (event: FormEvent) => {
    event.preventDefault();
    if (search.trim().length < 2) return;
    setSearching(true);
    setError("");
    try {
      const found = await recommendationApi.search(search.trim(), domainId);
      setSearchResults(found.items);
      setSearchTotal(found.total);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setSearching(false); }
  };

  const toggleCompare = (id: string) => setCompared((current) => current.includes(id)
    ? current.filter((item) => item !== id)
    : current.length < 3 ? [...current, id] : current);
  const comparedItems = activeRun?.items.filter((item) => compared.includes(item.teamId)) || [];

  return <main className="min-h-[420px] min-w-0 flex-1 space-y-4 overflow-y-auto pb-5 lg:min-h-0" aria-label="任务推荐">
    <section className="relative overflow-hidden rounded-2xl border border-slate-200 bg-white px-5 py-6 text-slate-900 shadow-sm sm:px-7 sm:py-7">
      <div className="relative flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-[11px] font-semibold tracking-[.24em] text-slate-400">AI4S · RESEARCH TEAMS</p>
          <h2 className="mt-2 text-2xl font-semibold tracking-tight sm:text-[30px]">输入任务，找到能承担它的国内团队</h2>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">基于已通过审核的团队成果，匹配任务所需能力，推荐依据可直接查看原文。</p>
        </div>
        <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-xs leading-5 text-slate-500">
          当前范围 <strong className="block text-sm font-medium text-slate-700">{subdomainName || domainName}</strong>
        </div>
      </div>
      <form onSubmit={(event) => void submit(event)} className="relative mt-6 rounded-xl border border-slate-200 bg-white p-2 focus-within:border-blue-300 focus-within:ring-2 focus-within:ring-blue-50">
        <label htmlFor="strategic-task-input" className="sr-only">输入领域或任务</label>
        <textarea id="strategic-task-input" rows={2} maxLength={500} value={draft}
          onChange={(event) => onDraftChange(event.target.value)}
          placeholder="例如：开发用于蛋白质结构预测的模型，需要哪些国内团队？"
          className="w-full resize-y rounded-lg px-3 py-2 text-[15px] leading-6 text-[#173a4f] outline-none placeholder:text-[#91a4ae]" />
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[#e6edf0] px-2 pt-2">
          <div className="flex items-center gap-2 text-xs text-[#64748b]">
            <span>推荐数量</span>
            <select aria-label="推荐数量" value={limit} onChange={(event) => setLimit(Number(event.target.value))}
              className="rounded-lg border border-[#e2e8f0] bg-[#f7fafb] px-2 py-1.5 text-[#194761]">
              {[5, 10, 20].map((value) => <option key={value} value={value}>{value} 支</option>)}
            </select>
          </div>
          <button type="submit" disabled={busy || draft.trim().length < 2}
            className="inline-flex items-center gap-2 rounded-lg bg-[#2563eb] px-5 py-2.5 text-sm font-semibold text-white hover:bg-[#1d4ed8] disabled:cursor-not-allowed disabled:opacity-50">
            {busy ? <LoaderCircle className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
            {busy ? "正在匹配" : "生成推荐"}
          </button>
        </div>
      </form>
      <div className="relative mt-3 flex flex-wrap gap-2 text-xs">{examples.map((example) =>
        <button key={example} type="button" onClick={() => onDraftChange(example)}
          className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-slate-500 hover:border-blue-200 hover:bg-blue-50 hover:text-blue-700">{example}</button>)}</div>
    </section>

    {error && <div role="alert" className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

    {activeRun ? <>
      <section className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[#e2e8f0] bg-white px-5 py-4 shadow-sm">
        <div className="flex flex-wrap items-center gap-5">
          <div><strong className="text-2xl text-[#0f172a]">{activeRun.items.length}</strong><span className="ml-2 text-sm text-[#64748b]">支可推荐</span></div>
          <div><strong className="text-2xl text-[#0f172a]">{activeRun.eligibleTeamCount}</strong><span className="ml-2 text-sm text-[#64748b]">支有团队级成果证据</span></div>
          {activeRun.shortfall > 0 && <span className="rounded-full bg-slate-50 px-3 py-1.5 text-xs text-slate-500">当前证据支持 {activeRun.items.length} 支，目标 {activeRun.requestedLimit} 支</span>}
        </div>
        <span className="inline-flex items-center gap-1.5 text-xs text-[#64748b]"><Clock3 className="size-3.5" />{new Date(activeRun.createdAt).toLocaleString("zh-CN")}</span>
      </section>

      <div className="flex flex-wrap items-center justify-between gap-3 px-1">
        <div><h3 className="text-lg font-semibold text-[#0f172a]">推荐依据</h3><p className="mt-1 text-xs text-[#64748b]">任务匹配与团队评分分别展示，最多选择三支团队比较。</p></div>
        <button type="button" onClick={() => void expand()} disabled={expanding || !!activeRun.expansion}
          className="inline-flex items-center gap-2 rounded-lg border border-[#bfdbfe] bg-[#eff6ff] px-4 py-2 text-sm font-semibold text-[#2563eb] hover:bg-[#dbeafe] disabled:opacity-60">
          {expanding ? <LoaderCircle className="size-4 animate-spin" /> : <Search className="size-4" />}
          {activeRun.expansion ? "扩展调查已启动" : "扩展调查 · 全网深搜"}
        </button>
      </div>

      {activeRun.expansion && <div className="rounded-xl border border-[#bddce2] bg-[#f8fafc] px-4 py-3 text-sm text-[#28596a]" role="status">
        扩展调查：{activeRun.expansion.stage || activeRun.expansion.state || activeRun.expansion.status || "排队中"}
        {activeRun.expansion.progress?.total ? ` · ${activeRun.expansion.progress.done}/${activeRun.expansion.progress.total}` : ""}
        {activeRun.expansion.error ? ` · ${activeRun.expansion.error}` : ""}
        <p className="mt-1 text-xs text-[#64818b]">调查结果完成身份与成果审核后更新推荐。</p>
      </div>}

      {activeRun.items.length ? <div className="grid gap-4 xl:grid-cols-2">{activeRun.items.map((item, index) => <article key={item.teamId}
        className="rounded-2xl border border-[#e2e8f0] bg-white p-5 shadow-[0_5px_18px_rgba(15,23,42,.035)]">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0"><span className="text-[11px] font-semibold tracking-[.14em] text-[#168095]">推荐 {String(index + 1).padStart(2, "0")}</span>
            <h4 className="mt-1 text-lg font-semibold text-[#0f172a]">{item.teamName}</h4>
            <p className="mt-0.5 text-sm text-[#64748b]">{item.institutionName}</p></div>
          <label className="flex shrink-0 items-center gap-1.5 text-xs text-[#64748b]">
            <input type="checkbox" checked={compared.includes(item.teamId)} disabled={!compared.includes(item.teamId) && compared.length >= 3}
              onChange={() => toggleCompare(item.teamId)} />比较</label>
        </div>
        <div className="mt-4 grid grid-cols-3 gap-2 rounded-xl bg-[#f8fafc] p-3 text-center">
          <div><strong className="block text-xl text-[#1e40af]">{item.taskMatchScore}</strong><span className="text-[11px] text-[#64748b]">任务匹配</span></div>
          <div><strong className="block text-xl text-[#1e40af]">{item.teamScore?.toFixed(1) ?? "—"}</strong><span className="text-[11px] text-[#64748b]">团队总分</span></div>
          <div><strong className="block text-xl text-[#1e40af]">{item.citations.length}</strong><span className="text-[11px] text-[#64748b]">原文证据</span></div>
        </div>
        <p className="mt-4 line-clamp-3 text-sm leading-6 text-[#475569]">{item.capability}</p>
        <div className="mt-4 space-y-2"><p className="text-xs font-semibold text-[#475569]">可追溯成果与能力</p>
          {item.citations.slice(0, 2).map((citation) => <a key={`${citation.url}${citation.quote}`} href={citation.url}
            target="_blank" rel="noopener noreferrer" className="block rounded-lg border border-[#e4edf0] bg-[#ffffff] px-3 py-2 text-xs leading-5 text-[#475569] hover:border-[#a8d0da]">
            <span className="line-clamp-2">{citation.quote}</span><span className="mt-1 inline-flex items-center gap-1 font-semibold text-[#2563eb]">查看原文 <ExternalLink className="size-3" /></span>
          </a>)}</div>
        <div className="mt-4 border-t border-[#edf1f3] pt-3 text-xs leading-5 text-[#64748b]">
          <p>适用范围：推荐依据为所列成果，具体交付条件需与团队沟通。</p>
          <p>下一步：查看成果原文，联系团队讨论任务方案。</p>
        </div>
        <button type="button" onClick={() => onOpenTeam(item.teamId)}
          className="mt-4 inline-flex items-center gap-1 text-sm font-semibold text-[#2563eb] hover:text-[#0b506a]">查看完整团队档案 <ArrowRight className="size-4" /></button>
      </article>)}</div> : <div className="rounded-xl border border-dashed border-[#bcd1d9] bg-white px-6 py-10 text-center text-sm text-[#5d7481]">
        当前范围没有同时具备已核实科研归属和任务相关成果原文的团队。可调整任务描述或显式启动扩展调查。
      </div>}

      {comparedItems.length >= 2 && <section className="overflow-x-auto rounded-2xl border border-[#e2e8f0] bg-white p-5">
        <h3 className="mb-3 text-base font-semibold text-[#0f172a]">团队横向比较</h3>
        <table className="w-full min-w-[600px] text-left text-sm"><thead><tr className="border-b text-[#637d89]"><th className="p-2">维度</th>{comparedItems.map((item) => <th className="p-2" key={item.teamId}>{item.teamName}</th>)}</tr></thead>
          <tbody>{[
            ["任务匹配", ...comparedItems.map((item) => String(item.taskMatchScore))],
            ["团队总分", ...comparedItems.map((item) => String(item.teamScore ?? "—"))],
            ["原文证据", ...comparedItems.map((item) => `${item.citations.length} 条`)],
            ["成果与能力", ...comparedItems.map((item) => item.capability)],
          ].map((row) => <tr key={row[0]} className="border-b border-[#eef2f4]">{row.map((value, index) => <td key={index} className="p-2 align-top text-[#475569]">{value}</td>)}</tr>)}</tbody></table>
      </section>}
    </> : <section className="grid gap-3 sm:grid-cols-3">{[
      [UsersRound, "身份核验", "机构、团队、人员分别核对，避免把机构新闻算给团队。"],
      [BookOpenText, "成果原文", "每条推荐依据可回到可访问的公开原文。"],
      [Check, "有据可查", "展示通过审核且有成果原文依据的团队。"],
    ].map(([Icon, title, description]) => { const CardIcon = Icon as typeof UsersRound; return <div key={title as string} className="rounded-xl border border-[#e2e8f0] bg-white p-4 shadow-sm"><CardIcon className="size-5 text-[#2563eb]" /><h3 className="mt-3 text-sm font-semibold text-[#0f172a]">{title as string}</h3><p className="mt-1 text-xs leading-5 text-[#64748b]">{description as string}</p></div>; })}</section>}

    <section className="rounded-2xl border border-[#e2e8f0] bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-base font-semibold text-[#0f172a]">检索团队成果与原文</h3><span className="text-xs text-[#64748b]">已审核证据</span></div>
      <form onSubmit={(event) => void doSearch(event)} className="mt-3 flex gap-2"><input aria-label="证据检索" value={search} onChange={(event) => setSearch(event.target.value)}
        placeholder="输入团队、成果、论文或事件关键词" className="min-w-0 flex-1 rounded-lg border border-[#cadce4] px-3 py-2 text-sm outline-none focus:border-[#1687a3]" />
      <button type="submit" disabled={search.trim().length < 2 || searching} className="rounded-lg bg-[#2563eb] px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">{searching ? "检索中" : "检索"}</button></form>
      {searchTotal !== null && <p className="mt-3 text-xs text-[#67808b]">找到 {searchTotal} 条档案与证据</p>}
      {searchResults.length > 0 && <div className="mt-3 space-y-2">{searchResults.map((result) => <div key={`${result.type}-${result.id}`} className="rounded-lg border border-[#e7eef1] p-3">
        <p className="text-xs text-[#64748b]">{{
          team_claim: "已核团队证据",
          team_profile: "团队档案",
          institution_event: "机构事件"
        }[result.type]}{result.date ? ` · ${result.date}` : ""}</p>
        <p className="mt-1 text-sm font-semibold text-[#0f172a]">{result.title}</p><p className="mt-1 line-clamp-2 text-xs leading-5 text-[#64748b]">{result.snippet}</p>
        {result.reviewNotice && <p className="mt-1 text-xs text-amber-700">{result.reviewNotice}</p>}
        {result.url && <a href={result.url} target="_blank" rel="noopener noreferrer" className="mt-1 inline-flex items-center gap-1 text-xs font-semibold text-[#2563eb]">打开原文 <ExternalLink className="size-3" /></a>}
      </div>)}</div>}
    </section>
  </main>;
}
