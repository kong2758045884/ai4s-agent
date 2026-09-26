import { useEffect, useMemo, useRef, useState } from "react";
import { Building2, CalendarDays, ExternalLink, FolderTree, X } from "lucide-react";
import { impactApi, type ImpactRanking, type ImpactDetail, type ImpactDirection, type ImpactEvent, type ImpactCandidate } from "@/services/impactTriage";
import { fusionApi, type FusionStatus, type FusionDaily } from "@/services/researchFusion";

const today = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
const panel = "rounded-2xl border border-slate-200 bg-white p-4 sm:p-5";
const input = "rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-blue-300";
const safeUrl = (url: string) => /^https?:\/\//i.test(url);
const levelColor = (tier: string) => tier === "A" ? "bg-blue-50 text-blue-700" : tier === "B" ? "bg-teal-50 text-teal-700" : "bg-slate-100 text-slate-600";

function EventCards({ events }: { events: ImpactEvent[] }) {
  return <div className="space-y-3">{events.map((event) => <article key={event.id} className="rounded-xl border border-slate-200 p-4">
    <div className="text-xs text-slate-400">{event.event_date || "日期未注明"}{event.is_flagship ? " · 代表成果" : ""}</div>
    <h4 className="mt-1 text-sm font-semibold text-slate-800">{event.title}</h4>
    <p className="mt-2 text-xs leading-6 text-slate-500">{event.summary}</p>
    <div className="mt-2 flex flex-wrap gap-3">{event.sources.filter((source) => safeUrl(source.url)).map((source, index) =>
      <a key={`${source.url}-${index}`} href={source.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-blue-700 hover:underline">{source.publisher || source.title || `原文 ${index + 1}`}<ExternalLink className="size-3" /></a>)}</div>
  </article>)}</div>;
}

function PeriodChart({ history }: { history: ImpactDetail["history"] }) {
  const streams = [...new Set(history.map((p) => `${p.entity_id}:${p.revision ? "revision" : "source"}`))];
  const [stream, setStream] = useState("");
  const activeStream = streams.includes(stream) ? stream : streams[0];
  const ordered = history.filter((p) => `${p.entity_id}:${p.revision ? "revision" : "source"}` === activeStream).sort((a, b) => a.scan_date.localeCompare(b.scan_date) || a.id.localeCompare(b.id));
  const series = [{ key: "eff_achievement", label: "成就", color: "#2563eb" }, { key: "eff_status", label: "地位", color: "#0d9488" }, { key: "eff_trend", label: "趋势", color: "#b45309" }] as const;
  return <section aria-label="跨期分数趋势" className="rounded-xl border border-slate-200 p-4">
    {streams.length > 1 && <label className="mb-3 block text-xs text-slate-500">独立评分记录 <select aria-label="评分记录" value={activeStream} onChange={(e) => setStream(e.target.value)} className={input}>{streams.map((key, i) => <option key={key} value={key}>{key.endsWith(":revision") ? "重评" : "来源"}记录 {i + 1}</option>)}</select></label>}
    <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="text-sm font-semibold">跨期分数趋势</h4><div className="flex gap-3 text-xs">{series.map((s) => <span key={s.key} style={{ color: s.color }}>{s.label}</span>)}</div></div>
    {ordered.length ? <><svg viewBox="0 0 500 190" role="img" aria-label="成就、地位、趋势三维历史分数" className="mt-3 w-full">
      {[0, 50, 100].map((value) => <g key={value}><line x1="32" x2="485" y1={160 - value * 1.35} y2={160 - value * 1.35} stroke="#e2e8f0" /><text x="2" y={164 - value * 1.35} fontSize="11" fill="#94a3b8">{value}</text></g>)}
      {series.map((s) => <g key={s.key}>{ordered.map((p, index) => {
        const score = p[s.key];
        const previous = index ? ordered[index - 1][s.key] : null;
        const x = 35 + index * 440 / Math.max(ordered.length - 1, 1);
        return score == null ? null : <g key={p.id}>{previous != null && <line x1={35 + (index - 1) * 440 / Math.max(ordered.length - 1, 1)} y1={160 - previous * 1.35} x2={x} y2={160 - score * 1.35} stroke={s.color} strokeWidth="2" />}<circle cx={x} cy={160 - score * 1.35} r="3" fill={s.color}><title>{p.scan_date} {s.label} {score}</title></circle></g>;
      })}</g>)}
      <text x="32" y="184" fontSize="11" fill="#64748b">{ordered[0].scan_date}</text><text x="485" y="184" textAnchor="end" fontSize="11" fill="#64748b">{ordered[ordered.length - 1]?.scan_date}</text>
    </svg><details className="mt-2 text-xs text-slate-500"><summary className="cursor-pointer">查看 {ordered.length} 个原始期次</summary><div className="mt-2 overflow-x-auto"><table className="w-full text-left"><thead><tr><th>日期</th><th>等级</th><th>成就</th><th>地位</th><th>趋势</th></tr></thead><tbody>{ordered.map((p) => <tr key={p.id}><td className="py-2">{p.scan_date}</td><td>{p.tier || "—"}</td><td>{p.eff_achievement ?? "—"}</td><td>{p.eff_status ?? "—"}</td><td>{p.eff_trend ?? "—"}</td></tr>)}</tbody></table></div></details></> : <p className="mt-3 text-sm text-slate-500">暂无历史期次。</p>}
  </section>;
}

export default function VerifiedIntelligence({ domainId, subdomainId = "" }: { domainId: string; subdomainId?: string }) {
  const [tab, setTab] = useState<"ranking" | "tree" | "daily">("ranking");
  const [status, setStatus] = useState<FusionStatus | null>(null);
  const [directions, setDirections] = useState<ImpactDirection[]>([]);
  const [candidates, setCandidates] = useState<ImpactCandidate[]>([]);
  const [audits, setAudits] = useState<Awaited<ReturnType<typeof impactApi.audits>>["items"]>([]);
  const eventRequest = useRef(0);
  const [institutions, setInstitutions] = useState<ImpactRanking[]>([]);
  const [query, setQuery] = useState("");
  const [tier, setTier] = useState("");
  const [onlyFollowed, setOnlyFollowed] = useState(false);
  const [detail, setDetail] = useState<ImpactDetail | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [treeEvents, setTreeEvents] = useState<ImpactEvent[]>([]);
  const [eventHeading, setEventHeading] = useState("");
  const [date, setDate] = useState(today);
  const [report, setReport] = useState<FusionDaily | null>(null);
  const [manage, setManage] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [reportLoading, setReportLoading] = useState(false);
  const [eventsLoading, setEventsLoading] = useState(false);

  useEffect(() => { const c = new AbortController(); void fusionApi.status(c.signal).then(setStatus).catch((e) => { if (!c.signal.aborted) setError(String(e)); }); return () => c.abort(); }, []);
  useEffect(() => {
    let disposed = false;
    eventRequest.current += 1; setEventsLoading(false);
    setLoading(true); setError(""); setInstitutions([]); setSelectedId(""); setDetail(null); setTreeEvents([]); setEventHeading("");
    void impactApi.directions().then(async (value) => {
      if (disposed) return;
      setDirections(value.items); setCandidates(value.candidates);
      const selected = value.items.find((item) => subdomainId ? item.ai4s_subdomain_id === subdomainId : item.level === 2 && item.ai4s_domain_id === domainId);
      if ((domainId || subdomainId) && !selected) return;
      const params = new URLSearchParams({ view: "official", country: "zn" });
      if (selected) params.set("direction_id", selected.id);
      const result = await impactApi.ranking(params);
      if (!disposed) setInstitutions(result.items.filter((item) => item.kind === "institution" && item.eligibility === "eligible" && item.mainland_confirmed === 1));
    }).catch((e) => { if (!disposed) setError(String(e)); }).finally(() => { if (!disposed) setLoading(false); });
    return () => { disposed = true; };
  }, [domainId, subdomainId, revision]);
  useEffect(() => {
    let disposed = false; setDetail(null);
    if (selectedId) void impactApi.entity(selectedId).then((value) => { if (!disposed) setDetail(value); }).catch((e) => { if (!disposed) setError(String(e)); });
    return () => { disposed = true; };
  }, [selectedId]);
  useEffect(() => {
    let disposed = false;
    if (manage) void impactApi.audits().then((result) => { if (!disposed) setAudits(result.items); })
      .catch((e) => { if (!disposed) setError(String(e)); });
    return () => { disposed = true; };
  }, [manage, revision]);
  useEffect(() => {
    const c = new AbortController(); setReport(null); setReportLoading(tab === "daily");
    if (tab === "daily") void fusionApi.daily(date, domainId, subdomainId, c.signal).then(setReport)
      .catch((e) => { if (!c.signal.aborted) setError(String(e)); }).finally(() => { if (!c.signal.aborted) setReportLoading(false); });
    return () => c.abort();
  }, [tab, date, domainId, subdomainId, revision]);
  const visible = useMemo(() => institutions.filter((item) => (!tier || item.tier === tier) && (!onlyFollowed || item.follow_status !== "unfollowed")
    && `${item.name} ${item.identity_aliases.map((a) => a.name).join(" ")} ${Object.values(item.reasons).join(" ")}`.toLowerCase().includes(query.toLowerCase())), [institutions, query, tier, onlyFollowed]);
  const mappedLeaves = directions.filter((d) => d.level === 3 && d.ai4s_subdomain_id === subdomainId);
  const l2 = directions.filter((d) => d.level === 2 && d.status === "formal" && (!domainId || d.ai4s_domain_id === domainId)
    && (!subdomainId || d.ai4s_subdomain_id === subdomainId || mappedLeaves.some((leaf) => leaf.parent_id === d.id)));
  const roots = directions.filter((d) => d.level === 1 && l2.some((child) => child.parent_id === d.id));
  const scopedCandidates = subdomainId ? [] : candidates.filter((c) => l2.some((d) => d.id === c.parent_id));
  const selectedRanking = institutions.find((item) => item.id === selectedId);
  const openEvents = async (item: ImpactDirection) => {
    if (subdomainId && item.ai4s_subdomain_id !== subdomainId) return;
    const request = ++eventRequest.current;
    setEventsLoading(true); setEventHeading(item.name); setTreeEvents([]);
    try { const events = await impactApi.directionEvents(item.id); if (request === eventRequest.current) setTreeEvents(events.items); }
    catch (e) { if (request === eventRequest.current) setError(String(e)); } finally { if (request === eventRequest.current) setEventsLoading(false); }
  };
  const rollback = async (id: number) => {
    setBusy(true); setError("");
    try { await impactApi.rollback(id); setRevision((n) => n + 1); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  };
  const review = async (candidate: ImpactCandidate, decision: "approve" | "reject") => {
    if (note.trim().length < 3) { setError("请填写类目调整依据（至少三个字）"); return; }
    setBusy(true); setError("");
    try { await impactApi.reviewCandidate(candidate.parent_id, candidate.name, decision, note); setRevision((n) => n + 1); setNote(""); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  };
  const follow = async () => {
    if (!detail) return; setBusy(true);
    try { await impactApi.follow(detail.id, detail.follow_status === "unfollowed"); const fresh = await impactApi.entity(detail.id); setDetail(fresh); setInstitutions((items) => items.map((item) => item.id === fresh.id ? { ...item, follow_status: fresh.follow_status } : item)); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  };

  return <main aria-label="领域动态情报" className="min-h-[420px] min-w-0 flex-1 space-y-4 overflow-y-auto pb-5 lg:min-h-0">
    <header className={panel}><p className="text-xs font-medium text-blue-600">战略图谱 · 领域影响力与动态</p><h2 className="mt-2 text-2xl font-semibold text-slate-900">机构排名、类目演进与每日报告</h2>
      <p className="mt-2 text-sm leading-6 text-slate-500">按当前领域联动。机构榜仅收中国内地科研机构；点击机构查看三维评语、逐期分数和成果原文。</p>
      {status && <div className="mt-4 grid grid-cols-3 gap-2">{[[status.triage.events, "来源事件"], [status.triage.periods, "评分期次"], [status.knowledgeGraph.reduce((n, s) => n + s.nodes, 0), "知识图谱节点"]].map(([n, label]) => <div key={label} className="rounded-xl bg-slate-50 p-3"><strong className="text-xl text-slate-900">{n}</strong><p className="mt-1 text-xs text-slate-500">{label}</p></div>)}</div>}
    </header>
    <nav aria-label="分诊视图" className="flex overflow-x-auto rounded-xl border border-slate-200 bg-white p-1.5">{([{ key: "ranking", title: "A/B/C 机构排名", icon: Building2 }, { key: "tree", title: "L1→L3 类目树", icon: FolderTree }, { key: "daily", title: "每日报告", icon: CalendarDays }] as const).map(({ key, title, icon: Icon }) =>
      <button key={key} aria-pressed={tab === key} onClick={() => { setTab(key); setError(""); }} className={`inline-flex shrink-0 items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium ${tab === key ? "bg-blue-50 text-blue-700" : "text-slate-500 hover:bg-slate-50"}`}><Icon className="size-4" />{title}</button>)}</nav>
    {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</p>}
    {tab === "ranking" && <>
      <div className={`${panel} flex flex-wrap items-center gap-3`}><input aria-label="检索科研机构" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="机构、别名或成果关键词" className={`${input} min-w-0 flex-1`} /><select aria-label="机构等级" value={tier} onChange={(e) => setTier(e.target.value)} className={input}><option value="">全部等级</option>{["A", "B", "C"].map((v) => <option key={v}>{v}</option>)}</select><label className="flex items-center gap-2 text-sm text-slate-600"><input type="checkbox" checked={onlyFollowed} onChange={(e) => setOnlyFollowed(e.target.checked)} />仅看关注</label></div>
      <p className="px-1 text-xs leading-5 text-slate-500">{visible.length} 个具备机构资格与评分记录的科研机构。历史来源评级尚未按 AI4S 样本校准，独立于团队评分和任务匹配分。</p>
      {loading && <p className="p-5 text-sm text-slate-500">正在读取机构排名…</p>}
      <div className="space-y-3">{visible.map((item, index) => <button key={item.id} type="button" data-impact-entity={item.id} onClick={() => setSelectedId(item.id)} className={`${panel} w-full text-left transition hover:border-blue-300`}>
        <div className="flex items-start gap-3"><span className="mt-1 font-mono text-sm text-slate-400">{String(index + 1).padStart(2, "0")}</span><div className="min-w-0 flex-1"><h3 className="font-semibold text-slate-900">{item.name}</h3><p className="mt-1 text-xs text-slate-500">{item.event_count} 条事件 · {item.flagship_count} 项代表成果 · {item.scan_date}</p></div><span className={`rounded-lg px-3 py-1.5 text-sm font-bold ${levelColor(item.tier)}`}>{item.tier}</span><strong className="text-xl text-slate-900">{item.total.toFixed(1)}</strong></div>
        <div className="mt-4 grid grid-cols-3 gap-3">{[["成就", item.eff_achievement], ["地位", item.eff_status], ["趋势", item.eff_trend]].map(([label, value]) => <div key={label} className="text-xs text-slate-500">{label}<strong className="ml-2 text-slate-700">{value}</strong><div className="mt-2 h-1 rounded bg-slate-100"><div className="h-1 rounded bg-blue-400" style={{ width: `${Math.max(0, Math.min(100, Number(value)))}%` }} /></div></div>)}</div>
        <p className="mt-3 line-clamp-2 text-xs leading-5 text-slate-500">{item.change_reason || "本期无等级变化"} · 查看评语与趋势 →</p>
      </button>)}</div>
      {!loading && !visible.length && <p className={`${panel} text-sm text-slate-500`}>当前范围没有符合筛选的机构评分记录。{subdomainId ? "可切换全部子领域查看所属领域的排名。" : "团队库仍可独立检索已核实团队。"}</p>}
      {selectedId && <section aria-label="机构影响力详情" className={panel}><div className="flex items-center justify-between gap-3"><h3 className="text-lg font-semibold">{detail?.name || "正在读取机构详情…"}</h3><button aria-label="关闭机构详情" onClick={() => setSelectedId("")}><X className="size-5 text-slate-400" /></button></div>
        {detail && <div className="mt-4 space-y-4"><div className="flex flex-wrap items-center gap-3"><button disabled={busy} onClick={() => void follow()} className="rounded-lg border border-blue-200 px-3 py-2 text-xs text-blue-700">{detail.follow_status === "unfollowed" ? "关注机构" : "取消关注"}</button>{safeUrl(detail.eligibility_basis_url) && <a href={detail.eligibility_basis_url} target="_blank" rel="noreferrer" className="text-xs text-blue-700">机构官网依据 ↗</a>}</div>
          <div className="grid gap-3 xl:grid-cols-3">{Object.entries(selectedRanking?.reasons || {}).map(([key, reason]) => <article key={key} className="rounded-xl bg-slate-50 p-4"><h4 className="text-xs font-semibold text-slate-700">{{ achievement: "成就评语", status: "地位评语", future: "趋势评语", trend: "趋势评语" }[key] || key}</h4><p className="mt-2 text-xs leading-6 text-slate-600">{reason}</p></article>)}</div>
          <PeriodChart history={detail.history} /><h4 className="text-sm font-semibold">关联事件与原文</h4><EventCards events={detail.events} />
          <h4 className="text-sm font-semibold">已审核机构—团队关系</h4>{detail.team_links.length ? detail.team_links.map((link) => <a key={link.team_id} href={`/strategic-map/team/${encodeURIComponent(link.team_id)}`} className="block text-sm text-blue-700">查看关联团队档案 →</a>) : <p className="text-xs leading-5 text-slate-500">当前未建立已审核的具体团队关系。机构事件不会自动计入下属团队成果。</p>}
        </div>}
      </section>}
    </>}
    {tab === "tree" && <>
      <section className={panel}><div className="flex items-center justify-between"><h3 className="font-semibold">领域分类树</h3><button onClick={() => setManage((v) => !v)} className="text-xs text-blue-700">{manage ? "收起类目管理" : "管理类目"}</button></div><p className="mt-2 text-xs leading-5 text-slate-500">高原／高峰 → 六大研究领域 → 来源主题标签。主题标签不会自动成为战略图谱子领域。</p>
        <div className="mt-4 space-y-3">{roots.map((root) => <section key={root.id} className="rounded-xl bg-slate-50 p-4"><h4 className="font-semibold text-slate-800">L1 · {root.name}</h4>{l2.filter((d) => d.parent_id === root.id).map((branch) => <div key={branch.id} className="mt-3 border-l-2 border-blue-200 pl-4"><button onClick={() => void openEvents(branch)} className="text-left text-sm font-semibold text-blue-700">L2 · {branch.name} <span className="ml-2 text-xs font-normal text-slate-400">{branch.event_count} 条来源事件</span></button><div className="mt-3 flex flex-wrap gap-2">{directions.filter((d) => d.level === 3 && d.status === "formal" && d.parent_id === branch.id && (!subdomainId || d.ai4s_subdomain_id === subdomainId)).map((leaf) => <button key={leaf.id} onClick={() => void openEvents(leaf)} className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600">L3 · {leaf.name} <span className="text-slate-400">{leaf.event_count}</span></button>)}</div></div>)}</section>)}</div>
        {!roots.length && <p className="mt-4 text-sm text-slate-500">当前范围没有来源类目。</p>}
      </section>
      {manage && <section className={panel}><h3 className="font-semibold">类目候选审核</h3><p className="mt-2 text-xs text-slate-500">依据来源事件确认是否纳入主题树，不改变团队入选资格。</p><textarea aria-label="类目审核依据" value={note} onChange={(e) => setNote(e.target.value)} placeholder="填写合并、纳入或拒绝的依据" className={`${input} mt-3 w-full`} />{scopedCandidates.map((candidate) => <div key={`${candidate.parent_id}:${candidate.name}`} className="mt-3 flex flex-wrap items-center gap-3 rounded-xl border border-slate-200 p-3"><span className="flex-1 text-sm">{candidate.name}<span className="ml-2 text-xs text-slate-400">{candidate.event_count} 条事件 · {candidate.independent_publishers} 个来源</span></span><button disabled={busy} onClick={() => void review(candidate, "approve")} className="text-xs text-blue-700">纳入类目</button><button disabled={busy} onClick={() => void review(candidate, "reject")} className="text-xs text-slate-500">拒绝</button></div>)}{!scopedCandidates.length && <p className="mt-3 text-sm text-slate-500">当前范围没有新增类目候选。</p>}</section>}
      {manage && <section className={panel}><h3 className="font-semibold">类目调整记录</h3>{audits.filter((audit) => directions.some((d) => d.id === audit.direction_id && (l2.some((branch) => branch.id === d.id || branch.id === d.parent_id)) && (!subdomainId || d.ai4s_subdomain_id === subdomainId))).map((audit) => <div key={audit.id} className="mt-3 flex items-center justify-between gap-3 text-xs text-slate-600"><span>{directions.find((d) => d.id === audit.direction_id)?.name} · {audit.action} · {audit.reviewed_on}</span>{audit.reverted_on ? <span className="text-slate-400">已撤销</span> : <button disabled={busy} onClick={() => void rollback(audit.id)} className="shrink-0 text-blue-700">撤销此项调整</button>}</div>)}</section>}
      {eventHeading && <section className={panel}><h3 className="mb-4 font-semibold">{eventHeading} · 来源事件</h3>{eventsLoading ? <p className="text-sm text-slate-500">正在读取事件…</p> : <EventCards events={treeEvents} />}{!eventsLoading && !treeEvents.length && <p className="text-sm text-slate-500">当前主题没有关联事件。</p>}</section>}
    </>}
    {tab === "daily" && <section className={panel}><div className="flex flex-wrap items-center justify-between gap-3"><h3 className="font-semibold">每日领域报告</h3><label className="text-xs text-slate-500">日期 <input aria-label="报告日期" type="date" value={date} onChange={(e) => setDate(e.target.value)} className={input} /></label></div>
      <div className="mt-3 flex flex-wrap gap-2">{status?.triage.dates.slice(0, 8).map((day) => <button key={day} onClick={() => setDate(day)} className={`rounded-lg px-2 py-1 text-xs ${date === day ? "bg-blue-50 text-blue-700" : "bg-slate-50 text-slate-500"}`}>{day}</button>)}</div>
      {reportLoading ? <p className="mt-6 text-sm text-slate-500">正在读取当期报告…</p> : report && <div className="mt-5 space-y-5">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">{[[report.sourceEvents.length, "领域事件"], [report.treeChanges.length, "类目变更"], [report.scoreChanges.length, "机构评分"], [report.teamChanges.length, "团队更新"]].map(([n, label]) => <div key={label} className="rounded-xl bg-slate-50 p-3"><strong className="text-xl">{n}</strong><p className="mt-1 text-xs text-slate-500">{label}</p></div>)}</div>
        <section><h4 className="text-sm font-semibold">一、数据流入与领域事件</h4><p className="mt-2 text-xs leading-5 text-slate-500">按事件日期回看来源记录；历史快照不计作今天的新流入。</p>{report.sourceEvents.map((event) => <article key={event.id} className="mt-3 rounded-xl border border-slate-200 p-4"><h5 className="text-sm font-medium">{event.title}</h5><p className="mt-2 text-xs leading-6 text-slate-500">{event.summary}</p>{event.sources.filter((s) => safeUrl(s.url)).map((s, i) => <a key={`${s.url}-${i}`} href={s.url} target="_blank" rel="noreferrer" className="mr-3 mt-2 inline-block text-xs text-blue-700">{s.title || `原文 ${i + 1}`} ↗</a>)}</article>)}{!report.sourceEvents.length && <p className="mt-2 text-xs text-slate-500">当日无符合当前范围的机构事件。</p>}</section>
        <section><h4 className="text-sm font-semibold">二、类目更新</h4>{report.treeChanges.map((item) => <p key={item.id} className="mt-2 text-xs text-slate-600">{item.label || item.action} · {item.reverted_on ? "已回滚" : item.reviewed_on}</p>)}{!report.treeChanges.length && <p className="mt-2 text-xs text-slate-500">当日无类目变更。</p>}</section>
        <section><h4 className="text-sm font-semibold">三、机构排名变动</h4>{report.scoreChanges.map((item) => <p key={item.id} className="mt-2 rounded-lg bg-slate-50 p-3 text-sm text-slate-600">{item.name} · {item.tier} · {item.change_reason || "本期无等级变化"}</p>)}{!report.scoreChanges.length && <p className="mt-2 text-xs text-slate-500">当日没有机构评分期次。</p>}</section>
        <section><h4 className="text-sm font-semibold">四、团队与推荐变化</h4>{report.teamChanges.map((item, i) => <p key={i} className="mt-2 text-sm leading-6 text-slate-600">{item.institutionName} · {item.teamName}：{item.reason}</p>)}{report.recommendationChanges.map((item, i) => <p key={i} className="mt-2 text-sm text-slate-600">{item.taskText}：{item.reason}（{item.before.length} → {item.after.length} 支）</p>)}{!report.teamChanges.length && !report.recommendationChanges.length && <p className="mt-2 text-xs text-slate-500">当日无已发布的团队或推荐变化。</p>}</section>
        <section><h4 className="text-sm font-semibold">五、领域摘要与跟进</h4><p className="mt-2 text-sm leading-6 text-slate-600">{report.date}，当前范围有 {report.sourceEvents.length} 条领域事件、{report.scoreChanges.length} 期机构评分和 {report.teamChanges.length} 次团队资料更新。</p>{report.teamChanges.map((item, i) => <p key={i} className="mt-2 text-xs leading-5 text-slate-500">跟进{item.teamName}新增成果的任务适用条件及合作接口。</p>)}</section>
        <footer className="break-all border-t border-slate-100 pt-3 text-[11px] text-slate-400">{report.frozen ? `团队与推荐快照 r${report.revision}` : "当前数据回放"} · 输入版本 {report.inputHash.slice(0, 16)} · 来源评分待校准</footer>
      </div>}
    </section>}
  </main>;
}
