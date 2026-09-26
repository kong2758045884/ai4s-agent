import { useEffect, useState } from "react";
import {
  CalendarDaysIcon,
  ExternalLinkIcon,
  SearchCheckIcon,
} from "lucide-react";

import {
  loadAi4sDailyToday,
  type Ai4sDailyHomeData,
  type Ai4sDailyHomeHotspot,
} from "@/utils/ai4sDailyHome";

type Ai4sDailyTodayProps = {
  onResearchHotspot?: (hotspot: Ai4sDailyHomeHotspot) => void;
};

function SourceLink({ url }: { url: string }) {
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="inline-flex min-w-0 items-center gap-1 text-[12px] text-[var(--color-text-link)] underline-offset-2 hover:underline"
      title={url}
    >
      <span className="min-w-0 truncate">查看来源</span>
      <ExternalLinkIcon className="size-3 shrink-0" />
    </a>
  );
}

function HotspotCard({
  hotspot,
  onResearch,
}: {
  hotspot: Ai4sDailyHomeHotspot;
  onResearch?: (hotspot: Ai4sDailyHomeHotspot) => void;
}) {
  return (
    <article
      className="flex min-h-[188px] flex-col rounded-2xl border border-[var(--chat-border)] bg-[var(--chat-surface)] p-4 shadow-[0_8px_24px_rgba(32,38,60,0.04)] transition hover:-translate-y-0.5 hover:border-[var(--chat-accent)]/35 hover:shadow-[0_12px_28px_rgba(32,38,60,0.08)]"
      data-testid="ai4s-daily-home-hotspot"
    >
      <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-[var(--chat-text-muted)]">
        <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-1 font-medium text-emerald-700 dark:text-emerald-300">
          {hotspot.section}
        </span>
        <span className="inline-flex items-center gap-1">
          <CalendarDaysIcon className="size-3.5" />
          {hotspot.reportDate}
        </span>
      </div>

      <h3 className="line-clamp-2 text-[15px] font-semibold leading-6 text-[var(--chat-text)]">
        {hotspot.title}
      </h3>
      <p className="mt-2 line-clamp-3 text-[13px] leading-5 text-[var(--chat-text-soft)]">
        {hotspot.summary}
      </p>

      <div className="mt-auto flex flex-wrap items-center gap-x-4 gap-y-2 pt-4">
        <SourceLink url={hotspot.reportUrl} />
        {hotspot.sourceUrls.slice(0, 2).map((url, index) => (
          <a
            key={url}
            href={url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex min-w-0 items-center gap-1 text-[12px] text-[var(--chat-text-muted)] underline-offset-2 hover:text-[var(--chat-text)] hover:underline"
            title={url}
          >
            <span>原始来源 {index + 1}</span>
            <ExternalLinkIcon className="size-3 shrink-0" />
          </a>
        ))}
        <button
          type="button"
          onClick={() => onResearch?.(hotspot)}
          title="启动多视角取证，生成专题研报与同题海报"
          className="ml-auto inline-flex items-center gap-1.5 rounded-full bg-[var(--chat-accent)] px-3 py-1.5 text-[12px] font-medium text-white transition hover:brightness-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--chat-accent)]/35"
          data-testid="ai4s-daily-home-research"
        >
          <SearchCheckIcon className="size-3.5" />
          研报与海报
        </button>
      </div>
    </article>
  );
}

function LoadingCards() {
  return (
    <section
      className="mx-auto mb-5 w-full max-w-[920px]"
      aria-label="今日 AI4S 热点正在加载"
      data-testid="ai4s-daily-home-loading"
    >
      <div className="mb-4 flex items-end justify-between gap-4">
        <div>
          <h2 className="text-[22px] font-semibold tracking-tight text-[var(--chat-text)]">
            今日 AI4S 热点
          </h2>
          <p className="mt-1 text-[13px] text-[var(--chat-text-muted)]">
            正在读取最新公开报告…
          </p>
        </div>
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        {[0, 1, 2].map((item) => (
          <div
            key={item}
            className="h-[188px] animate-pulse rounded-2xl border border-[var(--chat-border)] bg-[var(--chat-surface-soft)]"
          />
        ))}
      </div>
    </section>
  );
}

function LoadedHotspots({
  data,
  onResearchHotspot,
}: {
  data: Ai4sDailyHomeData;
  onResearchHotspot?: (hotspot: Ai4sDailyHomeHotspot) => void;
}) {
  return (
    <section
      className="mx-auto mb-5 w-full max-w-[920px]"
      aria-label="今日 AI4S 热点"
      data-testid="ai4s-daily-home"
    >
      <div className="mb-4 flex items-end justify-between gap-4">
        <div>
          <h2 className="text-[22px] font-semibold tracking-tight text-[var(--chat-text)]">
            今日 AI4S 热点
          </h2>
          <p className="mt-1 text-[13px] text-[var(--chat-text-muted)]">
            {data.reportTitle} · {data.reportDate}
          </p>
        </div>
        <a
          href={data.reportUrl}
          target="_blank"
          rel="noreferrer"
          className="hidden items-center gap-1.5 rounded-full border border-[var(--chat-border)] px-3 py-1.5 text-[12px] font-medium text-[var(--chat-text-soft)] transition hover:text-[var(--chat-text)] sm:inline-flex"
        >
          查看报告
          <ExternalLinkIcon className="size-3.5" />
        </a>
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        {data.hotspots.slice(0, 3).map((hotspot) => (
          <HotspotCard
            key={hotspot.id}
            hotspot={hotspot}
            onResearch={onResearchHotspot}
          />
        ))}
      </div>
    </section>
  );
}

export default function Ai4sDailyToday({
  onResearchHotspot,
}: Ai4sDailyTodayProps) {
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [data, setData] = useState<Ai4sDailyHomeData | null>(null);

  useEffect(() => {
    let disposed = false;
    void loadAi4sDailyToday()
      .then((nextData) => {
        if (disposed) {
          return;
        }
        setData(nextData);
        setStatus(nextData.hotspots.length ? "ready" : "error");
      })
      .catch((error) => {
        if (disposed) {
          return;
        }
        console.error("加载今日 AI4S 热点失败", error);
        setStatus("error");
      });

    return () => {
      disposed = true;
    };
  }, []);

  if (status === "loading") {
    return <LoadingCards />;
  }
  if (status === "error" || !data?.hotspots.length) {
    return null;
  }
  return (
    <LoadedHotspots data={data} onResearchHotspot={onResearchHotspot} />
  );
}
