import { copyFile, mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const sessionBase = resolve(root, "ai4s-tool", "skilloutput");

function firstSourceUrl(line) {
  const url = /https?:\/\/[^\s；|（）)]+/i.exec(line)?.[0]?.replace(/[.,，。]+$/, "");
  if (url) return new URL(url).href;
  const doi = /\bdoi:\s*(10\.\d{4,9}\/[^\s|；（）)]+)/i.exec(line)?.[1];
  return doi ? `https://doi.org/${doi}` : null;
}

export function parseLedger(ledger) {
  const headers = [...ledger.matchAll(/^### \[S(\d{2,3})\][^\n]*$/gm)];
  const urls = new Map();
  for (let index = 0; index < headers.length; index++) {
    const number = headers[index][1];
    const block = ledger.slice(headers[index].index, headers[index + 1]?.index ?? ledger.length);
    const urlLine = /^- url:\s*(.+)$/m.exec(block)?.[1];
    const url = urlLine && firstSourceUrl(urlLine);
    if (!url) throw new Error(`S${number} has no usable http(s) URL or DOI in the ledger`);
    if (urls.has(number)) throw new Error(`duplicate source S${number} in the ledger`);
    urls.set(number, url);
  }
  if (urls.size === 0) throw new Error("the ledger has no ### [SNN] entries");
  return urls;
}

export function indexOnlySourceIds(ledger) {
  const headers = [...ledger.matchAll(/^### \[S(\d{2,3})\][^\n]*$/gm)];
  const ids = new Set();
  for (let index = 0; index < headers.length; index++) {
    const block = ledger.slice(headers[index].index, headers[index + 1]?.index ?? ledger.length);
    if (/仅作索引|仅作线索/.test(block.slice(0, 250))) ids.add(headers[index][1]);
  }
  return ids;
}

export function normalizeLedger(ledger, urls) {
  const numbers = [...urls.keys()].map(Number);
  if (!numbers.every((number, index) => number === index + 1)) {
    throw new Error("ledger source IDs are not contiguous from S01");
  }
  const range = /S01\s*[–—-]\s*S\d{2,3}/;
  return range.test(ledger) ? ledger.replace(range, `S01–S${String(urls.size).padStart(2, "0")}`) : ledger;
}

function escapeAttribute(value) {
  return value.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

export function normalizeReport(report, urls, indexOnlyIds = new Set()) {
  const changes = { summaryHeading: 0, chapterHeading: 0, sourceLinks: 0, sourceLinkUpdates: 0, indexNotes: 0 };
  let result = report;
  const headings = [...result.matchAll(/<h2\b[^>]*>/gi)].length;
  if (headings === 10 && /<h2\b[^>]*>\s*结论摘要\s*<\/h2>/i.test(result)) {
    result = result.replace(/<h2\b([^>]*)>\s*结论摘要\s*<\/h2>/i, "<h3$1>结论摘要</h3>");
    changes.summaryHeading++;
  }
  if (/<h2\b[^>]*>\s*论文与团队\s*<\/h2>/i.test(result)) {
    result = result.replace(/(<h2\b[^>]*>)\s*论文与团队\s*(<\/h2>)/i, "$1论文团队$2");
    changes.chapterHeading++;
  }
  const seen = new Set();
  result = result.replace(/<tr\b(?=[^>]*\bid\s*=\s*["']s(\d{2,3})["'])[^>]*>[\s\S]*?<\/tr>/gi, (row, number) => {
    if (seen.has(number)) throw new Error(`duplicate report source anchor s${number}`);
    seen.add(number);
    const url = urls.get(number);
    if (!url) throw new Error(`report source s${number} is absent from the ledger`);
    const cells = [...row.matchAll(/<td\b[^>]*>[\s\S]*?<\/td>/gi)];
    if (cells.length < 2) throw new Error(`source s${number} row has fewer than two cells`);
    const titleCell = cells[1][0];
    const existingLinks = [...titleCell.matchAll(/<a\b[^>]*\bhref\s*=\s*["']([^"']+)["'][^>]*>/gi)];
    if (existingLinks.length > 1) throw new Error(`source s${number} title cell has multiple links; review manually`);
    if (existingLinks.length === 1) {
      const linkTag = existingLinks[0][0];
      const expected = escapeAttribute(url);
      if (existingLinks[0][1] === expected) return row;
      const updated = linkTag.replace(/\bhref\s*=\s*["'][^"']+["']/i, `href="${expected}"`);
      changes.sourceLinkUpdates++;
      return row.replace(linkTag, updated);
    }
    const linkedCell = titleCell.replace(/^(<td\b[^>]*>)([\s\S]*)(<\/td>)$/i,
      (_, open, title, close) => `${open}<a href="${escapeAttribute(url)}" target="_blank" rel="noopener noreferrer">${title}</a>${close}`);
    changes.sourceLinks++;
    return row.replace(titleCell, linkedCell);
  });
  if (seen.size === 0) throw new Error("report has no <tr id=\"sNN\"> source rows; no repair was applied");
  const sourceChapter = /<section\b[^>]*\bid\s*=\s*["'](?:ch9|c9)["'][^>]*>/i.exec(result);
  if (!sourceChapter) throw new Error("report has no source-evidence chapter section");
  const body = result.slice(0, sourceChapter.index);
  const bodyCitations = new Set([...body.matchAll(/\bhref\s*=\s*["']#s(\d{2,3})["']/gi)]
    .map((match) => match[1]));
  const indexNotes = [...indexOnlyIds].filter((id) => seen.has(id) && !bodyCitations.has(id));
  if (indexNotes.length > 0) {
    const firstChapter = /(<section\b[^>]*\bid\s*=\s*["'](?:ch1|c1)["'][^>]*>[\s\S]*?<\/h2>)/i;
    if (!firstChapter.test(result)) throw new Error("report has no first chapter for index-only source note");
    const links = indexNotes.map((id) => `<a href="#s${id}">S${id}</a>`).join("、");
    result = result.replace(firstChapter,
      `$1\n  <p class="source-discovery">检索线索（不作为事实依据）：${links}。</p>`);
    changes.indexNotes = indexNotes.length;
  }
  return { html: result, changes, sourceIds: seen };
}

export function normalizePoster(poster, sourceIds, urls) {
  let links = 0;
  let linkUpgrades = 0;
  const directLink = (span, number) => {
    if (!sourceIds.has(number)) throw new Error(`poster cites S${number}, absent from report source rows`);
    const url = urls.get(number);
    if (!url) throw new Error(`poster cites S${number}, absent from the evidence ledger`);
    return `<a href="${escapeAttribute(url)}" target="_blank" rel="noopener noreferrer">${span}</a>`;
  };
  let html = poster.replace(/<a\b[^>]*\bhref\s*=\s*["']([^"']+)["'][^>]*>\s*(<span\b(?=[^>]*\bclass\s*=\s*["'][^"']*\bcite\b[^"']*["'])[^>]*>\s*S(\d{2,3})\s*<\/span>)\s*<\/a>/gi,
    (anchor, _oldUrl, span, number) => {
      const expected = directLink(span, number);
      if (anchor === expected) return anchor;
      linkUpgrades++;
      return expected;
    });
  html = html.replace(/<span\b(?=[^>]*\bclass\s*=\s*["'][^"']*\bcite\b[^"']*["'])[^>]*>\s*S(\d{2,3})\s*<\/span>/gi,
    (span, number, offset) => {
      if (!sourceIds.has(number)) throw new Error(`poster cites S${number}, absent from report source rows`);
      const before = html.slice(0, offset);
      if (before.lastIndexOf("<a ") > before.lastIndexOf("</a>")) return span;
      links++;
      return directLink(span, number);
    });
  return { html, links, linkUpgrades };
}

async function soleHtml(directory) {
  const files = (await readdir(directory)).filter((name) => name.toLowerCase().endsWith(".html"));
  if (files.length !== 1) throw new Error(`${directory} must contain exactly one HTML file; found ${files.length}`);
  return join(directory, files[0]);
}

async function main() {
  const sessionIndex = process.argv.indexOf("--session");
  const sessionId = sessionIndex >= 0 ? process.argv[sessionIndex + 1] : "";
  const apply = process.argv.includes("--apply");
  if (!/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(sessionId)) {
    throw new Error("usage: node scripts/repair-ai4s-artifacts.mjs --session <UUID> [--apply]");
  }
  const directory = resolve(sessionBase, sessionId);
  if (!directory.startsWith(sessionBase + "\\") && !directory.startsWith(sessionBase + "/")) {
    throw new Error("session path escapes the AI4S skilloutput directory");
  }
  const ledgerPath = join(directory, "evidence", "ledger.md");
  const reportPath = await soleHtml(join(directory, "report"));
  const posterPath = await soleHtml(join(directory, "poster"));
  const [ledger, report, poster] = await Promise.all([
    readFile(ledgerPath, "utf8"), readFile(reportPath, "utf8"), readFile(posterPath, "utf8"),
  ]);
  const urls = parseLedger(ledger);
  const repairedLedger = normalizeLedger(ledger, urls);
  const repairedReport = normalizeReport(report, urls, indexOnlySourceIds(ledger));
  const repairedPoster = normalizePoster(poster, repairedReport.sourceIds, urls);
  const summary = {
    sessionId,
    ledgerSources: urls.size,
    ledgerHeadingChanged: repairedLedger !== ledger,
    ...repairedReport.changes,
    posterLinks: repairedPoster.links,
    posterLinkUpgrades: repairedPoster.linkUpgrades,
    remainingReview: "Unused sources and scientific claims require review; this tool never removes or invents evidence.",
  };
  if (!apply) {
    console.log(`DRY_RUN ${JSON.stringify(summary)}`);
    return;
  }
  if (repairedLedger === ledger && repairedReport.html === report && repairedPoster.html === poster) {
    console.log(`NO_CHANGES ${JSON.stringify(summary)}`);
    return;
  }
  const backupDir = join(directory, `.repair-backup-${new Date().toISOString().replace(/[:.]/g, "-")}`);
  await mkdir(backupDir);
  await Promise.all([
    copyFile(ledgerPath, join(backupDir, "ledger.md")),
    copyFile(reportPath, join(backupDir, basename(reportPath))),
    copyFile(posterPath, join(backupDir, basename(posterPath).replace(/\.html$/i, ".poster.html"))),
  ]);
  await writeFile(ledgerPath, repairedLedger, "utf8");
  await writeFile(reportPath, repairedReport.html, "utf8");
  await writeFile(posterPath, repairedPoster.html, "utf8");
  console.log(`APPLIED ${JSON.stringify({ ...summary, backupDir })}`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
