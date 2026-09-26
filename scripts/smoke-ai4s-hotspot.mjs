import { randomUUID } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { createInterface } from "node:readline/promises";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const requireFromUi = createRequire(join(root, "ui", "package.json"));
const ts = requireFromUi("typescript");
const dailyBase = process.env.AI4S_DAILY_BASE_URL || "https://ai4s-frontier.github.io/AI4S-Daily-HTML";
const agentBase = process.env.AI4S_AGENT_BASE_URL || "http://127.0.0.1:8100";

async function loadDailyParser() {
  const source = await readFile(join(root, "ui", "src", "utils", "ai4sDailyHome.ts"), "utf8");
  const standalone = source.replace("import.meta.env.VITE_AI4S_DAILY_BASE_URL", "undefined");
  if (standalone === source) throw new Error("AI4S Daily frontend parser changed; update smoke adapter");
  const code = ts.transpileModule(standalone, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
}

async function readJson(url) {
  const response = await fetch(url, { signal: AbortSignal.timeout(20_000) });
  if (!response.ok) throw new Error(`${response.status} ${url}`);
  return response.json();
}

async function selectLatestHotspot(parser) {
  const index = await readJson(`${dailyBase}/reports/index.json`);
  if (!Array.isArray(index) || typeof index[0] !== "string") {
    throw new Error("AI4S Daily report index has no usable entry");
  }
  const fileName = index[0];
  const reportUrl = `${dailyBase}/reports/${encodeURIComponent(fileName)}`;
  const response = await fetch(reportUrl, { signal: AbortSignal.timeout(25_000) });
  if (!response.ok) throw new Error(`${response.status} ${reportUrl}`);
  const report = parser.parseAi4sDailyReport(await response.text(), fileName, dailyBase);
  const hotspot = report.hotspots[0];
  if (!hotspot || hotspot.sourceUrls.length === 0) {
    throw new Error("Latest report has no sourced hotspot; refusing paid smoke run");
  }
  return {
    ...hotspot,
    id: `${report.reportId}-0-${hotspot.title}`,
    reportId: report.reportId,
    reportTitle: report.reportTitle,
    reportDate: report.reportDate,
    reportUrl: report.reportUrl,
  };
}

async function streamRun(response) {
  if (!response.ok || !response.body) {
    throw new Error(`Agent SSE failed: HTTP ${response.status}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const kinds = new Set();
  let frames = 0;
  let pending = "";
  const heartbeat = setInterval(() => console.log(`STILL_RUNNING frames=${frames}`), 30_000);
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      pending += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      if (pending.length > 2_000_000) throw new Error("SSE frame exceeds 2 MB");
      let boundary;
      while ((boundary = pending.indexOf("\n\n")) >= 0) {
        const frame = pending.slice(0, boundary);
        pending = pending.slice(boundary + 2);
        const raw = frame.split("\n").filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n");
        if (!raw || raw === "[DONE]") continue;
        let value;
        try { value = JSON.parse(raw); } catch { continue; }
        frames += 1;
        const map = value.resultMap || value.data?.resultMap || {};
        const event = map.eventData || {};
        const kind = String(map.messageType || event.messageType || event.type || value.event || "frame");
        if (!kinds.has(kind)) {
          kinds.add(kind);
          console.log(`EVENT_KIND ${kind}`);
        }
      }
    }
  } finally {
    clearInterval(heartbeat);
    reader.releaseLock();
  }
  console.log(`STREAM_CLOSED frames=${frames} kinds=${[...kinds].join(",")}`);
}

async function readRunStatus(sessionId, requestId, cookie) {
  if (!cookie) throw new Error("Visitor cookie missing; cannot verify or resume this run");
  const detail = await fetch(`${agentBase}/api/agent/conversation/sessions/${sessionId}`, {
    headers: { Cookie: cookie },
    signal: AbortSignal.timeout(15_000),
  });
  if (!detail.ok) throw new Error(`Conversation history failed: HTTP ${detail.status}`);
  const history = (await detail.json()).data || {};
  const run = history.runs?.find((item) => item.requestId === requestId);
  console.log(`RUN_STATUS ${run?.status || "unknown"}`);
  console.log(`REPLAY_FRAMES ${run?.replayFrames?.length || 0}`);
  console.log(`FINAL_SUMMARY_CHARS ${run?.finalSummaryText?.length || 0}`);
  return run;
}

async function postJson(path, body, cookie) {
  const response = await fetch(`${agentBase}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Cookie: cookie },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`${path} failed: HTTP ${response.status}`);
  return response.json();
}

async function continueApprovedPlan(sessionId, cookie) {
  const pendingResponse = await fetch(`${agentBase}/api/agent/plan-approval/pending?sessionId=${encodeURIComponent(sessionId)}`, {
    headers: { Cookie: cookie },
    signal: AbortSignal.timeout(15_000),
  });
  if (!pendingResponse.ok) throw new Error(`Pending plan lookup failed: HTTP ${pendingResponse.status}`);
  const pending = (await pendingResponse.json()).data || [];
  const plan = pending.find((item) => item.status === "pending");
  if (!plan?.approvalId || !plan?.planContent) throw new Error("WAITING_INPUT is not a pending plan approval");
  console.log(`PLAN_PENDING ${plan.approvalId} chars=${plan.planContent.length}`);
  console.log(`PLAN_HEADINGS ${plan.planContent.split("\n").filter((line) => /^#{1,3} /.test(line)).join(" | ").slice(0, 800)}`);
  const input = createInterface({ input: process.stdin, output: process.stdout });
  let answer;
  try {
    answer = await input.question("After reviewing this session's .ai4s/plan.md, type APPROVE to resume: ");
  } finally {
    input.close();
  }
  if (answer.trim() !== "APPROVE") {
    console.log("PLAN_NOT_APPROVED no continuation started");
    return;
  }
  const decision = await postJson("/api/agent/plan-approval/approve", { approvalId: plan.approvalId }, cookie);
  if (!decision.data?.accepted || !decision.data?.resumeRequestId) {
    throw new Error(`Plan approval rejected: ${decision.data?.message || decision.info || "unknown"}`);
  }
  const resumeRequestId = decision.data.resumeRequestId;
  console.log(`PLAN_APPROVED resumeRequestId=${resumeRequestId}`);
  const response = await fetch(`${agentBase}/api/agent/plan-approval/resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream", Cookie: cookie },
    body: JSON.stringify({ resumeRequestId }),
  });
  await streamRun(response);
  await readRunStatus(sessionId, resumeRequestId, cookie);
}

async function offerManualFollowup(sessionId, cookie) {
  const sessionRoot = join(root, "ai4s-tool", "skilloutput", sessionId);
  const listHtml = async (folder) => {
    try {
      return (await readdir(join(sessionRoot, folder))).filter((name) => name.endsWith(".html"));
    } catch (error) {
      if (error?.code === "ENOENT") return [];
      throw error;
    }
  };
  const reportFiles = await listHtml("report");
  const posterFiles = await listHtml("poster");
  console.log(`DELIVERABLES report=${reportFiles.join(",") || "missing"} poster=${posterFiles.join(",") || "missing"}`);
  const validate = async (reports, posters) => {
    const expected = ["事件概览", "技术路线", "主要创新", "论文团队", "前序工作", "竞争路线", "AI4S意义", "待观察问题", "来源证据"];
    if (reports.length === 0 || posters.length === 0) return false;
    const report = await readFile(join(sessionRoot, "report", reports[0]), "utf8");
    const poster = await readFile(join(sessionRoot, "poster", posters[0]), "utf8");
    let ledgerOk = false;
    let ledgerSummary = "missing";
    try {
      const ledger = await readFile(join(sessionRoot, "evidence", "ledger.md"), "utf8");
      const ids = [...ledger.matchAll(/^### \[S(\d{2,3})\]/gm)].map((match) => Number(match[1]));
      const declared = /S01\s*[–—-]\s*S(\d{2,3})/.exec(ledger)?.[1];
      const unique = new Set(ids);
      ledgerOk = ids.length > 0 && unique.size === ids.length
        && ids.every((id, index) => id === index + 1)
        && (!declared || Number(declared) === ids.length);
      ledgerSummary = `${ids.length}/${declared || "undeclared"}`;
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
    const headings = [...report.matchAll(/<h2\b[^>]*>([\s\S]*?)<\/h2>/gi)]
      .map((match) => match[1].replace(/<[^>]+>/g, "").replace(/^\s*\d+[.、]\s*/, "").replace(/\s+/g, ""));
    const chaptersOk = headings.length === expected.length && headings.every((heading, index) => heading === expected[index]);
    const placeholders = /<!--\s*CH\d+\s*-->/.test(report)
      || report.includes("<!--AI4S_APPEND-->")
      || poster.includes("<!--AI4S_APPEND-->");
    const htmlClosed = /<\/html\s*>/i.test(report) && /<\/html\s*>/i.test(poster);
    const printable = /@media\s+print/i.test(poster);
    const sourceMatch = /<section\b[^>]*id=["'](?:c9|ch9)["'][^>]*>([\s\S]*?)<\/section>/i.exec(report);
    const sourceSection = sourceMatch?.[1] || "";
    const body = report.slice(0, sourceMatch?.index ?? report.length);
    const linkedCitations = [...new Set([...body.matchAll(/<a\b[^>]*\bhref=["']#s(\d{2,3})["'][^>]*>([\s\S]*?)<\/a>/gi)]
      .filter((match) => new RegExp(`\\bS${match[1]}\\b`, "i").test(match[2].replace(/<[^>]+>/g, "")))
      .map((match) => `[S${match[1]}]`))];
    const cited = [...new Set([...(body.match(/\[S\d{2,3}\]/g) || []), ...linkedCitations])];
    const anchors = [...new Set([...sourceSection.matchAll(/\bid=["']s(\d{2,3})["']/gi)].map((match) => `[S${match[1]}]`))];
    const allAnchors = [...new Set([...report.matchAll(/\bid=["']s(\d{2,3})["']/gi)].map((match) => `[S${match[1]}]`))];
    const missingSources = cited.filter((id) => !anchors.includes(id));
    const unusedSources = anchors.filter((id) => !cited.includes(id));
    const unlinkedCitations = cited.filter((id) => !linkedCitations.includes(id));
    const outsideSources = allAnchors.filter((id) => !anchors.includes(id));
    const unlinkedSources = anchors.filter((id) => {
      const number = id.slice(2, -1);
      const entry = new RegExp(`<(?:tr|li|article|div|p|a)\\b(?=[^>]*\\bid=["']s${number}["'])[^>]*>[\\s\\S]*?<\\/(?:tr|li|article|div|p|a)>`, "i").exec(sourceSection)?.[0] || "";
      return !/\bhref=["']https?:\/\//i.test(entry);
    });
    const posterLinked = /\bhref=["'](?:https?:\/\/|[^"']+\.html#s\d{2,3})/i.test(poster);
    console.log(`DELIVERABLE_VALIDATION chapters=${headings.length}/9 chapterOrder=${chaptersOk} placeholders=${placeholders} htmlClosed=${htmlClosed} posterPrintable=${printable} posterLinked=${posterLinked} ledger=${ledgerSummary} ledgerOk=${ledgerOk} citations=${cited.length} missingSources=${missingSources.join(",") || "none"} unusedSources=${unusedSources.join(",") || "none"} unlinkedCitations=${unlinkedCitations.join(",") || "none"} outsideSources=${outsideSources.join(",") || "none"} unlinkedSources=${unlinkedSources.join(",") || "none"}`);
    return chaptersOk && !placeholders && htmlClosed && printable && posterLinked && ledgerOk
      && cited.length > 0 && missingSources.length === 0 && unusedSources.length === 0 && unlinkedCitations.length === 0
      && outsideSources.length === 0 && unlinkedSources.length === 0;
  };
  if (await validate(reportFiles, posterFiles)) return;
  if (process.argv.includes("--validate-only")) {
    process.exitCode = 1;
    return;
  }
  const input = createInterface({ input: process.stdin, output: process.stdout });
  let answer;
  try {
    answer = await input.question("Run ended without both deliverables. Type FOLLOWUP to continue same session, or STOP: ");
  } finally {
    input.close();
  }
  if (answer.trim() !== "FOLLOWUP") {
    console.log("NO_FOLLOWUP session remains available for inspection");
    return;
  }
  const requestId = randomUUID();
  console.log(`FOLLOWUP_REQUEST_ID ${requestId}`);
  const query = "请继续完成当前会话已批准的 AI4S 热点专题任务。先用 TaskOutput 读取已启动后台 Worker 的结果，不要重复取证；然后完成两条独立研究支线和交叉质询、严格九章 HTML 研报、同题可打印 HTML 海报，并逐一打开核验。只有真实文件都存在才宣布交付。";
  const response = await fetch(`${agentBase}/web/api/v1/gpt/queryAgentStreamIncr`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream", Cookie: cookie },
    body: JSON.stringify({ sessionId, requestId, query, deepThink: 1 }),
  });
  await streamRun(response);
  await readRunStatus(sessionId, requestId, cookie);
  const finalReports = await listHtml("report");
  const finalPosters = await listHtml("poster");
  console.log(`DELIVERABLES report=${finalReports.join(",") || "missing"} poster=${finalPosters.join(",") || "missing"}`);
  await validate(finalReports, finalPosters);
}

async function main() {
  const validationIndex = process.argv.indexOf("--validate-session");
  if (validationIndex >= 0) {
    if (!process.argv.includes("--validate-only")) {
      throw new Error("--validate-session must be paired with --validate-only (no paid follow-up)");
    }
    const sessionId = process.argv[validationIndex + 1];
    if (!sessionId) throw new Error("--validate-session requires a session ID");
    await offerManualFollowup(sessionId, null);
    return;
  }
  const parser = await loadDailyParser();
  const hotspot = await selectLatestHotspot(parser);
  console.log(`HOTSPOT ${hotspot.reportDate} ${hotspot.title}`);
  console.log(`SOURCE_URLS ${hotspot.sourceUrls.length}`);
  if (!process.argv.includes("--run-paid")) {
    console.log("DRY_RUN_ONLY pass --run-paid to call the configured model and search services");
    return;
  }
  const sessionId = randomUUID();
  const requestId = randomUUID();
  const prompt = parser.buildAi4sDailyResearchPrompt(hotspot);
  console.log(`SESSION_ID ${sessionId}`);
  console.log(`REQUEST_ID ${requestId}`);
  const response = await fetch(`${agentBase}/web/api/v1/gpt/queryAgentStreamIncr`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      "X-Device-Id": "codex-ai4s-hotspot-smoke",
    },
    body: JSON.stringify({ sessionId, requestId, query: prompt, deepThink: 1 }),
  });
  const cookie = response.headers.get("set-cookie")?.split(";", 1)[0] || "";
  await streamRun(response);
  const run = await readRunStatus(sessionId, requestId, cookie);
  if (run?.status === "WAITING_INPUT" && process.argv.includes("--interactive-approval")) {
    await continueApprovedPlan(sessionId, cookie);
  }
  if (process.argv.includes("--interactive-followup")) {
    await offerManualFollowup(sessionId, cookie);
  }
}

main().catch((error) => {
  console.error(`SMOKE_ERROR ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
});
