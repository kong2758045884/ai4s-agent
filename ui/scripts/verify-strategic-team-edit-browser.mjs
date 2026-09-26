import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdtemp, readFile, realpath, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

// Requires a disposable SQLite copy served on 1605, never the user's 1604 DB.
assert.equal(process.env.AI4S_EDIT_TEST_COPY, "1", "Start the isolated edit acceptance service first");
const api = "http://127.0.0.1:1605/v1/strategic-map";
const width = Number(process.env.AI4S_EDIT_TEST_WIDTH || 1440);
const chromePath = process.env.CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const get = async (url) => { const r = await fetch(url); assert.ok(r.ok, `${url}: ${r.status}`); return r.json(); };
const catalogue = await get(`${api}/intelligence/verified-teams`);
const team = catalogue.teams.find((t) => t.domainName === "高能物理与量子科技");
assert.ok(team);
const seed = await fetch(`${api}/teams/${team.id}`, { method: "PUT", headers: {"Content-Type": "application/json"},
  body: JSON.stringify({ attention: team.attention, contact: team.contact, dual_judgement: "111",
    ai_level: "一般", science_level: "较低",
    core_direction: "编辑验收：保留完整研究方向与人工补充，研究量子物理的理论方法、精密测量、实验装置和数据分析。".repeat(8) }) });
assert.ok(seed.ok);
const before = (await seed.json()).data;
const url = new URL("http://127.0.0.1:3000/workspace/strategic-map");
url.search = new URLSearchParams({ smMode: "teams", smDomain: team.domainId, smTeam: team.id, smPanel: "profile" }).toString();

const profile = await mkdtemp(path.join(os.tmpdir(), "ai4s-team-edit-"));
const chrome = spawn(chromePath, ["--headless=new", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
  "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore", windowsHide: true });
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(check, label) {
  const end = Date.now() + 25000;
  while (Date.now() < end) { const value = await check(); if (value) return value; await delay(100); }
  throw new Error(`Timed out: ${label}`);
}
let socket;
try {
  const portFile = path.join(profile, "DevToolsActivePort");
  const port = await until(async () => existsSync(portFile) ? Number((await readFile(portFile, "utf8")).split(/\r?\n/)[0]) : null, "Chrome");
  const target = (await get(`http://127.0.0.1:${port}/json/list`)).find((t) => t.type === "page");
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener("open", resolve, { once: true }); socket.addEventListener("error", reject, { once: true }); });
  let id = 0;
  const pending = new Map();
  socket.addEventListener("message", ({data}) => {
    const message = JSON.parse(data); const request = pending.get(message.id); if (!request) return;
    pending.delete(message.id); message.error ? request.reject(new Error(message.error.message)) : request.resolve(message.result);
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => { pending.set(++id, {resolve, reject}); socket.send(JSON.stringify({id, method, params})); });
  const evaluate = async (expression) => {
    const r = await send("Runtime.evaluate", {expression, awaitPromise: true, returnByValue: true});
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.text);
    return r.result.value;
  };
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Emulation.setDeviceMetricsOverride", {width, height: width < 600 ? 1000 : 900, deviceScaleFactor: 1, mobile: width < 600});
  await send("Page.addScriptToEvaluateOnNewDocument", {source: `(() => {
    const original = window.fetch.bind(window);
    window.__unexpectedWrites = [];
    window.fetch = (input, init = {}) => {
      const requested = new URL(typeof input === 'string' ? input : input.url, location.href);
      const routeStart = requested.pathname.indexOf('/v1/strategic-map');
      if (routeStart >= 0) {
        const method = String(init.method || 'GET').toUpperCase();
        if (method !== 'GET' && !(method === 'PUT' && requested.pathname.endsWith('/teams/${team.id}'))) {
          window.__unexpectedWrites.push(requested.pathname);
          return Promise.reject(new Error('Unexpected write blocked'));
        }
        requested.host = '127.0.0.1:1605';
        requested.pathname = requested.pathname.slice(routeStart);
        return original(requested.href, init);
      }
      return original(input, init);
    };
  })();`});
  await send("Page.navigate", {url: url.href});
  const row = `document.querySelector('[data-team-id="${team.id}"]')`;
  const ratingsMatch = (root, ai, science) => `${root}?.querySelector('[data-capability="ai"]')?.textContent === '${ai}' && ${root}?.querySelector('[data-capability="science"]')?.textContent === '${science}'`;
  await until(() => evaluate(ratingsMatch(row, "一般", "较低")), "independent AI and science list values");
  assert.ok(await evaluate("!document.querySelector('#strategic-team-profile').innerText.includes('双高判断')"));
  await evaluate(`${row}.click()`);
  const edit = "[...document.querySelectorAll('#strategic-team-profile button')].find(x=>x.textContent.trim()==='编辑')";
  await until(() => evaluate(`Boolean(${edit})`), "profile edit button");
  await evaluate(`${edit}.click()`);
  await until(() => evaluate("document.querySelector('select[aria-label=\"AI 能力\"]') !== null"), "two dropdowns");
  assert.equal(await evaluate("document.querySelectorAll('#strategic-team-profile select[aria-label]').length"), 2);
  const editorHeight = await evaluate("document.querySelector('.strategic-map-capabilities').getBoundingClientRect().height");
  assert.ok(editorHeight <= 56, `Compact editing height: ${editorHeight}px`);
  if (width >= 600) assert.ok(await evaluate("document.querySelector('.strategic-map-capabilities').getBoundingClientRect().bottom < innerHeight"), "both dropdowns are visible without scrolling");
  for (const [label, value] of [["AI 能力", "较高"], ["科学能力", "一般"]]) {
    await evaluate(`(() => { const s = document.querySelector('select[aria-label="${label}"]'); s.value = '${value}'; s.dispatchEvent(new Event('change', {bubbles:true})); })()`);
    await until(() => evaluate(`document.querySelector('select[aria-label="${label}"]').value==='${value}'`), label);
  }
  await evaluate("[...document.querySelectorAll('#strategic-team-profile button')].find(x=>x.textContent.trim()==='保存').click()");
  const expected = "AI 较高｜科学 一般";
  await until(() => evaluate(`${ratingsMatch(row, "较高", "一般")} && !document.querySelector('select[aria-label="AI 能力"]')`), "saved list update");
  assert.ok(await evaluate(ratingsMatch("document.querySelector('#strategic-team-profile')", "较高", "一般")));
  const readHeight = await evaluate("document.querySelector('.strategic-map-capabilities').getBoundingClientRect().height");
  assert.ok(readHeight <= 42, `Compact display height: ${readHeight}px`);
  console.log(`Capability content height: display ${readHeight}px, editor ${editorHeight}px`);
  if (width < 600) {
    await evaluate("[...document.querySelectorAll('button')].find(x=>x.textContent.trim().startsWith('团队列表（')).click()");
    await until(() => evaluate(`${row}.getBoundingClientRect().height > 0`), "mobile list tab");
  }
  assert.ok(await evaluate(`${row}.querySelector('.strategic-map-team-evaluation').getBoundingClientRect().height > 0`), "ratings are visible on every viewport");
  assert.ok(await evaluate(`${row}.querySelector('.strategic-map-team-evaluation').getBoundingClientRect().bottom <= ${row}.getBoundingClientRect().bottom`), "ratings stay inside row border");
  await evaluate(`${row}.click()`);
  await evaluate("window.__beforeReload = true");
  await send("Page.reload", {ignoreCache: true});
  await until(() => evaluate(`window.__beforeReload === undefined && ${ratingsMatch(row, "较高", "一般")}`), "saved value after full reload");
  await evaluate(`${row}.click()`);
  await until(() => evaluate("!!document.querySelector('.strategic-map-core-direction')"), "core direction after reload");
  assert.ok(await evaluate(`(() => { const e = document.querySelector('.strategic-map-core-direction'); const s = getComputedStyle(e); return s.webkitLineClamp === '3' && e.clientHeight <= parseFloat(s.lineHeight) * 3 + 1 && e.scrollHeight > e.clientHeight; })()`), "core direction is clamped to three lines with overflow");
  const after = (await get(`${api}/teams/${team.id}`)).data.team;
  assert.deepEqual([after.aiLevel, after.scienceLevel, after.dualJudgement], ["较高", "一般", expected]);
  assert.equal(after.scoreTotal, before.scoreTotal, "manual judgement does not change evidence score");
  assert.equal(after.coreDirection, before.coreDirection, "judgement edit preserves the full saved research direction");
  assert.equal(after.internalReview, before.internalReview, "judgement edit preserves other manual text");
  const published = (await get(`${api}/intelligence/verified-teams`)).teams.find((t) => t.id === team.id);
  assert.equal(published.dualJudgement, expected, "published cache invalidated after save");
  assert.equal(published.coreDirection, before.coreDirection, "public catalogue preserves the whole manual direction");
  assert.equal(published.capabilityAssessments.ai.source, "manual");
  assert.equal(published.capabilityAssessments.science.source, "manual");
  assert.deepEqual(await evaluate("window.__unexpectedWrites"), []);
  assert.ok(await evaluate("document.documentElement.scrollWidth <= innerWidth + 2"));
  if (process.env.AI4S_EDIT_SCREENSHOT) {
    const shot = await send("Page.captureScreenshot", {format: "png", captureBeyondViewport: false});
    await writeFile(process.env.AI4S_EDIT_SCREENSHOT, Buffer.from(shot.data, "base64"));
  }
  await evaluate("[...document.querySelectorAll('#strategic-team-profile button')].find(x=>x.textContent.trim()==='查看完整详情').click()");
  await until(() => evaluate("!!document.querySelector('.strategic-team-full-direction')"), "leader detail navigation");
  assert.equal(await evaluate("document.querySelector('.strategic-team-full-direction').textContent"), before.coreDirection);
  assert.ok(await evaluate("getComputedStyle(document.querySelector('.strategic-team-full-direction')).webkitLineClamp === 'none'"));
  assert.ok(await evaluate("document.documentElement.scrollWidth <= innerWidth + 2"));
  console.log(`Team capabilities acceptance passed at ${width}px: separate dropdowns, list/profile sync, isolated save and reload, manual provenance, score preserved, three-line clamp, full leader detail; no paid scan.`);
} finally {
  socket?.close();
  chrome.kill();
  const target = await realpath(profile);
  const tempRoot = await realpath(os.tmpdir());
  assert.equal(path.dirname(target).toLowerCase(), tempRoot.toLowerCase());
  assert.ok(path.basename(target).startsWith("ai4s-team-edit-"));
  await rm(target, {recursive: true, force: true, maxRetries: 5, retryDelay: 200});
}
