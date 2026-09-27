import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import GraphEvidenceDetails from "./GraphEvidenceDetails";

it("separates inference and frozen original proof, including missing review metadata", () => {
  const html = renderToStaticMarkup(<GraphEvidenceDetails element={{ id: "edge", source: "team:a", target: "claim:b", data: { raw: {
    status: "supported", basis: "本次任务中的匹配结果", citations: [{ id: "c1", text: "具体成果", quote: "当时保存的引文", url: "https://example.org/paper" }],
  } } }} />);
  expect(html).toContain("尚非交付承诺");
  expect(html).toContain("当时保存的引文");
  expect(html).toContain("此历史快照未保存");
  expect(html).toContain('href="https://example.org/paper"');
});

it("does not present an affiliation without citations as verified", () => {
  const html = renderToStaticMarkup(<GraphEvidenceDetails element={{ id: "edge", source: "team:a", target: "org:b", data: { raw: { status: "unconfirmed", citations: [] } } }} />);
  expect(html).toContain("归属引文未保存");
  expect(html).toContain("未附外部引文");
  expect(html).not.toContain("已核实");
});
