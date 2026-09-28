import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, BookOpenText, Check, Download, ExternalLink, FolderOpen, LoaderCircle, Network, Plus, Save, X } from "lucide-react";
import { assessmentApi, requestId, type Assessment, type AssessmentRun, type AssessmentScope, type AssessmentSummary, type Criterion, type FollowUp, type Interpretation } from "@/services/strategicAssessments";
import type { StrategicDomain } from "@/services/strategicMap";
import type { RecommendationCitation } from "@/services/strategicRecommendations";
import AssessmentInvestigation from "./AssessmentInvestigation";
import ClaimSourceDetails from "./ClaimSourceDetails";
import ClaimReviewEditor from "./ClaimReviewEditor";
import { primaryButton as primary, secondaryButton as button } from "./controls";
import CombinationCoverage from "./CombinationCoverage";
import DomainQuickSearch from "./DomainQuickSearch";
import { shortageStages, suggestedRole } from "./assessmentPresentation";

type Props = { domains: StrategicDomain[]; catalogueReady: boolean; taskId: string; runId: string;
  onContextChange: (taskId: string, runId: string) => void; onOpenTeam: (id: string) => void;
  onOpenRelations: (teamId: string) => void };
const AUTO: AssessmentScope = { mode: "auto", domainIds: [], domesticOnly: true };
const input = "min-h-11 w-full min-w-0 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100";
const panel = "rounded-2xl border border-slate-200 bg-white p-4 sm:p-6";
const labels = { goal: "研究目标", capability: "必要能力", outcome: "成果要求", constraint: "明确限制", preference: "偏好", exclusion: "排除条件", organization: "组织方式", unresolved: "含义待确认" };
const necessityLabels = { required: "必要", preferred: "优先", excluded: "排除", informational: "仅说明" };
const statuses = { supported: "有依据", insufficient: "依据不足", conditional: "条件性支持", not_met: "不满足", not_observed: "未观察到排除项" };

export default function AssessmentWorkbench({ domains, catalogueReady, taskId, runId, onContextChange, onOpenTeam, onOpenRelations }: Props) {
  const [record, setRecord] = useState<Assessment | null>(null);
  const recordRef = useRef<Assessment | null>(null);
  const [run, setRun] = useState<AssessmentRun | null>(null);
  const [list, setList] = useState<AssessmentSummary[]>([]);
  const [listTotal, setListTotal] = useState(0);
  const [listPage, setListPage] = useState(1);
  const [tab, setTab] = useState<"new" | "mine">("new");
  const [mode, setMode] = useState<"task" | "domain">("task");
  const [taskDraft, setTaskDraft] = useState("");
  const [domainDraft, setDomainDraft] = useState("");
  const [taskScope, setTaskScope] = useState<AssessmentScope>(AUTO);
  const [domainScope, setDomainScope] = useState<AssessmentScope>({ ...AUTO, mode: "selected" });
  const [limit, setLimit] = useState(5);
  const [windowDays, setWindowDays] = useState(90);
  const [interpretation, setInterpretation] = useState<Interpretation | null>(null);
  const [scopeResolution, setScopeResolution] = useState<string | null>(null);
  const [step, setStep] = useState<"input" | "confirm" | "results">("input");
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [error, setError] = useState("");
  const [saveStatus, setSaveStatus] = useState("尚未保存");
  const [evidence, setEvidence] = useState<{ citation: RecommendationCitation; team: string; requirement?: string; support?: string } | null>(null);
  const evidenceDialog = useRef<HTMLDialogElement>(null);
  const [followDraft, setFollowDraft] = useState<FollowUp | null>(null);
  const followDialog = useRef<HTMLDialogElement>(null);
  const [notes, setNotes] = useState("");
  const [roles, setRoles] = useState<Assessment["state"]["combination"]>([]);
  const sequence = useRef(0);
  const newRequest = useRef(requestId());
  const confirmRequest = useRef(requestId());
  const lastSavedDraft = useRef("");
  const failedDraft = useRef("");
  const loadedContext = useRef("");
  const scopeName = (scope: AssessmentScope) => scope.domainIds.length
    ? scope.domainIds.map((id, index) => scope.domainNames?.[index] || domains.find(d => d.id === id)?.name || "领域名称加载中").join("、") + (scope.subdomainId ? ` / ${scope.subdomainName || domains.flatMap(d => d.subdomains).find(s => s.id === scope.subdomainId)?.name || "子领域名称加载中"}` : "")
    : "全部领域（未限制）";
  const historical = !!run && !!record && record.state.activeRunId !== run.runId;
  const draftFields = () => ({ taskDraft, domainDraft, taskScope, ...(domainScope.domainIds.length ? { domainScope } : {}), requestedLimit: limit, windowDays, mode });

  function adopt(next: Assessment, hydrate = false) {
    recordRef.current = next; setRecord(next); setNotes(next.state.internalNotes); setRoles(next.state.combination);
    if (hydrate) {
      const nextTaskScope = next.state.taskScope || (next.run?.mode !== "domain" ? next.run?.scope : undefined) || AUTO;
      const nextDomainScope = next.state.domainScope || (next.run?.mode === "domain" ? next.run.scope : undefined) || { ...AUTO, mode: "selected" as const };
      setTaskDraft(next.state.taskDraft); setDomainDraft(next.state.domainDraft); setMode(next.mode);
      setTaskScope(nextTaskScope); setDomainScope(nextDomainScope); setLimit(next.state.requestedLimit || next.run?.requestedLimit || 5);
      setWindowDays(next.state.windowDays || next.run?.observation?.windowDays || 90);
      lastSavedDraft.current = JSON.stringify({ taskDraft: next.state.taskDraft, domainDraft: next.state.domainDraft,
        taskScope: nextTaskScope, ...(nextDomainScope.domainIds.length ? { domainScope: nextDomainScope } : {}),
        requestedLimit: next.state.requestedLimit || next.run?.requestedLimit || 5,
        windowDays: next.state.windowDays || next.run?.observation?.windowDays || 90, mode: next.mode });
    }
    setSaveStatus("已保存");
  }

  async function refreshList(page = listPage) {
    try { const value = await assessmentApi.list(page); setList(value.items); setListTotal(value.total); }
    catch (reason) { setError(String(reason instanceof Error ? reason.message : reason)); }
  }
  useEffect(() => { void refreshList(listPage); }, [listPage]);

  useEffect(() => {
    const key = `${taskId}:${runId}`;
    if (!taskId || loadedContext.current === key) return;
    const controller = new AbortController(); const seq = ++sequence.current;
    setBusy(true); busyRef.current = true; setError("");
    void assessmentApi.get(taskId, controller.signal).then(async value => {
      const valueRun = runId && value.run?.runId !== runId ? await assessmentApi.run(taskId, runId) : value.run;
      if (seq !== sequence.current) return;
      loadedContext.current = key; adopt(value, true); setRun(valueRun || null); setStep(valueRun ? "results" : "input"); setTab("new");
      if (valueRun?.runId !== value.state.activeRunId) setRoles(valueRun?.selection?.combination || []);
    }).catch(reason => { if (!controller.signal.aborted) setError(String(reason.message || reason)); })
      .finally(() => { if (seq === sequence.current) { setBusy(false); busyRef.current = false; } });
    return () => { controller.abort(); sequence.current++; };
  }, [taskId, runId]);

  async function operation(fn: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setError("");
    try { await fn(); }
    catch (reason) { setError(String(reason instanceof Error ? reason.message : reason)); setSaveStatus("保存失败，草稿保留在当前页面"); }
    finally { busyRef.current = false; setBusy(false); }
  }

  async function ensureRecord() {
    if (recordRef.current) return recordRef.current;
    const value = await assessmentApi.create({ requestId: newRequest.current, mode, taskDraft, domainDraft,
      title: (mode === "task" ? taskDraft : domainDraft || scopeName(domainScope)).slice(0, 80) || "新建研判" });
    adopt(value); loadedContext.current = `${value.taskId}:`; onContextChange(value.taskId, "");
    return value;
  }

  async function saveDraft() {
    const current = await ensureRecord(); const fields = draftFields();
    try {
      const value = await assessmentApi.patch(current.taskId, current.revision, fields);
      adopt(value); lastSavedDraft.current = JSON.stringify(fields); failedDraft.current = "";
      return value;
    } catch (error) { failedDraft.current = JSON.stringify(fields); throw error; }
  }
  const draftKey = JSON.stringify(draftFields());
  useEffect(() => {
    if (busy || historical || step !== "input" || lastSavedDraft.current === draftKey || failedDraft.current === draftKey ||
      (!record && !taskDraft.trim() && !domainDraft.trim())) return;
    setSaveStatus("草稿待保存");
    const timer = setTimeout(() => { void operation(async () => { await saveDraft(); }); }, 800);
    return () => clearTimeout(timer);
  }, [draftKey, busy, record?.taskId, historical, step]);

  function showResult(next: Assessment) {
    adopt(next); setRun(next.run || null); setStep("results"); setInterpretation(null);
    loadedContext.current = `${next.taskId}:${next.run?.runId || ""}`;
    onContextChange(next.taskId, next.run?.runId || "");
    void refreshList();
  }
  async function start() {
    await operation(async () => {
      const current = await saveDraft();
      if (mode === "domain") {
        const next = await assessmentApi.observe(current.taskId, { expectedRevision: current.revision, scope: domainScope, limit, windowDays, requestId: confirmRequest.current });
        showResult(next); confirmRequest.current = requestId();
      } else {
        const value = await assessmentApi.interpret(taskDraft.trim(), taskScope);
        setInterpretation(value); setScopeResolution(null); setStep("confirm"); confirmRequest.current = requestId();
      }
    });
  }
  async function confirm() {
    if (!interpretation || !recordRef.current) return;
    await operation(async () => {
      const current = recordRef.current!;
      const next = await assessmentApi.confirm(current.taskId, { expectedRevision: current.revision, taskText: interpretation.taskText,
        scope: interpretation.scope, criteria: interpretation.criteria, evidenceVersion: interpretation.evidenceVersion,
        scopeResolution, limit, requestId: confirmRequest.current });
      showResult(next); confirmRequest.current = requestId();
    });
  }
  function editCriterion(index: number, change: Partial<Criterion>) {
    setInterpretation(value => value ? { ...value, criteria: value.criteria.map((c, i) => i === index ? { ...c, ...change } : c) } : null);
    confirmRequest.current = requestId();
  }
  async function patchState(changes: Partial<Assessment["state"]>) {
    await operation(async () => { if (!recordRef.current) return;
      adopt(await assessmentApi.patch(recordRef.current.taskId, recordRef.current.revision, changes)); });
  }
  function fresh() {
    if (busy) return;
    sequence.current++; recordRef.current = null; setRecord(null); setRun(null); setTaskDraft(""); setDomainDraft("");
    setMode("task"); setTaskScope(AUTO); setDomainScope({ ...AUTO, mode: "selected" }); setStep("input"); setTab("new");
    setInterpretation(null); setLimit(5); setWindowDays(90); setError(""); setSaveStatus("尚未保存"); loadedContext.current = ":";
    lastSavedDraft.current = ""; failedDraft.current = "";
    newRequest.current = requestId(); confirmRequest.current = requestId(); onContextChange("", "");
  }
  function openEvidence(citation: RecommendationCitation, team: string, requirement?: string, support?: string) { setEvidence({ citation, team, requirement, support }); evidenceDialog.current?.showModal(); }
  function openFollowUp(teamId: string, claimIds: string[]) {
    setFollowDraft({ id: requestId(), teamId, claimIds, question: "", method: "原文核对并联系团队确认", owner: "", dueDate: null, result: "", status: "open" });
    followDialog.current?.showModal();
  }
  const comparedIds = historical ? run?.selection?.comparedTeamIds || [] : record?.state.comparedTeamIds || [];
  const compared = run?.items.filter(item => comparedIds.includes(item.teamId)) || [];

  function scopePicker(value: AssessmentScope, change: (v: AssessmentScope) => void, allowAuto: boolean) {
    return <fieldset className="min-w-0 space-y-3"><legend className="mb-2 text-sm font-medium">{allowAuto ? "检索范围" : "观察领域"}</legend>
      {allowAuto && <select className={input} aria-label="范围选择方式" value={value.mode} onChange={e => change({ ...value, mode: e.target.value as AssessmentScope["mode"], domainIds: [], subdomainId: null })}>
        <option value="auto">根据任务建议领域，生成前确认</option><option value="selected">手动选择领域（可多选）</option></select>}
      {(value.mode === "selected" || !allowAuto) && <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{domains.map(domain => <label key={domain.id} className="flex min-h-11 items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm">
        <input type={allowAuto ? "checkbox" : "radio"} name={allowAuto ? "task-domains" : "observe-domain"} checked={value.domainIds.includes(domain.id)} onChange={() => change({ ...value, mode: "selected", subdomainId: null,
          domainIds: allowAuto ? value.domainIds.includes(domain.id) ? value.domainIds.filter(id => id !== domain.id) : [...value.domainIds, domain.id] : [domain.id] })} />{domain.name}</label>)}</div>}
      {value.domainIds.length === 1 && <select className={input} aria-label="研判子领域" value={value.subdomainId || ""} onChange={e => change({ ...value, subdomainId: e.target.value || null })}>
        <option value="">全部子领域</option>{domains.find(d => d.id === value.domainIds[0])?.subdomains.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select>}
      <p className="text-xs leading-5 text-slate-500">仅中国内地科研机构及具体科研单元。普通解析与推荐使用已有资料。</p>
    </fieldset>;
  }

  return <main className="assessment-workbench min-w-0 flex-1 space-y-4 overflow-y-auto pb-5 lg:min-h-0" aria-label="研判工作台">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <nav className="flex gap-1" aria-label="研判入口"><button className={button} onClick={fresh} disabled={busy}><Plus className="size-4" />新建研判</button>
        <button className={button} onClick={() => { setTab(tab === "mine" ? "new" : "mine"); void refreshList(); }}><FolderOpen className="size-4" />我的研判</button></nav>
      <span role="status" className="text-xs text-slate-500">{busy ? "处理中…" : saveStatus}</span>
    </div>
    {error && <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700"><p>{error}</p>
      {record && <button className={`${button} mt-2`} onClick={() => void operation(async () => {
        const value = await assessmentApi.get(record.taskId); adopt(value, true); setRun(value.run || null); setError("");
      })}>重新载入已保存版本</button>}</div>}
    {tab === "mine" ? <section className={panel}><h2 className="text-xl font-semibold">我的研判</h2><p className="mt-1 text-sm text-slate-500">当前浏览器访客可访问的记录；链接按同一访客身份读取。</p>
      <div className="mt-4 space-y-2">{list.map(value => <button key={value.taskId} className="flex min-h-16 w-full flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-200 p-4 text-left hover:bg-slate-50"
        onClick={() => { setTab("new"); loadedContext.current = ""; onContextChange(value.taskId, ""); }}><span className="min-w-0 break-words font-medium">{value.title}</span><span className="text-xs text-slate-500">{value.mode === "task" ? "任务选队" : "领域观察"} · {new Date(value.updatedAt).toLocaleString("zh-CN")}</span></button>)}
        {!list.length && <p className="py-8 text-center text-slate-500">还没有保存的研判。从新建研判开始。</p>}</div>
      <div className="mt-4 flex items-center gap-3"><button className={button} disabled={listPage === 1} onClick={() => setListPage(p => p - 1)}>上一页</button><span className="text-xs">共 {listTotal} 条 · 第 {listPage} 页</span><button className={button} disabled={listPage * 20 >= listTotal} onClick={() => setListPage(p => p + 1)}>下一页</button></div>
    </section> : <>
      {record && <section className="rounded-xl border border-blue-100 bg-blue-50/50 px-4 py-3 text-xs leading-6 text-slate-600" aria-label="研判上下文">
        <p className="font-medium text-slate-800">{(run?.mode || mode) === "domain" ? "领域观察" : "任务选队"} · 中国内地 · {run ? scopeName(run.scope) : mode === "domain" ? (domainScope.domainIds.length ? scopeName(domainScope) : "请选择观察领域") : taskScope.mode === "auto" ? "领域待条件确认" : scopeName(taskScope)}</p>
        <details><summary className="min-h-11 cursor-pointer leading-[44px]">研判 {record.taskId.slice(-8)} {run ? ` · 条件 v${run.inputVersion} · 结果 ${run.runId.slice(-8)}` : " · 草稿"}</summary>
          <p className="break-all">完整编号：{record.taskId}{run ? ` · ${run.runId}` : ""}</p></details>
        {historical && <p className="font-medium text-amber-700">正在查看历史快照，修改条件将生成新的版本。</p>}
        {step !== "results" && run && <p>下方正在编辑新条件，已有结果仍按原范围保存。</p>}
      </section>}
      {step === "input" && <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1fr)_280px]">
      <section className={panel}>
        <p className="text-xs font-medium tracking-widest text-blue-700">AI4S · 以证据支持研判</p><h2 className="mt-2 text-2xl font-semibold">从一个任务，或一个领域开始</h2>
        <div className="my-5 flex rounded-xl bg-slate-100 p-1" aria-label="研判模式">{([{ id: "task", name: "任务选队" }, { id: "domain", name: "领域观察" }] as const).map(item => <button key={item.id} aria-pressed={mode === item.id}
          className={`min-h-11 flex-1 rounded-lg px-3 text-sm font-semibold ${mode === item.id ? "bg-white text-blue-700 shadow-sm" : "text-slate-500"}`} onClick={() => setMode(item.id)}>{item.name}</button>)}</div>
        <div className="space-y-5">
          <label className="block text-sm font-medium">{mode === "task" ? "描述研究目标、必要能力和限制" : "观察重点（选填）"}<textarea className={`${input} mt-2 min-h-28 resize-y`} rows={4} maxLength={mode === "task" ? 2000 : 500}
            aria-label={mode === "task" ? "研判任务输入" : "领域观察草稿"} value={mode === "task" ? taskDraft : domainDraft} onChange={e => mode === "task" ? setTaskDraft(e.target.value) : setDomainDraft(e.target.value)}
            placeholder={mode === "task" ? "例如：寻找能够开展蛋白质结构预测的国内团队，要求有对应成果原文；优先提供开源模型。" : "例如：关注该领域的能力分布和近期成果"} /></label>
          {mode === "domain" && <DomainQuickSearch domains={domains} selectedDomainId={domainScope.domainIds[0] || ""}
            onSelect={match => { setDomainScope({ ...domainScope, mode: "selected", domainIds: [match.domainId], subdomainId: match.subdomainId }); confirmRequest.current = requestId(); }} />}
          {scopePicker(mode === "task" ? taskScope : domainScope, mode === "task" ? setTaskScope : setDomainScope, mode === "task")}
          <div className="flex flex-wrap items-end gap-4"><label className="text-sm">{mode === "task" ? "推荐数量" : "关注数量"}<input className={`${input} mt-1 max-w-24`} type="number" min={1} max={20} value={limit} onChange={e => setLimit(Math.max(1, Math.min(20, Number(e.target.value) || 1)))} /></label>
            {mode === "domain" && <label className="text-sm">近期窗口<select className={`${input} mt-1`} value={windowDays} onChange={e => setWindowDays(Number(e.target.value))}>{[30, 90, 180, 365].map(n => <option key={n} value={n}>{n} 天</option>)}</select></label>}
            <button className={primary} disabled={busy || !catalogueReady || (mode === "task" ? taskDraft.trim().length < 2 || taskScope.mode === "selected" && !taskScope.domainIds.length : !domainScope.domainIds.length)} onClick={() => void start()}>
              {busy || !catalogueReady ? <LoaderCircle className="size-4 animate-spin" /> : <ArrowRight className="size-4" />}{mode === "task" ? "解析任务并确认条件" : "查看领域力量分布"}</button>
            <button className={button} disabled={busy} onClick={() => void operation(async () => { await saveDraft(); })}><Save className="size-4" />保存草稿</button>
          </div>
          {!catalogueReady && <p role="status" className="text-sm text-slate-600">正在加载领域资料，加载完成后即可继续。</p>}
          {catalogueReady && (mode === "task" ? taskDraft.trim().length < 2 : !domainScope.domainIds.length) && <p className="text-sm text-slate-600">{mode === "task" ? "填写至少两个字的研究任务后，即可解析条件。" : "请选择一个观察领域后继续。"}</p>}
          {run && <button className={button} onClick={() => setStep("results")}>返回已保存的结果</button>}
        </div>
      </section>
      <aside className={`${panel} self-start border-[#d7e4ed] bg-[#f8fbfd]`} aria-label="本次研判将形成的内容">
        <h3 className="text-base font-semibold text-[#1c4f6a]">这次研判将形成</h3>
        <p className="mt-2 text-xs leading-5 text-[#637f90]">{mode === "task" ? "先确认任务条件，再查看有依据的结果。" : "先选择领域，再查看已收录的力量分布。"}</p>
        <ol className="mt-4 space-y-3 text-sm leading-6 text-[#365b70]">{(mode === "task"
          ? ["可修改的任务条件", "有依据的候选名单", "团队能力对照", "待核验事项"]
          : ["研究方向与能力分布", "关注团队线索", "代表成果与近期变化", "转为具体任务的入口"]
        ).map((item, index) => <li key={item} className="flex items-start gap-2"><span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-[#e6f1f7] text-xs font-semibold text-[#176587]">{index + 1}</span><span>{item}</span></li>)}</ol>
        <p className="mt-4 border-t border-[#dbe8ef] pt-3 text-xs leading-5 text-[#6a8190]">资料不足或合作条件未确认时，会保留明确的待核验状态。</p>
      </aside>
      </div>}
      {step === "confirm" && interpretation && <section className={panel} aria-label="任务条件确认">
        <h2 className="text-xl font-semibold">先确认系统理解的任务</h2><p className="mt-2 text-sm leading-6 text-slate-500">{interpretation.notice}</p>
        <blockquote className="mt-4 rounded-lg bg-slate-50 p-4 text-sm leading-6">{interpretation.taskText}</blockquote>
        <div className="mt-4 grid gap-2 sm:grid-cols-3" aria-label="条件分类摘要">{[
          ["必须核对", interpretation.criteria.filter(item => item.necessity === "required").length],
          ["优先考虑", interpretation.criteria.filter(item => item.necessity === "preferred").length],
          ["排除或待澄清", interpretation.criteria.filter(item => item.necessity === "excluded" || item.necessity === "informational").length],
        ].map(([label, count]) => <div key={label} className="rounded-lg border border-[#d8e5ee] bg-[#f7fbfd] px-3 py-2"><span className="text-xs text-[#607d8e]">{label}</span><strong className="ml-2 text-base text-[#1a5f83]">{count}</strong></div>)}</div>
        <p className="mt-3 text-xs leading-5 text-[#637c8c]">逐项修改后再生成结果；找不到直接依据的必要条件会标为证据不足。</p>
        <div className="mt-3 space-y-3">{interpretation.criteria.map((criterion, index) => <div key={criterion.id} role="group" aria-label={`条件${index + 1}`} className={`grid min-w-0 gap-2 rounded-xl border p-3 sm:grid-cols-[140px_1fr_110px] ${criterion.necessity === "required" ? "border-[#bfd8e8] bg-[#f9fcfe]" : "border-slate-200 bg-white"}`}>
          <select className={input} aria-label={`条件${index + 1}类型`} value={criterion.kind} onChange={e => editCriterion(index, { kind: e.target.value as Criterion["kind"] })}>{Object.entries(labels).map(([key, value]) => <option key={key} value={key}>{value}</option>)}</select>
          <textarea className={input} rows={2} aria-label={`条件${index + 1}内容`} value={criterion.text} onChange={e => editCriterion(index, { text: e.target.value, sourceSpan: null })} />
          <select className={input} aria-label={`条件${index + 1}要求`} value={criterion.necessity} onChange={e => editCriterion(index, { necessity: e.target.value as Criterion["necessity"] })}>{Object.entries(necessityLabels).map(([key, value]) => <option key={key} value={key}>{value}</option>)}</select>
          <p className="break-words text-xs leading-5 text-slate-500 sm:col-span-3">{criterion.sourceSpan ? `对应原文：“${criterion.sourceSpan.text}”（第 ${criterion.sourceSpan.start + 1}–${criterion.sourceSpan.end} 字）` : "用户新增或修改的条件"}{criterion.kind === "constraint" || criterion.kind === "unresolved" ? " · 必要条件未有直接证据时不算满足" : ""}</p>
          <label className="flex min-h-11 items-center gap-2 text-xs sm:col-span-3">多个要素的关系<select className="min-h-11 rounded-lg border border-slate-200 px-2" value={criterion.operator || "all"} onChange={e => editCriterion(index, { operator: e.target.value as "all" | "any" })}><option value="all">需要全部具备</option><option value="any">满足其中一项即可</option></select></label>
          {!!criterion.unresolvedTerms?.length && <p className="text-xs text-amber-700 sm:col-span-3">尚未识别的表述：{criterion.unresolvedTerms.join("、")}。请确认含义，必要条件未有依据时不通过。</p>}
        </div>)}</div>
        <button className={`${button} mt-3`} onClick={() => { setInterpretation(value => value ? { ...value, criteria: [...value.criteria, { id: requestId(), kind: "capability", text: "", necessity: "required", sourceSpan: null }] } : null); confirmRequest.current = requestId(); }}><Plus className="size-4" />补充条件</button>
        <div className="my-5 rounded-xl border border-blue-100 bg-blue-50 p-4 text-sm leading-6"><p>待确认范围：{scopeName({ ...interpretation.scope, domainIds: interpretation.resolvedDomainIds, domainNames: interpretation.resolvedDomainNames })}</p>
          {interpretation.scopeConflict && <><p className="mt-2 font-semibold">任务也涉及：{scopeName({ ...AUTO, domainIds: interpretation.proposedDomainIds, domainNames: interpretation.proposedDomainNames })}。请选择范围处理方式：</p>
            <div className="mt-2 flex flex-wrap gap-2">{[["keep", "保留原范围"], ["switch", "切换到建议领域"], ["expand", "扩大到上述领域"]].map(([key, value]) => <label key={key} className="flex min-h-11 items-center gap-2"><input type="radio" name="scope-resolution" value={key} checked={scopeResolution === key} onChange={() => { setScopeResolution(key); confirmRequest.current = requestId(); }} />{value}</label>)}</div></>}
          <p className="mt-2 text-slate-600">未给定：{interpretation.unknowns.join("、") || "请核对上述条件的具体适用范围"}</p></div>
        <div className="flex flex-wrap gap-3"><button className={button} onClick={() => setStep("input")} disabled={busy}><ArrowLeft className="size-4" />返回修改</button>
          <button className={primary} disabled={busy || interpretation.criteria.some(c => !c.text.trim()) || interpretation.scopeConflict && !scopeResolution} onClick={() => void confirm()}>{busy && <LoaderCircle className="size-4 animate-spin" />}确认条件，生成结果</button></div>
      </section>}
      {step === "results" && run && <>
        <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-xl font-semibold">{run.observation ? "领域力量分布" : "任务候选与推荐依据"}</h2><p className="mt-1 text-xs text-slate-500">资料截至 {new Date(run.createdAt).toLocaleString("zh-CN")} · 已保存快照</p></div>
          <div className="flex flex-wrap gap-2"><button className={button} onClick={() => setStep("input")}>修改条件</button><button className={button} onClick={() => void operation(async () => {
            const data = await assessmentApi.export(record!.taskId, run.runId); const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
            const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = `AI4S-${run.runId}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
          })}><Download className="size-4" />导出证据快照</button></div></div>
        {record && record.runs.length > 1 && <label className="block text-sm">查看保存版本<select className={`${input} mt-1`} value={run.runId} onChange={e => onContextChange(record.taskId, e.target.value)}>
          {record.runs.map(r => <option key={r.runId} value={r.runId}>条件 v{r.inputVersion} · {new Date(r.createdAt).toLocaleString("zh-CN")}</option>)}</select></label>}
        {run.changes && <div className="rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm">{run.changes.reason} · 新增 {run.changes.added.length} · 移除 {run.changes.removed.length} · 内容变化 {run.changes.updated.length}{run.changes.coverageChanged ? " · 范围或证据覆盖发生变化" : ""}</div>}
        {!run.observation && <div id="assessment-investigation"><AssessmentInvestigation key={run.runId} run={run} historical={historical} onOpenRun={id => onContextChange(run.taskId, id)} /></div>}
        {run.observation ? <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">{[["收录科研单元", run.observation.totalUnits], ["已有成果证据", run.observation.outcomeBackedUnits], ["身份资料已收录，成果尚缺", run.observation.identityOnlyUnits]].map(([label, value]) => <div className={panel} key={label}><strong className="text-3xl">{value}</strong><p className="mt-2 text-sm text-slate-500">{label}</p></div>)}</div>
          <section className={panel}><h3 className="font-semibold">研究方向分布</h3><p className="mt-1 text-xs leading-5 text-slate-500">同一团队可能涉及多个方向，总数始终按唯一团队编号去重。</p><div className="mt-3 flex flex-wrap gap-2">{run.observation.directions.map(d => <span key={d.name} className="rounded-lg bg-slate-50 px-3 py-2 text-sm">{d.name} · {d.teamCount}</span>)}</div></section>
          <p className="text-sm leading-6 text-slate-500">{run.observation.notice}</p>
          {run.observation.units.map(unit => <article className={panel} key={unit.teamId}><h3 className="font-semibold">{unit.teamName}</h3><p className="mt-1 text-sm text-slate-500">{unit.institutionName}</p><p className="mt-3 text-sm">{unit.reason} · 成果 {unit.outcomeCount} 条 · 近 {run.observation!.windowDays} 天 {unit.recentOutcomeCount} 条</p>
            <div className="my-3 space-y-2">{unit.citations.map(c => <button className="block min-h-11 text-left text-sm text-blue-700" key={c.id} onClick={() => openEvidence(c, unit.teamName)}>{c.text} ↗</button>)}</div>
            <button className={button} onClick={() => onOpenTeam(unit.teamId)}>查看团队档案</button></article>)}
          <button className={primary} onClick={() => { setTaskScope(run.scope); setMode("task"); setStep("input"); }}>沿用观察范围，转为任务选队<ArrowRight className="size-4" /></button>
        </> : <>
          <section className={`${panel} flex flex-wrap items-center gap-5`}><p><strong className="text-3xl">{run.items.length}</strong><span className="ml-2 text-sm">支有据候选 / 目标 {run.requestedLimit} 支</span></p><p className="text-xs text-slate-500">范围内 {run.eligibleTeamCount} 支已有团队级成果证据</p></section>
          {run.shortfall > 0 && <section className="rounded-xl border border-amber-200 bg-amber-50/50 p-4 text-sm leading-6"><h3 className="font-semibold text-amber-950">推荐数量与缺口</h3><p className="mt-1">当前可推荐 {run.items.length} 支，距目标差 {run.shortfall} 支；没有放宽必要条件或补入无依据团队。</p>
            {shortageStages(run).length > 0 && <ul className="mt-3 grid gap-2 sm:grid-cols-3">{shortageStages(run).map(stage => <li key={stage.title} className="rounded-lg border border-amber-200 bg-white p-3"><strong className="text-amber-950">{stage.title}</strong><p className="mt-1 text-xs leading-5 text-slate-600">{stage.detail}</p></li>)}</ul>}
            {run.unresolvedConditions?.map(c => <p key={c.criterionId}>• {c.text}：{c.reason}</p>)}
            {!run.coverage && <p className="mt-2 text-slate-600">此历史版本未保存分层统计，请结合原条件和证据逐项核对。</p>}
            <div className="mt-3 flex flex-wrap gap-2"><button className={button} onClick={() => setStep("input")}>修改条件或扩大范围</button><button className={button} onClick={() => document.getElementById("assessment-investigation")?.scrollIntoView({ behavior: "smooth", block: "start" })}>查看补充调查</button></div></section>}
          <div className="grid gap-4 xl:grid-cols-2">{run.items.map((item, index) => { const role = suggestedRole(item, run.criteria || []); return <article className={panel} key={item.teamId}>
            <div className="flex items-start justify-between gap-3"><div className="min-w-0"><p className="text-xs font-medium text-blue-700">候选 {index + 1}</p><h3 className="mt-1 text-lg font-semibold">{item.teamName}</h3><p className="mt-1 text-sm text-slate-500">{item.institutionName}</p></div>
              <label className="flex min-h-11 shrink-0 items-center gap-2 text-xs"><input type="checkbox" checked={comparedIds.includes(item.teamId)}
                disabled={busy || historical || !record?.state.comparedTeamIds.includes(item.teamId) && (record?.state.comparedTeamIds.length || 0) >= 3}
                onChange={() => void patchState({ comparedTeamIds: record!.state.comparedTeamIds.includes(item.teamId) ? record!.state.comparedTeamIds.filter(id => id !== item.teamId) : [...record!.state.comparedTeamIds, item.teamId] })} />比较</label></div>
            <div className="mt-4 rounded-xl border border-blue-100 bg-blue-50/60 p-3"><h4 className="text-sm font-semibold text-blue-950">建议承担环节</h4><p className="mt-1 text-sm leading-6">{role?.text || "暂无可由团队级成果直接支持的具体分工，需进一步核验"}</p>
              {role && <div className="mt-2 flex flex-wrap gap-2">{role.claimIds.map(id => { const c = item.citations.find(value => value.id === id); return c && <button key={id} className="min-h-11 rounded-lg border border-blue-200 bg-white px-3 py-2 text-left text-xs text-blue-700" onClick={() => openEvidence(c, item.teamName, role.text, "成果引文支持建议环节；具体分工待团队确认")}>证据 {id} · {c.text}</button>; })}</div>}</div>
            {item.capability && <p className="mt-2 line-clamp-3 text-sm leading-6 text-slate-500">档案能力描述：{item.capability}</p>}
            <div className="mt-4 space-y-2">{item.criteriaMatrix.map(row => <div key={row.criterionId} className="rounded-lg border border-slate-100 p-3 text-sm"><p><span className={row.status === "supported" ? "text-emerald-700" : "text-slate-500"}>{statuses[row.status]}</span> · {row.text}</p>
              {row.claimIds.map(id => { const c = item.citations.find(c => c.id === id); return c ? <button key={id} className="mt-1 min-h-11 text-left text-xs text-blue-700" onClick={() => openEvidence(c, item.teamName, row.text, statuses[row.status])}><BookOpenText className="mr-1 inline size-3.5" />证据 {id} · {c.text}</button> : null; })}</div>)}</div>
            <p className="mt-3 text-xs leading-5 text-slate-500">尚缺信息：可投入人员、交付周期与合作资源需团队确认。AI 或来源检查不等于专家人工审定。</p>
            <details className="mt-3 rounded-lg bg-slate-50 p-3"><summary className="min-h-8 cursor-pointer text-xs font-medium">查看独立评分与版本</summary><p className="mt-2 text-xs leading-6">任务匹配 {item.taskMatchScore} · 团队原分 {item.teamScore ?? "暂无"} · {item.institutionImpact}<br />任务版本 {item.matchVersion}；任务分按证据60%、方向25%、成果覆盖15%计算，未与机构或团队分相加。</p></details>
            <div className="mt-4 flex flex-wrap gap-2"><button className={button} onClick={() => onOpenTeam(item.teamId)}>团队档案<ArrowRight className="size-4" /></button><button className={button} onClick={() => onOpenRelations(item.teamId)}><Network className="size-4" />相关关系</button>
              <button className={button} disabled={historical} onClick={() => openFollowUp(item.teamId, item.citations.map(c => c.id!).filter(Boolean))}>记录跟进</button></div>
          </article>; })}</div>
          {compared.length >= 2 && <section className={panel} aria-label="团队逐项比较"><h3 className="text-lg font-semibold">按同一任务条件比较</h3><p className="mt-1 text-xs leading-5 text-slate-500">全部依据来自当前保存的推荐快照；缺少依据不等于能力为零。</p>
            <div className="mt-4 space-y-3">{run.criteria?.map(criterion => <details className="rounded-xl border border-slate-200 p-3" open key={criterion.id}><summary className="min-h-11 cursor-pointer text-sm font-semibold">{criterion.text}</summary>
              <div className="grid gap-3 md:grid-cols-3">{compared.map(item => { const row = item.criteriaMatrix.find(c => c.criterionId === criterion.id); return <div className="rounded-lg bg-slate-50 p-3" key={item.teamId}><h4 className="text-sm font-semibold">{item.teamName}</h4><p className="mt-2 text-xs">{row ? statuses[row.status] : "依据不足"}</p>
                {row?.claimIds.map(id => { const c = item.citations.find(c => c.id === id); return c ? <button key={id} className="mt-1 min-h-11 text-left text-xs text-blue-700" onClick={() => openEvidence(c, item.teamName, criterion.text, statuses[row.status])}>{c.text} ↗</button> : null; })}</div>; })}</div></details>)}</div>
            <h4 className="mt-5 font-semibold">讨论候选分工</h4><p className="mt-1 text-xs text-slate-500">这是待沟通方案；尚未确认资源、协作接口和协调成本，不代表已经分派任务。</p>
            <CombinationCoverage criteria={run.criteria || []} teams={compared} />
            <div className="mt-3 space-y-3">{compared.map(item => { const role = roles.find(r => r.teamId === item.teamId); return <div key={item.teamId} className="grid gap-2 rounded-lg bg-slate-50 p-3 sm:grid-cols-2"><label className="text-xs">{item.teamName} · 建议角色<input disabled={historical} className={`${input} mt-1`} placeholder={suggestedRole(item, run.criteria || [])?.text || "请基于证据填写待讨论角色"} value={role?.role || ""} onChange={e => setRoles(values => [...values.filter(v => v.teamId !== item.teamId), { teamId: item.teamId, role: e.target.value, rationale: role?.rationale || "" }])} /></label>
              <label className="text-xs">分工依据和协作缺口<input disabled={historical} className={`${input} mt-1`} value={role?.rationale || ""} onChange={e => setRoles(values => [...values.filter(v => v.teamId !== item.teamId), { teamId: item.teamId, role: role?.role || "", rationale: e.target.value }])} /></label></div>; })}</div>
            <button className={`${primary} mt-3`} disabled={busy || historical || compared.some(i => !roles.find(r => r.teamId === i.teamId)?.role.trim())} onClick={() => void patchState({ combination: roles.filter(r => compared.some(i => i.teamId === r.teamId)) })}><Save className="size-4" />保存候选组合</button>
          </section>}
        </>}
        {record && <section className={panel}><h3 className="font-semibold">跟进与内部记录</h3><p className="mt-1 text-xs text-slate-500">只对当前访客可见；公开证据导出不包含这些内容。</p>
          <div className="mt-3 space-y-2">{record.state.followUps.map(f => <div className="rounded-lg bg-slate-50 p-3 text-sm" key={f.id}><p>{f.question}</p><p className="mt-1 text-xs text-slate-500">{f.method} · {f.owner || "负责人未指定"} · {f.dueDate || "日期未定"} · {({ open: "未开始", in_progress: "进行中", done: "已完成", cancelled: "取消" })[f.status]}</p>
            <button className={`${button} mt-2`} onClick={() => { setFollowDraft(f); followDialog.current?.showModal(); }}>更新跟进</button></div>)}</div>
          <label className="mt-4 block text-sm">内部备注<textarea className={`${input} mt-2`} rows={3} maxLength={10000} value={notes} onChange={e => setNotes(e.target.value)} /></label><button className={`${button} mt-2`} disabled={busy} onClick={() => void patchState({ internalNotes: notes })}><Save className="size-4" />保存备注</button>
        </section>}
      </>}
    </>}
    <dialog ref={evidenceDialog} onClose={() => setEvidence(null)} className="fixed inset-0 m-auto max-h-[85dvh] w-[min(680px,calc(100%-24px))] overflow-y-auto rounded-2xl border border-slate-200 bg-white p-5 text-slate-900 shadow-xl backdrop:bg-slate-900/30">
      <div className="flex items-center justify-between gap-3"><h2 className="text-lg font-semibold">判断依据与原文</h2><button className={button} aria-label="关闭证据详情" onClick={() => evidenceDialog.current?.close()}><X className="size-4" /></button></div>
      {evidence && <div className="mt-4 space-y-4 text-sm leading-6"><p>所属科研单元：<strong>{evidence.team}</strong></p>
        {evidence.requirement && <p className="rounded-xl bg-blue-50 p-3">对应任务条件：{evidence.requirement}<br />本次判断：{evidence.support}</p>}
        <p>{evidence.citation.text}</p><blockquote className="rounded-xl border-l-4 border-blue-300 bg-slate-50 p-4 whitespace-pre-wrap">{evidence.citation.quote}</blockquote>
        <p className="break-all text-xs text-slate-500">引文编号：{evidence.citation.id} · 保存于研判 {run?.inputVersionId}</p>
        <ClaimSourceDetails citation={evidence.citation} />
        {evidence.citation.id && <ClaimReviewEditor key={evidence.citation.id} claimId={evidence.citation.id} />}
        <p>以上是本次研判保存的引文。适用范围限于原文中的主体和成果；人员投入、资源可用性与交付承诺需另行确认。</p>
        <a className={`${button} break-all`} href={evidence.citation.url} target="_blank" rel="noopener noreferrer"><ExternalLink className="size-4 shrink-0" />打开来源原文</a><p className="break-all text-xs text-slate-500">{evidence.citation.url}</p>
        <p className="text-xs text-slate-500">若原链接无法访问，可保留此引文快照并记录核验事项；当前未实时检测链接可用性。</p></div>}
    </dialog>
    <dialog ref={followDialog} onClose={() => setFollowDraft(null)} className="fixed inset-0 m-auto max-h-[85dvh] w-[min(640px,calc(100%-24px))] overflow-y-auto rounded-2xl border border-slate-200 bg-white p-5 text-slate-900 shadow-xl backdrop:bg-slate-900/30">
      <div className="flex items-center justify-between"><h2 className="text-lg font-semibold">记录跟进事项</h2><button className={button} aria-label="关闭跟进编辑" onClick={() => followDialog.current?.close()}><X className="size-4" /></button></div>
      {followDraft && <form className="mt-4 space-y-3" onSubmit={e => { e.preventDefault(); void operation(async () => {
        const current = recordRef.current!; const next = await assessmentApi.patch(current.taskId, current.revision, { followUps: [...current.state.followUps.filter(f => f.id !== followDraft.id), followDraft] });
        adopt(next); followDialog.current?.close();
      }); }}>
        {([['question', '待确认问题'], ['method', '验证方法'], ['owner', '跟进负责人'], ['result', '反馈或验证结果']] as const).map(([key, label]) => <label key={key} className="block text-sm">{label}<textarea rows={key === "question" || key === "result" ? 2 : 1} required={key === "question"} className={`${input} mt-1`} value={followDraft[key]} onChange={e => setFollowDraft({ ...followDraft, [key]: e.target.value })} /></label>)}
        <label className="block text-sm">计划日期<input type="date" className={`${input} mt-1`} value={followDraft.dueDate || ""} onChange={e => setFollowDraft({ ...followDraft, dueDate: e.target.value || null })} /></label>
        <label className="block text-sm">状态<select className={`${input} mt-1`} value={followDraft.status} onChange={e => setFollowDraft({ ...followDraft, status: e.target.value as FollowUp["status"] })}><option value="open">未开始</option><option value="in_progress">进行中</option><option value="done">已完成</option><option value="cancelled">取消</option></select></label>
        <button className={primary} disabled={busy} type="submit"><Check className="size-4" />保存跟进</button>
      </form>}
    </dialog>
  </main>;
}
