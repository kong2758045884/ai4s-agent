import { useEffect, useState } from "react";
import { Clock3, ExternalLink } from "lucide-react";
import { assessmentApi, type FollowUpHistory } from "@/services/strategicAssessments";
import { secondaryButton } from "./controls";

type Event = FollowUpHistory["events"][number];
const kinds = { added: "新建", updated: "更新", removed: "移除" };
const fields: Record<string, string> = { question: "问题", method: "验证方式", owner: "负责人", dueDate: "计划日期",
  result: "验证结果", status: "状态", claimIds: "成果依据", teamId: "团队" };
const statuses = { open: "未开始", in_progress: "进行中", done: "已完成", cancelled: "取消" };

export default function FollowUpTimeline({ taskId, revision, onOpenRun }: { taskId: string; revision: number; onOpenRun: (runId: string) => void }) {
  const [open, setOpen] = useState(false);
  const [events, setEvents] = useState<Event[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    setOpen(false); setEvents([]); setCursor(null); setError("");
  }, [taskId]);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setBusy(true);
    void assessmentApi.followUpHistory(taskId, undefined, controller.signal).then(value => {
      if (!controller.signal.aborted) { setEvents(value.events); setCursor(value.nextBeforeRevision); setError(""); }
    }).catch(reason => { if (!controller.signal.aborted) setError(reason.message); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [taskId, revision, open]);

  async function older() {
    if (!cursor || busy) return;
    setBusy(true);
    try {
      const value = await assessmentApi.followUpHistory(taskId, cursor);
      setEvents(previous => [...previous, ...value.events]);
      setCursor(value.nextBeforeRevision); setError("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "读取历史失败"); }
    finally { setBusy(false); }
  }

  return <section className="mt-5 border-t border-slate-100 pt-4" aria-label="跟进变更记录">
    <button type="button" className={secondaryButton} aria-expanded={open} onClick={() => setOpen(value => !value)}><Clock3 className="size-4" />{open ? "收起跟进历史" : "查看跟进历史"}</button>
    {open && <div className="mt-3 space-y-3">
      <p className="text-xs leading-5 text-slate-500">仅当前研判所有者可见；每次状态、负责人和验证结果的变更来自已保存的审计版本。</p>
      {error && <p role="alert" className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800">{error}</p>}
      {!busy && !events.length && !error && <p className="rounded-lg bg-slate-50 p-3 text-sm text-slate-500">尚无跟进变更记录。</p>}
      {events.map(event => <article key={event.revision} className="min-w-0 rounded-xl border border-slate-200 bg-white p-3 text-sm">
        <p className="text-xs font-medium text-slate-500">记录修订 {event.revision} · {new Date(event.createdAt).toLocaleString("zh-CN")}</p>
        {event.changes.map(change => { const value = change.after || change.before; return <div key={change.followUpId} className="mt-3 min-w-0 border-t border-slate-100 pt-3 first:border-0 first:pt-0">
          <p className="font-semibold text-slate-900">{kinds[change.kind]} · {value?.question || change.followUpId}</p>
          <p className="mt-1 text-xs leading-5 text-slate-500">变更项：{change.changedFields.map(field => fields[field] || field).join("、")} · 当前记录状态：{value ? statuses[value.status] : "已移除"}</p>
          {value && <div className="mt-2 grid gap-1 text-xs leading-5 text-slate-700 sm:grid-cols-2"><p>验证方式：{value.method || "待补"}</p><p>负责人：{value.owner || "待指定"}</p><p>计划日期：{value.dueDate || "未定"}</p><p>验证结果：{value.result || "待记录"}</p></div>}
          {change.evidence.length > 0 && <div className="mt-2 space-y-2">{change.evidence.map(ref => <div key={ref.claimId} className="min-w-0 rounded-lg bg-blue-50/60 p-2 text-xs leading-5">
            <p className="break-all text-blue-900">证据 {ref.claimId} {ref.inputVersion ? `· 研判条件 v${ref.inputVersion}` : ""}</p>
            <p className="mt-1 break-words text-slate-700">{ref.text || "旧引文快照未找到，需人工复核"}</p>
            {ref.runId && <div className="mt-2 flex flex-wrap gap-3"><button className="min-h-11 text-blue-700 underline" onClick={() => onOpenRun(ref.runId!)}>查看对应研判版本</button>{ref.url && <a className="inline-flex min-h-11 items-center gap-1 break-all text-blue-700 underline" href={ref.url} target="_blank" rel="noopener noreferrer">打开来源原文<ExternalLink className="size-3" /></a>}</div>}
            {ref.quote && <details><summary className="min-h-11 cursor-pointer py-2 text-blue-700">查看保存的引文片段</summary><blockquote className="break-words whitespace-pre-wrap border-l-2 border-blue-200 pl-2 text-slate-600">{ref.quote}</blockquote></details>}
          </div>)}</div>}
        </div>; })}
      </article>)}
      {cursor && <button type="button" className={secondaryButton} disabled={busy} onClick={() => void older()}>加载更早记录</button>}
      {busy && <p className="text-xs text-slate-500">正在读取跟进历史…</p>}
    </div>}
  </section>;
}
