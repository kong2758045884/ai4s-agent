import { useEffect, useState, type ReactNode } from "react";
import { assessmentApi, type AssessmentUpdate } from "@/services/strategicAssessments";
import type { StrategicDomain } from "@/services/strategicMap";
import AssessmentReports from "./AssessmentReports";

const button = "min-h-11 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50";

export default function AssessmentUpdates({ children, onOpenRun, domains, domainId }: { children: ReactNode; onOpenRun: (taskId: string, runId: string) => void; domains: StrategicDomain[]; domainId: string }) {
  const [view, setView] = useState<"mine" | "domain" | "reports">("mine");
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<AssessmentUpdate[]>([]);
  const [total, setTotal] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    if (view !== "mine") return;
    const controller = new AbortController(); setBusy(true); setError(""); setItems([]);
    void assessmentApi.updates(page, "", controller.signal).then(data => {
      if (!controller.signal.aborted) { setItems(data.items); setTotal(data.total); }
    }).catch(reason => { if (!controller.signal.aborted) setError(reason.message); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [view, page, reload]);
  return <section className="flex min-h-0 min-w-0 flex-1 flex-col gap-3 overflow-y-auto" aria-label="情报观察">
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap gap-2"><button className={`${button} ${view === "mine" ? "border-blue-300 text-blue-700!" : ""}`} aria-pressed={view === "mine"} onClick={() => setView("mine")}>我的研判变化</button>
        <button className={`${button} ${view === "reports" ? "border-blue-300 text-blue-700!" : ""}`} aria-pressed={view === "reports"} onClick={() => setView("reports")}>我的领域报告</button>
        <button className={`${button} ${view === "domain" ? "border-blue-300 text-blue-700!" : ""}`} aria-pressed={view === "domain"} onClick={() => setView("domain")}>领域情报、机构榜与日报</button></div>
      <p className="mt-2 text-xs leading-5 text-slate-500">{view === "mine" ? "显示当前访客所有已保存任务的证据变化，不受左侧领域筛选影响。新资料通过发布规则后，相关任务产生新版本；原版本仍可查看。" : view === "reports" ? "选择一个领域保存日报，再按固定日报版本汇总；仅当前访客可见，导出不包含内部备注和跟进反馈。" : "按左侧领域浏览公开情报；机构影响力与任务匹配采用不同评分，机构事件不自动计为下属团队成果。"}</p>
    </div>
    {view === "domain" ? children : view === "reports" ? <AssessmentReports domains={domains} initialDomainId={domainId} onOpenRun={onOpenRun} /> : <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-lg font-semibold">与已保存研判相关的变化</h2><button className={button} disabled={busy} onClick={() => setReload(n => n + 1)}>刷新变化</button></div>
      {busy && <p role="status" className="p-4 text-sm text-slate-500">正在读取变化…</p>}
      {error && <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}<button className={`${button} ml-2`} onClick={() => setReload(n => n + 1)}>重试</button></div>}
      {!busy && !error && !items.length && <div className="rounded-xl border border-slate-200 bg-white p-6"><h3 className="font-medium">暂未发现影响已保存研判的新证据</h3><p className="mt-2 text-sm leading-6 text-slate-500">先在研判工作台保存任务。没有新证据时保留原名单；修改任务条件也不会被记成科研进展。公开领域情报可在上方另一视图查看。</p></div>}
      {!busy && !error && items.map(item => <article className="rounded-xl border border-slate-200 bg-white p-4 sm:p-6" key={item.id}>
        <h3 className="font-semibold">{item.title}</h3><p className="mt-2 text-sm">{item.reason}</p><p className="mt-2 text-xs text-slate-500">{new Date(item.createdAt).toLocaleString("zh-CN")} · 新增 {item.changes.added.length} · 移除 {item.changes.removed.length} · 依据或内容变化 {item.changes.updated.length}</p>
        <div className="mt-3 flex flex-wrap gap-2"><button className={button} onClick={() => onOpenRun(item.taskId, item.beforeRunId)}>查看变化前的依据</button><button className={`${button} text-blue-700!`} onClick={() => onOpenRun(item.taskId, item.afterRunId)}>查看变化后的研判</button></div>
      </article>)}
      {total > 20 && <div className="flex flex-wrap items-center gap-3 text-sm"><button className={button} disabled={busy || page === 1} onClick={() => setPage(p => p - 1)}>上一页</button><span>第 {page} 页 / 共 {total} 条</span><button className={button} disabled={busy || page * 20 >= total} onClick={() => setPage(p => p + 1)}>下一页</button></div>}
    </div>}
  </section>;
}
