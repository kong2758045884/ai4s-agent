import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const chromePath = process.env.CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
assert.ok(existsSync(chromePath), `Chrome not found: ${chromePath}`);
const url = process.env.AI4S_IMPACT_BROWSER_URL || "http://localhost:3002/workspace/impact-triage";
const viewportWidth = Number(process.env.AI4S_IMPACT_VIEWPORT_WIDTH || 1440);
const viewportHeight = Number(process.env.AI4S_IMPACT_VIEWPORT_HEIGHT || 900);
const profile = await mkdtemp(path.join(os.tmpdir(), "ai4s-impact-browser-"));
const chrome = spawn(chromePath, ["--headless=new", "--no-first-run", "--no-default-browser-check",
  "--disable-extensions", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"],
{ stdio: "ignore", windowsHide: true });
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(check, label, timeoutMs = 25000) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    const value = await check();
    if (value) return value;
    await delay(150);
  }
  throw new Error(`Timed out: ${label}`);
}
let socket;
try {
  const portFile = path.join(profile, "DevToolsActivePort");
  const port = await until(async () => existsSync(portFile) ? Number((await readFile(portFile, "utf8")).split(/\r?\n/)[0]) : null, "Chrome port");
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const page = targets.find((t) => t.type === "page");
  assert.ok(page);
  socket = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  let id = 0;
  const pending = new Map();
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    if (!message.id || !pending.has(message.id)) return;
    const p = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) p.reject(new Error(message.error.message));
    else p.resolve(message.result);
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const next = ++id;
    pending.set(next, { resolve, reject });
    socket.send(JSON.stringify({ id: next, method, params }));
  });
  const evaluate = async (expression) => {
    const result = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
    return result.result.value;
  };
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: viewportWidth, height: viewportHeight, deviceScaleFactor: 1, mobile: false });
  await send("Page.addScriptToEvaluateOnNewDocument", { source: `(() => {
    const original = window.fetch.bind(window);
    window.__impactWrites = [];
    window.fetch = async (input, init = {}) => {
      const url = new URL(typeof input === 'string' ? input : input.url, location.href);
      const method = String(init.method || 'GET').toUpperCase();
      if (url.pathname.includes('/v1/impact-triage') && method !== 'GET') {
        window.__impactWrites.push({method, path:url.pathname});
        return new Response(JSON.stringify({detail:'blocked by browser acceptance'}), {status:403,headers:{'Content-Type':'application/json'}});
      }
      return original(input, init);
    };
  })();` });
  await send("Page.navigate", { url });
  try {
    await until(() => evaluate("document.body?.innerText.includes('国内科研机构影响力') && document.body?.innerText.includes('208')"), "ranking loaded");
  } catch (error) {
    console.error("Browser URL:", await evaluate("location.href"));
    console.error("Browser body:", await evaluate("document.body?.innerText.slice(0,2500)"));
    throw error;
  }
  await until(() => evaluate("document.querySelectorAll('table tbody tr').length >= 4"), "verified domestic institutions");
  assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth + 2"), true, "no page horizontal overflow");
  assert.equal(await evaluate("[...document.querySelectorAll('table tbody tr')].every(x=>/中国科学院|清华大学|复旦大学|浙江大学/.test(x.innerText))"), true);
  if (process.env.AI4S_IMPACT_SCREENSHOT) {
    const screenshot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    await writeFile(process.env.AI4S_IMPACT_SCREENSHOT, Buffer.from(screenshot.data, "base64"));
  }
  await evaluate("[...document.querySelectorAll('button')].find(x=>x.textContent.includes('来源参考')).click()");
  await until(() => evaluate("document.querySelectorAll('table tbody tr').length > 0 && document.body.innerText.includes('迁移待核查')"), "reference rows");
  if (process.env.AI4S_IMPACT_REFERENCE_SCREENSHOT) {
    const screenshot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    await writeFile(process.env.AI4S_IMPACT_REFERENCE_SCREENSHOT, Buffer.from(screenshot.data, "base64"));
  }
  assert.equal(await evaluate("[...document.querySelectorAll('table tbody button')].some(x=>x.textContent==='Z.ai')"), false);
  await evaluate("document.querySelector('input[aria-label=\"包含待核查主体\"]').click()");
  await until(() => evaluate("[...document.querySelectorAll('table tbody tr')].some(x=>x.innerText.includes('智谱') && x.innerText.includes('Z.ai'))"), "consolidated identity visible on demand");
  await evaluate("(() => { const x=document.querySelector('select[aria-label=\"等级筛选\"]'); x.value='A'; x.dispatchEvent(new Event('change',{bubbles:true})); })()");
  await until(() => evaluate("[...document.querySelectorAll('table tbody tr')].every(x=>x.children[2]?.innerText==='A')"), "A tier filter");
  await evaluate("(() => { const x=document.querySelector('select[aria-label=\"等级筛选\"]'); x.value=''; x.dispatchEvent(new Event('change',{bubbles:true})); })()");
  await until(() => evaluate("[...document.querySelectorAll('table tbody button')].some(x=>x.textContent.includes('智谱'))"), "ranking reset");
  await evaluate("(() => { const x=document.querySelector('input[aria-label=\"检索主体、事件和摘要\"]'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(x,'Z.ai'); x.dispatchEvent(new Event('input',{bubbles:true})); })()");
  await until(() => evaluate("document.querySelectorAll('table tbody tr').length > 0 && [...document.querySelectorAll('table tbody tr')].every(x=>x.innerText.includes('智谱'))"), "search results");
  await evaluate("[...document.querySelectorAll('table tbody button')].find(x=>x.textContent.includes('智谱')).click()");
  try {
    await until(() => evaluate("document.querySelector('[aria-label=\"主体证据详情\"]')?.innerText.includes('逐期分数')"), "entity drilldown");
  } catch (error) {
    console.error("Drilldown body:", await evaluate("document.body.innerText.slice(-2500)"));
    throw error;
  }
  assert.equal(await evaluate("document.querySelectorAll('[aria-label=\"主体证据详情\"] a[href^=\"https://\"]').length > 0"), true);
  assert.equal(await evaluate("document.querySelector('[aria-label=\"主体证据详情\"]')?.innerText.includes('Zhipu AI')"), true);
  await evaluate("[...document.querySelectorAll('nav[aria-label=\"分诊视图\"] button')].find(x=>x.textContent.includes('类目审核')).click()");
  await until(() => evaluate("document.body.innerText.includes('L1 → L3 来源树')"), "taxonomy tab");
  assert.equal(await evaluate("document.body.innerText.includes('高原') && document.body.innerText.includes('高峰')"), true);
  assert.equal(await evaluate("document.body.innerText.includes('树外 L2 标签') && document.body.innerText.includes('具身智能 → 已有 L3')"), true);
  await evaluate("[...document.querySelectorAll('button')].find(x=>/[1-9][0-9]* 条事件/.test(x.textContent)).click()");
  await until(() => evaluate("document.querySelector('[aria-label=\"方向事件明细\"]')?.innerText.includes('事件明细')"), "direction events");
  assert.equal(await evaluate("document.querySelectorAll('[aria-label=\"方向事件明细\"] a[href^=\"https://\"]').length > 0"), true);
  await evaluate("[...document.querySelectorAll('nav[aria-label=\"分诊视图\"] button')].find(x=>x.textContent.includes('每日报告')).click()");
  await until(() => evaluate("document.body.innerText.includes('数据流入') && document.body.innerText.includes('类目审核') && document.body.innerText.includes('排名变动') && document.body.innerText.includes('归一标签')"), "daily report");
  assert.deepEqual(await evaluate("window.__impactWrites"), []);
  console.log("Impact triage browser acceptance passed: ranking, filters, evidence, taxonomy, daily; no writes.");
} finally {
  socket?.close();
  chrome.kill();
  await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
}
