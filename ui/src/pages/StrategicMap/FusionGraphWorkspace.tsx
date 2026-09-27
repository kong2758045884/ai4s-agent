import { useEffect, useState } from "react";
import GraphWorkspace from "./GraphWorkspace";
import type { ComponentProps } from "react";
import { assessmentApi } from "@/services/strategicAssessments";
import type { StrategicGraphData } from "@/services/strategicMap";

type Props = ComponentProps<typeof GraphWorkspace> & { taskId?: string; runId?: string; onFocusTeam?: (id: string) => void };

export default function FusionGraphWorkspace(props: Props) {
  if (props.taskId && props.runId) return <AssessmentGraph key={`${props.taskId}/${props.runId}`} {...props} taskId={props.taskId} runId={props.runId} />;
  return <LiveGraph {...props} />;
}

function AssessmentGraph(props: Props & { taskId: string; runId: string }) {
  const [data, setData] = useState<StrategicGraphData | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [live, setLive] = useState(false);
  const [allTeams, setAllTeams] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setError(""); setData(null);
    void assessmentApi.graph(props.taskId, props.runId, controller.signal).then(value => {
      if (!controller.signal.aborted) setData(value);
    }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "快照读取失败"); });
    return () => controller.abort();
  }, [props.taskId, props.runId, attempt]);
  const teams = data?.nodes.filter(n => n.type === "科研团队") || [];
  const focus = props.focusNodeId || teams[0]?.id;
  const snapshot = data?.meta.snapshot;
  return <div className="flex min-h-[600px] min-w-0 flex-1 flex-col gap-3 lg:min-h-0">
    <header className="space-y-2 rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-lg font-semibold text-slate-900">{live ? "领域探索 · 当前资料" : "当前研判的关系快照"}</h2>
        <button className="min-h-11 rounded-lg border border-slate-200 px-3 text-sm text-blue-700" onClick={() => setLive(v => !v)}>{live ? "返回研判关系快照" : "切换至领域当前资料"}</button></div>
      {live ? <p className="text-xs text-slate-500">当前探索范围：{props.subdomainName || props.domainName}。此处为当前资料，可能与已保存研判不同；可在领域导航切换，上方返回原版本。</p> : <>
        <p className="text-sm font-medium text-slate-700">研判范围：{snapshot?.scopeLabel || "按本次保存的条件"} · 中国内地</p>
        <p className="break-words text-xs leading-5 text-slate-500">输入第 {snapshot?.inputVersion || "—"} 版 · {snapshot?.frozenAt ? new Date(snapshot.frozenAt).toLocaleString("zh-CN") : "读取中"} · {snapshot?.notice}</p>
        <p className="text-xs leading-5 text-slate-500">默认展示团队直接关联及这些节点之间的依据。任务与能力要求是需求；匹配关系不是合作承诺。虚线表示条件支持、缺少依据或尚未全面排查。</p>
        {teams.length > 0 && <div className="flex flex-wrap items-center gap-2">
          <label className="flex min-w-0 max-w-full flex-wrap items-center gap-2 text-xs text-slate-700">关系中心
            <select aria-label="研判图谱关系中心" className="min-h-11 min-w-0 max-w-full rounded-lg border border-slate-200 px-2" value={focus} disabled={allTeams}
              onChange={e => props.onFocusTeam?.(String(data?.nodes.find(n => n.id === e.target.value)?.data?.raw?.teamId || ""))}>
              {focus && !teams.some(n => n.id === focus) && <option value={focus}>本版本未保存该团队</option>}
              {teams.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
            </select>
          </label>
          <button className="min-h-11 rounded-lg border border-slate-200 px-3 text-xs text-blue-700" onClick={() => setAllTeams(v => !v)}>{allTeams ? "只看当前团队" : "展开本次研判全部团队"}</button>
        </div>}
        <p className="text-xs text-slate-500">{snapshot?.limitations}</p>
        <p className="text-xs text-slate-500">领域导航用于探索当前资料，不改变这份研判快照。</p>
      </>}
    </header>
    {live ? <LiveGraph {...props} focusNodeId={undefined} /> : error ? <div role="alert" className="rounded-xl border border-amber-200 bg-white p-6 text-sm text-slate-700">
      <p>研判快照加载失败：{error}</p><button className="mt-3 min-h-11 rounded-lg border px-4" onClick={() => setAttempt(n => n + 1)}>重试读取快照</button>
    </div> : data ? <GraphWorkspace {...props} frozenData={data} focusNodeId={allTeams ? undefined : focus} verifiedOnly integrated={false} /> : <p role="status" className="p-6 text-sm text-slate-500">正在读取保存的研判图谱…</p>}
  </div>;
}

function LiveGraph(props: ComponentProps<typeof GraphWorkspace>) {
  const [view, setView] = useState<"fusion" | "teams">(props.focusNodeId ? "teams" : "fusion");
  const [wholeDomain, setWholeDomain] = useState(false);
  return <div className="flex min-h-[600px] min-w-0 flex-1 flex-col gap-3 lg:min-h-0">
    <header className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white p-4">
      <div><h2 className="text-lg font-semibold leading-7 text-slate-900 sm:text-xl">{props.focusNodeId && !wholeDomain ? "当前团队的直接关联" : "领域知识与团队成果图谱"}</h2>
        <p className="mt-1 text-xs leading-5 text-slate-500">{props.focusNodeId && !wholeDomain ? "仅展示当前团队一跳内的机构、方向与成果依据；不会因同属机构推定团队之间的合作。" : "领域知识连接机构、作者与事件；科研单元及成果来自有来源的团队资料。"}</p></div>
      {props.focusNodeId && <button className="min-h-11 rounded-lg border border-slate-200 px-3 text-sm text-blue-700" onClick={() => setWholeDomain(value => !value)}>{wholeDomain ? "返回当前团队关系" : "切换至全领域探索"}</button>}
      {(!props.focusNodeId || wholeDomain) &&
      <div className="flex rounded-lg bg-slate-100 p-1" aria-label="图谱数据范围">
        {([{ id: "fusion", label: "融合知识图谱" }, { id: "teams", label: "已核实团队证据" }] as const).map((item) =>
          <button key={item.id} aria-pressed={view === item.id} onClick={() => setView(item.id)} className={`rounded-md px-3 py-2 text-xs font-medium ${view === item.id ? "bg-white text-blue-700 shadow-sm" : "text-slate-500"}`}>{item.label}</button>)}
      </div>}
    </header>
    <GraphWorkspace key={`${view}-${wholeDomain}`} {...props} focusNodeId={wholeDomain ? undefined : props.focusNodeId} verifiedOnly={view === "teams"} integrated={view === "fusion"} />
  </div>;
}
