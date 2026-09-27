import { useEffect, useRef, useState } from "react";
import { claimReviews, claimDecisionLabels, type ClaimDecision, type ClaimReviewView } from "@/services/claimReviews";
import { useStrategicAccess } from "@/services/strategicAccess";
import { requestId } from "@/services/strategicAssessments";

const input = "mt-1 min-h-11 w-full min-w-0 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900";
const button = "min-h-11 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-blue-700 disabled:opacity-50";

function ReviewForm({ claimId, onSaved }: { claimId: string; onSaved?: () => void }) {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<ClaimReviewView | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [signedName, setSignedName] = useState(""), [decision, setDecision] = useState<ClaimDecision>("supported");
  const [scope, setScope] = useState(""), [reason, setReason] = useState(""), [linkStatus, setLinkStatus] = useState("not_checked");
  const [reference, setReference] = useState({ title: "", url: "", quote: "" });
  const idempotency = useRef({ signature: "", id: "" });
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  async function load() {
    setBusy(true); setError("");
    try { const result = await claimReviews.get(claimId); if (mounted.current) { setView(result); setLinkStatus(result.linkStatus); } }
    catch (e) { if (mounted.current) setError((e as Error).message); }
    finally { if (mounted.current) setBusy(false); }
  }
  async function submit(revert = false) {
    if (!view) return;
    setBusy(true); setError(""); setNotice("");
    const common = { expectedRevision: view.revision, sourceFingerprint: view.claim.fingerprint, signedName: signedName.trim(), reason: reason.trim() };
    const body = revert ? common : { ...common, decision, scope: scope.trim(), linkStatus, relatedEvidence: reference.url || reference.title || reference.quote ? [reference] : [] };
    const signature = JSON.stringify({ claimId, revert, body });
    if (signature !== idempotency.current.signature) idempotency.current = { signature, id: requestId() };
    try {
      if (revert) await claimReviews.revert(claimId, view.review.reviewId!, { ...common, requestId: idempotency.current.id });
      else await claimReviews.save(claimId, { ...common, requestId: idempotency.current.id, decision, scope: scope.trim(), linkStatus, relatedEvidence: reference.url || reference.title || reference.quote ? [reference] : [] });
      if (!mounted.current) return;
      setNotice("已保存。相关研判将在本地更新；历史结果和引文快照保留。新结论可在“我的研判”中查看。");
      idempotency.current = { signature: "", id: "" };
      setView(await claimReviews.get(claimId)); onSaved?.();
    } catch (e) { if (mounted.current) setError((e as Error).message); }
    finally { if (mounted.current) setBusy(false); }
  }
  return <section className="space-y-3 rounded-xl border border-blue-100 bg-blue-50/40 p-3 text-sm" aria-label="逐条证据审核">
    <button className={button} type="button" aria-expanded={open} onClick={() => { setOpen(!open); if (!open && !view) void load(); }}> {open ? "收起当前证据审核" : "审核此条当前证据"}</button>
    {open && <>
      <p className="text-xs leading-6 text-slate-600">审核只作用于下面这条当前原文及主张。签名、结论和依据会随公开证据展示，请勿填写内部联系记录或私有任务内容；不会修改历史研判快照。</p>
      {busy && <p role="status">正在读取或保存…</p>}
      {error && <div role="alert" className="text-red-700">{error}<button type="button" className={`${button} ml-2`} disabled={busy} onClick={() => void load()}>重新读取最新记录</button></div>}
      {notice && <p role="status" className="text-emerald-700">{notice}</p>}
      {view && <>
        <div className="space-y-2 break-words rounded-lg border border-slate-200 bg-white p-3"><p className="font-semibold">当前主张：{view.claim.text}</p><blockquote className="border-l-2 border-blue-200 pl-3 leading-6">{view.claim.quote}</blockquote><a href={view.claim.url} target="_blank" rel="noopener noreferrer" className="break-all text-blue-700 underline">打开当前来源原文</a><p>审核版本 {view.revision} · {view.review.status === "source_changed" ? "来源已更新，原人工结论须重新核验" : claimDecisionLabels[view.review.decision || ""] || "尚无人工审核"}</p>{view.review.scope && <p>适用条件：{view.review.scope}</p>}{view.review.reason && <p>判断依据：{view.review.reason}</p>}</div>
        {!view.available ? <p role="status">此来源已退出当前公开资料，保留历史审核；需先核查团队或成果归属。</p> : <form className="space-y-3" data-testid="claim-review-form" onSubmit={event => { event.preventDefault(); void submit(); }}>
          <div className="grid gap-3 sm:grid-cols-2"><label>审核签名<input className={input} required maxLength={80} value={signedName} onChange={e => setSignedName(e.target.value)} /></label><label>本条判断<select className={input} value={decision} onChange={e => setDecision(e.target.value as ClaimDecision)}>{Object.entries(claimDecisionLabels).filter(([key]) => key !== "unreviewed").map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label></div>
          <label className="block">适用条件与判断边界<textarea className={input} required minLength={2} maxLength={500} rows={2} value={scope} onChange={e => setScope(e.target.value)} placeholder="例如：仅证明该团队完成此论文实验，不证明设备可对外使用" /></label>
          <label className="block">公开审核依据／撤销原因<textarea className={input} required minLength={5} maxLength={2000} rows={2} value={reason} onChange={e => setReason(e.target.value)} /></label>
          <label className="block">本次人工查看原文链接<select className={input} value={linkStatus} onChange={e => setLinkStatus(e.target.value)}><option value="not_checked">未检查</option><option value="available">本次可以访问</option><option value="unavailable">本次暂不可访问（仍保留引文）</option></select></label>
          <details><summary className="min-h-11 cursor-pointer py-3">补充一条对照或反对证据</summary><p className="text-xs leading-6 text-slate-500">保留为审核人员提供的对照资料，不自动视为已验证的新成果。</p>{([['title', '对照资料标题'], ['url', '对照资料原文网址'], ['quote', '对照资料原文片段']] as const).map(([key, label]) => <label className="mt-2 block" key={key}>{label}<textarea className={input} value={reference[key]} onChange={e => setReference({ ...reference, [key]: e.target.value })} rows={key === "quote" ? 2 : 1} /></label>)}</details>
          <p className="text-xs leading-6 text-slate-500">条件支持只保留为带适用条件的资料；存在冲突、证据不足或撤回的主张退出当前推荐依据。保存后不会自动补满推荐数量。</p>
          <div className="flex flex-wrap gap-2"><button type="submit" disabled={busy} className={`${button} font-semibold`}>保存本条审核</button>{view.review.reviewId && <button type="button" className={button} disabled={busy || !signedName.trim() || reason.trim().length < 5 || view.review.status === "source_changed"} onClick={() => void submit(true)}>撤销最新审核并记录原因</button>}</div>
        </form>}
        <details><summary className="min-h-11 cursor-pointer py-3">查看逐条审核历史（{view.history?.length || 0}）</summary><div className="max-h-64 space-y-2 overflow-y-auto">{view.history?.map(row => <article key={row.reviewId} className="break-words rounded-lg bg-white p-3 text-xs leading-6"><p>v{row.revision} · {claimDecisionLabels[row.decision || ""]} · {row.reviewer} · {row.reviewedAt}</p><p>{row.scope}</p><p>{row.reason}</p><p className="break-all text-slate-500">操作者编号：{row.actorId}</p>{row.relatedEvidence?.map(ref => <p key={ref.url}><a href={ref.url} target="_blank" rel="noopener noreferrer" className="text-blue-700 underline">{ref.title}</a>：{ref.quote}</p>)}</article>)}</div></details>
      </>}
    </>}
  </section>;
}

export default function ClaimReviewEditor(props: { claimId: string; onSaved?: () => void }) {
  const access = useStrategicAccess();
  return access.canReview ? <ReviewForm {...props} /> : null;
}

export function TeamClaimReviews({ teamId }: { teamId: string }) {
  const [open, setOpen] = useState(false), [status, setStatus] = useState("all"), [page, setPage] = useState(1);
  const [data, setData] = useState<{ items: ClaimReviewView[]; total: number } | null>(null), [error, setError] = useState("");
  const [busy, setBusy] = useState(false), [refresh, setRefresh] = useState(0);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController(); setBusy(true); setError(""); setData(null);
    void claimReviews.list(teamId, status, page, controller.signal).then(value => { if (!controller.signal.aborted) setData(value); })
      .catch(e => { if (!controller.signal.aborted) setError(e.message); }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [teamId, status, page, open, refresh]);
  return <section className="mt-3 space-y-3" aria-label="团队逐条证据核验">
    <button className={button} type="button" aria-expanded={open} onClick={() => setOpen(!open)}>团队证据审核队列</button>
    {open && <><label className="block">审核状态<select className={input} value={status} onChange={e => { setStatus(e.target.value); setPage(1); }}><option value="all">全部引文</option><option value="not_recorded">未人工审核</option><option value="source_changed">来源更新需复核</option>{Object.entries(claimDecisionLabels).filter(([key]) => key !== "unreviewed").map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      {busy && <p role="status">正在读取证据…</p>}{error && <p role="alert">{error}<button className={button} onClick={() => setRefresh(v => v + 1)}>重试</button></p>}
      {data && <><p>共 {data.total} 条 · 第 {page} 页 · 每页 20 条</p>{data.items.length === 0 && <p>当前筛选没有引文。</p>}<div className="max-h-[60vh] space-y-3 overflow-y-auto">{data.items.map(row => <article className="space-y-2" key={row.claim.id}><p className="font-medium leading-6">{row.claim.text}</p><p>{row.review.status === "source_changed" ? "来源更新需复核" : claimDecisionLabels[row.review.decision || ""] || "未人工审核"}</p><ReviewForm claimId={row.claim.id} /></article>)}</div><div className="flex gap-2"><button className={button} disabled={page <= 1} onClick={() => setPage(p => p - 1)}>上一页</button><button className={button} disabled={page * 20 >= data.total} onClick={() => setPage(p => p + 1)}>下一页</button><button className={button} onClick={() => setRefresh(v => v + 1)}>更新队列</button></div></>}
    </>}
  </section>;
}
