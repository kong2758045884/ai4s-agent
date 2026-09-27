import { useEffect, useRef, useState } from "react";
import { Download } from "lucide-react";
import { assessmentApi, requestId, type AssessmentReport, type AssessmentReportSummary } from "@/services/strategicAssessments";
import type { StrategicDomain } from "@/services/strategicMap";

import { primaryButton, secondaryButton as button } from "./controls";
const input = "mt-1 min-h-11 w-full min-w-0 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm";
const kinds = { daily: "日报", weekly: "近七日报告", monthly: "本月至所选日报告" };

export default function AssessmentReports({ domains, initialDomainId, onOpenRun }: { domains: StrategicDomain[]; initialDomainId: string; onOpenRun: (taskId: string, runId: string) => void }) {
  const [domainId, setDomainId] = useState(initialDomainId || domains[0]?.id || "");
  const [kind, setKind] = useState<AssessmentReport["kind"]>("daily");
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  const [day, setDay] = useState(today);
  const [page, setPage] = useState(1);
  const [list, setList] = useState<AssessmentReportSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [report, setReport] = useState<AssessmentReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const idempotencyKey = useRef(requestId());
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (domains.length && !domains.some(d => d.id === domainId)) {
      setDomainId(domains.some(d => d.id === initialDomainId) ? initialDomainId : domains[0].id);
      setPage(1); setReport(null);
    }
  }, [domains, domainId, initialDomainId]);
  useEffect(() => {
    if (!domainId) return;
    const controller = new AbortController(); setLoading(true); setError(""); setList([]);
    void assessmentApi.reports(domainId, page, controller.signal).then(data => {
      if (!controller.signal.aborted) { setList(data.items); setTotal(data.total); }
    }).catch(reason => { if (!controller.signal.aborted) setError(reason.message); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [domainId, page, reload]);
  useEffect(() => { idempotencyKey.current = requestId(); }, [domainId, kind, day]);

  async function act(fn: () => Promise<AssessmentReport>) {
    if (busy) return;
    setBusy(true); setError("");
    try { const value = await fn(); if (mounted.current) { setReport(value); setReload(n => n + 1); idempotencyKey.current = requestId(); } }
    catch (reason) { if (mounted.current) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (mounted.current) setBusy(false); }
  }
  function exportReport() {
    if (!report) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
    const link = document.createElement("a"); link.href = url; link.download = `AI4S-${report.reportId}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return <div className="space-y-4" aria-label="我的领域报告">
    <section className="rounded-xl border border-slate-200 bg-white p-4 sm:p-6"><h2 className="text-lg font-semibold">按同一证据版本汇报</h2>
      <p className="mt-2 text-sm leading-6 text-slate-500">每份报告聚焦一个领域，保存与自己任务相关的变化及已有公开日报。周月报告汇总已保存日报，列出缺失日期。此操作使用本地资料。</p>
      <form className="mt-4 space-y-3" onSubmit={e => { e.preventDefault(); void act(() => assessmentApi.freezeReport({ day, domainId, kind, requestId: idempotencyKey.current })); }}>
        <div className="grid gap-3 sm:grid-cols-3"><label className="text-sm">报告领域<select aria-label="报告领域" className={input} value={domainId} disabled={busy} required onChange={e => { setDomainId(e.target.value); setPage(1); setReport(null); }}>{!domains.length && <option value="">领域加载中</option>}{domains.map(d => <option value={d.id} key={d.id}>{d.name}</option>)}</select></label>
          <label className="text-sm">时间范围<select aria-label="报告时间范围" className={input} value={kind} disabled={busy} onChange={e => setKind(e.target.value as AssessmentReport["kind"])}>{Object.entries(kinds).map(([k, label]) => <option value={k} key={k}>{label}</option>)}</select></label>
          <label className="text-sm">截止日期（北京时间）<input aria-label="报告截止日期" type="date" required max={today} className={input} value={day} disabled={busy} onChange={e => setDay(e.target.value)} /></label></div>
        <button className={primaryButton} disabled={busy || !domains.some(d => d.id === domainId) || !day}>{busy ? "正在保存…" : "生成并保存报告"}</button>
      </form>
      {error && <div role="alert" className="mt-3 rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}<button className={`${button} ml-2`} onClick={() => setReload(n => n + 1)}>重新加载记录</button></div>}
    </section>
    {report && <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 sm:p-6" aria-label="已保存领域报告">
      <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-lg font-semibold">{report.scope.domainName} · {kinds[report.kind]}</h2><p className="mt-1 text-xs text-slate-500">{report.startDay} — {report.endDay} · 修订 {report.revision} · 北京时间</p></div><button className={button} onClick={exportReport}><Download className="size-4" />导出当前报告</button></div>
      <p className="text-sm leading-6">{report.summary}</p>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">{[["研判变化", report.counts.taskUpdates], ["涉及团队", report.counts.affectedTeams], ["新增 / 移除", `${report.counts.added} / ${report.counts.removed}`], ["依据变化", report.counts.updated]].map(([label, value]) => <div className="rounded-lg bg-slate-50 p-3" key={label}><dt className="text-xs text-slate-500">{label}</dt><dd className="mt-1 text-xl font-semibold">{value}</dd></div>)}</dl>
      {report.missingDays.length > 0 && <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm leading-6">有 {report.missingDays.length} 天尚未保存日报，当前汇总不完整。<details><summary className="min-h-11 cursor-pointer leading-[44px]">查看缺失日期</summary>{report.missingDays.join("、")}</details></div>}
      {report.kind === "daily" && !report.publicDaily && <p className="text-sm text-slate-500">当天尚无已冻结的公开领域日报；本报告只包含已保存研判的变化，不能据此认定该领域没有新动态。</p>}
      {!report.changes.length && <p className="text-sm leading-6 text-slate-500">当前已保存输入中没有影响本领域研判的新证据，原名单保持不变。</p>}
      {report.changes.map(change => <article className="rounded-xl border border-slate-200 p-4" key={change.updateId}><h3 className="font-semibold">{change.title}</h3><p className="mt-2 text-sm leading-6">发生了什么：{change.reason}</p><p className="mt-1 text-sm">判断变化：新增 {change.changes.added.length}，移除 {change.changes.removed.length}，依据或内容变化 {change.changes.updated.length}。</p>
        <details className="mt-2"><summary className="min-h-11 cursor-pointer text-sm leading-[44px]">查看团队与保存的成果原文</summary>{[...change.after.items, ...change.before.items.filter(t => !change.after.items.some(a => a.teamId === t.teamId))].map(team => <div className="mt-2 rounded-lg bg-slate-50 p-3 text-sm" key={team.teamId}><strong>{team.teamName}</strong>{team.citations.map(c => <div className="mt-2" key={c.id}><a className="inline-block min-h-11 break-words text-blue-700" href={c.url} target="_blank" rel="noopener noreferrer">{c.text} ↗</a><blockquote className="border-l-2 border-slate-300 pl-2 text-xs leading-5 text-slate-600">{c.quote}</blockquote></div>)}</div>)}</details>
        <p className="mt-2 text-xs leading-5 text-slate-500">建议跟进：{change.nextStep}</p><div className="mt-3 flex flex-wrap gap-2"><button className={button} onClick={() => onOpenRun(change.taskId, change.before.runId)}>打开变化前研判</button><button className={button} onClick={() => onOpenRun(change.taskId, change.after.runId)}>打开变化后研判</button></div>
      </article>)}
      {!!report.publicDaily && <details><summary className="min-h-11 cursor-pointer text-sm leading-[44px]">已保存公开日报：{report.publicDaily.summary}</summary>{report.publicDaily.sourceEvents.map(event => <div className="mt-2 rounded-lg bg-slate-50 p-3 text-sm" key={event.id}><strong>{event.title}</strong><p className="mt-1 text-xs">发生于 {event.event_date} · 入库于 {event.imported_on}{event.lateArrival ? " · 迟到补录" : ""}</p>{event.sources.map(source => <a className="block min-h-11 break-words text-blue-700" href={source.url} target="_blank" rel="noopener noreferrer" key={source.url}>{source.title || "来源原文"} ↗</a>)}</div>)}</details>}
      {!!report.dailyInputs.length && <details><summary className="min-h-11 cursor-pointer text-sm leading-[44px]">汇总使用的日报版本（{report.dailyInputs.length} 份）</summary>{report.dailyInputs.map(d => <div className="mt-2 break-all text-xs leading-5" key={d.reportId}>{d.day} · 修订 {d.revision}<button className={`${button} ml-2`} disabled={busy} onClick={() => void act(() => assessmentApi.report(d.reportId))}>打开当时日报</button><p>{d.inputHash}</p></div>)}</details>}
      <p className="text-xs leading-5 text-slate-500">{report.notice}</p><details><summary className="min-h-11 cursor-pointer text-xs leading-[44px]">核对保存版本</summary><p className="break-all text-xs leading-5">报告 {report.reportId}<br />输入哈希 {report.inputHash}<br />保存于 {new Date(report.frozenAt).toLocaleString("zh-CN")}</p></details>
    </section>}
    <section className="rounded-xl border border-slate-200 bg-white p-4"><h3 className="font-semibold">历史报告及修订</h3>{loading && <p role="status" className="mt-3 text-sm text-slate-500">正在读取…</p>}
      {!loading && !list.length && <p className="mt-3 text-sm text-slate-500">本领域暂无已保存报告。</p>}
      <div className="mt-3 space-y-2">{list.map(item => <button className={`${button} w-full justify-between! text-left`} key={item.reportId} disabled={busy} onClick={() => void act(() => assessmentApi.report(item.reportId))}><span>{kinds[item.kind]} · {item.endDay}</span><span>修订 {item.revision}</span></button>)}</div>
      {total > 20 && <div className="mt-3 flex flex-wrap items-center gap-2 text-sm"><button className={button} disabled={loading || busy || page <= 1} onClick={() => setPage(p => p - 1)}>上一页</button>第 {page} 页 / {total} 份<button className={button} disabled={loading || busy || page * 20 >= total} onClick={() => setPage(p => p + 1)}>下一页</button></div>}
    </section>
  </div>;
}
