import { useEffect, useRef } from "react";
import { ArrowRight, BookOpen, Database, FileSearch, FlaskConical, Network, Radio, X } from "lucide-react";

export type DemoDestination = "recommend" | "search" | "pilot" | "graph" | "intelligence";

const workflow = [
  "输入任务或选择领域",
  "核对研判条件",
  "查看团队与推荐依据",
  "查阅原文和团队档案",
  "比较团队并记录跟进",
  "补充资料、观察变化",
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
          <div className="min-w-0"><p className="text-xs font-semibold tracking-[0.1em] text-[#176587]">AI4S 战略图谱</p>
            <h2 id="feature-demo-title" className="mt-1 text-xl font-bold text-[#143e57] sm:text-2xl">从科研任务到团队研判</h2>
            <p className="mt-1 text-xs leading-5 text-[#5d7686] sm:text-sm">以任务或领域为起点，核对团队资料和成果依据，保存可回看的研判记录。</p></div>
          <button ref={closeRef} type="button" onClick={onClose} aria-label="关闭页面导览" className="flex size-11 shrink-0 items-center justify-center rounded-lg border border-[#cadce6] text-[#42677a] hover:bg-[#edf5f9] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#176587]"><X className="size-5" /></button>
        </div>
      </header>

      <div className="space-y-5 p-4 sm:p-7">
        <div className="grid gap-3 md:grid-cols-3" aria-label="主要功能">
          <section className="flex min-w-0 flex-col rounded-xl border border-[#b6d5e4] bg-white p-4 shadow-sm">
            <div className="flex items-center gap-2 text-[#145f80]"><FlaskConical className="size-5" aria-hidden="true" /><h3 className="text-base font-bold">研判工作台</h3></div>
            <p className="mt-2 flex-1 text-sm leading-6 text-[#557082]">按任务找团队，或按领域看力量；确认条件后查看推荐、原文、比较与跟进。</p>
            <p className="mt-3 text-xs font-medium text-[#708797]">任务输入 · 证据核对 · 团队比较</p>
            <button type="button" onClick={() => onNavigate("recommend")} className="mt-4 inline-flex min-h-11 items-center justify-between rounded-lg bg-[#176587] px-3.5 text-sm font-semibold text-white hover:bg-[#104d69] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#176587]">打开研判工作台<ArrowRight className="size-4" aria-hidden="true" /></button>
          </section>
          <section className="flex min-w-0 flex-col rounded-xl border border-[#d3e1e9] bg-white p-4 shadow-sm">
            <div className="flex items-center gap-2 text-[#145f80]"><FileSearch className="size-5" aria-hidden="true" /><h3 className="text-base font-bold">团队资料</h3></div>
            <p className="mt-2 flex-1 text-sm leading-6 text-[#557082]">查团队、别名、研究方向和成果引文；档案与当前任务判断共用团队编号。</p>
            <p className="mt-3 text-xs font-medium text-[#708797]">团队档案 · 资料检索 · 来源查看</p>
            <button type="button" onClick={() => onNavigate("search")} className="mt-4 inline-flex min-h-11 items-center justify-between rounded-lg border border-[#9fc5d8] bg-white px-3.5 text-sm font-semibold text-[#176587] hover:bg-[#eef6fa] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#176587]">查看团队资料<ArrowRight className="size-4" aria-hidden="true" /></button>
          </section>
          <section className="flex min-w-0 flex-col rounded-xl border border-[#d3e1e9] bg-white p-4 shadow-sm">
            <div className="flex items-center gap-2 text-[#145f80]"><Radio className="size-5" aria-hidden="true" /><h3 className="text-base font-bold">情报观察</h3></div>
            <p className="mt-2 flex-1 text-sm leading-6 text-[#557082]">看已关注研判的变化、明确方向的日报，以及机构影响力与历史报告。</p>
            <p className="mt-3 text-xs font-medium text-[#708797]">关注变化 · 日报 · 历史报告</p>
            <button type="button" onClick={() => onNavigate("intelligence")} className="mt-4 inline-flex min-h-11 items-center justify-between rounded-lg border border-[#9fc5d8] bg-white px-3.5 text-sm font-semibold text-[#176587] hover:bg-[#eef6fa] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#176587]">查看情报观察<ArrowRight className="size-4" aria-hidden="true" /></button>
          </section>
        </div>

        <section aria-label="研判流程" className="rounded-xl border border-[#d3e1e9] bg-white p-4 sm:p-5">
          <div className="flex items-center gap-2 text-[#164e6a]"><BookOpen className="size-5" aria-hidden="true" /><h3 className="font-bold">一次研判如何形成结论</h3></div>
          <p className="mt-1 text-xs leading-5 text-[#617d8d]">沿同一任务查看条件、推荐与依据；保存后可回看记录，并比较后续版本的变化。</p>
          <ol className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{workflow.map((step, index) => <li key={step} className="flex min-w-0 items-center gap-3 rounded-lg border border-[#e0eaf0] bg-[#f8fbfd] p-3"><span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-[#176587] text-xs font-bold text-white">{index + 1}</span><span className="min-w-0 text-sm font-medium text-[#27495d]">{step}</span></li>)}</ol>
        </section>

        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[#d4e2ea] bg-[#eaf3f8] p-4">
          <p className="max-w-[700px] text-xs leading-6 text-[#506d7e]">从蛋白质结构与设计方向开始，可以查看团队收录范围、成果依据和待复核资料。具体数量与核验状态以团队资料页实时显示为准。</p>
          <div className="flex flex-wrap gap-2"><button type="button" onClick={() => onNavigate("pilot")} className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-[#9fc5d8] bg-white px-3 text-xs font-semibold text-[#176587] hover:bg-[#f5fbff]"><Database className="size-4" />查看领域团队</button><button type="button" onClick={() => onNavigate("graph")} className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-[#9fc5d8] bg-white px-3 text-xs font-semibold text-[#176587] hover:bg-[#f5fbff]"><Network className="size-4" />查看关联关系</button></div>
        </div>
      </div>
    </div>
  </div>;
}
