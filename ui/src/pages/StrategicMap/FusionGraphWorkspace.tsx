import { useState } from "react";
import GraphWorkspace from "./GraphWorkspace";
import type { ComponentProps } from "react";

export default function FusionGraphWorkspace(props: ComponentProps<typeof GraphWorkspace>) {
  const [view, setView] = useState<"fusion" | "teams">("fusion");
  return <div className="flex min-h-[600px] min-w-0 flex-1 flex-col gap-3 lg:min-h-0">
    <header className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white p-4">
      <div><h2 className="text-lg font-semibold leading-7 text-slate-900 sm:text-xl">领域知识与团队成果图谱</h2>
        <p className="mt-1 text-xs leading-5 text-slate-500">领域知识连接机构、作者与事件；国内团队及成果来自已核实团队库。</p></div>
      <div className="flex rounded-lg bg-slate-100 p-1" aria-label="图谱数据范围">
        {([{ id: "fusion", label: "融合知识图谱" }, { id: "teams", label: "已核实团队证据" }] as const).map((item) =>
          <button key={item.id} aria-pressed={view === item.id} onClick={() => setView(item.id)} className={`rounded-md px-3 py-2 text-xs font-medium ${view === item.id ? "bg-white text-blue-700 shadow-sm" : "text-slate-500"}`}>{item.label}</button>)}
      </div>
    </header>
    <GraphWorkspace key={view} {...props} verifiedOnly={view === "teams"} integrated={view === "fusion"} />
  </div>;
}
