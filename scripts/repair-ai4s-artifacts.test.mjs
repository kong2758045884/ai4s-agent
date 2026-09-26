import test from "node:test";
import assert from "node:assert/strict";
import { indexOnlySourceIds, normalizeLedger, normalizePoster, normalizeReport, parseLedger } from "./repair-ai4s-artifacts.mjs";

const ledger = `# 证据台账（去重后统一编号 S01–S42）
### [S01] First source
- url: https://example.org/first | doi: 无
### [S02] Second source
- url: doi:10.1234/second | doi: 10.1234/second
`;

test("ledger IDs, URLs and announced range are derived from actual entries", () => {
  const urls = parseLedger(ledger);
  assert.equal(urls.get("01"), "https://example.org/first");
  assert.equal(urls.get("02"), "https://doi.org/10.1234/second");
  assert.match(normalizeLedger(ledger, urls), /S01–S02/);
  assert.throws(() => parseLedger("### [S01] missing URL"), /no usable/);
});

test("report repair preserves prose while correcting headings and linking source titles", () => {
  const urls = parseLedger(ledger);
  const chapters = ["事件概览", "技术路线", "主要创新", "论文与团队", "前序工作",
    "竞争路线", "AI4S意义", "待观察问题", "来源证据"];
  const report = `<html><body><h2>结论摘要</h2>${chapters.slice(0, -1).map((heading) => `<h2>${heading}</h2>`).join("")}
    <p>结论 <a href="#s01">S01</a></p>
    <section id="ch9"><h2>来源证据</h2>
    <tr id="s01"><td>S01</td><td>First title</td></tr>
    <tr id="s02"><td>S02</td><td>Second title</td></tr></section></body></html>`;
  const repaired = normalizeReport(report, urls);
  assert.equal(repaired.changes.summaryHeading, 1);
  assert.equal(repaired.changes.chapterHeading, 1);
  assert.equal(repaired.changes.sourceLinks, 2);
  assert.match(repaired.html, /<h3>结论摘要<\/h3>/);
  assert.match(repaired.html, /<h2>论文团队<\/h2>/);
  assert.match(repaired.html, /href="https:\/\/example.org\/first"/);
  assert.match(repaired.html, /结论 <a href="#s01">S01<\/a>/);
  assert.equal(normalizeReport(repaired.html, urls).changes.sourceLinks, 0);
  const correctedUrls = new Map(urls);
  correctedUrls.set("01", "https://example.org/corrected");
  const updated = normalizeReport(repaired.html, correctedUrls);
  assert.equal(updated.changes.sourceLinkUpdates, 1);
  assert.match(updated.html, /href="https:\/\/example.org\/corrected"/);
});

test("poster citations link directly to original sources without inventing an absent source", () => {
  const poster = `<p><span class="cite">S01</span></p><a href="../report/item.html#s02"><span class="cite">S02</span></a>`;
  const urls = parseLedger(ledger);
  const repaired = normalizePoster(poster, new Set(["01", "02"]), urls);
  assert.equal(repaired.links, 1);
  assert.equal(repaired.linkUpgrades, 1);
  assert.match(repaired.html, /href="https:\/\/example\.org\/first"/);
  assert.match(repaired.html, /href="https:\/\/doi\.org\/10\.1234\/second"/);
  assert.equal(normalizePoster(repaired.html, new Set(["01", "02"]), urls).links, 0);
  const correctedUrls = new Map(urls);
  correctedUrls.set("01", "https://example.org/corrected");
  assert.equal(normalizePoster(repaired.html, new Set(["01", "02"]), correctedUrls).linkUpgrades, 1);
  assert.throws(() => normalizePoster("<span class='cite'>S03</span>", new Set(["01"]), urls), /absent/);
});

test("explicitly index-only sources get a non-evidence note, other unused sources do not", () => {
  const sourceLedger = `### [S01] Primary\n- url: https://example.org/a\n### [S02] Daily digest（仅作索引）\n- url: https://example.org/b\n`;
  const urls = parseLedger(sourceLedger);
  assert.deepEqual([...indexOnlySourceIds(sourceLedger)], ["02"]);
  const report = `<section id="ch1"><h2>事件概览</h2><p><a href="#s01">S01</a></p></section>
    <section id="ch9"><h2>来源证据</h2>
    <tr id="s01"><td>S01</td><td>Primary</td></tr>
    <tr id="s02"><td>S02</td><td>Digest</td></tr></section>`;
  const repaired = normalizeReport(report, urls, indexOnlySourceIds(sourceLedger));
  assert.equal(repaired.changes.indexNotes, 1);
  assert.match(repaired.html, /检索线索（不作为事实依据）：<a href="#s02">S02<\/a>/);
  assert.equal(normalizeReport(repaired.html, urls, indexOnlySourceIds(sourceLedger)).changes.indexNotes, 0);
  assert.equal(normalizeReport(report, urls).changes.indexNotes, 0);
});
