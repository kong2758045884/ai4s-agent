import { type ReactNode } from "react";
import { ExternalLinkIcon, FileTextIcon, SearchCheckIcon } from "lucide-react";
import type { Ai4sDailyReport, Ai4sDailyResult } from "@/utils/ai4sDaily";

function displayDate(report: Ai4sDailyReport): string {
  return report.date || report.pushDate || report.pushTime || "日期未提供";
}

function ExternalLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="inline-flex min-w-0 items-center gap-1 text-[var(--color-text-link)] underline-offset-2 hover:underline"
      title={href}
    >
      <span className="min-w-0 truncate">{children}</span>
      <ExternalLinkIcon className="size-3 shrink-0" />
    </a>
  );
}

export function Ai4sDailySourceCard({
  data,
  followUpDeepSearch = false,
}: {
  data: Ai4sDailyResult;
  followUpDeepSearch?: boolean;
}) {
  const reports = data.reports.filter((report) => report.matched).length
    ? data.reports.filter((report) => report.matched)
    : data.reports;
  const hotspots = reports
    .flatMap((report) =>
      report.sections.flatMap((section) =>
        section.hotspots.map((hotspot) => ({ report, section, hotspot }))
      )
    )
    .slice(0, 4);

  return (
    <section
      className="mb-3 rounded-lg border border-emerald-500/30 bg-emerald-500/[0.06] p-3 text-[12px] leading-5"
      data-testid="ai4s-daily-source-card"
    >
      <div className="flex flex-wrap items-center gap-2 font-medium text-[var(--color-text)]">
        <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-emerald-700 dark:text-emerald-300">
          <FileTextIcon className="size-3.5" />
          AI4S Daily 领域数据
        </span>
        <span className="text-[var(--color-text-muted)]">
          {data.matched ? "已命中相关内容" : "未命中直接相关内容"}
        </span>
      </div>

      {reports.length ? (
        <div className="mt-2 space-y-2">
          {reports.slice(0, 3).map((report) => (
            <div key={report.reportId} className="rounded-md bg-[var(--color-bg)]/60 px-2.5 py-2">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 font-medium">
                {report.reportUrl ? (
                  <ExternalLink href={report.reportUrl}>{report.title || report.reportId}</ExternalLink>
                ) : (
                  <span>{report.title || report.reportId}</span>
                )}
                <span className="text-[var(--color-text-muted)]">{displayDate(report)}</span>
              </div>
              {report.reportUrl ? (
                <div className="mt-0.5 text-[var(--color-text-muted)]">
                  报告：<ExternalLink href={report.reportUrl}>{report.reportUrl}</ExternalLink>
                </div>
              ) : null}
            </div>
          ))}
        </div>
      ) : (
        <p className="mt-2 text-[var(--color-text-muted)]">
          当前报告中没有可展示的相关条目；Agent 可以继续使用外部搜索补充证据。
        </p>
      )}

      {hotspots.length ? (
        <div className="mt-2 space-y-1.5">
          <div className="font-medium text-[var(--color-text)]">命中的热点 / 相关内容</div>
          {hotspots.map(({ report, section, hotspot }, index) => (
            <div key={`${report.reportId}-${section.section}-${hotspot.title}-${index}`} className="pl-2">
              <div className="font-medium">{hotspot.title}</div>
              <div className="text-[var(--color-text-muted)]">SECTION: {section.section}</div>
              {hotspot.sourceUrls.length ? (
                <div className="flex flex-wrap gap-x-2 gap-y-0.5">
                  <span className="text-[var(--color-text-muted)]">原始来源：</span>
                  {hotspot.sourceUrls.slice(0, 3).map((url) => (
                    <ExternalLink key={url} href={url}>{url}</ExternalLink>
                  ))}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}

      {followUpDeepSearch ? (
        <div className="mt-2 inline-flex items-center gap-1.5 rounded-md bg-sky-500/10 px-2 py-1 text-sky-700 dark:text-sky-300">
          <SearchCheckIcon className="size-3.5" />
          已基于 AI4S Daily 线索继续进行外部补充 / 核验
        </div>
      ) : null}
    </section>
  );
}
