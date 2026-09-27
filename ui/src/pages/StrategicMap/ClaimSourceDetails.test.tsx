import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { RecommendationCitation } from "@/services/strategicRecommendations";
import ClaimSourceDetails from "./ClaimSourceDetails";

const citation: RecommendationCitation = { id: "c1", kind: "outcome", text: "成果主张", quote: "原文", url: "https://example.org" };
describe("saved source checks", () => {
  it("does not turn legacy missing metadata into verified facts", () => {
    const html = renderToStaticMarkup(<ClaimSourceDetails citation={citation} />);
    expect(html).toContain("此历史快照未保存");
    expect(html).not.toContain("已有独立复核记录");
  });
  it("separates deterministic source checks from AI and human review", () => {
    const html = renderToStaticMarkup(<ClaimSourceDetails citation={{ ...citation, provenance: {
      version: "v1", sourceRunId: "r1", sourceTitle: "成果页面", sourceType: "official_institution",
      publishedAt: "2026-09-01", fetchedAt: "2026-09-20", contentHash: "hash", quoteHash: "quote",
      locator: { status: "exact", start: 4, end: 6, basis: "saved_extracted_text" },
      sourceCheck: { method: "official-directory-citation-rule", checkedAt: "2026-09-21", status: "recorded" },
      modelReview: { status: "not_used", version: "", reviewedAt: "" },
      humanReview: { status: "not_recorded", reviewer: "", reviewedAt: "" },
      linkCheck: { status: "not_checked", checkedAt: "" },
    } }} />);
    expect(html).toContain("2026-09-20");
    expect(html).toContain("第 5–6 字符");
    expect(html).toContain("此来源未使用 AI 复核");
    expect(html).toContain("未记录人工审核");
    expect(html).not.toContain("已有独立复核记录");
  });
});
