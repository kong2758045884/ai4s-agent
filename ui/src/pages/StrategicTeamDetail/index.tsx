import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ArrowLeft, ExternalLink, MapPin, UsersRound } from "lucide-react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import "../StrategicMap/mobile.css";
import {
  loadStrategicTeamDetail, mapTeam, reviewedTeamPeople,
  type StrategicPerson, type StrategicTeam, type StrategicTeamDetail as TeamDetail,
} from "@/services/strategicMap";
import { recommendationApi, type PublishedTeamClaim } from "@/services/strategicRecommendations";
import { buildStrategicMapPath, readStrategicTeamDetailSource } from "@/router/strategicMapNavigation";
import { teamDirectionSections } from "./directions";

function SourceLink({ url, children }: { url: string; children: ReactNode }) {
  if (!/^https?:\/\//i.test(url)) return null;
  return <a href={url} target="_blank" rel="noreferrer" className="inline-flex min-h-11 max-w-full items-center gap-1 break-all text-[14px] font-medium text-[#216993] underline-offset-2 hover:underline focus-visible:underline">
    {children}<ExternalLink className="size-4 shrink-0" aria-hidden="true" />
  </a>;
}

function PersonCard({ person, leader = false }: { person: StrategicPerson; leader?: boolean }) {
  const [avatarFailed, setAvatarFailed] = useState(false);
  const urls = [...new Set(person.sourceUrls.filter((url) => /^https?:\/\//i.test(url)))];
  return <article className="flex min-w-0 items-start gap-3 border-b border-[#e7eef3] py-4 last:border-b-0">
    <div className="flex size-12 shrink-0 items-center justify-center overflow-hidden rounded-full bg-[#e8f2f8] font-semibold text-[#23668f]">
      {person.avatarUrl && !avatarFailed ? <img src={person.avatarUrl} alt={person.name} className="h-full w-full object-cover" onError={() => setAvatarFailed(true)} /> : person.name.trim().slice(0, 1) || "人"}
    </div>
    <div className="min-w-0 flex-1 text-[14px] leading-6 text-[#3b566a]">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1"><h4 className="break-words text-[16px] font-semibold text-[#183f5a]">{person.name}</h4>{person.title && <span className="break-words text-[#63798a]">{person.title}</span>}</div>
      <p className="mt-0.5 font-medium text-[#2d6b92]">{person.role || (leader ? "团队负责人" : "团队成员")}</p>
      {person.researchDirection && <p className="mt-2 whitespace-pre-wrap break-words">研究方向：{person.researchDirection}</p>}
      {person.bio && <p className="mt-2 whitespace-pre-wrap break-words">{person.bio}</p>}
      <div className="mt-1 flex flex-wrap gap-x-4">
        {person.profileUrl && <SourceLink url={person.profileUrl}>官方主页</SourceLink>}
        {urls.filter((url) => url !== person.profileUrl).map((url, index) => <SourceLink key={url} url={url}>人员来源{urls.length > 1 ? ` ${index + 1}` : ""}</SourceLink>)}
      </div>
    </div>
  </article>;
}

function TeamBasics({ team, leaders }: { team: StrategicTeam; leaders: StrategicPerson[] }) {
  const [expanded, setExpanded] = useState(false);
  const description = team.description?.trim() || "";
  const longDescription = description.length > 650;
  return <section aria-label="团队身份与简介" className="rounded-xl border border-[#d5e3ec] bg-white px-5 py-5 sm:px-7 sm:py-6">
    <p className="break-words text-[14px] font-medium text-[#607c91]">{team.institutionName || team.organization || "所属机构待核实"}</p>
    <h1 className="mt-1 break-words text-[26px] font-semibold leading-tight text-slate-900 sm:text-[30px]">{team.teamName || team.name}</h1>
    <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1 text-[14px] leading-6 text-[#5a7182]">
      {team.location && <span className="inline-flex items-center gap-1"><MapPin className="size-4" aria-hidden="true" />{team.location}</span>}
      <span>{leaders.length ? `负责人：${leaders.map((person) => person.name).join("、")}` : "负责人资料待核实"}</span>
      {team.recentUpdate && <span>资料更新：{team.recentUpdate}</span>}
      {team.source && <span>资料来源：{team.source}</span>}
    </div>
    <div className="mt-5 border-t border-[#e6edf3] pt-5">
      <h2 className="text-[18px] font-semibold text-[#174f70]">团队简介</h2>
      {description ? <>
        <p className="mt-3 max-w-[84ch] whitespace-pre-wrap break-words text-[15px] leading-7 text-[#36536a]">{longDescription && !expanded ? `${description.slice(0, 420)}…` : description}</p>
        {longDescription && <button type="button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)} className="mt-2 min-h-11 text-[14px] font-medium text-[#216993] hover:underline">{expanded ? "收起简介" : "查看完整简介"}</button>}
      </> : <p className="mt-3 text-[14px] text-[#62798b]">暂无已收录的团队简介。</p>}
    </div>
  </section>;
}

function ClaimItem({ claim }: { claim: PublishedTeamClaim }) {
  const provenance = claim.provenance || {};
  const displayTitle = claim.kind !== "outcome" && claim.title.length > 100
    ? provenance.sourceTitle && provenance.sourceTitle.length <= 100
      ? provenance.sourceTitle
      : claim.kind === "outcome" ? "成果资料原文" : "团队档案原文"
    : claim.title || "来源引文";
  const human = provenance.humanReview?.status === "reviewed"
    ? provenance.humanReview.decision === "supported" ? "人工复核：支持" : provenance.humanReview.decision === "conditional" ? "人工复核：有条件" : provenance.humanReview.decision === "rejected" ? "人工复核：不支持" : "人工复核已记录"
    : provenance.humanReview?.status === "source_changed" ? "来源变化，人工结论待复核" : "人工复核待记录";
  const source = provenance.sourceCheck?.status === "recorded" ? "来源检查已记录" : "来源检查待核实";
  const model = provenance.modelReview?.status === "reviewed" ? "AI 复核已记录" : provenance.modelReview?.status === "not_used" ? "AI 复核未使用" : "AI 复核待记录";
  const link = provenance.linkCheck?.status === "broken" || provenance.linkCheck?.status === "failed" ? "来源链接失效" : provenance.linkCheck?.status === "checked" ? "链接检查已记录" : "链接未实时检查";
  return <li className="min-w-0 border-b border-[#e7eef3] py-4 last:border-b-0">
    <h3 className="break-words text-[16px] font-semibold leading-6 text-[#234e6b]">{displayTitle}</h3>
    <p className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[13px] leading-5 text-[#60798a]">
      {claim.publishedAt && <span>发表：{claim.publishedAt}</span>}<span>{source}</span><span>{model}</span><span>{human}</span><span>{link}</span>
    </p>
    <details className="mt-2 group">
      <summary className="flex min-h-11 w-fit cursor-pointer list-none items-center text-[14px] font-medium text-[#216993] hover:underline focus-visible:underline">查看引文与核验记录 <span className="ml-1 text-[12px] group-open:rotate-90">›</span></summary>
      {claim.kind !== "outcome" && claim.title.length > 100 && claim.title !== claim.quote && <p className="mt-2 max-w-[84ch] whitespace-pre-wrap break-words text-[14px] leading-6 text-[#526d80]">资料记录：{claim.title}</p>}
      <blockquote className="mt-2 max-w-[84ch] whitespace-pre-wrap break-words border-l-[3px] border-[#c7dce9] bg-[#f7fafc] px-4 py-3 text-[15px] leading-7 text-[#36536a]">{claim.quote || "未收录原文引文"}</blockquote>
      <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1 text-[13px] text-[#61798a]">
        {provenance.sourceTitle && <span className="break-words">来源标题：{provenance.sourceTitle}</span>}
        {provenance.fetchedAt && <span>采集：{provenance.fetchedAt.slice(0, 10)}</span>}
        <SourceLink url={claim.url}>打开来源原文</SourceLink>
      </div>
    </details>
  </li>;
}

export default function StrategicTeamDetail() {
  const navigate = useNavigate();
  const location = useLocation();
  const { teamId } = useParams<{ teamId: string }>();
  const [detail, setDetail] = useState<TeamDetail | null>(null);
  const [claims, setClaims] = useState<PublishedTeamClaim[]>([]);
  const [loading, setLoading] = useState(true);
  const [claimsLoading, setClaimsLoading] = useState(true);
  const [error, setError] = useState("");
  const [claimsError, setClaimsError] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [showAllSources, setShowAllSources] = useState(false);

  useEffect(() => {
    let disposed = false;
    const controller = new AbortController();
    setLoading(true); setClaimsLoading(true); setDetail(null); setClaims([]); setShowAll(false); setShowAllSources(false);
    if (!teamId) {
      setError("缺少团队编号"); setLoading(false); setClaimsLoading(false);
      return () => { disposed = true; controller.abort(); };
    }
    Promise.all([loadStrategicTeamDetail(teamId, { signal: controller.signal }), recommendationApi.verifiedTeams(controller.signal)])
      .then(([value, catalogue]) => {
        if (disposed) return;
        if (!catalogue.teamIds.includes(teamId)) throw new Error("该团队尚未发布，请返回团队资料选择其他团队。");
        const published = catalogue.teams?.find((team) => team.id === teamId);
        setDetail({
          ...value,
          team: published ? mapTeam(published) : value.team,
          leaders: reviewedTeamPeople(value.leaders),
          leader: reviewedTeamPeople(value.leader ? [value.leader] : [])[0] || null,
          members: reviewedTeamPeople(value.members)
        });
        setError("");
        return recommendationApi.teamClaims(teamId, controller.signal)
          .then((result) => { if (!disposed) { setClaims(result.items); setClaimsError(""); } })
          .catch((reason) => { if (!disposed) setClaimsError(reason instanceof Error ? reason.message : "依据暂时无法读取"); })
          .finally(() => { if (!disposed) setClaimsLoading(false); });
      })
      .catch((reason) => { if (!disposed) { setError(reason instanceof Error ? reason.message : "读取团队详情失败"); setClaimsLoading(false); } })
      .finally(() => { if (!disposed) setLoading(false); });
    return () => { disposed = true; controller.abort(); };
  }, [teamId]);

  const sourceContext = useMemo(() => readStrategicTeamDetailSource(location.search, teamId ?? ""), [location.search, teamId]);
  const returnContext = useMemo(() => ({
    ...sourceContext,
    mode: sourceContext.mode || (sourceContext.assessmentId ? "recommend" as const : "teams" as const),
    domainId: sourceContext.domainId || detail?.domain?.id || detail?.team.domainId || "",
    subdomainId: sourceContext.domainId ? sourceContext.subdomainId : detail?.subdomain?.id || detail?.team.subdomainId || "",
    teamId: sourceContext.teamId || detail?.team.id || teamId || "",
  }), [detail, sourceContext, teamId]);
  const returnPath = useMemo(() => buildStrategicMapPath(returnContext), [returnContext]);
  const returnLabel = returnContext.assessmentId ? "返回研判" : returnContext.mode === "graph" ? "返回关系图" : "返回团队资料";
  const leaders = detail?.leaders ?? (detail?.leader ? [detail.leader] : []);
  const visibleMembers = showAll ? detail?.members || [] : detail?.members.slice(0, 6) || [];
  const outcomes = claims.filter((claim) => claim.kind === "outcome");
  const otherClaims = claims.filter((claim) => claim.kind !== "outcome");
  const visibleOtherClaims = showAllSources ? otherClaims : otherClaims.slice(0, 5);
  const directionSections = detail ? teamDirectionSections(detail.team) : [];

  return <div className="strategic-team-detail flex h-full min-h-0 min-w-0 flex-col overflow-y-auto bg-[#f7f9fb] text-slate-900">
    <header className="shrink-0 border-b border-slate-200 bg-white px-4 py-2 sm:px-6">
      <div className="mx-auto flex w-full max-w-[1180px] min-w-0 items-center gap-3">
        <button type="button" onClick={() => navigate(returnPath)} className="inline-flex min-h-11 shrink-0 items-center gap-1.5 rounded-lg px-2 text-[14px] font-medium text-slate-700 hover:bg-slate-50" aria-label={returnLabel}>
          <ArrowLeft className="size-4" aria-hidden="true" />{returnLabel}
        </button>
        <span className="hidden min-w-0 truncate text-[13px] text-slate-500 sm:block">{detail?.domain?.name || "团队档案"}</span>
      </div>
    </header>
    <main className="mx-auto flex w-full max-w-[1180px] min-w-0 flex-1 flex-col gap-4 p-3 sm:gap-5 sm:p-5 lg:p-7">
      {loading && <div role="status" className="rounded-xl border border-[#d5e3ec] bg-white p-8 text-center text-[14px] text-[#6b8293]">正在读取团队详情…</div>}
      {error && <div role="alert" className="rounded-xl border border-[#efcaca] bg-[#fff7f7] p-5 text-[14px] text-[#b44747]">{error}</div>}
      {detail && <>
        <TeamBasics key={detail.team.id} team={detail.team} leaders={leaders} />
        <div className="min-w-0 rounded-xl border border-[#d5e3ec] bg-white px-5 sm:px-7">
          <section aria-label="研究方向" className="border-b border-[#e2eaf0] py-6">
            <h2 className="text-[20px] font-semibold text-[#174f70]">研究方向</h2>
            {directionSections.length ? <div className="mt-4 space-y-5">
              {directionSections.map((section, index) => <div key={`${section.title}-${index}`} className="max-w-[84ch] border-l-[3px] border-[#b7d6e9] pl-4">
                {section.title && <h3 className="mb-2 text-[16px] font-semibold text-[#234e6b]">{section.title}</h3>}
                {section.paragraphs.map((paragraph, paragraphIndex) => <p key={paragraphIndex} className="mb-2 whitespace-pre-wrap break-words text-[15px] leading-7 text-[#36536a] last:mb-0">{paragraph}</p>)}
              </div>)}
            </div> : <p className="mt-3 text-[14px] text-[#62798b]">暂无已收录的研究方向。</p>}
          </section>
          <section aria-label="代表成果与依据" className="border-b border-[#e2eaf0] py-6">
            <div className="flex flex-wrap items-baseline justify-between gap-2"><h2 className="text-[20px] font-semibold text-[#174f70]">代表成果与依据</h2>{!claimsLoading && !claimsError && <span className="text-[14px] text-[#60798a]">已收录 {outcomes.length} 条</span>}</div>
            {claimsLoading ? <p role="status" className="mt-3 text-[14px] text-[#62798b]">正在读取成果依据…</p> : claimsError ? <p role="alert" className="mt-3 text-[14px] text-[#a04444]">{claimsError}，请稍后重新打开档案。</p> : outcomes.length
              ? <ul className="mt-2">{outcomes.map((claim) => <ClaimItem key={claim.id} claim={claim} />)}</ul>
              : <p className="mt-3 text-[14px] leading-6 text-[#62798b]">当前未收录该团队的代表成果依据。团队身份与研究方向资料不能代替成果证明。</p>}
          </section>
          <section aria-label="人员与归属" className="border-b border-[#e2eaf0] py-6">
            <div className="flex items-center gap-2"><UsersRound className="size-5 text-[#347ba4]" aria-hidden="true" /><h2 className="text-[20px] font-semibold text-[#174f70]">人员与归属</h2></div>
            <h3 className="mt-5 text-[16px] font-semibold text-[#234e6b]">团队负责人</h3>
            {leaders.length ? <div className="grid min-w-0 gap-x-7 md:grid-cols-2">{leaders.map((person) => <PersonCard key={person.id} person={person} leader />)}</div> : <p className="mt-2 text-[14px] text-[#62798b]">暂无可公开核验的负责人资料。</p>}
            <div className="mt-5 flex flex-wrap items-baseline justify-between gap-2"><h3 className="text-[16px] font-semibold text-[#234e6b]">已收录成员</h3><span className="text-[14px] text-[#60798a]">{detail.members.length} 人</span></div>
            {detail.members.length ? <div className="grid min-w-0 gap-x-7 md:grid-cols-2">{visibleMembers.map((person) => <PersonCard key={person.id} person={person} />)}</div> : <p className="mt-2 text-[14px] text-[#62798b]">暂无已收录的成员资料。</p>}
            {detail.members.length > 6 && <button type="button" aria-expanded={showAll} onClick={() => setShowAll((value) => !value)} className="mt-2 min-h-11 rounded-lg border border-[#bfd4e2] px-4 text-[14px] font-medium text-[#216993] hover:bg-[#f2f8fc]">{showAll ? "收起成员" : `查看全部 ${detail.members.length} 位成员`}</button>}
          </section>
          <section aria-label="来源、联系与核验" className="py-6">
            <h2 className="text-[20px] font-semibold text-[#174f70]">来源、联系与核验</h2>
            <p className="mt-3 text-[14px] leading-6 text-[#526d80]">公开合作与联系记录：未收录；资源可用性和合作意愿待人工确认。</p>
            {!!detail.team.sourceUrls.length && <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1">{[...new Set(detail.team.sourceUrls)].map((url, index) => <SourceLink key={url} url={url}>团队资料来源{detail.team.sourceUrls.length > 1 ? ` ${index + 1}` : ""}</SourceLink>)}</div>}
            {claimsLoading ? <p className="mt-3 text-[14px] text-[#62798b]">正在读取来源记录…</p> : claimsError ? <p className="mt-3 text-[14px] text-[#a04444]">来源记录暂时无法读取。</p> : otherClaims.length
              ? <><ul className="mt-3">{visibleOtherClaims.map((claim) => <ClaimItem key={claim.id} claim={claim} />)}</ul>
                {otherClaims.length > 5 && <button type="button" aria-expanded={showAllSources} onClick={() => setShowAllSources((value) => !value)} className="mt-2 min-h-11 rounded-lg border border-[#bfd4e2] px-4 text-[14px] font-medium text-[#216993] hover:bg-[#f2f8fc]">{showAllSources ? "收起来源记录" : `查看全部 ${otherClaims.length} 条来源记录`}</button>}</>
              : <p className="mt-3 text-[14px] text-[#62798b]">暂无逐条收录的身份与方向引文；请查看上方资料来源。</p>}
          </section>
        </div>
      </>}
    </main>
  </div>;
}
