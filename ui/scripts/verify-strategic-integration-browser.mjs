import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const chromePath = process.env.CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
assert.ok(existsSync(chromePath), `Chrome not found: ${chromePath}`);
const url = process.env.AI4S_INTEGRATION_BROWSER_URL || "http://127.0.0.1:3003/workspace/strategic-map";
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
  await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: width < 600 });
  await send("Page.addScriptToEvaluateOnNewDocument", { source: `(() => {
    const compareFixture = ${compareFixture};
    const original = window.fetch.bind(window);
    window.__blockedPaidWrites = [];
    window.__taskCalls = [];
    window.fetch = async (input, init = {}) => {
      const url = new URL(typeof input === 'string' ? input : input.url, location.href);
      const method = String(init.method || 'GET').toUpperCase();
      if (url.pathname.endsWith('/task-recommendations') && method === 'POST') window.__taskCalls.push(url.pathname);
      if (method !== 'GET' && ['/expand','/refreshes','/scans'].some(suffix => url.pathname.endsWith(suffix))) {
        window.__blockedPaidWrites.push(url.pathname);
        return new Response(JSON.stringify({detail:'blocked by browser acceptance'}), {status:403,headers:{'Content-Type':'application/json'}});
      }
      const response = await original(input, init);
      if (url.pathname.endsWith('/intelligence/verified-graph')) window.__verifiedGraph = {status:response.status,body:await response.clone().json()};
      if (compareFixture && url.pathname.endsWith('/task-recommendations') && method === 'POST' && response.ok) {
        const body = await response.clone().json();
        const run = body.data || body;
        if (run.items?.length >= 2) {
          run.items.push({...run.items[1], teamId:'browser-fixture-third',teamName:'浏览器验收第三团队'});
          run.matchedTeamCount = run.items.length;
          run.shortfall = Math.max(0, run.requestedLimit - run.items.length);
        }
        return new Response(JSON.stringify(body), {status:response.status,headers:{'Content-Type':'application/json'}});
      }
      return response;
    };
  })();` });
  await send("Page.navigate", { url });
  try {
    await until(() => evaluate("document.body?.innerText.includes('输入任务，找到能承担它的国内团队')"), "recommendation home");
  } catch (error) {
    console.error("URL:", await evaluate("location.href"));
    console.error("Body:", await evaluate("document.body?.innerText.slice(0,2500)"));
    throw error;
  }
  assert.equal(await evaluate("document.querySelector('button[aria-pressed=true]')?.textContent.includes('任务推荐')"), true);
  await evaluate("document.querySelector('#strategic-task-input').focus()");
  await send("Input.insertText", { text: taskText });
  assert.equal(await evaluate("document.querySelector('#strategic-task-input').value"), taskText);
  await evaluate("[...document.querySelectorAll('button')].find(x=>x.textContent.includes('生成推荐')).click()");
  try {
    await until(() => evaluate("document.body.innerText.includes('支可推荐') && document.body.innerText.includes('可追溯成果与能力')"), "cited recommendation");
  } catch (error) {
    console.error("After submit:", await evaluate("document.body.innerText.slice(0,3500)"));
    console.error("Submit diagnostics:", await evaluate("({value:document.querySelector('#strategic-task-input').value,disabled:[...document.querySelectorAll('button')].find(x=>x.textContent.includes('生成推荐'))?.disabled,calls:window.__taskCalls})"));
    throw error;
  }
  assert.equal(await evaluate("document.querySelectorAll('main[aria-label=\"任务推荐\"] a[href^=\"http\"]').length > 0"), true);
  assert.equal(await evaluate("document.body.innerText.includes('任务匹配') && document.body.innerText.includes('团队总分') && document.body.innerText.includes('原文证据')"), true);
  assert.equal(await evaluate("/待核|待审|线索/.test(document.body.innerText)"), false, "only reviewed recommendations are published");
  assert.equal(await evaluate("getComputedStyle(document.querySelector('.strategic-map-header')).backgroundColor"), "rgb(255, 255, 255)", "white header");
  assert.equal(await evaluate("getComputedStyle(document.querySelector('main[aria-label=\"任务推荐\"] > section')).backgroundColor"), "rgb(255, 255, 255)", "white task surface");
  if (compareFixture || compareReal) {
    if (compareFixture) await until(() => evaluate("document.body.innerText.includes('浏览器验收第三团队')"), "three-team browser fixture");
    if (compareReal) assert.ok(await evaluate("document.querySelectorAll('main[aria-label=\"任务推荐\"] input[type=checkbox]').length >= 3"), "three actual evidence-backed recommendations");
    for (let index = 0; index < 3; index += 1) {
      await evaluate(`[...document.querySelectorAll('main[aria-label=\"任务推荐\"] input[type=checkbox]')][${index}].click()`);
      await until(() => evaluate(`[...document.querySelectorAll('main[aria-label=\"任务推荐\"] input[type=checkbox]')].filter(x=>x.checked).length===${index + 1}`), `compare selection ${index + 1}`);
    }
    assert.equal(await evaluate("document.querySelectorAll('section table thead th').length"), 4, "three teams plus metric column");
  }
  await evaluate("document.querySelector('input[aria-label=\"证据检索\"]').focus()");
  await send("Input.insertText", { text: taskText });
  await until(() => evaluate(`document.querySelector('input[aria-label="证据检索"]').value===${JSON.stringify(taskText)}`), "search input");
  await evaluate("[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='检索').click()");
  try {
    await until(() => evaluate("document.body.innerText.includes('已核团队证据')"), "reviewed evidence search");
  } catch (error) {
    console.error("Search diagnostics:", await evaluate("({value:document.querySelector('input[aria-label=\"证据检索\"]')?.value,body:document.body.innerText.slice(-1600)})"));
    throw error;
  }
  if (process.env.AI4S_INTEGRATION_SCREENSHOT) {
    await evaluate("document.querySelectorAll('*').forEach(element => { if (element.scrollHeight > element.clientHeight) element.scrollTop = 0 }); window.scrollTo(0, 0)");
    await delay(150);
    const screenshot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
    await writeFile(process.env.AI4S_INTEGRATION_SCREENSHOT, Buffer.from(screenshot.data, "base64"));
  }
  assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth + 2"), true, "no document horizontal overflow");
  await evaluate("[...document.querySelectorAll('header button')].find(x=>x.textContent.includes('动态情报')).click()");
  await until(() => evaluate("document.body.innerText.includes('领域动态情报') && document.body.innerText.includes('中国内地科研机构')"), "embedded intelligence");
  assert.equal(await evaluate("/待核|待审|待校准/.test(document.body.innerText)"), false, "public institution facts only");
  await evaluate("[...document.querySelectorAll('nav[aria-label=\"分诊视图\"] button')].find(x=>x.textContent.includes('研究类目')).click()");
  await until(() => evaluate("document.body.innerText.includes('战略图谱已配置的领域与子领域')"), "published taxonomy");
  await evaluate("[...document.querySelectorAll('nav[aria-label=\"分诊视图\"] button')].find(x=>x.textContent.includes('每日报告')).click()");
  await until(() => evaluate("document.body.innerText.includes('团队与推荐变化') && document.body.innerText.includes('跟进事项')"), "combined daily");
  await evaluate("[...document.querySelectorAll('header button')].find(x=>x.textContent.trim()==='团队').click()");
  await until(() => evaluate("Boolean(document.querySelector('#strategic-team-list'))"), "existing team library");
  assert.equal(await evaluate("document.querySelector('#strategic-team-list').innerText.includes('待核验线索')"), false, "reviewed team library");
  if (width >= 600) {
    await evaluate("[...document.querySelectorAll('.strategic-map-domain-nav button')].find(x=>x.textContent.trim()==='通用 AI').click()");
    try {
      await until(() => evaluate("[...document.querySelectorAll('.strategic-map-domain-nav button')].some(x=>x.textContent.trim()==='通用 AI' && x.getAttribute('aria-pressed')==='true')"), "existing domain filter");
    } catch (error) {
      console.error("Domain buttons:", await evaluate("[...document.querySelectorAll('.strategic-map-domain-nav button')].slice(0,12).map(x=>({text:x.textContent.trim(),pressed:x.getAttribute('aria-pressed')}))"));
      console.error("URL after filter:", await evaluate("location.href"));
      throw error;
    }
  }
  assert.equal(await evaluate("/待核|待审|线索/.test(document.querySelector('#strategic-team-list').innerText)"), false, "team list excludes unresolved records");
  if (width >= 600) {
    const catalogue = await evaluate("fetch('/tool/v1/strategic-map/intelligence/verified-teams').then(r=>r.json())");
    const counts = new Map();
    for (const team of catalogue.teams) counts.set(team.domainName, (counts.get(team.domainName) || 0) + 1);
    for (const [domain, count] of counts) {
      await evaluate(`[...document.querySelectorAll('.strategic-map-domain-nav button')].find(x=>x.textContent.trim()===${JSON.stringify(domain)}).click()`);
      await until(() => evaluate(`document.querySelectorAll('#strategic-team-list [data-team-id]').length===${count}`), `${domain}: ${count} published teams`);
    }
    assert.ok(catalogue.teams.some(team => !team.candidateQualified), "verified directory includes teams without complete staffing");
    if (process.env.AI4S_TEAM_LIBRARY_SCREENSHOT) {
      const shotDomain = process.env.AI4S_SCREENSHOT_DOMAIN || "化学与材料";
      await evaluate(`[...document.querySelectorAll('.strategic-map-domain-nav button')].find(x=>x.textContent.trim()===${JSON.stringify(shotDomain)}).click()`);
      await delay(200);
      const shot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
      await writeFile(process.env.AI4S_TEAM_LIBRARY_SCREENSHOT, Buffer.from(shot.data, "base64"));
    }
  }
  await evaluate("[...document.querySelectorAll('header button')].find(x=>x.textContent.includes('关系图谱')).click()");
  await until(() => evaluate("Boolean(document.querySelector('.strategic-graph-workspace'))"), "existing relation graph");
  await until(() => evaluate("window.__verifiedGraph?.status===200"), "reviewed graph API");
  assert.equal(await evaluate("window.__verifiedGraph.body.provider"), "AI4S 原文证据");
  await evaluate("[...document.querySelectorAll('header button')].find(x=>x.textContent.includes('任务推荐')).click()");
  await evaluate("[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='全部六大领域')?.click()");
  await until(() => evaluate("document.body.innerText.includes('查看完整团队档案')"), "restore recommendation result");
  await evaluate("[...document.querySelectorAll('button')].find(x=>x.textContent.includes('查看完整团队档案')).click()");
  await until(() => evaluate("document.body.innerText.includes('团队负责人') && document.body.innerText.includes('核心成员')"), "published team detail");
  assert.equal(await evaluate("getComputedStyle(document.querySelector('.strategic-team-detail > header')).backgroundColor"), "rgb(255, 255, 255)", "white team detail");
  assert.deepEqual(await evaluate("window.__blockedPaidWrites"), []);
  console.log(`Strategic integration browser acceptance passed at ${width}x${height}: task, citations, intelligence, taxonomy, daily, teams, graph; no paid scan.`);
} finally {
  socket?.close();
  chrome.kill();
  await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
}
