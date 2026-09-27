import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const chromePath = process.env.CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
assert.ok(existsSync(chromePath), `Chrome not found: ${chromePath}`);
const url = process.env.AI4S_INTEGRATION_BROWSER_URL || "http://127.0.0.1:3004/";
const compareFixture = process.env.AI4S_COMPARE_FIXTURE === "1";
const compareReal = process.env.AI4S_COMPARE_REAL === "1";
const taskText = process.env.AI4S_ACCEPTANCE_TASK || "具身智能";
const width = Number(process.env.AI4S_INTEGRATION_WIDTH || 1440);
const height = Number(process.env.AI4S_INTEGRATION_HEIGHT || 900);
const profile = await mkdtemp(path.join(os.tmpdir(), "ai4s-integration-browser-"));
const chrome = spawn(chromePath, ["--headless=new", "--no-first-run", "--no-default-browser-check",
  "--disable-extensions", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"],
{ stdio: "ignore", windowsHide: true });
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(check, label, timeoutMs = 30000) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    const value = await check();
    if (value) return value;
    await delay(150);
  }
  throw new Error(`Timed out: ${label}`);
}
let socket;
let inspect;
try {
  const portFile = path.join(profile, "DevToolsActivePort");
  const port = await until(async () => existsSync(portFile) ? Number((await readFile(portFile, "utf8")).split(/\r?\n/)[0]) : null, "Chrome port");
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const page = targets.find((target) => target.type === "page");
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
    const request = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) request.reject(new Error(message.error.message));
    else request.resolve(message.result);
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const next = ++id;
    const timer = setTimeout(() => { pending.delete(next); reject(new Error(`CDP timeout: ${method}`)); }, 15000);
    pending.set(next, { resolve: value => { clearTimeout(timer); resolve(value); }, reject: error => { clearTimeout(timer); reject(error); } });
    socket.send(JSON.stringify({ id: next, method, params }));
  });
  const evaluate = async (expression) => {
    const result = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
    return result.result.value;
  };
  inspect = evaluate;
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: width < 600 });
  await send("Page.addScriptToEvaluateOnNewDocument", { source: `(() => {
    const original = window.fetch.bind(window); window.__requests = []; window.__paid = [];
    window.fetch = async (input, init = {}) => {
      const u = new URL(typeof input === 'string' ? input : input.url, location.href);
      const method = String(init.method || 'GET').toUpperCase();
      if (method !== 'GET' && ['/expand','/expand/retry','/refreshes','/scans'].some(suffix => u.pathname.endsWith(suffix))) {
        window.__paid.push(u.pathname); throw new Error('Paid operation forbidden in acceptance');
      }
      const started = performance.now(); const r = await original(input, init);
      window.__requests.push({url:u.pathname+u.search,status:r.status,ms:performance.now()-started}); return r;
    };
  })();` });
  const click = async text => {
    await until(() => evaluate(`[...document.querySelectorAll('button,a')].some(x=>x.textContent.trim()===${JSON.stringify(text)})`), `button ${text}`);
    await evaluate(`[...document.querySelectorAll('button,a')].find(x=>x.textContent.trim()===${JSON.stringify(text)}).click()`);
  };
  const fill = async (selector, text) => {
    await until(() => evaluate(`Boolean(document.querySelector(${JSON.stringify(selector)}))`), selector);
    await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});e.focus();e.select?.();})()`);
    await send('Input.insertText', {text});
    await delay(80);
  };
  const select = async (label, value) => {
    await evaluate(`(()=>{const e=document.querySelector('select[aria-label=${JSON.stringify(label)}]');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(e,${JSON.stringify(value)});e.dispatchEvent(new Event('change',{bubbles:true}));})()`);
    await delay(100);
  };
  await send('Page.navigate', {url});
  await until(() => evaluate(`Boolean(document.querySelector('input[placeholder="输入你的名字，即可体验"]')) || document.body?.innerText.includes("战略图谱")`), 'visitor bootstrap');
  if (await evaluate(`Boolean(document.querySelector('input[placeholder="输入你的名字，即可体验"]'))`)) {
    await fill('input[placeholder="输入你的名字，即可体验"]', '界面验收');
    await click('进入AI原生研判系统');
  }
  if (width < 1024) {
    await until(() => evaluate('Boolean(document.querySelector("button[aria-label=打开侧边栏]"))'), 'mobile navigation');
    await evaluate('document.querySelector("button[aria-label=打开侧边栏]")?.click()');
  }
  await until(() => evaluate('document.body?.innerText.includes("战略图谱")'), 'home sidebar');
  await click('战略图谱');
  await until(() => evaluate('document.body.innerText.includes("输入任务，找到能承担它的国内团队")'), 'canonical recommendation');
  assert.ok((await evaluate('location.pathname')).includes('/workspace/strategic-map'));
  await select('推荐范围', 'all');
  await fill('#strategic-task-input', '蛋白质');
  await click('生成推荐');
  await until(() => evaluate('document.body.innerText.includes("任务理解") && document.querySelectorAll("main input[type=checkbox]").length>=3'), 'three actual teams');
  for (let i=0;i<3;i++) {
    await evaluate(`document.querySelectorAll('main input[type=checkbox]')[${i}].click()`);
    await delay(60);
  }
  assert.equal(await evaluate('document.querySelectorAll("main table thead th").length'), 4);
  assert.ok(await evaluate('document.querySelectorAll("main table a[href^=https]").length>0'));
  assert.ok(await evaluate('document.body.innerText.includes("联网补充资料")'));
  await select('证据检索范围','all');
  await fill('input[aria-label="证据检索"]','中国科学院');
  await click('检索');
  await until(() => evaluate('document.querySelector("nav[aria-label=证据分页]")?.innerText.includes("下一页")'), 'evidence pagination');
  await click('下一页');
  await until(() => evaluate('document.querySelector("section[aria-label=团队资料与原文检索]").innerText.includes("第 21–")'), 'second page');
  await fill('input[aria-label="证据检索"]','南京大学');
  await click('检索');
  await until(() => evaluate('document.querySelector("section[aria-label=团队资料与原文检索]").innerText.includes("第 1–")'), 'query resets page');
  assert.ok(await evaluate('document.querySelector("section[aria-label=团队资料与原文检索] article")?.innerText.includes("南京大学")'));
  // Delay an obsolete query while entering a new one: it must never replace the new result.
  await evaluate(`(()=>{const old=fetch;window.fetch=async(input,init)=>{const u=String(input);const r=await old(input,init);if(u.includes('intelligence/search')&&new URL(u,location.href).searchParams.get('q')==='中国科学院')await new Promise(resolve=>setTimeout(resolve,700));return r;};})()`);
  await fill('input[aria-label="证据检索"]','中国科学院'); await click('检索');
  await fill('input[aria-label="证据检索"]','南京大学'); await click('检索'); await delay(1000);
  assert.ok(await evaluate('document.querySelector("section[aria-label=团队资料与原文检索] article")?.innerText.includes("南京大学")'), 'stale results not shown');
  assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth+2'),true,'responsive task view');
  if (process.env.AI4S_INTEGRATION_SCREENSHOT) {
    await evaluate('document.querySelectorAll("*").forEach(e=>{if(e.scrollHeight>e.clientHeight)e.scrollTop=0});window.scrollTo(0,0)');
    const shot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
    await writeFile(process.env.AI4S_INTEGRATION_SCREENSHOT,Buffer.from(shot.data,'base64'));
  }
  await click('关系图谱');
  await until(() => evaluate('Boolean(document.querySelector(".strategic-graph-workspace canvas"))'), 'relationship graph rendered');
  await evaluate('document.querySelector(".strategic-graph-workspace button[aria-label=搜索]").click()');
  await until(() => evaluate('document.body.innerText.includes("当前图谱内搜索")'), 'graph search');
  await select('图谱检索对象','edges');
  await fill('input[aria-label="搜索图谱"]','证据支持');
  await evaluate('document.querySelector("button[aria-label=执行搜索]").click()');
  await until(() => evaluate('document.querySelector(".strategic-graph-panel").innerText.includes("关系 · 连接数")'), 'relationship matches');
  await evaluate('Array.from(document.querySelectorAll(".strategic-graph-panel button")).find(e=>e.textContent.includes("连接数")).click()');
  assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth+2'),true,'responsive graph view');
  await click('动态情报');
  await fill('input[aria-label="检索科研机构"]','中科院');
  await until(() => evaluate('window.__requests.some(r=>r.url.includes("/ranking?")&&decodeURIComponent(r.url).includes("q=中科院"))'), 'ranking receives q');
  await click('L1→L3 类目树');
  await until(() => evaluate('document.body.innerText.includes("领域分类树")'), 'taxonomy');
  await click('每日报告');
  await until(() => evaluate('document.body.innerText.includes("团队与推荐变化")'), 'complete daily');
  assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth+2'),true,'responsive daily view');
  assert.deepEqual(await evaluate('window.__paid'),[]);
  const failed=await evaluate('window.__requests.filter(r=>r.status>=400&&r.url.includes("/strategic-map"))');
  assert.deepEqual(failed,[],'strategic APIs succeed');
  await send('Page.reload', {ignoreCache:false});
  await until(() => evaluate('document.body.innerText.includes("AI4S战略力量图谱")'), 'ordinary refresh');
  console.log(JSON.stringify({width,height,passed:['home-sidebar','canonical-route','three-real-teams','citation-comparison','pagination','scope','stale-request','edge-search','rank-q','taxonomy','daily','ordinary-refresh'],paidCalls:0}));
} catch(error) {
  console.error(await inspect?.('({url:location.href,body:document.body?.innerText.slice(0,1800)})'));
  console.error(error);
  // Page text only; never credentials or network response bodies.
  throw error;
} finally {
  socket?.close(); chrome.kill();
  const safe=path.resolve(profile);
  assert.ok(safe.startsWith(path.resolve(os.tmpdir())+path.sep)&&path.basename(safe).startsWith('ai4s-integration-browser-'));
  await rm(safe,{recursive:true,force:true,maxRetries:5,retryDelay:200});
}
