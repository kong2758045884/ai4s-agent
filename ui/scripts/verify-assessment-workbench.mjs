import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdtemp, readFile, writeFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

const url = process.env.AI4S_ASSESSMENT_URL || 'http://127.0.0.1:3003/';
const width = Number(process.env.AI4S_ASSESSMENT_WIDTH || 1440);
const output = process.env.AI4S_ASSESSMENT_OUTPUT;
const replayInvestigation = process.env.AI4S_ASSESSMENT_INVESTIGATION_REPLAY === '1';
const replayRole = process.env.AI4S_ASSESSMENT_ROLE_REPLAY === '1';
const project = path.resolve(process.cwd(), '..');
let grantedVisitor = '';
const setTestRole = (visitor, role) => new Promise((resolve, reject) => {
  assert.ok(['localhost', '127.0.0.1'].includes(new URL(url).hostname), 'Role replay is restricted to localhost');
  const child = spawn(path.join(project, 'ai4s-tool/.venv/Scripts/python.exe'), [
    path.join(project, 'ai4s-tool/scripts/manage_strategic_roles.py'),
    '--db', path.join(project, 'runtime/optimization-20260926/teacher-preview.db'),
    '--visitor', visitor, '--role', role, '--operator', 'isolated-browser-acceptance', '--reason', '副本浏览器权限验收'],
  { stdio: 'ignore', windowsHide: true });
  child.on('error', reject); child.on('exit', code => code === 0 ? resolve() : reject(Error('Isolated role command failed')));
});
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
  await send('Page.addScriptToEvaluateOnNewDocument', { source: `(() => {window.__paid=[];window.__requests=[];const old=fetch;window.fetch=async(input,init={})=>{const p=new URL(typeof input==='string'?input:input.url,location.href).pathname;if(init.method&&init.method!=='GET'&&(/\\/(expand|retry|scans|refreshes)$/.test(p)||p.includes('/investigations'))){window.__paid.push(p);throw Error('Paid calls forbidden');}const response=await old(input,init);window.__requests.push({path:p,status:response.status});return response;};})();` });
  if (replayInvestigation) await send('Page.addScriptToEvaluateOnNewDocument', { source: `(() => {
    window.__webReplay=[]; const old=fetch; let job=null;
    const answer=data=>Promise.resolve(new Response(JSON.stringify({data}),{status:200,headers:{'Content-Type':'application/json'}}));
    window.fetch=async(input,init={})=>{const p=new URL(typeof input==='string'?input:input.url,location.href).pathname;
      if(!p.includes('/investigations'))return old(input,init);
      if(!init.method||init.method==='GET'){
        if(p.endsWith('/investigations')){const r=await old(input,init);const v=await r.json();v.data.configured=true;v.data.canStart=true;v.data.canRetry=true;v.data.configurationNotice='离线浏览器回放：不会调用提供方';return new Response(JSON.stringify(v),{status:r.status});}
        return answer(job);
      }
      window.__webReplay.push(p);
      if(p.endsWith('/cancel')){job={...job,state:'cancelled',stage:'已取消，已发布资料和原推荐保留',cancelRequested:true};return answer(job);}
      if(p.endsWith('/retry')){job={...job,jobId:'offline-retry',state:'partial',stage:'调查完成',cancelRequested:false,failures:[{teamId:job.investigationOptions.teamIds[0],reason:'离线回放：来源不可访问'}]};return answer(job);}
      const options=JSON.parse(init.body).options;
      job={jobId:'offline-web',state:'running',stage:'检索来源',stages:['检索来源','抓取原文','核验归属','发布证据','更新推荐'],error:'',progress:{done:0,total:options.teamIds.length},publishedTeams:[],failures:[],calls:{search:0,fetch:0,llm:0},costCny:null,costNotice:'离线回放，未调用提供方',investigationOptions:options};return answer(job);
    };
  })();` });
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
  assert.ok(await evaluate(`document.querySelector('dialog[open]').innerText.includes('来源校验') && document.querySelector('dialog[open]').innerText.includes('AI 复核') && document.querySelector('dialog[open]').innerText.includes('未记录人工审核')`));
  assert.ok(await evaluate(`document.querySelector('dialog[open]').innerText.includes('对应任务条件')`));
  assert.ok(await evaluate(`!!document.querySelector('dialog[open] a[href^="http"]')`));
  await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
  await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
  await until(() => evaluate('!document.querySelector("dialog[open]")'), 'keyboard close');
  passed.push('three-team-criteria-comparison', 'evidence-snapshot-keyboard');
  passed.push('claim-source-review-provenance');
  await send('Page.reload', { ignoreCache: false });
  await until(() => evaluate(`document.querySelectorAll('.assessment-workbench article input[type=checkbox]:checked').length===3`), 'refresh restores comparison');
  assert.equal(await evaluate('location.href'), savedUrl);
  passed.push('refresh-restores-input-run-comparison');
  if (replayInvestigation) {
    assert.deepEqual(await evaluate('window.__webReplay'), [], 'recommendation must not start investigation');
    await click('联网补充资料');
    await until(() => evaluate(`document.querySelector('dialog[open]')?.innerText.includes('明确本次联网调查范围')`), 'investigation scope');
    assert.ok(await evaluate(`document.querySelector('dialog[open]').innerText.includes('每日预算未设上限')`));
    await click('明确启动联网补证');
    await until(() => evaluate(`document.querySelector('section[aria-label="联网补充资料"]').innerText.includes('执行中')`), 'investigation progress');
    await click('取消调查');
    await until(() => evaluate(`document.querySelector('section[aria-label="联网补充资料"]').innerText.includes('已取消')`), 'cancel investigation');
    await click('重试未成功部分');
    await until(() => evaluate(`document.querySelector('section[aria-label="联网补充资料"]').innerText.includes('部分完成')`), 'partial retry');
    assert.equal(await evaluate('window.__webReplay.length'), 3);
    assert.equal(await evaluate(`document.querySelectorAll('.assessment-workbench article input[type=checkbox]:checked').length`), 3);
    passed.push('offline-explicit-web-scope-cancel-retry-preserves-results');
  }
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
  await click('情报观察');
  await until(() => evaluate(`document.body.innerText.includes('与已保存研判相关的变化') && document.body.innerText.includes('暂未发现影响已保存研判的新证据')`), 'private update empty state');
  await click('我的领域报告');
  await until(() => evaluate(`document.querySelector('select[aria-label="报告领域"]')?.options.length>1`), 'report domain catalogue');
  assert.ok(await evaluate(`document.querySelector('select[aria-label="报告领域"]').value.startsWith('domain_')`), 'report initialized to a real domain ID');
  await click('生成并保存报告');
  await until(() => evaluate(`!!document.querySelector('section[aria-label="已保存领域报告"]') && !document.body.innerText.includes('正在保存…')`), 'frozen daily');
  assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth+2'), true, 'responsive daily report');
  await evaluate(`(()=>{const e=document.querySelector('select[aria-label="报告时间范围"]');e.value='weekly';e.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await click('生成并保存报告');
  await until(() => evaluate(`document.querySelector('section[aria-label="已保存领域报告"]')?.innerText.includes('有 6 天尚未保存日报') && !document.body.innerText.includes('正在保存…')`), 'weekly missing days explicit');
  await evaluate(`(()=>{const old=URL.createObjectURL;URL.createObjectURL=b=>{window.__reportExport=b.text().then(JSON.parse);return old(b);};const click=HTMLAnchorElement.prototype.click;HTMLAnchorElement.prototype.click=function(){if(this.download)return;return click.call(this);};})()`);
  await click('导出当前报告');
  const exported = await evaluate('window.__reportExport');
  assert.equal(exported.kind, 'weekly'); assert.equal(exported.missingDays.length, 6); assert.equal(exported.dailyInputs.length, 1);
  assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth+2'), true, 'responsive weekly report');
  passed.push('private-daily-weekly-export-same-snapshot');
  await click('领域情报、机构榜与日报');
  await until(() => evaluate(`document.body.innerText.includes('机构影响力') || document.body.innerText.includes('机构榜')`), 'domain intelligence accessible');
  await click('研判工作台');
  await until(() => evaluate(`document.querySelectorAll('.assessment-workbench article input[type=checkbox]:checked').length===3`), 'intelligence return retains run');
  passed.push('private-changes-and-domain-intelligence');
  if (replayRole) {
    const access = await evaluate(`fetch('/tool/v1/strategic-map/access').then(r=>r.json()).then(r=>r.data)`);
    assert.deepEqual(access.permissions, []);
    grantedVisitor = access.visitorId;
    await setTestRole(grantedVisitor, 'reviewer');
    await send('Page.reload', { ignoreCache: true });
    await click('团队资料');
    if (width < 1440) await click('团队画像');
    await until(() => evaluate(`[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='编辑'&&!b.disabled)`), 'authorized editor');
    const selectedId = await evaluate(`new URLSearchParams(location.search).get('smTeam')`);
    const profileUrl = await evaluate('location.href');
    assert.ok(selectedId);
    const before = await evaluate(`Promise.all([fetch('/tool/v1/strategic-map/teams/'+${JSON.stringify(selectedId)}).then(r=>r.json()).then(r=>r.data.team),fetch('/tool/v1/strategic-map/teams/'+${JSON.stringify(selectedId)}+'/internal').then(r=>r.json()).then(r=>r.data)])`);
    await click('编辑');
    const newLevel = before[0].aiLevel === '一般' ? '较高' : '一般';
    await evaluate(`(()=>{const e=document.querySelector('select[aria-label="AI 能力"]');e.value=${JSON.stringify(newLevel)};e.dispatchEvent(new Event('change',{bubbles:true}));})()`);
    await click('保存');
    await until(() => evaluate(`document.querySelector('[data-team-id="${selectedId}"] [data-capability="ai"]')?.textContent===${JSON.stringify(newLevel)} && !document.querySelector('select[aria-label="AI 能力"]')`), 'saved manual capability');
    const audit = await evaluate(`fetch('/tool/v1/strategic-map/teams/'+${JSON.stringify(selectedId)}+'/internal').then(r=>r.json()).then(r=>r.data.audit[0])`);
    assert.equal(audit.actorId, grantedVisitor);
    assert.equal(audit.changes.ai_level, newLevel);
    const restore = { attention: before[1].fields.attention, contact: before[1].fields.contact, ai_level: before[0].aiLevel };
    assert.equal(await evaluate(`fetch('/tool/v1/strategic-map/teams/'+${JSON.stringify(selectedId)},{method:'PUT',headers:{'Content-Type':'application/json'},body:${JSON.stringify(JSON.stringify(restore))}}).then(r=>r.status)`), 200);
    // Real authenticated review on the isolated DB: preserve the saved run and
    // revert through the same public API so the test does not erase audit rows.
    await send('Page.navigate', { url: savedUrl });
    await until(() => evaluate(`document.querySelectorAll('.assessment-workbench article input[type=checkbox]:checked').length===3`), 'saved context for reviewer');
    const frozen = await evaluate(`(()=>{const p=new URLSearchParams(location.search);return fetch('/tool/v1/strategic-map/assessments/'+p.get('taskId')+'/runs/'+p.get('runId')).then(r=>r.json()).then(r=>r.data);})()`);
    await evaluate(`document.querySelector('.assessment-workbench article .text-blue-700 button')?.click()`);
    await evaluate(`document.querySelector('.assessment-workbench article div.rounded-lg button')?.click()`);
    await until(() => evaluate('!!document.querySelector("dialog[open]")'), 'review evidence dialog');
    await click('审核此条当前证据');
    await until(() => evaluate(`!!document.querySelector('[data-testid="claim-review-form"]')`), 'current source reviewer form');
    await fill('[data-testid="claim-review-form"] input', '副本浏览器审核员');
    await fill('[data-testid="claim-review-form"] textarea', '仅限本次副本验收，此结论不进入正式数据库');
    await evaluate(`(()=>{const f=document.querySelector('[data-testid="claim-review-form"]');const s=f.querySelector('select');s.value='conflict';s.dispatchEvent(new Event('change',{bubbles:true}));})()`);
    // Each textarea is nested in a label, so use a temporary stable marker for the reason.
    await evaluate(`document.querySelectorAll('[data-testid="claim-review-form"] textarea')[1].setAttribute('data-browser-reason','true')`);
    await fill('[data-browser-reason]', '离线验收构造争议，确认不会覆盖历史推荐');
    await click('保存本条审核');
    await until(() => evaluate(`document.querySelector('dialog[open]')?.innerText.includes('已保存。相关研判将在本地更新') && document.querySelector('dialog[open]')?.innerText.includes('存在冲突')`), 'saved individual review');
    assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth+2'), true, 'responsive claim review');
    if (output) {
      await evaluate(`document.querySelector('[data-testid="claim-review-form"]').scrollIntoView({block:'start'})`);
      const reviewImage = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
      await writeFile(output + '-review.png', Buffer.from(reviewImage.data, 'base64'));
    }
    const unchanged = await evaluate(`fetch('/tool/v1/strategic-map/assessments/${frozen.taskId}/runs/${frozen.runId}').then(r=>r.json()).then(r=>r.data)`);
    assert.deepEqual(unchanged, frozen, 'old run source and comparison snapshot not rewritten by review');
    await fill('[data-browser-reason]', '离线验收完成，撤销测试审核并保留日志');
    await click('撤销最新审核并记录原因');
    await until(() => evaluate(`document.querySelector('dialog[open]')?.innerText.includes('恢复来源检查状态')`), 'review rollback retained in history');
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
    passed.push('isolated-claim-review-conflict-frozen-history-revert');
    await setTestRole(grantedVisitor, 'revoked'); grantedVisitor = '';
    await send('Page.navigate', { url: profileUrl });
    if (width < 1440) await click('团队画像');
    await until(() => evaluate(`[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='只读'&&b.disabled)`), 'revoked editor');
    assert.ok(await evaluate(`document.body.innerText.includes('仅维护人员可见')`));
    passed.push('isolated-role-grant-manual-edit-audit-revoke');
  }
  assert.deepEqual(await evaluate('window.__paid'), []);
  assert.deepEqual(await evaluate(`window.__requests.filter(r=>r.path.includes('/strategic-map')&&r.status>=400)`), []);
  if (output) {
    await until(() => evaluate(`!document.querySelector('section[aria-label="联网补充资料"]')?.innerText.includes('正在读取调查配置')`), 'investigation configuration loaded');
    await evaluate('document.querySelectorAll("*").forEach(e=>{if(e.scrollHeight>e.clientHeight)e.scrollTop=0})');
    const screenshot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    await writeFile(output + '.png', Buffer.from(screenshot.data, 'base64'));
    await writeFile(output + '.json', JSON.stringify({ width, passed, paidCalls: 0 }, null, 2));
  }
  console.log(JSON.stringify({ width, passed, paidCalls: 0 }));
} catch (error) {
  console.error(await evaluate?.(replayRole ? `({url:location.href,errors:window.__requests.filter(r=>r.status>=400),alerts:[...document.querySelectorAll('[role=alert]')].map(e=>e.textContent)})` : '({url:location.href,reportDomain:document.querySelector("select[aria-label=报告领域]")?.value,text:document.body.innerText.slice(0,7000)})'));
  if (output) await writeFile(output + '.json', JSON.stringify({ width, passed, error: String(error) }, null, 2));
  throw error;
} finally {
  if (grantedVisitor) await setTestRole(grantedVisitor, 'revoked');
  socket?.close(); chrome.kill();
  const safe = path.resolve(profile);
  assert.ok(safe.startsWith(path.resolve(os.tmpdir()) + path.sep) && path.basename(safe).startsWith('ai4s-assessment-'));
  await rm(safe, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
}
