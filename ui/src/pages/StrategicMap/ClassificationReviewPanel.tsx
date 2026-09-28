import { useEffect, useState } from "react";
import { ExternalLink, LoaderCircle, RotateCcw, X } from "lucide-react";
import { normalizeToolBaseUrlForBrowser } from "@/utils/fileUrl";
import { requestId } from "@/services/strategicAssessments";

export type ClassificationTeam = {
  teamId: string; teamName: string; institutionName: string; subdomainId: string | null;
  classificationSources: { claimId: string; title: string; url: string }[];
};
type History = { reviewId: string; revision: number; beforeSubdomainId: string | null;
  afterSubdomainId: string | null; evidenceClaimId: string; evidenceUrl: string;
  reason: string; actorId: string; revertOf: string | null; createdAt: string };
type ReviewState = { teamId: string; subdomainId: string | null; revision: number; history: History[] };

export default function ClassificationReviewPanel({ team, subdomains, sourceVersion, onSaved, onClose }: {
  team: ClassificationTeam; subdomains: { id: string; name: string }[]; sourceVersion: string;
  onSaved: () => void | Promise<void>; onClose: () => void;
}) {
  const [current, setCurrent] = useState<ReviewState | null>(null);
  const [target, setTarget] = useState(team.subdomainId || "");
  const [evidenceId, setEvidenceId] = useState(team.classificationSources[0]?.claimId || "");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const base = `${normalizeToolBaseUrlForBrowser("")}/v1/strategic-map/teams/${encodeURIComponent(team.teamId)}/classification-reviews`;
  useEffect(() => {
    const controller = new AbortController();
    setCurrent(null); setError(""); setTarget(team.subdomainId || "");
    setEvidenceId(team.classificationSources[0]?.claimId || ""); setReason("");
    void fetch(base, { signal: controller.signal, credentials: "include" })
      .then(async response => { if (!response.ok) throw Error("分类审核记录暂时不可用"); return response.json() as Promise<{ data: ReviewState }>; })
      .then(value => { if (!controller.signal.aborted) setCurrent(value.data); })
      .catch(cause => { if (!controller.signal.aborted) setError(cause.message || "分类审核记录读取失败"); });
    return () => controller.abort();
  }, [base, team.teamId]);
  const selectedSource = team.classificationSources.find(source => source.claimId === evidenceId);
  const save = async (revert = false) => {
    if (!current || busy) return;
    setBusy(true); setError("");
    try {
      const latest = current.history[0];
      const url = revert ? `${base}/${encodeURIComponent(latest.reviewId)}/revert` : base;
      const body = revert ? { requestId: requestId(), expectedRevision: current.revision, reason: reason.trim() }
        : { requestId: requestId(), expectedRevision: current.revision,
            subdomainId: target || null, evidenceClaimId: evidenceId, sourceVersion, reason: reason.trim() };
      const response = await fetch(url, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const value = await response.json();
      if (!response.ok) throw Error(typeof value.detail === "string" ? value.detail : "分类审核保存失败，请刷新后重试");
      setCurrent(value.data as ReviewState); setTarget(value.data.subdomainId || ""); setReason("");
      await onSaved();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "分类审核保存失败"); }
    finally { setBusy(false); }
  };
  return <section aria-label="团队细分领域复核" className="mt-3 rounded-2xl border border-[#b8d8c6] bg-[#f7fcf8] p-4 shadow-sm">
    <div className="flex items-start justify-between gap-3"><div><p className="text-[11px] font-semibold tracking-[0.1em] text-[#347356]">REVIEW WORKFLOW</p><h4 className="mt-1 text-sm font-semibold text-[#194732]">复核细分领域 · {team.teamName}</h4><p className="mt-1 text-xs text-[#63816f]">{team.institutionName} · 仅有审核权限的账号可提交，操作者和依据会留痕。</p></div>
      <button type="button" onClick={onClose} aria-label="关闭分类复核" className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-[#c9e2d2] text-[#486d56] hover:bg-white"><X className="size-4" /></button></div>
    {error && <p role="alert" className="mt-3 rounded-lg border border-[#edcec3] bg-[#fff8f5] p-2 text-xs text-[#a34f40]">{error}</p>}
    {!current && !error && <p role="status" className="mt-3 flex items-center gap-2 text-xs text-[#63816f]"><LoaderCircle className="size-4 animate-spin" />正在读取审核记录…</p>}
    {current && <div className="mt-4 space-y-3">
      <p className="text-xs leading-5 text-[#5c7868]">当前分类：{subdomains.find(value => value.id === current.subdomainId)?.name || "细分领域待确认"} · 审核修订 {current.revision}</p>
      <label className="block text-xs font-medium text-[#315940]">调整为
        <select value={target} onChange={event => setTarget(event.target.value)} aria-label="目标细分领域" className="mt-1 min-h-11 w-full rounded-lg border border-[#bdd8c7] bg-white px-3 text-sm text-[#204e35]"><option value="">暂不归入细分领域</option>{subdomains.map(value => <option key={value.id} value={value.id}>{value.name}</option>)}</select>
      </label>
      <label className="block text-xs font-medium text-[#315940]">已保存的团队身份／方向原文
        <select value={evidenceId} onChange={event => setEvidenceId(event.target.value)} aria-label="分类依据原文" className="mt-1 min-h-11 w-full rounded-lg border border-[#bdd8c7] bg-white px-3 text-sm text-[#204e35]">{team.classificationSources.map(source => <option key={source.claimId} value={source.claimId}>{source.title}</option>)}</select>
      </label>
      {selectedSource && <a href={selectedSource.url} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-9 items-center gap-1 text-xs font-semibold text-[#18765a] hover:underline">打开选定原文 <ExternalLink className="size-3.5" /></a>}
      <label className="block text-xs font-medium text-[#315940]">审核理由（至少 10 字）
        <textarea value={reason} onChange={event => setReason(event.target.value)} maxLength={2000} rows={3} placeholder="说明原文如何支持或否定该团队归入目标细分领域" aria-label="分类审核理由" className="mt-1 w-full rounded-lg border border-[#bdd8c7] bg-white p-3 text-sm leading-6 text-[#204e35] outline-none focus-visible:border-[#18765a] focus-visible:ring-2 focus-visible:ring-[#18765a]/20" />
      </label>
      <div className="flex flex-wrap gap-2"><button type="button" onClick={() => void save()} disabled={busy || reason.trim().length < 10 || !evidenceId || target === (current.subdomainId || "")}
        className="min-h-11 rounded-xl bg-[#18765a] px-4 text-sm font-semibold text-white hover:bg-[#115e47] disabled:cursor-not-allowed disabled:bg-[#c7d9ce] disabled:text-[#526d5c]">{busy ? "保存中…" : "保存分类审核"}</button>
        {!!current.history.length && <button type="button" onClick={() => void save(true)} disabled={busy || reason.trim().length < 10}
          className="inline-flex min-h-11 items-center gap-1 rounded-xl border border-[#bdd8c7] bg-white px-4 text-sm font-semibold text-[#276b4c] hover:bg-[#edf8f0] disabled:opacity-50"><RotateCcw className="size-3.5" />撤销最近一次审核</button>}</div>
      <p className="text-[11px] leading-5 text-[#6d8472]">提交只改变该团队的细分领域归类，保留 teamId、原始引文和历史研判；来源与审核记录不代表专家签署。</p>
      {!!current.history.length && <details className="rounded-lg border border-[#d6e9dc] bg-white p-3"><summary className="min-h-9 cursor-pointer text-xs font-semibold text-[#315f42]">查看 {current.history.length} 条分类审核记录</summary>
        <ol className="mt-2 space-y-2">{current.history.map(row => <li key={row.reviewId} className="border-t border-[#e1eee4] pt-2 text-xs leading-5 text-[#617768]">第 {row.revision} 次 · {row.createdAt} · 操作者 {row.actorId} · {row.revertOf ? "撤销审核" : "分类调整"}<br />{row.reason}<br /><a href={row.evidenceUrl} target="_blank" rel="noopener noreferrer" className="font-semibold text-[#18765a] underline">查看当时引用原文</a></li>)}</ol></details>}
    </div>}
  </section>;
}
