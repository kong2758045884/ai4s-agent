import { useEffect, useRef, useState } from "react";
import { Globe, LoaderCircle, X } from "lucide-react";
import { assessmentApi, requestId, type AssessmentRun, type AssessmentInvestigation as Job, type InvestigationOverview, type InvestigationOptions } from "@/services/strategicAssessments";

import { primaryButton, secondaryButton as button } from "./controls";
const stateNames: Record<Job["state"], string> = { queued: "等待开始", running: "执行中", completed: "已完成", partial: "部分完成", failed: "失败", interrupted: "服务重启后中断", cancelled: "已取消" };
const active = (job: Job | null) => job?.state === "queued" || job?.state === "running";

export default function AssessmentInvestigation({ run, historical, onOpenRun }: { run: AssessmentRun; historical: boolean; onOpenRun: (id: string) => void }) {
  const [overview, setOverview] = useState<InvestigationOverview | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [options, setOptions] = useState<InvestigationOptions>({ teamIds: [], criterionIds: [], publishedAfter: null });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const dialog = useRef<HTMLDialogElement>(null);
  const idempotencyKey = useRef(requestId());
  const context = `${run.taskId}:${run.runId}`;
  const current = useRef(context); current.current = context;

  useEffect(() => {
    const controller = new AbortController(); setOverview(null); setJob(null); setError("");
    void assessmentApi.investigations(run.taskId, run.runId, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      setOverview(value); setJob(value.jobs[0] || null);
      const candidates = run.items.map(i => i.teamId);
      setOptions({ teamIds: candidates.filter(id => value.teams.some(t => t.teamId === id)).slice(0, 12),
        criterionIds: (run.criteria || []).filter(c => c.necessity !== "excluded").map(c => c.id), publishedAfter: null });
      idempotencyKey.current = requestId();
    }).catch(reason => { if (!controller.signal.aborted) setError(reason.message); });
    return () => controller.abort();
  }, [context, reload]);

  useEffect(() => {
    if (!active(job)) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      void assessmentApi.investigation(run.taskId, job!.jobId, controller.signal).then(value => {
        if (!controller.signal.aborted) { setJob(value); setError(""); }
      }).catch(reason => { if (!controller.signal.aborted) setError(`进度读取失败：${reason.message}。可点击重新加载。`); });
    }, 2000);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [context, job]);

  function editOptions(value: InvestigationOptions) { setOptions(value); idempotencyKey.current = requestId(); }
  async function mutate(fn: () => Promise<Job>) {
    if (busy) return;
    setBusy(true); setError("");
    try { const next = await fn(); if (current.current === context) { setJob(next); dialog.current?.close(); idempotencyKey.current = requestId(); } }
    catch (reason) { if (current.current === context) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (current.current === context) setBusy(false); }
  }
  return <section className="rounded-2xl border border-slate-200 bg-white p-4 sm:p-6" aria-label="联网补充资料">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="font-semibold">缺少成果或任务依据？</h3><p className="mt-1 text-xs leading-5 text-slate-500">先选团队和问题，再联网补证。现有名单保留，新证据通过复核后产生更新版本。</p></div>
      <button className={button} disabled={historical || !overview?.configured || !overview?.canStart || active(job) || busy} onClick={() => { idempotencyKey.current = requestId(); dialog.current?.showModal(); }}><Globe className="size-4" />联网补充资料</button></div>
    <p className="mt-2 text-xs leading-5 text-slate-500">{overview?.configurationNotice || (error ? "未能读取调查配置" : "正在读取调查配置…")}{historical && " · 历史版本只读"}</p>
    {error && <div role="alert" className="mt-3 rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}<button className={`${button} ml-2`} onClick={() => setReload(n => n + 1)}>重新加载</button></div>}
    {job && <div className="mt-4 space-y-3 rounded-xl bg-slate-50 p-4 text-sm" aria-live="polite">
      <p className="flex items-center gap-2">{active(job) && <LoaderCircle className="size-4 animate-spin" />}<strong>{stateNames[job.state]}</strong> · {job.stage} · {job.progress.done}/{job.progress.total} 个团队</p>
      <ol className="flex flex-wrap gap-x-3 gap-y-2 text-xs text-slate-500">{job.stages.map((s, i) => <li key={s} className={s === job.stage ? "font-semibold text-blue-700" : ""}>{i + 1}. {s}</li>)}</ol>
      <p className="text-xs leading-5 text-slate-500">{job.scopeNotice} 已发布 {job.publishedTeams.length} 个团队；尚未发布或失败 {job.failures.length} 项。费用{job.costCny === null ? "未知" : ` ¥${job.costCny}`}。</p>
      {job.error && <p role="alert" className="text-red-700">{job.error}</p>}
      <div className="flex flex-wrap gap-2">
        {active(job) && <button className={button} disabled={busy || job.cancelRequested} onClick={() => void mutate(() => assessmentApi.cancelInvestigation(run.taskId, job.jobId))}>{job.cancelRequested ? "等待当前请求结束" : "取消调查"}</button>}
        {overview?.canRetry && ["partial", "failed", "interrupted", "cancelled"].includes(job.state) && <button className={button} disabled={busy} onClick={() => void mutate(() => assessmentApi.retryInvestigation(run.taskId, job.jobId, idempotencyKey.current))}>重试未成功部分</button>}
        {job.updatedRunId && <button className={`${button} text-blue-700!`} onClick={() => onOpenRun(job.updatedRunId!)}>查看更新后的推荐</button>}
      </div>
      <details><summary className="min-h-11 cursor-pointer leading-[44px]">调用与未发布明细</summary><p className="text-xs leading-5">检索 {job.calls.search || 0} 次 · 抓取 {job.calls.fetch || 0} 次 · 模型 {job.calls.llm || 0} 次；{job.costNotice}</p>
        {job.failures.map((f, i) => <p className="mt-2 text-xs" key={`${f.teamId}-${i}`}>{overview?.teams.find(t => t.teamId === f.teamId)?.teamName || f.teamId}：{f.reason}</p>)}</details>
    </div>}
    <dialog ref={dialog} className="fixed inset-0 m-auto max-h-[85dvh] w-[min(640px,calc(100%-24px))] overflow-y-auto rounded-2xl border border-slate-200 bg-white p-5 text-slate-900 shadow-xl backdrop:bg-slate-900/30">
      <div className="flex items-center justify-between gap-3"><h2 className="text-lg font-semibold">明确本次联网调查范围</h2><button className={button} aria-label="关闭联网调查" onClick={() => dialog.current?.close()}><X className="size-4" /></button></div>
      <p className="mt-3 text-xs leading-5 text-slate-500">{overview?.scopeNotice} 此操作可能产生搜索及模型费用，每日预算未设上限。</p>
      <form className="mt-4 space-y-4" onSubmit={e => { e.preventDefault(); void mutate(() => assessmentApi.investigate(run.taskId, run.runId, options, idempotencyKey.current)); }}>
        <fieldset><legend className="font-medium">本次补充哪些问题</legend>{run.criteria?.map(c => <label className="flex min-h-11 items-start gap-2 py-2 text-sm" key={c.id}><input className="mt-1" type="checkbox" checked={options.criterionIds.includes(c.id)} onChange={() => editOptions({ ...options, criterionIds: options.criterionIds.includes(c.id) ? options.criterionIds.filter(id => id !== c.id) : [...options.criterionIds, c.id] })} />{c.text}</label>)}</fieldset>
        <fieldset><legend className="font-medium">科研单元（已选 {options.teamIds.length} / 最多 12）</legend><div className="max-h-60 overflow-y-auto">{overview?.teams.map(t => <label key={t.teamId} className="flex min-h-11 items-start gap-2 py-2 text-sm"><input className="mt-1" type="checkbox" checked={options.teamIds.includes(t.teamId)} disabled={!options.teamIds.includes(t.teamId) && options.teamIds.length >= 12} onChange={() => editOptions({ ...options, teamIds: options.teamIds.includes(t.teamId) ? options.teamIds.filter(id => id !== t.teamId) : [...options.teamIds, t.teamId] })} /><span>{t.teamName}<span className="block text-xs text-slate-500">{t.institutionName}</span></span></label>)}</div></fieldset>
        <label className="block text-sm">优先检索此日期之后的成果（可空）<input type="date" className="mt-1 min-h-11 w-full rounded-lg border border-slate-200 px-3" value={options.publishedAfter || ""} onChange={e => editOptions({ ...options, publishedAfter: e.target.value || null })} /></label>
        {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
        <button className={primaryButton} disabled={busy || !options.teamIds.length || !options.criterionIds.length}>明确启动联网补证</button>
      </form>
    </dialog>
  </section>;
}
