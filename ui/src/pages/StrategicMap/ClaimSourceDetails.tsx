import type { RecommendationCitation } from "@/services/strategicRecommendations";

const types: Record<string, string> = {
  official_institution: "高校／科研院所官网", official_directory: "官网目录",
  official: "官方页面", publication_or_webpage: "公开论文或网页",
};
const methods: Record<string, string> = {
  "official-directory-citation-rule": "官网目录及引文规则检查",
  "independent-model-and-quote-validation": "独立模型复核及引文检查",
};

/** Render only facts saved in this run. Do not refresh metadata from live records. */
export default function ClaimSourceDetails({ citation }: { citation: RecommendationCitation }) {
  const value = citation.provenance;
  if (!value) return <p className="text-xs leading-6 text-slate-500">原始网页抓取时间、逐条审核记录：此历史快照未保存。保留当时引文，不推断已人工审核。</p>;
  const locator = value.locator;
  const located = ["exact", "whitespace_normalized"].includes(locator.status) && locator.start !== null && locator.end !== null;
  const cells = [
    ["来源标题", value.sourceTitle || "未记录网页标题"],
    ["来源类型", types[value.sourceType] || "公开来源（类型见采集记录）"],
    ["来源发布日期", value.publishedAt || "原文未注明／未记录"],
    ["原始网页抓取时间", value.fetchedAt || "未记录"],
    ["原文位置", located ? `提取正文第 ${locator.start! + 1}–${locator.end} 字符${locator.status === "whitespace_normalized" ? "（忽略空白匹配）" : ""}` : "历史记录未保存可定位的正文"],
  ];
  return <section aria-label="来源与核验记录" className="space-y-3">
    <dl className="grid grid-cols-1 gap-3 rounded-xl bg-slate-50 p-4 text-xs sm:grid-cols-2">{cells.map(([label, text]) => <div key={label} className="min-w-0"><dt className="text-slate-500">{label}</dt><dd className="mt-1 break-words leading-5 text-slate-800">{text}</dd></div>)}</dl>
    <div className="grid gap-2 text-xs sm:grid-cols-3">
      <div className="rounded-lg border border-slate-200 p-3"><h3 className="font-semibold">来源校验</h3><p className="mt-1">{value.sourceCheck.status === "quote_mismatch" ? "引文与保存正文不一致" : "已有规则检查记录"}</p><p className="mt-1 break-words text-slate-500">{methods[value.sourceCheck.method] || value.sourceCheck.method || "检查方法未记录"}</p><p className="mt-1 text-slate-500">{value.sourceCheck.checkedAt || "时间未记录"}</p></div>
      <div className="rounded-lg border border-slate-200 p-3"><h3 className="font-semibold">AI 复核</h3><p className="mt-1">{value.modelReview.status === "reviewed" ? "已有独立复核记录" : "此来源未使用 AI 复核"}</p><p className="mt-1 break-words text-slate-500">{value.modelReview.reviewedAt || "—"}</p></div>
      <div className="rounded-lg border border-slate-200 p-3"><h3 className="font-semibold">人工审核</h3><p className="mt-1">{value.humanReview.status === "reviewed" ? `已记录：${value.humanReview.reviewer}` : "未记录人工审核"}</p><p className="mt-1 text-slate-500">{value.humanReview.reviewedAt || "不等于专家已确认"}</p></div>
    </div>
    <details className="text-xs text-slate-500"><summary className="min-h-11 cursor-pointer py-3">查看保存版本与定位说明</summary><div className="space-y-1 break-all leading-5"><p>采集批次：{value.sourceRunId}</p><p>正文版本：{value.contentHash || "未保存正文摘要值"}</p><p>引文版本：{value.quoteHash}</p><p>AI 复核版本：{value.modelReview.version || "未使用／未记录"}</p><p>位置以采集时提取的正文为准，不是网页页码；网页后续修改不会改变此快照。</p></div></details>
  </section>;
}
