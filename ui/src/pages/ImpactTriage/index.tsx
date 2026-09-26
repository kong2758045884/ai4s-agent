import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import { ROUTES } from "@/router/routes";
import VerifiedIntelligence from "./VerifiedIntelligence";
import { recommendationApi, type IntelligenceDaily } from "@/services/strategicRecommendations";
import {
  impactApi, type ImpactCandidate, type ImpactDaily, type ImpactDetail, type ImpactEvent, type ImpactL2Candidate,
  type ImpactDirection, type ImpactRanking,
} from "@/services/impactTriage";

type Tab = "ranking" | "tree" | "daily";
type Audit = { id: number; direction_id: string; action: string; reviewed_on: string; reverted_on: string | null };
type EventMapping = { id: number; label: string; target_direction_id: string; reviewed_on: string; reverted_on: string | null };
const dateToday = () => new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit"
}).format(new Date());

type ImpactProps = { embedded?: boolean; domainId?: string; subdomainId?: string; verifiedOnly?: boolean };

export default function ImpactTriage(props: ImpactProps) {
  return props.verifiedOnly ? <VerifiedIntelligence domainId={props.domainId || ""} subdomainId={props.subdomainId || ""} /> : <ImpactReviewWorkspace {...props} />;
}

function ImpactReviewWorkspace({ embedded = false, domainId = "", subdomainId = "" }: {
  embedded?: boolean; domainId?: string; subdomainId?: string;
}) {
  const [tab, setTab] = useState<Tab>("ranking");
  const [status, setStatus] = useState<{ ready: boolean; entities: number; events: number; scores: number } | null>(null);
  const [directions, setDirections] = useState<ImpactDirection[]>([]);
  const [candidates, setCandidates] = useState<ImpactCandidate[]>([]);
  const [l2Candidates, setL2Candidates] = useState<ImpactL2Candidate[]>([]);
  const [unclassifiedEvents, setUnclassifiedEvents] = useState(0);
  const [treeEvents, setTreeEvents] = useState<ImpactEvent[]>([]);
  const [treeEventTitle, setTreeEventTitle] = useState("");
  const [ranking, setRanking] = useState<ImpactRanking[]>([]);
  const [reviews, setReviews] = useState<{ id: string; kind: string; reason: string; status: string }[]>([]);
  const [audits, setAudits] = useState<Audit[]>([]);
  const [eventMappings, setEventMappings] = useState<EventMapping[]>([]);
  const [rankingView, setRankingView] = useState<"official" | "reference">("official");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [selectedDirection, setSelectedDirection] = useState("");
  const [country, setCountry] = useState("");
  const [tier, setTier] = useState("");
  const [followed, setFollowed] = useState("");
  const [includePending, setIncludePending] = useState(false);
  const [rankingRevision, setRankingRevision] = useState(0);
  const [selectedEntity, setSelectedEntity] = useState<ImpactDetail | null>(null);
  const [teamQuery, setTeamQuery] = useState("");
  const [teamCandidates, setTeamCandidates] = useState<{ id: string; institution_name: string; team_name: string }[]>([]);
  const [selectedTeamId, setSelectedTeamId] = useState("");
  const [teamRelation, setTeamRelation] = useState<"member" | "affiliated" | "same_organization">("affiliated");
  const [teamBasisUrl, setTeamBasisUrl] = useState("");
  const [teamNote, setTeamNote] = useState("");
  const selectedRankingRow = ranking.find((row) => row.id === selectedEntity?.id);
  const [reportDate, setReportDate] = useState(dateToday);
  const [report, setReport] = useState<ImpactDaily | null>(null);
  const [combinedReport, setCombinedReport] = useState<IntelligenceDaily | null>(null);
  const [reviewNote, setReviewNote] = useState("");
  const [basisUrl, setBasisUrl] = useState("");
  const [researchType, setResearchType] = useState("public_research");
  const [mainlandConfirmed, setMainlandConfirmed] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    const s = await impactApi.status();
    setStatus(s);
    if (!s.ready) return;
    const [d, r, a] = await Promise.all([impactApi.directions(), impactApi.reviews(), impactApi.audits()]);
    setDirections(d.items);
    setCandidates(d.candidates);
    setL2Candidates(d.l2_candidates);
    setUnclassifiedEvents(d.unclassified_events);
    setReviews(r.items);
    setAudits(a.items);
    setEventMappings(a.event_mappings);
  };

  useEffect(() => {
    void refresh().catch((reason) => setError(String(reason)));
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(searchInput.trim()), 180);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  useEffect(() => { setSelectedDirection(""); setSelectedEntity(null); }, [domainId, subdomainId]);
  const mappedDirection = directions.find((d) => d.level === 2 && d.ai4s_subdomain_id === subdomainId && subdomainId)
    || directions.find((d) => d.level === 2 && d.ai4s_domain_id === domainId && domainId);
  const scopeUnavailable = embedded && !!domainId && !mappedDirection;
  const effectiveDirection = selectedDirection || (embedded ? mappedDirection?.id || "" : "");
  const scopedL2Ids = new Set(directions.filter((d) => d.level === 2 && (!embedded || d.ai4s_domain_id === domainId)).map((d) => d.id));
  const scopedDirections = directions.filter((d) => !embedded || (d.level === 2 && scopedL2Ids.has(d.id)) || (d.level === 3 && !!d.parent_id && scopedL2Ids.has(d.parent_id)));

  useEffect(() => {
    if (!status?.ready) return;
    if (scopeUnavailable) { setRanking([]); return; }
    const controller = new AbortController();
    const query = new URLSearchParams();
    if (effectiveDirection) query.set("direction_id", effectiveDirection);
    if (country) query.set("country", country);
    if (tier) query.set("tier", tier);
    if (followed) query.set("followed", followed);
    if (includePending) query.set("include_pending", "true");
    query.set("view", rankingView);
    if (search) query.set("q", search);
    void impactApi.ranking(query, controller.signal).then((r) => setRanking(r.items)).catch((reason) => {
      if (!controller.signal.aborted) setError(String(reason));
    });
    return () => controller.abort();
  }, [status?.ready, effectiveDirection, scopeUnavailable, country, tier, followed, includePending, rankingView, search, rankingRevision]);

  useEffect(() => {
    if (status?.ready && tab === "daily") {
      void impactApi.daily(reportDate).then(setReport).catch((reason) => setError(String(reason)));
      if (embedded) void recommendationApi.daily(reportDate).then(setCombinedReport).catch((reason) => setError(String(reason)));
    }
  }, [status?.ready, tab, reportDate, audits, embedded]);

  const nameById = useMemo(() => new Map(directions.map((d) => [d.id, d.name])), [directions]);
  const rankingGroups = useMemo(() => {
    if (effectiveDirection) return [{
      id: effectiveDirection,
      name: nameById.get(effectiveDirection) || "所选方向",
      items: ranking
    }];
    const l2 = directions.filter((d) => d.level === 2 && d.status === "formal");
    const groups = l2.map((d) => ({
      id: d.id,
      name: d.name,
      items: ranking.filter((r) => r.direction_ids.includes(d.id))
    }))
      .filter((group) => group.items.length > 0);
    const unlinked = ranking.filter((r) => !r.direction_ids.some((id) => l2.some((d) => d.id === id)));
    if (unlinked.length) groups.push({
      id: "unlinked",
      name: "未挂正式方向",
      items: unlinked
    });
    return groups;
  }, [effectiveDirection, nameById, ranking, directions]);
  const roots = directions.filter((d) => d.level === 1 && (!embedded || directions.some((child) => child.parent_id === d.id && scopedL2Ids.has(child.id))));
  const dailyTeamChanges = combinedReport?.teamChanges.filter((item) => !domainId || item.domainId === domainId) || [];
  const dailyRecommendationChanges = combinedReport?.recommendationChanges.filter((item) => !domainId || item.domainId === domainId) || [];
  const dailyImpactEvents = combinedReport?.impact.events.filter((item) => !domainId || directions.some(
    (direction) => direction.id === item.direction_id && direction.ai4s_domain_id === domainId)) || [];
  const dailyFollowUps = domainId ? combinedReport?.domains[domainId]?.followUps || []
    : Object.values(combinedReport?.domains || {}).flatMap((item) => item.followUps);
  const openEntity = async (id: string) => {
    try { const item = await impactApi.entity(id); setSelectedEntity(item); setMainlandConfirmed(!!item.mainland_confirmed); setError(""); }
    catch (reason) { setError(String(reason)); }
  };
  const openTreeEvents = async (id: string, title: string, label?: string) => {
    try { setTreeEvents((await impactApi.directionEvents(id, label)).items); setTreeEventTitle(title); setError(""); }
    catch (reason) { setError(String(reason)); }
  };
  const openPoolEvents = async (label?: string) => {
    try { setTreeEvents((await impactApi.unclassifiedEvents(label)).items); setTreeEventTitle(label || "未归类池"); setError(""); }
    catch (reason) { setError(String(reason)); }
  };
  const act = async (work: () => Promise<unknown>) => {
    if (reviewNote.trim().length < 3) { setError("请先填写至少三个字的审核依据"); return; }
    setBusy(true);
    try { await work(); await refresh(); setRankingRevision((value) => value + 1); setSelectedEntity(null); setReviewNote(""); setError(""); }
    catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  };
  const rollback = async (id: number) => {
    setBusy(true);
    try { await impactApi.rollback(id); await refresh(); setError(""); }
    catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  };
  const rollbackMapped = async (id: number) => {
    setBusy(true);
    try { await impactApi.rollbackMapping(id); await refresh(); setError(""); }
    catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  };
  const rollbackIdentity = async (sourceId: string) => {
    setBusy(true);
    try {
      await impactApi.rollbackIdentity(sourceId);
      await refresh();
      setRankingRevision((value) => value + 1);
      setSelectedEntity(null);
      setError("");
    } catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  };
  const toggleFollow = async () => {
    if (!selectedEntity) return;
    setBusy(true);
    try {
      await impactApi.follow(selectedEntity.id, selectedEntity.follow_status === "unfollowed");
      setSelectedEntity(await impactApi.entity(selectedEntity.id));
      setRankingRevision((value) => value + 1);
      setError("");
    } catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  };
  const searchTeams = async () => {
    if (!selectedEntity || teamQuery.trim().length < 2) return;
    try { setTeamCandidates((await impactApi.teamCandidates(selectedEntity.id, teamQuery)).items); setError(""); }
    catch (reason) { setError(String(reason)); }
  };
  const saveTeamLink = async () => {
    if (!selectedEntity || !selectedTeamId) return;
    setBusy(true);
    try {
      await impactApi.linkTeam(selectedEntity.id, selectedTeamId, teamRelation, teamBasisUrl, teamNote);
      setSelectedEntity(await impactApi.entity(selectedEntity.id));
      setSelectedTeamId(""); setTeamBasisUrl(""); setTeamNote(""); setError("");
    } catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  };
  const undoTeamLink = async (auditId: number) => {
    if (!selectedEntity) return;
    setBusy(true);
    try { await impactApi.rollbackTeamLink(auditId); setSelectedEntity(await impactApi.entity(selectedEntity.id)); setError(""); }
    catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  };

  return (
    <main className={embedded ? "min-h-[420px] min-w-0 flex-1 overflow-y-auto rounded-2xl bg-[#f3f6f9] text-[#18334a] lg:min-h-0" : "h-full overflow-y-auto bg-[#f3f6f9] px-4 py-6 text-[#18334a] md:px-8"}>
      <div className="mx-auto max-w-[1500px] space-y-5">
        {embedded ? <div className="rounded-2xl bg-gradient-to-r from-[#0f526d] to-[#21828e] px-5 py-5 text-white">
          <p className="text-[11px] font-semibold tracking-[.22em] text-cyan-100">AI4S · DYNAMIC INTELLIGENCE</p>
          <h2 className="mt-1 text-2xl font-semibold">领域动态情报</h2>
          <p className="mt-1 text-sm text-white/80">机构影响力、类目审核与每日变化，沿用左侧领域范围。</p>
        </div> : null}
        {!embedded &&
        <header className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-[#0d3048] via-[#105e78] to-[#26859a] px-6 py-7 text-white shadow-lg md:flex md:items-center md:justify-between md:gap-8 md:px-8">
          <div className="absolute -right-10 -top-24 h-64 w-64 rounded-full border border-white/20" aria-hidden="true" />
          <div className="absolute right-8 top-7 h-32 w-32 rounded-full border border-white/10" aria-hidden="true" />
          <div className="relative max-w-2xl">
            <Link to={ROUTES.WORKSPACE_STRATEGIC_MAP} className="text-sm text-white/80 hover:text-white">← 返回战略图谱</Link>
            <p className="mt-5 text-xs font-semibold tracking-[0.24em] text-[#a6e1e4]">AI4S · RESEARCH INTELLIGENCE</p>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight md:text-4xl">国内科研机构影响力</h1>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-white/80">从可追溯事件到研究方向、跨期变化与每日报告。机构主体与战略地图中的团队分别管理。</p>
          </div>
          {status?.ready && <div className="relative mt-6 grid grid-cols-3 gap-3 border-t border-white/20 pt-5 md:mt-0 md:w-[390px] md:flex-none md:gap-6 md:border-l md:border-t-0 md:pl-7 md:pt-0">
            {[{
              value: status.entities,
              label: "来源主体"
            }, {
              value: status.events,
              label: "证据事件"
            }, {
              value: status.scores,
              label: "历史期次"
            }].map((item) =>
              <div key={item.label}><strong className="text-2xl tabular-nums">{item.value}</strong><p className="mt-1 text-xs text-white/70">{item.label}</p></div>)}
          </div>}
        </header>}
        {scopeUnavailable && <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">当前子领域尚无审核通过的影响力类目映射。可在类目审核中查看候选，榜单不会借用其他领域的数据。</div>}
        {error && <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}
        {status === null && !error && <p>正在加载分诊数据…</p>}
        {status && !status.ready && <div className="rounded-xl border bg-white p-6">尚无分诊快照。请先运行迁移预检并导入隔离库。</div>}
        {status?.ready && <>
          <nav aria-label="分诊视图" className="flex gap-2 border-b border-[#d6e1e9] pb-2">
            {(["ranking", "tree", "daily"] as const).map((key) => (
              <button key={key} type="button" aria-pressed={tab === key} onClick={() => setTab(key)}
                className={`rounded-lg px-4 py-2 text-sm ${tab === key ? "bg-[#105d79] text-white" : "bg-white text-[#23536d]"}`}>
                {{
                  ranking: "影响力榜单",
                  tree: "类目审核",
                  daily: "每日报告"
                }[key]}
              </button>
            ))}
          </nav>
          {tab === "ranking" && <section className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-[#dbe6e9] bg-white p-3 shadow-sm">
              <div className="flex rounded-xl bg-[#edf3f5] p-1" role="group" aria-label="榜单口径">
                <button type="button" aria-pressed={rankingView === "official"} onClick={() => setRankingView("official")}
                  className={`rounded-lg px-4 py-2 text-sm font-medium ${rankingView === "official" ? "bg-[#145d73] text-white shadow" : "text-[#496777]"}`}>正式榜 · 国内科研机构</button>
                <button type="button" aria-pressed={rankingView === "reference"} onClick={() => setRankingView("reference")}
                  className={`rounded-lg px-4 py-2 text-sm font-medium ${rankingView === "reference" ? "bg-[#145d73] text-white shadow" : "text-[#496777]"}`}>来源参考 · 待核验</button>
              </div>
              <label className="relative w-full md:w-[360px]"><span className="sr-only">检索主体、事件和摘要</span>
                <input aria-label="检索主体、事件和摘要" value={searchInput} onChange={(e) => setSearchInput(e.target.value)}
                  placeholder="检索机构、别名、成果或事件…" className="w-full rounded-xl border border-[#cddce2] bg-[#f9fbfc] px-4 py-2.5 text-sm outline-none focus:border-[#1687a3] focus:ring-2 focus:ring-[#1687a3]/20" /></label>
            </div>
            <div className="flex flex-wrap gap-3 rounded-xl border bg-white p-4">
              <label className="text-sm">方向 <select aria-label="方向筛选" className="ml-2 rounded border p-2" value={selectedDirection} onChange={(e) => setSelectedDirection(e.target.value)}>
                <option value="">{embedded ? "当前领域" : "全部方向"}</option>{scopedDirections.filter((d) => d.level > 1 && d.status === "formal").map((d) =>
                  <option key={d.id} value={d.id}>{d.level === 3 ? "　" : ""}{d.name}</option>)}</select></label>
              {rankingView === "reference" && <label className="text-sm">国别 <select aria-label="国别筛选" className="ml-2 rounded border p-2" value={country} onChange={(e) => setCountry(e.target.value)}>
                <option value="">全部</option><option value="zn">国内</option><option value="gw">国外</option></select></label>}
              <label className="text-sm">等级 <select aria-label="等级筛选" className="ml-2 rounded border p-2" value={tier} onChange={(e) => setTier(e.target.value)}>
                <option value="">全部</option><option value="A">A</option><option value="B">B</option><option value="C">C</option></select></label>
              <label className="text-sm">关注 <select aria-label="关注筛选" className="ml-2 rounded border p-2" value={followed} onChange={(e) => setFollowed(e.target.value)}>
                <option value="">全部</option><option value="true">已关注</option><option value="false">未关注</option></select></label>
              {rankingView === "reference" && <label className="flex items-center gap-2 text-sm"><input aria-label="包含待核查主体" type="checkbox" checked={includePending} onChange={(e) => setIncludePending(e.target.checked)} />包含待核查主体</label>}
            </div>
            <p className="rounded-xl border border-[#b8d8df] bg-[#e9f5f6] p-4 text-sm leading-6 text-[#245268]">{rankingView === "official"
              ? "正式榜只收录已用一手来源核验的国内科研机构。来源 A/B/C 尚待跨领域校准，未校准主体按名称排列；校准版本单独标识。"
              : "参考页保留原系统的机构与作者数据供溯源和审核；这里的 A/B/C 尚未重评，不能视作正式科研机构排名。"}</p>
            {rankingView === "reference" && reviews.filter((r) => r.status === "pending").length > 0 &&
              <details className="rounded-xl border border-amber-200 bg-amber-50/70 px-4 py-3 text-sm">
                <summary className="cursor-pointer font-medium text-amber-900">迁移待核查 · {reviews.filter((r) => r.status === "pending").length} 条（展开查看）</summary>
                {reviews.filter((r) => r.status === "pending").map((r) => <p key={r.id} className="mt-2 text-amber-800">{r.reason}</p>)}
              </details>}
            <p className="text-xs text-[#607486]">{ranking.length} 个主体 · 同一机构可归属多个方向，分组行数可能大于机构数 · 本地快照即时检索</p>
            {rankingGroups.map((group) => <section key={group.id} className="overflow-x-auto rounded-xl border bg-white">
              <h2 className="border-b bg-[#f8fbfd] px-4 py-3 font-semibold">{group.name} · {group.items.length} 主体</h2>
              <table className="w-full min-w-[800px] text-left text-sm"><thead className="bg-[#eaf1f5] text-[#426079]"><tr>
                <th className="p-3">主体</th><th>类型／国别</th><th>级别与版本</th><th>总分</th><th>成就／地位／趋势</th><th>事件</th><th>期次</th><th>审核</th>
              </tr></thead><tbody>{group.items.map((row) => <tr key={row.id} className="border-t">
                <td className="p-3"><button type="button" onClick={() => void openEntity(row.id)} className="font-semibold text-[#17658f] underline">{row.name}</button>
                  {row.identity_aliases.length > 0 && <p className="text-xs text-[#718492]">含 {row.identity_aliases.map((x) => x.name).join("、")}</p>}
                  {search && row.match_reason && <p className="mt-1 max-w-[320px] truncate text-xs text-[#607486]" title={row.match_snippet}>命中{row.match_reason}：{row.match_snippet}</p>}</td>
                <td>{row.kind === "institution" ? "机构" : "作者"} · {row.country === "zn" ? "国内" : "国外"}</td>
                <td><strong>{row.tier}</strong><p className="text-xs text-[#718492]">{row.score_source === "calibrated_revision" ? "校准版" : "来源分 · 待校准"}</p></td><td>{row.total.toFixed(1)}</td>
                <td>{row.eff_achievement} / {row.eff_status} / {row.eff_trend}</td><td>{row.event_count}（旗舰 {row.flagship_count}）</td>
                <td>{row.scan_date}</td><td>{row.identity_recheck_required && row.score_source !== "calibrated_revision" ? "身份已归一 · 待重评" : row.review_cases.length ? "待核查" : row.eligibility === "eligible" ? "已核资格" : "未核资格"}</td>
              </tr>)}</tbody></table>
            </section>)}
            {ranking.length === 0 && <div className="rounded-2xl border border-[#d9e5e9] bg-white px-6 py-10 text-center shadow-sm">
              <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-[#e7f2f4] text-xl text-[#1e7084]">⌕</div>
              <h2 className="mt-4 text-lg font-semibold">{rankingView === "official" ? "正式榜正在核验入榜机构" : "当前条件下没有匹配主体"}</h2>
              <p className="mx-auto mt-2 max-w-lg text-sm leading-6 text-[#607486]">{rankingView === "official"
                ? "来源快照尚未提供足够的一手机构资格证明。参考页可查看全部来源证据，并逐个审核进入正式榜。"
                : "试试机构名称、别名、成果关键词，或清空方向与等级筛选。"}</p>
              {rankingView === "official" && <button type="button" onClick={() => setRankingView("reference")}
                className="mt-5 rounded-lg bg-[#145d73] px-4 py-2 text-sm font-medium text-white hover:bg-[#0e4c60]">查看来源参考与证据</button>}
            </div>}
            {selectedEntity && <section aria-label="主体证据详情" className="rounded-xl border bg-white p-5">
              <div className="flex justify-between"><h2 className="text-lg font-semibold">{selectedEntity.name} · 证据与历史</h2>
                <div className="flex gap-3"><button type="button" disabled={busy} onClick={() => void toggleFollow()} className="text-[#17658f] underline">{selectedEntity.follow_status === "unfollowed" ? "关注主体" : "取消关注"}</button>
                  <button type="button" onClick={() => setSelectedEntity(null)}>关闭</button></div></div>
              <p className="mt-2 text-sm">资格：{selectedEntity.eligibility}；机构与团队是独立主体，关联不会将机构事件计作团队成果。</p>
              <details className="mt-3 rounded-xl border border-[#dbe6e9] bg-[#f8fbfc] p-3 text-sm">
                <summary className="cursor-pointer font-medium">机构—团队审核关联 · {selectedEntity.team_links.length} 条</summary>
                <div className="mt-3 space-y-2">
                  {selectedEntity.team_links.map((link) => <div key={link.team_id} className="flex flex-wrap items-center gap-2 rounded-lg bg-white p-2">
                    <span>{link.team_id} · {link.relation}</span><a href={link.evidence_url} target="_blank" rel="noreferrer" className="text-[#17658f] underline">关联依据</a>
                    {!!link.audit_id && <button type="button" disabled={busy} onClick={() => void undoTeamLink(link.audit_id!)} className="text-[#a44949] underline">撤销关联</button>}
                  </div>)}
                  {selectedEntity.eligibility === "eligible" && <div className="space-y-2 border-t pt-3">
                    <div className="flex flex-wrap gap-2"><input aria-label="查找团队" value={teamQuery} onChange={(event) => setTeamQuery(event.target.value)} placeholder="输入机构或团队名" className="min-w-0 flex-1 rounded border p-2" />
                      <button type="button" onClick={() => void searchTeams()} className="rounded bg-[#145d73] px-3 py-2 text-white">查找团队</button></div>
                    {teamCandidates.length > 0 && <select aria-label="待关联团队" value={selectedTeamId} onChange={(event) => setSelectedTeamId(event.target.value)} className="w-full rounded border p-2">
                      <option value="">选择已查到的团队</option>{teamCandidates.map((team) => <option key={team.id} value={team.id}>{team.institution_name} · {team.team_name}（{team.id}）</option>)}
                    </select>}
                    <div className="flex flex-wrap gap-2"><select aria-label="关联类型" value={teamRelation} onChange={(event) => setTeamRelation(event.target.value as typeof teamRelation)} className="rounded border p-2">
                      <option value="member">机构直属团队</option><option value="affiliated">机构附属团队</option><option value="same_organization">同一机构</option></select>
                    <input aria-label="关联官方来源" value={teamBasisUrl} onChange={(event) => setTeamBasisUrl(event.target.value)} placeholder="https:// 官网团队或机构页面" className="min-w-0 flex-1 rounded border p-2" /></div>
                    <textarea aria-label="关联审核说明" value={teamNote} onChange={(event) => setTeamNote(event.target.value)} placeholder="写明官网原文如何证明归属" className="w-full rounded border p-2" rows={2} />
                    <button type="button" disabled={busy || !selectedTeamId || !teamBasisUrl.startsWith("https://") || teamNote.trim().length < 3}
                      onClick={() => void saveTeamLink()} className="rounded bg-[#145d73] px-4 py-2 text-white disabled:opacity-50">保存审核关联</button>
                  </div>}
                </div>
              </details>
              {selectedEntity.identity_aliases.length > 0 && <p className="mt-2 rounded-lg bg-[#eef5f6] p-3 text-sm">同一展示主体：{selectedEntity.identity_aliases.map((x) => x.name).join("、")}。事件合并展示，原始评分期次分别保留，须重新评分。
                {selectedEntity.identity_aliases.map((x) => <span key={x.id} className="ml-2">
                  <a href={x.evidence_url} target="_blank" rel="noreferrer" className="text-[#17658f] underline">身份依据</a>
                  <button type="button" disabled={busy} onClick={() => void rollbackIdentity(x.id)} className="ml-2 text-[#17658f] underline">撤销归一</button>
                </span>)}</p>}
              <div className="mt-3 flex flex-wrap items-center gap-2 border-b pb-3 text-sm">
                <input aria-label="主体审核依据" value={reviewNote} onChange={(e) => setReviewNote(e.target.value)} placeholder="资格或身份核验依据" className="rounded border p-2" />
                <select aria-label="科研机构类型" value={researchType} onChange={(e) => setResearchType(e.target.value)} className="rounded border p-2">
                  <option value="public_research">科研院所／实验室</option><option value="university">高等学校</option><option value="corporate_research">企业研发机构</option>
                </select>
                <input aria-label="资格一手来源" value={basisUrl} onChange={(e) => setBasisUrl(e.target.value)} placeholder="https:// 官方资格依据" className="min-w-60 rounded border p-2" />
                <label className="flex items-center gap-1"><input type="checkbox" checked={mainlandConfirmed} onChange={(e) => setMainlandConfirmed(e.target.checked)} />官网确认位于中国内地</label>
                <button type="button" disabled={busy || reviewNote.trim().length < 3 || !basisUrl.startsWith("https://") || !mainlandConfirmed}
                  onClick={() => void act(() => impactApi.eligibility(selectedEntity.id, "eligible", reviewNote, researchType, basisUrl, mainlandConfirmed))} className="text-[#17658f] underline">确认入榜资格</button>
                <button type="button" disabled={busy || reviewNote.trim().length < 3} onClick={() => void act(() => impactApi.eligibility(selectedEntity.id, "excluded", reviewNote))} className="text-[#a44949] underline">排除主体</button>
              </div>
              {selectedRankingRow?.review_cases.filter((c) => c.kind === "possible_duplicate").map((c) => <div key={c.id} className="mt-3 rounded border border-amber-200 bg-amber-50 p-3 text-sm">
                <p>{c.reason}</p>
                <button type="button" disabled={busy || reviewNote.trim().length < 3 || !basisUrl.startsWith("https://")}
                  onClick={() => void act(() => impactApi.reviewIdentity(c.id, "confirmed_duplicate", reviewNote, selectedEntity.id, basisUrl))}
                  className="mr-4 mt-2 text-[#8a5d18] underline">按此主体归一身份</button>
                <button type="button" disabled={busy || reviewNote.trim().length < 3} onClick={() => void act(() => impactApi.reviewIdentity(c.id, "false_positive", reviewNote))} className="text-[#17658f] underline">核实为不同主体</button>
              </div>)}
              <h3 className="mt-4 font-semibold">逐期分数</h3>
              <div className="mt-2 flex flex-wrap gap-2">{selectedEntity.history.map((score) => <div key={score.id} className="rounded border bg-[#f8fbfd] p-3 text-xs">
                <strong>{score.scan_date} · {score.revision ? score.tier ? `重评分级 ${score.tier}` : "身份归一证据重建" : score.tier}</strong>
                {score.revision ? <p>截至当期可核事件 {score.input_event_ids?.length ?? 0} 条 · 来源原评分 {score.source_period_ids?.length ?? 0} 期；{score.calibration_status === "calibrated" ? "已校准" : "新分数待校准"}</p>
                  : <p>成就 {score.eff_achievement} / 地位 {score.eff_status} / 趋势 {score.eff_trend}</p>}
                {score.entity_id !== selectedEntity.id && <p className="text-amber-800">别名记录原分 · 不参与合并计算</p>}
                <p>{score.change_reason || "无等级变动"} · {score.calibration_status}</p>
                {Object.entries(score.reasons).map(([key, reason]) => <p key={key}>{key}：{reason}</p>)}
              </div>)}</div>
              <h3 className="mt-4 font-semibold">事件与原始来源</h3>
              <div className="mt-2 max-h-80 space-y-3 overflow-y-auto">{selectedEntity.events.map((event) => <article key={event.id} className="border-b pb-2 text-sm">
                <p>{event.event_date || "无日期"} · {event.title} {event.is_flagship ? "[旗舰标记待核]" : ""}</p>
                <p className="text-[#607486]">{event.summary}</p>
                {event.sources.filter((source) => /^https?:\/\//i.test(source.url)).map((source) => <a key={source.id} href={source.url} target="_blank" rel="noreferrer" className="mr-3 text-[#17658f] underline">{source.publisher || source.title || "原始来源"}</a>)}
              </article>)}</div>
            </section>}
          </section>}
          {tab === "tree" && <section className="grid gap-4 lg:grid-cols-[1fr_350px]">
            <div className="rounded-xl border bg-white p-5"><h2 className="font-semibold">L1 → L3 来源树</h2>
              <p className="mb-3 text-xs text-[#607486]">源 L1“高原／高峰”只是来源分类；AI4S 六大领域映射在 L2，L3 不自动写入战略地图子领域。另有 {unclassifiedEvents} 条无方向事件保留待审。</p>
              {roots.map((root) => <div key={root.id} className="mt-4 border-t pt-3"><h3 className="font-semibold">{root.name}</h3>
                {directions.filter((d) => d.parent_id === root.id && (!embedded || scopedL2Ids.has(d.id))).map((l2) => <div key={l2.id} className="ml-4 mt-2 rounded border p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2"><span className="font-medium">{l2.name} <small>· {l2.review_status}</small></span>
                    <div className="flex gap-3"><button type="button" onClick={() => void openTreeEvents(l2.id, l2.name)} className="text-xs text-[#17658f] underline">{l2.event_count} 条事件</button>
                      <button type="button" disabled={busy || reviewNote.trim().length < 3} onClick={() => void act(() => impactApi.reviewDirection(l2.id, "approve", reviewNote))} className="text-xs text-[#17658f] underline">确认映射</button></div></div>
                  <div className="mt-2 flex flex-wrap gap-2">{directions.filter((d) => d.parent_id === l2.id).map((l3) =>
                    <div key={l3.id} className="rounded bg-[#edf4f8] px-2 py-1 text-xs">{l3.name} · {l3.review_status}
                      <button type="button" onClick={() => void openTreeEvents(l3.id, l3.name)} className="ml-2 underline">{l3.event_count} 条事件</button>
                      <button type="button" disabled={busy || reviewNote.trim().length < 3} onClick={() => void act(() => impactApi.reviewDirection(l3.id, "approve", reviewNote))} className="ml-2 underline">确认</button></div>)}</div>
                </div>)}</div>)}
            </div>
            <aside className="space-y-4"><div className="rounded-xl border bg-white p-4"><label className="text-sm font-semibold">审核依据
              <textarea aria-label="审核依据" value={reviewNote} onChange={(e) => setReviewNote(e.target.value)} className="mt-2 w-full rounded border p-2" rows={3} placeholder="填写来源或核验理由" /></label></div>
            <div className="rounded-xl border bg-white p-4"><h3 className="font-semibold">L3 候选标签</h3>
              <p className="mt-1 text-xs text-[#607486]">当前显示事件数至少 3 的候选，仅人工审核可固化；该阈值待校准。</p>
              {candidates.filter((c) => !embedded || scopedL2Ids.has(c.parent_id)).map((c) => <div key={c.parent_id + c.name} className="mt-3 border-t pt-2 text-sm">
                <p>{nameById.get(c.parent_id)} / {c.name} · {c.event_count} 事件 · {c.independent_publishers} 个来源机构 · {c.active_dates} 个事件日期</p>
                {!c.multi_source_ready && <p className="text-xs text-amber-800">独立来源或持续日期不足；请在审核时说明证据局限。</p>}
                <button type="button" onClick={() => void openTreeEvents(c.parent_id, c.name, c.name)} className="mr-3 text-[#17658f] underline">查看事件</button>
                <button type="button" disabled={busy || reviewNote.trim().length < 3} onClick={() => void act(() => impactApi.reviewCandidate(c.parent_id, c.name, "approve", reviewNote))} className="mr-3 text-[#17658f] underline">通过</button>
                <button type="button" disabled={busy || reviewNote.trim().length < 3} onClick={() => void act(() => impactApi.reviewCandidate(c.parent_id, c.name, "reject", reviewNote))} className="text-[#a44949] underline">驳回</button>
              </div>)}
              {candidates.length === 0 && <p className="mt-3 text-sm text-[#607486]">当前无候选</p>}
            </div>
            {!embedded && <div className="rounded-xl border bg-white p-4"><h3 className="font-semibold">树外 L2 标签</h3>
              <p className="mt-1 text-xs text-[#607486]">保留原事件的待归类标签。精确同名数仅供审核，语义聚类及新 L2 固化须先确定领域映射和阈值。</p>
              <button type="button" onClick={() => void openPoolEvents()} className="mt-2 text-xs text-[#17658f] underline">查看全部 {unclassifiedEvents} 条树外事件</button>
              {l2Candidates.map((c) => <div key={c.name} className="mt-2 border-t pt-2 text-xs">
                <button type="button" onClick={() => void openPoolEvents(c.name)} className="text-[#17658f] underline">{c.name} · {c.event_count} 条</button>
                {c.name_collision ? <span className="ml-2 text-amber-700">与现有节点撞名</span> : null}
                {c.name_collision ? directions.filter((d) => d.level === 3 && d.name === c.name).map((match) =>
                  <button key={match.id} type="button" disabled={busy || reviewNote.trim().length < 3}
                    onClick={() => void act(() => impactApi.resolveExisting(c.name, match.id, reviewNote))}
                    className="ml-2 text-[#17658f] underline">归入已有 L3 · 可回滚</button>) : null}
              </div>)}
            </div>}
            {treeEventTitle && <div aria-label="方向事件明细" className="rounded-xl border bg-white p-4"><h3 className="font-semibold">{treeEventTitle} · 事件明细</h3>
              <p className="text-xs text-[#607486]">最多显示 100 条，按事件日期倒序；来源链接指向原始页面。</p>
              <div className="mt-2 max-h-80 space-y-2 overflow-y-auto">{treeEvents.map((event) => <article key={event.id} className="border-t pt-2 text-xs">
                <p>{event.event_date || "无日期"} · {event.entity_name || "未归属主体"} · {event.title} {event.is_flagship ? "[旗舰标记待核]" : ""}</p>
                {event.sources.filter((source) => /^https?:\/\//i.test(source.url)).map((source, index) => <a key={index} href={source.url} target="_blank" rel="noreferrer" className="mr-2 text-[#17658f] underline">{source.publisher || source.title || "原始来源"}</a>)}
              </article>)}</div>
            </div>}
            <div className="rounded-xl border bg-white p-4"><h3 className="font-semibold">审核记录与回滚</h3>
              {eventMappings.filter((a) => !embedded || scopedDirections.some((d) => d.id === a.target_direction_id)).map((a) => <div key={`mapping-${a.id}`} className="mt-2 border-t pt-2 text-xs"><p>{a.reviewed_on} · {a.label} → 已有 L3 · 事件归一</p>
                {!a.reverted_on && <button type="button" disabled={busy} onClick={() => void rollbackMapped(a.id)} className="text-[#17658f] underline">回滚事件归一</button>}</div>)}
              {audits.filter((a) => !embedded || scopedDirections.some((d) => d.id === a.direction_id)).map((a) => <div key={a.id} className="mt-2 border-t pt-2 text-xs"><p>{a.reviewed_on} · {nameById.get(a.direction_id) || a.direction_id} · {a.action}</p>
                {!a.reverted_on && <button type="button" disabled={busy} onClick={() => void rollback(a.id)} className="text-[#17658f] underline">回滚此项</button>}</div>)}
            </div>
            </aside>
          </section>}
          {tab === "daily" && <section className="rounded-xl border bg-white p-5">
            <label className="text-sm">报告日期 <input aria-label="报告日期" type="date" className="ml-2 rounded border p-2" value={reportDate} onChange={(e) => setReportDate(e.target.value)} /></label>
            {embedded ? <>
              <p className="mt-2 text-xs text-[#607486]">{combinedReport?.frozen ? `固定快照 · 修订版 ${combinedReport.revision}` : "当前为未冻结预览；固定后补证据会产生修订版"}</p>
              <div className="mt-4 grid gap-3 sm:grid-cols-3">
                <div className="rounded-xl bg-[#eef7f9] p-4"><strong className="text-2xl text-[#13667e]">{dailyImpactEvents.length}</strong><p className="text-xs text-[#607889]">{domainId ? "当前领域机构事件" : "全库机构事件"}</p></div>
                <div className="rounded-xl bg-[#eef7f9] p-4"><strong className="text-2xl text-[#13667e]">{dailyTeamChanges.length}</strong><p className="text-xs text-[#607889]">{domainId ? "当前领域" : "全部领域"}团队更新</p></div>
                <div className="rounded-xl bg-[#eef7f9] p-4"><strong className="text-2xl text-[#13667e]">{dailyRecommendationChanges.length}</strong><p className="text-xs text-[#607889]">推荐名单变化</p></div>
              </div>
              <h3 className="mt-5 font-semibold">领域摘要</h3>
              <div className="mt-2 grid gap-2 lg:grid-cols-2">{Object.entries(combinedReport?.domainSummaries || {})
                .filter(([id]) => !domainId || id === domainId).map(([id, value]) =>
                  <p key={id} className="rounded-xl bg-[#f0f7f9] p-3 text-sm leading-6"><strong className="text-[#155e77]">{value.domainName}</strong><br />{value.summary}</p>)}</div>
              <h3 className="mt-5 font-semibold">团队与推荐变化</h3>
              {dailyTeamChanges.map((item, index) => <p key={`${item.teamId}-${index}`} className="mt-2 rounded-lg border p-3 text-sm">{item.institutionName} · {item.teamName}：{item.reason}{item.scoreAfter != null ? `；团队分 ${item.scoreBefore ?? "待核"} → ${item.scoreAfter}（${item.scoreVersion || "原版本"}）` : ""}</p>)}
              {dailyRecommendationChanges.map((item, index) => <p key={`${item.taskText}-${index}`} className="mt-2 rounded-lg border p-3 text-sm">“{item.taskText}”：{item.reason}（原 {item.before.length} 支 → 新 {item.after.length} 支）</p>)}
              {!dailyTeamChanges.length && !dailyRecommendationChanges.length && <p className="mt-2 text-sm text-[#6b818e]">当前范围暂无已审核团队或推荐变化。</p>}
              <h3 className="mt-5 font-semibold">后续核验清单</h3>
              {dailyFollowUps.map((item) => <p key={item} className="mt-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">{item}</p>)}
              {!dailyFollowUps.length && <p className="mt-2 text-sm text-[#6b818e]">暂无新增跟进事项。</p>}
              {combinedReport?.inputVersions && <details className="mt-4 rounded-xl border p-3 text-xs text-[#607486]"><summary className="cursor-pointer font-medium">查看日报输入版本</summary>
                <p className="mt-2">入库批次 {combinedReport.inputVersions.impactBatchIds.length} · 类目审核 {combinedReport.inputVersions.taxonomyAuditIds.length} · 团队审核期次 {combinedReport.inputVersions.teamResearchRunIds.length} · 推荐快照 {combinedReport.inputVersions.recommendationRunIds.length} · 团队证据版本 {combinedReport.inputVersions.teamEvidenceVersion || "暂无"}</p></details>}
              <details className="mt-5 rounded-xl border p-4"><summary className="cursor-pointer text-sm font-semibold">查看机构影响力全库日报与来源</summary>
                <div className="prose mt-4 max-w-none"><ReactMarkdown>{report?.markdown || "正在加载日报…"}</ReactMarkdown></div></details>
            </> : <>
              <p className="mt-2 text-xs text-[#607486]">根据当日已入库批次、审核记录及 AI4S 评分期次确定性生成；无数据时明确显示无更新。</p>
              <div className="prose mt-5 max-w-none whitespace-normal"><ReactMarkdown>{report?.markdown || "正在加载日报…"}</ReactMarkdown></div>
            </>}
          </section>}
        </>}
      </div>
    </main>
  );
}
