import { useEffect, useState } from "react";
import { ArrowRight, BookOpenCheck, Database, FlaskConical, Network, Search, Sparkles, X } from "lucide-react";

export type DemoDestination = "recommend" | "search" | "pilot" | "graph" | "intelligence";

const features: { number: string; title: string; detail: string; destination: DemoDestination; action: string; icon: typeof Search; tone: string }[] = [
  { number: "01", title: "描述科研任务", detail: "从目标和必要条件出发，查看逐条解析、推荐理由与缺口。", destination: "recommend", action: "打开研判工作台", icon: FlaskConical, tone: "bg-[#e7eefc] text-[#355eaf]" },
  { number: "02", title: "搜索公开证据", detail: "按团队、成果、时间和来源状态检索，直达原文与团队档案。", destination: "search", action: "体验证据搜索", icon: Search, tone: "bg-[#e2f4f1] text-[#18776b]" },
  { number: "03", title: "核对蛋白方向试点", detail: "查看已收录团队和 4 支机构官网支持的待复核候选，逐支追溯依据。", destination: "pilot", action: "查看试点清单", icon: BookOpenCheck, tone: "bg-[#fff0d7] text-[#956117]" },
  { number: "04", title: "比较与保存研判", detail: "按必要条件比较多支团队，保存证据版本与未确认事项。", destination: "recommend", action: "体验比较流程", icon: Database, tone: "bg-[#e9e9fa] text-[#6355ad]" },
  { number: "05", title: "探索关系图谱", detail: "查看团队、证据和任务之间的关系，沿来源继续核查。", destination: "graph", action: "打开关系图谱", icon: Network, tone: "bg-[#e6f2fb] text-[#276d9a]" },
  { number: "06", title: "观察情报变化", detail: "追踪已保存研判的变化、调查进度与证据缺口。", destination: "intelligence", action: "打开情报观察", icon: Sparkles, tone: "bg-[#f5eafa] text-[#8a4c9b]" },
];

export default function FeatureDemo({ onClose, onNavigate }: { onClose: () => void; onNavigate: (destination: DemoDestination) => void }) {
  const [active, setActive] = useState(0);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);
  const feature = features[active];
  return <div className="fixed inset-0 z-[80] overflow-y-auto bg-[#102837]/70 p-2 backdrop-blur-[6px] sm:p-5" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div role="dialog" aria-modal="true" aria-labelledby="feature-demo-title" className="mx-auto min-h-[min(740px,95vh)] w-full max-w-[1100px] overflow-hidden rounded-[26px] bg-[#f8fbfc] shadow-[0_35px_100px_rgba(3,20,30,0.4)]">
      <div className="relative overflow-hidden bg-[radial-gradient(circle_at_80%_20%,#2b6570_0%,transparent_45%),linear-gradient(120deg,#122a3a,#193c48)] px-5 pb-8 pt-6 text-white sm:px-9 sm:pb-10 sm:pt-8">
        <div className="absolute -right-10 -top-20 size-64 rounded-full border border-white/10" aria-hidden="true" />
        <div className="relative flex items-start justify-between gap-3"><span className="inline-flex items-center gap-2 rounded-full border border-white/25 bg-white/10 px-3 py-1.5 text-xs font-semibold tracking-[0.08em]"><Sparkles className="size-3.5" />AI4S · PRODUCT TOUR</span><button type="button" onClick={onClose} aria-label="关闭功能演示" className="flex size-10 shrink-0 items-center justify-center rounded-xl border border-white/25 bg-white/10 hover:bg-white/20 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white"><X className="size-5" /></button></div>
        <h2 id="feature-demo-title" className="relative mt-5 max-w-[760px] text-3xl font-bold leading-tight tracking-tight sm:text-[42px]">从科研问题，到有依据的团队研判</h2>
        <p className="relative mt-3 max-w-[700px] text-sm leading-7 text-[#d7e8e9] sm:text-base">六个可点击的入口，带你走完整条核心路径。试点候选与专家签署状态会按真实资料显示，演示不预填科研结论。</p>
        <div className="relative mt-5 flex flex-wrap gap-2 text-xs text-[#d3e8e7]"><span className="rounded-full bg-white/10 px-3 py-1.5">任务解析与推荐</span><span className="rounded-full bg-white/10 px-3 py-1.5">来源可追溯</span><span className="rounded-full bg-white/10 px-3 py-1.5">历史与变化</span></div>
      </div>
      <div className="grid gap-5 p-4 sm:p-7 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="grid gap-2 sm:grid-cols-2">{features.map((item, index) => <button key={item.number} type="button" onClick={() => setActive(index)} aria-pressed={active === index} className={`group min-w-0 rounded-2xl border p-4 text-left transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#287c83] ${active === index ? "border-[#4c9e9e] bg-white shadow-[0_10px_25px_-18px_#1b5d63]" : "border-[#e0e9ea] bg-white/75 hover:border-[#9ac9c9] hover:bg-white"}`}>
          <div className="flex items-start justify-between gap-2"><span className={`flex size-10 items-center justify-center rounded-xl ${item.tone}`}><item.icon className="size-5" aria-hidden="true" /></span><span className="text-xs font-bold text-[#91a3a8]">{item.number} / 06</span></div>
          <h3 className="mt-3 text-sm font-bold text-[#173c45] sm:text-base">{item.title}</h3><p className="mt-1.5 text-xs leading-5 text-[#637b82]">{item.detail}</p>
        </button>)}</div>
        <aside className="flex flex-col rounded-2xl border border-[#cce2e2] bg-[#e9f5f3] p-5 lg:sticky lg:top-0">
          <span className="text-xs font-bold tracking-[0.16em] text-[#28736e]">当前演示 · {feature.number} / 06</span>
          <span className={`mt-6 flex size-14 items-center justify-center rounded-2xl ${feature.tone}`}><feature.icon className="size-7" aria-hidden="true" /></span>
          <h3 className="mt-5 text-2xl font-bold text-[#163d42]">{feature.title}</h3><p className="mt-3 flex-1 text-sm leading-7 text-[#527078]">{feature.detail}</p>
          <button type="button" onClick={() => onNavigate(feature.destination)} className="mt-7 inline-flex min-h-12 w-full items-center justify-between rounded-xl bg-[#176d70] px-4 text-sm font-bold text-white shadow-[0_8px_20px_-12px_#176d70] hover:bg-[#10585b] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#176d70]">{feature.action}<ArrowRight className="size-4" aria-hidden="true" /></button>
          <p className="mt-3 text-xs leading-5 text-[#68848a]">将进入实际功能页面；部分数据仍等待人工核验。</p>
        </aside>
      </div>
    </div>
  </div>;
}
