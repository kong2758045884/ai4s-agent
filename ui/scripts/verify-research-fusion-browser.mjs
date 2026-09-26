import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

// Read-only acceptance. Every write to the tool API is blocked in the browser.
const width = Number(process.env.AI4S_FUSION_WIDTH || 1440);
const origin = process.env.AI4S_FUSION_ORIGIN || 'http://127.0.0.1:3000';
const api = process.env.AI4S_FUSION_ORIGIN ? `${origin}/tool/v1/strategic-map` : 'http://127.0.0.1:1604/v1/strategic-map';
const get = async url => { const r = await fetch(url); assert.ok(r.ok, `${url}: ${r.status}`); return r.json(); };
const teams = (await get(`${api}/intelligence/verified-teams`)).teams;
const foundation = teams.find(t => t.domainName === '科学通用底座');
assert.ok(foundation);
const profile = await mkdtemp(path.join(os.tmpdir(), 'ai4s-fusion-browser-'));
const chrome = spawn(process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  ['--headless=new', '--no-first-run', '--no-default-browser-check', '--disable-extensions', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'],
  {stdio:'ignore', windowsHide:true});
const delay = ms => new Promise(r => setTimeout(r, ms));
async function until(fn, label) { const end=Date.now()+30000; while(Date.now()<end) { const result=await fn(); if(result) return result; await delay(150); } throw new Error(`Timed out: ${label}`); }
let socket;
try {
  const portFile=path.join(profile, 'DevToolsActivePort');
  const port=await until(async()=>existsSync(portFile) ? Number((await readFile(portFile,'utf8')).split(/\r?\n/)[0]) : null,'Chrome');
  const target=(await get(`http://127.0.0.1:${port}/json/list`)).find(t=>t.type==='page');
  socket=new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
  let id=0;const pending=new Map();const errors=[];
  socket.addEventListener('message',({data})=>{const m=JSON.parse(data);if(m.method==='Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.text); const p=pending.get(m.id);if(!p)return;pending.delete(m.id);m.error?p.reject(new Error(m.error.message)):p.resolve(m.result);});
  const send=(method,params={})=>new Promise((resolve,reject)=>{pending.set(++id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
  const evaluate=async expression=>{const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true}); if(r.exceptionDetails)throw new Error(r.exceptionDetails.text);return r.result.value;};
  const click=async label=>{const b=`[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(label)})`;await until(()=>evaluate(`Boolean(${b})`),label);await evaluate(`${b}.click()`);};
  await send('Page.enable');await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride',{width,height:width<600?1000:900,deviceScaleFactor:1,mobile:width<600});
  await send('Page.addScriptToEvaluateOnNewDocument',{source:`(() => {
    const original=window.fetch.bind(window);window.__blockedWrites=[];window.__graphRequests=[];
    window.fetch=async(input,init={})=>{
      const u=new URL(typeof input==='string'?input:input.url,location.href);
      const method=String(init.method||input.method||'GET').toUpperCase();
      if(window.__scanFixture && method==='POST' && u.pathname.endsWith('/graph/scans')) {
        window.__mockedScans=(window.__mockedScans||0)+1;
        return new Response(JSON.stringify({success:true,data:window.__scanFixture}),{status:202,headers:{'Content-Type':'application/json'}});
      }
      if(u.pathname.includes('/v1/')&&!['GET','HEAD'].includes(method)){
        window.__blockedWrites.push(u.pathname);throw new Error('Acceptance blocked write');
      }
      if(u.pathname.includes('/integration/graph')){
        window.__graphRequests.push(u.search);
        if(window.__failGraph)return new Response(JSON.stringify({detail:'Not Found'}),{status:404,headers:{'Content-Type':'application/json'}});
      }
      return original(input,init);
    };
  })();`});
  const graphUrl=`${origin}/workspace/strategic-map?smMode=graph&smDomain=${foundation.domainId}`;
  await send('Page.navigate',{url:graphUrl});
  const ready="document.querySelector('.strategic-graph-workspace')?.getAttribute('aria-busy')==='false' && Number(document.querySelector('.strategic-graph-workspace')?.dataset.nodeCount)>0";
  await until(()=>evaluate(ready),'loaded integrated graph');
  assert.ok(await evaluate("Boolean(document.querySelector('[aria-label=\"AI4S 知识关系图谱\"] canvas'))"),'canvas rendered');
  assert.ok(await evaluate("!document.body.innerText.includes('Not Found')"));
  const counts=await evaluate("({nodes:document.querySelector('.strategic-graph-workspace').dataset.nodeCount,edges:document.querySelector('.strategic-graph-workspace').dataset.edgeCount})");
  assert.ok(Number(counts.nodes)>100,'source knowledge nodes restored');
  await click('已核实团队证据');await until(()=>evaluate(ready),'verified evidence graph');
  await click('融合知识图谱');await until(()=>evaluate(ready),'fusion restored');
  await evaluate('window.__failGraph=true');await click('国外');
  await until(()=>evaluate("document.querySelector('[role=alert]')?.textContent.includes('图谱接口暂不可用')"),'honest error state');
  assert.ok(await evaluate("!document.body.innerText.includes('当前领域暂无匹配图谱节点')"),'error not presented as empty data');
  await evaluate('window.__failGraph=false');await click('重新加载图谱');await until(()=>evaluate(ready),'retry succeeded');
  await click('国内');await until(()=>evaluate(ready),'domestic restored');
  assert.ok(await evaluate('document.documentElement.scrollWidth<=innerWidth+2'),'graph no horizontal page overflow');
  if(process.env.AI4S_FUSION_SCREENSHOT){await delay(1000);const shot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});await writeFile(process.env.AI4S_FUSION_SCREENSHOT,Buffer.from(shot.data,'base64'));}
  assert.deepEqual(await evaluate('window.__blockedWrites'),[]);
  await evaluate(`window.__scanFixture=${JSON.stringify({jobId:'scan-'+'a'.repeat(32),keyword:'科学通用底座',status:'done',stage:'完成',progress:{done:1,total:1},persistent:false,createdAt:'',updatedAt:'',finishedAt:'',result:{summary:'来源机构扫描验收',scoringSummary:'旧团队门槛不可作为来源机构分数',candidates:[{rank:1,legacyRank:1,name:'浏览器测试研究机构',type:'机构',event_count:3,total:0,legacyScores:{achievement:80,status:70,future:65,total:72.5},eligibility:{eligible:false},scoreBreakdown:{},reasons:{achievement:'来源成果依据',status:'来源地位依据',future:'来源趋势依据'},comment:'浏览器拦截测试，未调用模型'}]}})}`);
  await click('扫描');
  await until(()=>evaluate("!!document.querySelector('button[aria-label=\"开始扫描\"]')"),'explicit scan service available');
  await evaluate("document.querySelector('button[aria-label=\"开始扫描\"]').click()");
  await until(()=>evaluate("document.body.innerText.includes('浏览器测试研究机构')"),'mocked source scan');
  assert.ok(await evaluate("[...document.querySelectorAll('article')].find(a=>a.textContent.includes('浏览器测试研究机构'))?.textContent.includes('72.5')"),'source influence score preserved independently');
  assert.ok(await evaluate("!document.body.innerText.includes('旧团队门槛不可作为来源机构分数')"));
  assert.equal(await evaluate('window.__mockedScans'),1);
  assert.deepEqual(await evaluate('window.__blockedWrites'),[]);
  // All-domain ranking, detail, source periods, tree and actual historical daily.
  await send('Page.navigate',{url:`${origin}/workspace/strategic-map?smMode=intelligence&smAll=1`});
  await until(()=>evaluate("document.querySelectorAll('[data-impact-entity]').length>0"),'official rankings');
  const institutionCount=await evaluate("document.querySelectorAll('[data-impact-entity]').length");
  await evaluate("document.querySelector('[data-impact-entity]').click()");
  await until(()=>evaluate("!!document.querySelector('[aria-label=\"跨期分数趋势\"] svg')"),'score chart');
  await click('L1→L3 类目树');
  await until(()=>evaluate("document.querySelector('[aria-label=\"领域动态情报\"]')?.textContent.includes('L1 ·')"),'source taxonomy');
  await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent.trim().startsWith('L2 ·')).click()");
  await until(()=>evaluate("document.querySelector('[aria-label=\"领域动态情报\"]')?.textContent.includes(' · 来源事件')"),'taxonomy event section');
  await click('每日报告');await click('2026-09-08');
  await until(()=>evaluate("document.querySelector('[aria-label=\"领域动态情报\"] footer')?.textContent.includes('输入版本')"),'daily report');
  assert.ok(await evaluate("document.querySelector('input[aria-label=\"报告日期\"]').value==='2026-09-08'"));
  assert.ok(await evaluate('document.documentElement.scrollWidth<=innerWidth+2'),'intelligence no horizontal overflow');
  assert.deepEqual(await evaluate('window.__blockedWrites'),[]);
  assert.deepEqual(errors,[],'no uncaught browser errors');
  console.log(JSON.stringify({width,graph:counts,institutions:institutionCount,checks:'graph, evidence switch, 404/retry, ranking, history, tree, daily, overflow',paidCalls:0}));
} finally {
  socket?.close();chrome.kill();
  const target=await realpath(profile);const root=await realpath(os.tmpdir());
  assert.equal(path.dirname(target).toLowerCase(),root.toLowerCase());assert.ok(path.basename(target).startsWith('ai4s-fusion-browser-'));
  await rm(target,{recursive:true,force:true,maxRetries:5,retryDelay:200});
}
