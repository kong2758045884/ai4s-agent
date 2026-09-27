import { useEffect, useState } from "react";
import { normalizeToolBaseUrlForBrowser } from "@/utils/fileUrl";

type Coverage = {
  dataVersion: string; totalUnits: number; outcomeBackedUnits: number; identityOnlyUnits: number;
  claimCount: number; humanReviewedClaims: number; officialDirectoryUnits: number; modelReviewedUnits: number;
  latestFetchedAt: string | null; notice: string;
  sources: { host: string; url: string; unitCount: number; claimCount: number; outcomeCount: number; latestFetchedAt: string | null }[];
};

export default function SourceCoverage({ domainId = "", subdomainId = "" }: { domainId?: string; subdomainId?: string }) {
  const [data, setData] = useState<Coverage | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setData(null); setError("");
    const params = new URLSearchParams({ domain_id: domainId, subdomain_id: subdomainId });
    void fetch(`${normalizeToolBaseUrlForBrowser("")}/v1/strategic-map/intelligence/source-coverage?${params}`, { signal: controller.signal })
      .then(async response => { if (!response.ok) throw Error("来源覆盖信息暂时不可用"); return response.json() as Promise<Coverage>; })
      .then(value => { if (!controller.signal.aborted) setData(value); })
      .catch(reason => { if (!controller.signal.aborted) setError(reason.message || "来源信息读取失败"); });
    return () => controller.abort();
  }, [domainId, subdomainId, attempt]);
  return <section aria-label="资料来源与覆盖" className="min-w-0 rounded-2xl border border-slate-200 bg-white p-4 sm:p-5">
    <h3 className="font-semibold text-slate-900">团队资料从哪里来</h3>
    <p className="mt-1 text-xs leading-6 text-slate-600">高校、科研院所官网目录及公开成果原文。{domainId ? "下列统计限于当前选择范围。" : "下列统计覆盖全部已收录领域。"}每条来源可打开核对。</p>
    {error ? <p role="alert" className="mt-2 text-sm text-amber-800">{error}。<button className="ml-2 min-h-11 underline" onClick={() => setAttempt(n => n + 1)}>重试来源统计</button></p> : !data ? <p role="status" className="mt-2 text-sm text-slate-600">正在读取已保存来源…</p> : <>
      <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">{[
        ["已收录科研单元", data.totalUnits], ["有成果证据的单元", data.outcomeBackedUnits],
        ["成果尚待补充", data.identityOnlyUnits], ["公开引文", data.claimCount],
      ].map(([label, count]) => <div key={label} className="rounded-xl bg-slate-50 p-3"><dt className="text-xs leading-5 text-slate-600">{label}</dt><dd className="mt-1 text-xl font-semibold text-slate-900">{count}</dd></div>)}</dl>
      <p className="mt-3 text-xs leading-6 text-slate-600">官网目录规则校验 {data.officialDirectoryUnits} 个 · 网页抽取及模型复核 {data.modelReviewedUnits} 个。当前公开引文中 {data.humanReviewedClaims} 条有人工审核记录；具体结论以证据页为准。</p>
      <p className="mt-1 text-xs leading-6 text-slate-600">{data.notice}可推荐数量还取决于本次任务条件，不能用资料总量替代。</p>
      <details className="mt-2"><summary className="min-h-11 cursor-pointer py-3 text-sm font-medium text-blue-700">查看 {data.sources.length} 个来源站点与采集时间</summary>
        <p className="mb-3 break-all text-xs leading-5 text-slate-500">资料版本 {data.dataVersion} · 最近保存的原文采集时间：{data.latestFetchedAt || "未记录"}。此时间不代表全部来源已同步更新；同一单元可能有多个来源，各站点数量不可相加。</p>
        <ul className="max-h-80 space-y-2 overflow-y-auto">{data.sources.map(source => <li key={source.host} className="rounded-lg border border-slate-200 p-3 text-xs leading-6">
          <a href={source.url} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-11 items-center break-all font-medium text-blue-700 underline">{source.host} · 查看已保存原文 ↗</a>
          <p>{source.unitCount} 个科研单元 · {source.claimCount} 条引文，其中成果 {source.outcomeCount} 条</p>
          <p className="break-words text-slate-500">最近采集：{source.latestFetchedAt || "历史资料未记录"}</p>
        </li>)}</ul>
        {!data.sources.length && <p className="text-sm text-slate-600">当前范围尚未收录可展示的来源链接。</p>}
      </details>
    </>}
  </section>;
}
