import { useEffect, useState } from "react";
import { assessmentApi, type AssessmentRun } from "@/services/strategicAssessments";
import { compareVersions } from "./assessmentVersionDiff";

export default function VersionComparison({ run, onOpenRun }: { run: AssessmentRun; onOpenRun: (runId: string) => void }) {
  const [open, setOpen] = useState(false);
  const [previous, setPrevious] = useState<AssessmentRun | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { setOpen(false); setPrevious(null); setError(""); }, [run.runId]);
  useEffect(() => {
    if (!open || !run.previousRunId || previous?.runId === run.previousRunId) return;
    let live = true;
    setError("");
    assessmentApi.run(run.taskId, run.previousRunId).then(value => { if (live) setPrevious(value); })
      .catch(reason => { if (live) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => { live = false; };
  }, [open, run.taskId, run.previousRunId, previous?.runId]);
  if (!run.previousRunId) return null;
  const changes = previous ? compareVersions(previous, run) : [];
  const counts = {
    added: changes.filter(c => c.kind === "added").length,
    removed: changes.filter(c => c.kind === "removed").length,
    evidence: changes.filter(c => c.addedEvidence.length || c.removedEvidence.length || c.changedEvidence.length).length,
    role: changes.filter(c => c.roleChanged).length
  };
  return <section aria-label="研判版本对照" className="min-w-0 rounded-2xl border border-blue-200 bg-blue-50/50 p-4 sm:p-5">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="font-semibold text-slate-900">与上一研判版本对照</h3>
      <p className="mt-1 text-xs leading-5 text-slate-600">逐支核对名单、保存的成果引文与拟议分工；新版本的分工需重新确认。</p></div>
    <button type="button" className="min-h-11 rounded-lg border border-blue-300 bg-white px-4 text-sm font-medium text-blue-800 hover:bg-blue-100" onClick={() => setOpen(value => !value)}>{open ? "收起版本对照" : "查看版本对照"}</button></div>
    {open && <div className="mt-4 space-y-3">{error ? <p className="text-sm text-red-700" role="alert">{error}</p> : !previous ? <p className="text-sm text-slate-600">正在读取上一保存版本…</p> : <>
      <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">{[["新增团队", counts.added], ["移除团队", counts.removed], ["成果依据变化", counts.evidence], ["分工变化／待确认", counts.role]].map(([label, value]) => <p key={label} className="rounded-xl border border-blue-100 bg-white p-3"><strong className="block text-lg text-slate-900">{value}</strong>{label}</p>)}</div>
      {!changes.length && <p className="text-sm text-slate-600">两版的候选、保存引文与拟议分工一致。</p>}
      {changes.map(change => <article key={change.teamId} className="min-w-0 rounded-xl border border-slate-200 bg-white p-3 text-sm leading-6">
        <h4 className="font-semibold text-slate-900">{change.teamName} <span className="ml-1 text-xs font-normal text-slate-500">{change.kind === "added" ? "本版新增" : change.kind === "removed" ? "本版移除" : "保留候选"}</span></h4>
        {(change.addedEvidence.length > 0 || change.removedEvidence.length > 0 || change.changedEvidence.length > 0) && <p className="mt-1 text-slate-700">成果依据：新增 {change.addedEvidence.length} 条、移除 {change.removedEvidence.length} 条、内容变化 {change.changedEvidence.length} 条。请进入本版证据卡核对适用边界。</p>}
        {([ ["新增引文", change.addedEvidence], ["移除引文", change.removedEvidence], ["内容变更", change.changedEvidence] ] as const).map(([label, ids]) => ids.length > 0 && <p key={label} className="mt-1 break-all text-xs text-slate-600">{label}：{ids.join("、")}</p>)}
        {change.roleChanged && <div className="mt-2 rounded-lg bg-amber-50 p-2 text-amber-950"><p>上一版拟议分工：{change.previousRole?.role || "未记录"}</p>
          <p>本版拟议分工：{change.currentRole?.role || "尚未确认"}</p>
          {(change.previousRole?.rationale || change.currentRole?.rationale) && <p className="text-xs">依据说明：{change.previousRole?.rationale || "无"} → {change.currentRole?.rationale || "待补"}</p>}
          <p className="break-all text-xs">分工依据：{change.previousRole?.claimIds?.join("、") || "无"} → {change.currentRole?.claimIds?.join("、") || "待补"}</p></div>}
        <div className="mt-2 flex flex-wrap gap-2"><button type="button" className="min-h-11 rounded-lg border border-slate-200 px-3 text-xs text-blue-700" onClick={() => onOpenRun(previous.runId)}>查看上一版</button><button type="button" className="min-h-11 rounded-lg border border-slate-200 px-3 text-xs text-blue-700" onClick={() => onOpenRun(run.runId)}>查看本版</button></div>
      </article>)}
    </>}</div>}
  </section>;
}
