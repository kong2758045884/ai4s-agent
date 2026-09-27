import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdtemp, readFile, writeFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

const url = process.env.AI4S_ASSESSMENT_URL || 'http://127.0.0.1:3003/';
const width = Number(process.env.AI4S_ASSESSMENT_WIDTH || 1440);
const output = process.env.AI4S_ASSESSMENT_OUTPUT;
const profile = await mkdtemp(path.join(os.tmpdir(), 'ai4s-assessment-'));
const chrome = spawn(process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', [
  '--headless=new', '--no-first-run', '--no-default-browser-check', '--no-proxy-server', '--disable-extensions',
  '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore', windowsHide: true });
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check, label, timeout = 45000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { if (await check()) return; await delay(150); }
  throw new Error(`Timed out: ${label}`);
}
let socket, evaluate;
const passed = [];
try {
  const portFile = path.join(profile, 'DevToolsActivePort');
  await until(() => existsSync(portFile), 'Chrome port');
  const port = Number((await readFile(portFile, 'utf8')).split(/\r?\n/)[0]);
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  socket = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }); });
  let id = 0; const pending = new Map();
  socket.addEventListener('message', ({ data }) => { const value = JSON.parse(data); const item = pending.get(value.id);
    if (item) { pending.delete(value.id); value.error ? item.reject(Error(value.error.message)) : item.resolve(value.result); } });
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const next = ++id; const timer = setTimeout(() => { pending.delete(next); reject(Error(`CDP timeout ${method}`)); }, 45000);
    pending.set(next, { resolve: value => { clearTimeout(timer); resolve(value); }, reject: error => { clearTimeout(timer); reject(error); } });
    socket.send(JSON.stringify({ id: next, method, params }));
  });
  evaluate = async expression => { const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    return result.result.value; };
  await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
  await send('Emulation.setDeviceMetricsOverride', { width, height: 960, deviceScaleFactor: 1, mobile: width < 600 });
  await send('Page.addScriptToEvaluateOnNewDocument', { source: `(() => {window.__paid=[];window.__requests=[];const old=fetch;window.fetch=async(input,init={})=>{const p=new URL(typeof input==='string'?input:input.url,location.href).pathname;if(init.method&&init.method!=='GET'&&/\\/(expand|retry|scans|refreshes)$/.test(p)){window.__paid.push(p);throw Error('Paid calls forbidden');}const response=await old(input,init);window.__requests.push({path:p,status:response.status});return response;};})();` });
  const click = async text => {
    await until(() => evaluate(`[...document.querySelectorAll('button,a')].some(e=>e.textContent.trim()===${JSON.stringify(text)}&&!e.disabled)`), `button ${text}`);
    await evaluate(`[...document.querySelectorAll('button,a')].find(e=>e.textContent.trim()===${JSON.stringify(text)}&&!e.disabled).click()`);
  };
  const fill = async (selector, text) => {
    await until(() => evaluate(`!!document.querySelector(${JSON.stringify(selector)})`), selector);
    await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});e.focus();e.select?.();})()`);
    await send('Input.insertText', { text }); await delay(100);
  };
  await send('Page.navigate', { url });
  await until(() => evaluate(`document.body?.innerText.includes('战略图谱')||!!document.querySelector('input[placeholder="输入你的名字，即可体验"]')`), 'home');
  if (await evaluate(`!!document.querySelector('input[placeholder="输入你的名字，即可体验"]')`)) {
    await fill('input[placeholder="输入你的名字，即可体验"]', '研判浏览器验收'); await click('进入AI原生研判系统');
  }
  if (width < 1024) { await until(() => evaluate(`!!document.querySelector('button[aria-label="打开侧边栏"]')`), 'sidebar toggle');
    await evaluate(`document.querySelector('button[aria-label="打开侧边栏"]').click()`); }
  await click('战略图谱');
  await until(() => evaluate(`document.body.innerText.includes('从一个任务，或一个领域开始')`), 'workbench');
  assert.equal(await evaluate('location.pathname'), '/workspace/strategic-map'); passed.push('home-canonical-workbench');
  await fill('textarea[aria-label="研判任务输入"]', '蛋白质');
  await click('领域观察'); await fill('textarea[aria-label="领域观察草稿"]', '观察蛋白质方向');
  await click('任务选队'); assert.equal(await evaluate(`document.querySelector('textarea[aria-label="研判任务输入"]').value`), '蛋白质');
  passed.push('separate-drafts');
  await click('解析任务并确认条件');
  await until(() => evaluate(`!!document.querySelector('section[aria-label="任务条件确认"]')`), 'criteria confirmation');
  assert.ok(await evaluate(`document.body.innerText.includes('生命科学与医学')`));
  assert.equal(await evaluate(`document.querySelectorAll('.assessment-workbench article').length`), 0);
  await click('确认条件，生成结果');
  await until(() => evaluate(`document.querySelectorAll('.assessment-workbench article input[type=checkbox]').length>=3`), 'real candidates');
  passed.push('confirmed-local-recommendation');
  const savedUrl = await evaluate('location.href');
  assert.ok(savedUrl.includes('taskId=assessment-') && savedUrl.includes('runId=recommend-'));
  for (let i = 0; i < 3; i++) {
    await until(() => evaluate(`!document.querySelectorAll('.assessment-workbench article input[type=checkbox]')[${i}].disabled`), `comparison ${i}`);
    await evaluate(`document.querySelectorAll('.assessment-workbench article input[type=checkbox]')[${i}].click()`);
    await until(() => evaluate(`document.querySelectorAll('.assessment-workbench article input[type=checkbox]:checked').length===${i + 1} && !document.body.innerText.includes('处理中…')`), 'saved comparison');
  }
  assert.ok(await evaluate(`document.body.innerText.includes('按同一任务条件比较')`));
  assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth+2'), true, 'responsive comparison');
  await evaluate(`document.querySelector('.assessment-workbench article .text-blue-700 button')?.click()`);
  await evaluate(`document.querySelector('.assessment-workbench article div.rounded-lg button')?.click()`);
  await until(() => evaluate('!!document.querySelector("dialog[open]")'), 'evidence dialog');
  assert.ok(await evaluate(`document.querySelector('dialog[open]').innerText.includes('原始网页抓取时间')`));
  assert.ok(await evaluate(`!!document.querySelector('dialog[open] a[href^="http"]')`));
  await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
  await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
  await until(() => evaluate('!document.querySelector("dialog[open]")'), 'keyboard close');
  passed.push('three-team-criteria-comparison', 'evidence-snapshot-keyboard');
  await send('Page.reload', { ignoreCache: false });
  await until(() => evaluate(`document.querySelectorAll('.assessment-workbench article input[type=checkbox]:checked').length===3`), 'refresh restores comparison');
  assert.equal(await evaluate('location.href'), savedUrl);
  passed.push('refresh-restores-input-run-comparison');
  assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth+2'), true, 'responsive saved result');
  await click('相关关系');
  await until(() => evaluate(`document.body.innerText.includes('当前团队的直接关联') && Number(document.querySelector('.strategic-graph-workspace')?.dataset.nodeCount)>0`), 'one-hop related graph');
  assert.ok(await evaluate(`Number(document.querySelector('.strategic-graph-workspace').dataset.nodeCount)<40`), 'context graph is bounded');
  await click('返回当前研判');
  await until(() => evaluate(`document.querySelectorAll('.assessment-workbench article input[type=checkbox]:checked').length===3`), 'graph return retains selection');
  await click('团队档案');
  await until(() => evaluate(`!!document.querySelector('button[aria-label="返回战略图谱"]')`), 'team profile');
  assert.ok(await evaluate(`new URLSearchParams(location.search).has('taskId') && new URLSearchParams(location.search).has('runId')`));
  await click('返回战略图谱');
  await until(() => evaluate(`document.querySelectorAll('.assessment-workbench article input[type=checkbox]:checked').length===3`), 'profile return retains run');
  passed.push('one-hop-context-graph', 'team-profile-return-context');
  assert.deepEqual(await evaluate('window.__paid'), []);
  assert.deepEqual(await evaluate(`window.__requests.filter(r=>r.path.includes('/strategic-map')&&r.status>=400)`), []);
  if (output) {
    await evaluate('document.querySelectorAll("*").forEach(e=>{if(e.scrollHeight>e.clientHeight)e.scrollTop=0})');
    const screenshot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    await writeFile(output + '.png', Buffer.from(screenshot.data, 'base64'));
    await writeFile(output + '.json', JSON.stringify({ width, passed, paidCalls: 0 }, null, 2));
  }
  console.log(JSON.stringify({ width, passed, paidCalls: 0 }));
} catch (error) {
  console.error(await evaluate?.('({url:location.href,text:document.body.innerText.slice(0,7000)})'));
  if (output) await writeFile(output + '.json', JSON.stringify({ width, passed, error: String(error) }, null, 2));
  throw error;
} finally {
  socket?.close(); chrome.kill();
  const safe = path.resolve(profile);
  assert.ok(safe.startsWith(path.resolve(os.tmpdir()) + path.sep) && path.basename(safe).startsWith('ai4s-assessment-'));
  await rm(safe, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
}
