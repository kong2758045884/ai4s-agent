import { useState } from "react";
import { ArrowRight, Search } from "lucide-react";
import type { StrategicDomain } from "@/services/strategicMap";
import { findDomainMatches, type DomainMatch } from "./domainSearch";

type Props = {
  domains: StrategicDomain[];
  selectedDomainId: string;
  onSelect: (match: DomainMatch) => void;
};

export default function DomainQuickSearch({ domains, selectedDomainId, onSelect }: Props) {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const results = findDomainMatches(domains, submitted);
  return <section aria-label="观察领域快捷检索" className="rounded-2xl border border-[#d5e5f1] bg-gradient-to-br from-[#eef7ff] to-white p-4">
    <div className="flex items-center gap-2 text-sm font-semibold text-[#173e5f]"><Search className="size-4 text-[#1768a2]" aria-hidden="true" />按名称或常用说法查找领域</div>
    <p className="mt-1 text-xs leading-5 text-[#607b8d]">例如“蛋白”“量子计算”“算力”；找到后请明确选择观察范围。</p>
    <form className="mt-3 flex flex-col gap-2 sm:flex-row" onSubmit={event => { event.preventDefault(); setSubmitted(query.trim()); }}>
      <input aria-label="查找观察领域" value={query} maxLength={80} onChange={event => { setQuery(event.target.value); setSubmitted(""); }}
        placeholder="输入领域、子领域或常用说法" className="min-h-11 min-w-0 flex-1 rounded-xl border border-[#bfd5e6] bg-white px-3 text-sm text-[#15384f] outline-none placeholder:text-[#8199a9] focus-visible:border-[#1768a2] focus-visible:ring-2 focus-visible:ring-[#1768a2]/20" />
      <button type="submit" disabled={query.trim().length < 2}
        className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-[#1768a2] px-4 text-sm font-semibold text-white hover:bg-[#125886] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#1768a2] disabled:bg-[#dce7ee] disabled:text-[#4a6274]">查找领域 <ArrowRight className="size-4" aria-hidden="true" /></button>
    </form>
    {submitted && <div className="mt-3" role="status">
      <p className="text-xs font-medium text-[#526f84]">{results.length ? "找到 " + results.length + " 个可选范围" : "没有找到对应领域；可在下方手动选择。"}</p>
      {results.length > 0 && <div className="mt-2 grid gap-2 sm:grid-cols-2">{results.map(match => <button key={match.domainId + ":" + match.subdomainId} type="button"
        onClick={() => { onSelect(match); setSubmitted(""); setQuery(match.subdomainName || match.domainName); }}
        className="min-h-11 rounded-xl border border-[#cbdfea] bg-white px-3 py-2 text-left transition hover:border-[#63a5d0] hover:bg-[#f5fbff] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#1768a2]">
        <span className="block text-sm font-semibold text-[#174b70]">{match.domainName}{match.subdomainName ? " / " + match.subdomainName : ""}</span>
        <span className="mt-0.5 block text-[11px] text-[#668196]">{match.matchedBy}{selectedDomainId === match.domainId ? " · 当前已选领域" : ""}</span>
      </button>)}</div>}
    </div>}
  </section>;
}
