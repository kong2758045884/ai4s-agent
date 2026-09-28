import { useEffect, useRef } from "react";
import { ArrowRight, BookOpen, Database, FileSearch, FlaskConical, Network, Radio, X } from "lucide-react";

export type DemoDestination = "recommend" | "search" | "pilot" | "graph" | "intelligence";

const workflow = [
  { code: "P01", title: "输入任务或领域" },
  { code: "P02", title: "确认任务条件" },
  { code: "P03 / P04", title: "查看力量与推荐" },
  { code: "P05 / P06", title: "查原文与团队档案" },
  { code: "P07 / P08", title: "比较、保存与跟进" },
  { code: "P09 / P10", title: "补证、观察变化" },
];

export default function FeatureDemo({ onClose, onNavigate }: {
  onClose: () => void;
  onNavigate: (destination: DemoDestination) => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { onClose(); return; }
      if (event.key !== "Tab") return;
      const buttons = dialogRef.current?.querySelectorAll<HTMLButtonElement>("button:not([disabled])");
      if (!buttons?.length) return;
      const first = buttons[0], last = buttons[buttons.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return <div className="fixed inset-0 z-[80] overflow-y-auto bg-[#0d3047]/55 p-2 sm:p-5" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="feature-demo-title" className="mx-auto w-full max-w-[1040px] overflow-hidden rounded-2xl border border-[#cbdde8] bg-[#f6f9fc] shadow-[0_24px_72px_rgba(10,37,55,0.28)]">
      <header className="border-b border-[#d5e2e9] bg-white px-4 py-4 sm:px-7">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0"><p className="text-xs font-semibold tracking-[0.1em] text-[#176587]">AI4S 战略图谱 · 页面导览</p>
            <h2 id="feature-demo-title" className="mt-1 text-xl font-bold text-[#143e57] sm:text-2xl">按老师文档走一遍研判主流程</h2>
            <p className="mt-1 text-xs leading-5 text-[#5d7686] sm:text-sm">按 P01–P14 原型说明页面归属；点击入口进入当前已实现的页面。</p></div>
          <button ref={closeRef} type="button" onClick={onClose} aria-label="关闭页面导览" className="flex size-11 shrink-0 items-center justify-center rounded-lg border border-[#cadce6] text-[#42677a] hover:bg-[#edf5f9] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#176587]"><X className="size-5" /></button>
        </div>
      </header>

      <div className="space-y-5 p-4 sm:p-7">
        <div className="grid gap-3 md:grid-cols-3" aria-label="文档规定的三个主模块">
          <section className="flex min-w-0 flex-col rounded-xl border border-[#b6d5e4] bg-white p-4 shadow-sm">
            <div className="flex items-center gap-2 text-[#145f80]"><FlaskConical className="size-5" aria-hidden="true" /><h3 className="text-base font-bold">研判工作台</h3></div>
            <p className="mt-2 flex-1 text-sm leading-6 text-[#557082]">按任务找团队，或按领域看力量；确认条件后查看推荐、原文、比较与跟进。</p>
            <p className="mt-3 text-xs font-medium text-[#708797]">P01–P09 · P11 关系下钻</p>
            <button type="button" onClick={() => onNavigate("recommend")} className="mt-4 inline-flex min-h-11 items-center justify-between rounded-lg bg-[#176587] px-3.5 text-sm font-semibold text-white hover:bg-[#104d69] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#176587]">打开研判工作台<ArrowRight className="size-4" aria-hidden="true" /></button>
          </section>
          <section className="flex min-w-0 flex-col rounded-xl border border-[#d3e1e9] bg-white p-4 shadow-sm">
            <div className="flex items-center gap-2 text-[#145f80]"><FileSearch className="size-5" aria-hidden="true" /><h3 className="text-base font-bold">团队资料</h3></div>
            <p className="mt-2 flex-1 text-sm leading-6 text-[#557082]">查团队、别名、研究方向和成果引文；档案与当前任务判断共用团队编号。</p>
            <p className="mt-3 text-xs font-medium text-[#708797]">P06 档案 · P12 检索 · P13 授权维护</p>
            <button type="button" onClick={() => onNavigate("search")} className="mt-4 inline-flex min-h-11 items-center justify-between rounded-lg border border-[#9fc5d8] bg-white px-3.5 text-sm font-semibold text-[#176587] hover:bg-[#eef6fa] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#176587]">查看团队资料<ArrowRight className="size-4" aria-hidden="true" /></button>
          </section>
          <section className="flex min-w-0 flex-col rounded-xl border border-[#d3e1e9] bg-white p-4 shadow-sm">
            <div className="flex items-center gap-2 text-[#145f80]"><Radio className="size-5" aria-hidden="true" /><h3 className="text-base font-bold">情报观察</h3></div>
            <p className="mt-2 flex-1 text-sm leading-6 text-[#557082]">看已关注研判的变化、明确方向的日报，以及机构影响力与历史报告。</p>
            <p className="mt-3 text-xs font-medium text-[#708797]">P10 动态与报告</p>
            <button type="button" onClick={() => onNavigate("intelligence")} className="mt-4 inline-flex min-h-11 items-center justify-between rounded-lg border border-[#9fc5d8] bg-white px-3.5 text-sm font-semibold text-[#176587] hover:bg-[#eef6fa] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#176587]">查看情报观察<ArrowRight className="size-4" aria-hidden="true" /></button>
          </section>
        </div>

        <section aria-label="老师文档的研判流程" className="rounded-xl border border-[#d3e1e9] bg-white p-4 sm:p-5">
          <div className="flex items-center gap-2 text-[#164e6a]"><BookOpen className="size-5" aria-hidden="true" /><h3 className="font-bold">一次研判如何形成结论</h3></div>
          <p className="mt-1 text-xs leading-5 text-[#617d8d]">文档要求保持同一研判上下文；新证据核验后形成新版本，旧结论可回看。尚未完成的环节以实施计划为准。</p>
          <ol className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{workflow.map((step, index) => <li key={step.code} className="flex min-w-0 items-start gap-3 rounded-lg border border-[#e0eaf0] bg-[#f8fbfd] p-3"><span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-[#176587] text-xs font-bold text-white">{index + 1}</span><span className="min-w-0"><span className="block text-xs font-semibold text-[#3980a0]">{step.code}</span><span className="block text-sm font-medium text-[#27495d]">{step.title}</span></span></li>)}</ol>
        </section>

        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[#d4e2ea] bg-[#eaf3f8] p-4">
          <p className="max-w-[700px] text-xs leading-6 text-[#506d7e]"><strong className="text-[#254d63]">资料边界：</strong>蛋白质结构与设计试点当前正式收录 16 支，另有 4 支机构官网支持的待复核候选；候选不能算作专家验收。文档中的其余未完成项仍按实施计划推进。</p>
          <div className="flex flex-wrap gap-2"><button type="button" onClick={() => onNavigate("pilot")} className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-[#9fc5d8] bg-white px-3 text-xs font-semibold text-[#176587] hover:bg-[#f5fbff]"><Database className="size-4" />核对试点团队</button><button type="button" onClick={() => onNavigate("graph")} className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-[#9fc5d8] bg-white px-3 text-xs font-semibold text-[#176587] hover:bg-[#f5fbff]"><Network className="size-4" />查看相关关系</button></div>
        </div>
      </div>
    </div>
  </div>;
}
