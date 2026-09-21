import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, ExternalLink, MapPin, UsersRound } from "lucide-react";
import { useLocation, useNavigate, useParams } from "react-router-dom";

import {
  loadStrategicTeamDetail,
  type StrategicPerson,
  type StrategicTeam,
  type StrategicTeamDetail as TeamDetail,
} from "@/services/strategicMap";
import {
  buildStrategicMapPath,
  buildStrategicTeamDetailNavigationPath,
  readStrategicTeamDetailSource,
} from "@/router/strategicMapNavigation";

function PersonAvatar({ person, size = "size-14" }: { person: StrategicPerson; size?: string }) {
  const [failed, setFailed] = useState(false);
  const initials = person.name.trim().slice(0, 1) || "人";
  return (
    <div className={`${size} flex shrink-0 items-center justify-center overflow-hidden rounded-full bg-[#e8f2f8] font-semibold text-[#23668f]`}>
      {person.avatarUrl && !failed ? (
        <img
          src={person.avatarUrl}
          alt={person.name}
          className="h-full w-full object-cover"
          onError={() => setFailed(true)}
        />
      ) : initials}
    </div>
  );
}

function PersonCard({ person, leader = false }: { person: StrategicPerson; leader?: boolean }) {
  return (
    <article className="flex min-w-0 items-start gap-3 rounded-xl border border-[#dbe7ef] bg-white p-4">
      <PersonAvatar person={person} />
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
          <h3 className="break-words text-[16px] font-semibold text-[#24455d]">{person.name}</h3>
          {person.title ? <span className="break-words text-[12px] text-[#6a8194]">{person.title}</span> : null}
        </div>
        <p className="mt-1 break-words text-[12px] font-medium text-[#3f789b]">{person.role || (leader ? "团队负责人" : "核心成员")}</p>
        {person.researchDirection ? <p className="mt-2 break-words text-[13px] leading-5 text-[#4d6577]">研究方向：{person.researchDirection}</p> : null}
        {person.bio ? <p className="mt-2 break-words text-[13px] leading-5 text-[#617687]">{person.bio}</p> : null}
        {person.profileUrl ? (
          <a href={person.profileUrl} target="_blank" rel="noreferrer" className="mt-2 inline-flex max-w-full items-center gap-1 break-all text-[12px] text-[#2c76a6] hover:underline">
            官方主页 <ExternalLink className="size-3.5" />
          </a>
        ) : null}
        {person.sourceUrls.length ? (
          <p className="mt-2 break-all text-[11px] text-[#8597a5]">来源：{person.sourceType || "公开来源"}</p>
        ) : null}
      </div>
    </article>
  );
}

function TeamBasics({ team }: { team: StrategicTeam }) {
  const directions = team.researchDirections?.length ? team.researchDirections : [team.focus].filter(Boolean);
  return (
    <section className="rounded-2xl border border-[#d5e3ec] bg-white p-5 shadow-[0_4px_16px_rgba(27,64,96,0.05)] sm:p-6">
      {team.verificationStatus === "legacy_unverified" ? (
        <p role="note" className="mb-4 rounded-lg bg-amber-50 px-3 py-2 text-[13px] text-amber-800">历史线索 · 待审核。以下为原有记录，尚未通过新版证据审核；原人工信息已保留。</p>
      ) : null}
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="break-words text-[13px] font-medium text-[#668096]">{team.institutionName || team.organization || team.name}</p>
          <h1 className="mt-1 break-words text-[24px] font-semibold leading-tight text-[#174f70] sm:text-[30px]">{team.teamName || "团队信息待核实"}</h1>
        </div>
        <div className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-[#eef7fd] px-3 py-1.5 text-[12px] font-semibold text-[#2c6a98]">
          <MapPin className="size-3.5" />
          {team.location || "中国"}
        </div>
      </div>
      <p className="mt-5 break-words text-[14px] leading-7 text-[#456074]">{team.description || "暂无团队简介，等待更多官方资料确认。"}</p>
      <div className="mt-5 grid min-w-0 gap-4 border-t border-[#e7eef3] pt-4 sm:grid-cols-2">
        <div className="min-w-0">
          <h2 className="text-[12px] font-semibold tracking-wide text-[#71899a]">核心研究方向</h2>
          <div className="mt-2 flex min-w-0 flex-wrap gap-2">
            {directions.length ? directions.map((direction) => <span key={direction} className="max-w-full break-words rounded-full bg-[#f1f7fb] px-2.5 py-1 text-[12px] text-[#326b8d]">{direction}</span>) : <span className="text-[13px] text-[#7d909f]">暂无已确认方向</span>}
          </div>
        </div>
        <div className="min-w-0 text-[13px] leading-6 text-[#617687]">
          <div>最近更新时间：{team.recentUpdate || "暂无"}</div>
          <div className="break-words">数据来源：{team.source || "公开来源"}</div>
        </div>
      </div>
    </section>
  );
}

export default function StrategicTeamDetail() {
  const navigate = useNavigate();
  const location = useLocation();
  const { teamId } = useParams<{ teamId: string }>();
  const [detail, setDetail] = useState<TeamDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    let disposed = false;
    if (!teamId) {
      setError("缺少团队编号");
      setLoading(false);
      return () => { disposed = true; };
    }
    setLoading(true);
    loadStrategicTeamDetail(teamId)
      .then((value) => { if (!disposed) { setDetail(value); setError(""); } })
      .catch((reason) => { if (!disposed) setError(reason instanceof Error ? reason.message : "读取团队详情失败"); })
      .finally(() => { if (!disposed) setLoading(false); });
    return () => { disposed = true; };
  }, [teamId]);

  const visibleMembers = useMemo(() => {
    const members = detail?.members ?? [];
    return showAll ? members : members.slice(0, 6);
  }, [detail?.members, showAll]);

  const sourceContext = useMemo(
    () => readStrategicTeamDetailSource(location.search, teamId ?? ""),
    [location.search, teamId],
  );
  const returnContext = useMemo(() => {
    if (sourceContext.domainId) return sourceContext;
    return {
      ...sourceContext,
      domainId: detail?.domain?.id || detail?.team.domainId || "",
      subdomainId: detail?.subdomain?.id || detail?.team.subdomainId || "",
      teamId: detail?.team.id || teamId || "",
    };
  }, [detail, sourceContext, teamId]);
  const returnPath = useMemo(() => buildStrategicMapPath(returnContext), [returnContext]);

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col overflow-x-hidden overflow-y-auto bg-[#f3f6f9] text-[var(--chat-text)]">
      <header className="shrink-0 border-b border-[#1a6683] bg-[#105d79] px-4 py-3 text-white sm:px-6 sm:py-4">
        <div className="mx-auto flex w-full max-w-[1180px] min-w-0 items-center gap-3">
          <button type="button" onClick={() => navigate(returnPath)} className="inline-flex shrink-0 items-center gap-1.5 rounded-lg px-2 py-2 text-[13px] font-medium text-white/90 hover:bg-white/10" aria-label="返回战略图谱">
            <ArrowLeft className="size-4" /> 返回战略图谱
          </button>
          <span className="hidden min-w-0 truncate text-[13px] text-white/70 sm:block">战略图谱{detail?.domain ? ` · ${detail.domain.name}` : ""}</span>
        </div>
      </header>
      <main className="mx-auto flex w-full max-w-[1180px] min-w-0 flex-1 flex-col gap-4 p-3 sm:gap-5 sm:p-5 lg:p-7">
        {loading ? <div className="rounded-2xl border border-[#d5e3ec] bg-white p-8 text-center text-[14px] text-[#6b8293]">正在读取团队详情…</div> : null}
        {error ? <div className="rounded-2xl border border-[#efcaca] bg-[#fff7f7] p-5 text-[14px] text-[#b44747]">{error}</div> : null}
        {detail ? (
          <>
            <TeamBasics team={detail.team} />
            <section className="min-w-0 rounded-2xl border border-[#d5e3ec] bg-[#f9fcfe] p-5 sm:p-6">
              <div className="flex min-w-0 items-center gap-2">
                <UsersRound className="size-5 text-[#347ba4]" />
                <h2 className="text-[19px] font-semibold text-[#174f70]">团队负责人</h2>
              </div>
              <div className="mt-4 min-w-0">
                {detail.leader ? <PersonCard person={detail.leader} leader /> : <p className="rounded-xl border border-dashed border-[#cbdde8] bg-white px-4 py-5 text-[13px] leading-6 text-[#718797]">当前资料尚未可靠确认负责人，页面不会根据机构名称推测个人。</p>}
              </div>
            </section>
            <section className="min-w-0 rounded-2xl border border-[#d5e3ec] bg-white p-5 shadow-[0_4px_16px_rgba(27,64,96,0.04)] sm:p-6">
              <div className="flex min-w-0 flex-wrap items-center justify-between gap-3">
                <h2 className="text-[19px] font-semibold text-[#174f70]">核心成员</h2>
                <span className="text-[12px] text-[#718797]">已确认 {detail.members.length} 人</span>
              </div>
              {detail.members.length ? <div className="mt-4 grid min-w-0 gap-3 md:grid-cols-2">{visibleMembers.map((member) => <PersonCard key={member.id} person={member} />)}</div> : <p className="mt-4 rounded-xl border border-dashed border-[#d2e0e8] bg-[#fbfdff] px-4 py-5 text-[13px] leading-6 text-[#718797]">暂无能够确认属于该团队的核心成员。</p>}
              {detail.members.length > 6 ? <button type="button" onClick={() => setShowAll((value) => !value)} className="mt-4 rounded-lg border border-[#cbdde8] px-3.5 py-2 text-[12px] font-semibold text-[#2e7096] hover:bg-[#f2f8fc]">{showAll ? "收起成员" : `查看更多（${detail.members.length - 6}）`}</button> : null}
            </section>
            <div className="flex flex-wrap items-center gap-2 text-[12px] text-[#718797]">
              <button type="button" onClick={() => navigate(buildStrategicTeamDetailNavigationPath(detail.team.id, returnContext), { replace: true })} className="text-[#2e7096] hover:underline">当前团队编号：{detail.team.id}</button>
              {detail.team.sourceUrls?.[0] ? <a href={detail.team.sourceUrls[0]} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[#2e7096] hover:underline">查看团队来源 <ExternalLink className="size-3" /></a> : null}
            </div>
          </>
        ) : null}
      </main>
    </div>
  );
}
