import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

const origin = process.env.AI4S_FUSION_ORIGIN || 'http://81.71.163.184';
const width = Number(process.env.AI4S_FUSION_WIDTH || 1440);
const legacyHtml = process.env.AI4S_LEGACY_ENTRY ? await readFile(process.env.AI4S_LEGACY_ENTRY,'utf8') : null;
const profile = await mkdtemp(path.join(os.tmpdir(), 'ai4s-home-entry-'));
const chrome = spawn(process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  ['--headless=new','--no-first-run','--no-default-browser-check','--disable-extensions',
    // Guest avoids OS-account sign-in; preserve the explicitly requested HTTP
    // origin instead of Edge's optional automatic HTTPS upgrade in private mode.
    ...(process.env.CHROME_PATH?.includes('msedge') ? ['--guest','--disable-features=msImplicitSignin,msSingleSignOnOSForPrimaryAccountIsShared,HttpsUpgrades,msAutomaticHttps,msAutomaticHttpsDefault'] : []),
    '--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank'],
  {stdio:'ignore',windowsHide:true});
const delay = ms => new Promise(resolve => setTimeout(resolve,ms));
let diagnostics = async () => ({});
async function until(fn,label) { const end=Date.now()+45000;while(Date.now()<end){const v=await fn();if(v)return v;await delay(150);}throw new Error(`Timed out: ${label}; ${JSON.stringify(await diagnostics())}`); }
let socket;
try {
  const portFile=path.join(profile,'DevToolsActivePort');
  const port=await until(async()=>existsSync(portFile)?Number((await readFile(portFile,'utf8')).split(/\r?\n/)[0]):null,'Chrome');
  const target=(await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t=>t.type==='page');
  socket=new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(resolve=>socket.addEventListener('open',resolve,{once:true}));
  let id=0;const pending=new Map();const errors=[];const blocked=[];const documentHeaders=[];const requests=new Map();const networkFailures=[];let seededLegacy=false;
  const send=(method,params={})=>new Promise((resolve,reject)=>{pending.set(++id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
  socket.addEventListener('message',({data})=>{
    const m=JSON.parse(data);
    if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails.text);
    if(m.method==='Network.requestWillBeSent')requests.set(m.params.requestId,m.params.request.url);
    if(m.method==='Network.loadingFailed')networkFailures.push({url:requests.get(m.params.requestId),error:m.params.errorText});
    if(m.method==='Network.responseReceived' && m.params.type==='Document')documentHeaders.push(m.params.response.headers);
    if(m.method==='Fetch.requestPaused'){
      const {requestId,request}=m.params;const url=new URL(request.url);let body;
      // Simulate a cached pre-upgrade HTML document. Its real old entry script
      // must now recover to /app without executing the obsolete application.
      if(legacyHtml && !seededLegacy && url.href===origin+'/') {
        seededLegacy=true;
        void send('Fetch.fulfillRequest',{requestId,responseCode:200,responseHeaders:[{name:'Content-Type',value:'text/html'},{name:'Cache-Control',value:'no-store'}],body:Buffer.from(legacyHtml).toString('base64')});return;
      }
      // Identity and empty conversation list only: all strategic-map reads are real.
      if(url.pathname==='/api/agent/visitor/bootstrap')body={code:'0000',data:{visitorId:'browser-acceptance',username:'页面验收',named:true}};
      else if(url.pathname==='/api/agent/conversation/sessions')body={code:'0000',data:[]};
      if(body)void send('Fetch.fulfillRequest',{requestId,responseCode:200,responseHeaders:[{name:'Content-Type',value:'application/json'}],body:Buffer.from(JSON.stringify(body)).toString('base64')});
      else if(!['GET','HEAD','OPTIONS'].includes(request.method)){blocked.push(url.pathname);void send('Fetch.failRequest',{requestId,errorReason:'BlockedByClient'});}
      else void send('Fetch.continueRequest',{requestId});
    }
    const p=pending.get(m.id);if(!p)return;pending.delete(m.id);m.error?p.reject(new Error(m.error.message)):p.resolve(m.result);
  });
  const evaluate=async expression=>{const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw new Error(r.exceptionDetails.text);return r.result.value;};
  diagnostics=async()=>({page:await evaluate("({url:location.href,title:document.title,text:document.body?.innerText.slice(0,1200),links:[...document.querySelectorAll('a')].map(a=>({text:a.textContent,href:a.getAttribute('href'),width:a.getBoundingClientRect().width})).slice(0,10)})"),errors,blocked,networkFailures});
  const click=async label=>{const expr=`[...document.querySelectorAll('button,a')].find(b=>b.textContent.trim()===${JSON.stringify(label)} && b.getBoundingClientRect().width>0)`;await until(()=>evaluate(`Boolean(${expr})`),label);await evaluate(`${expr}.click()`);};
  await send('Page.enable');await send('Runtime.enable');await send('Network.enable');
  await send('Fetch.enable',{patterns:[{urlPattern:'*://*/api/*'},{urlPattern:'*://*/tool/*'},...(legacyHtml?[{urlPattern:origin+'/'}]:[])]});
  await send('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:width<600});
  await send('Page.navigate',{url:origin+'/'});
  await until(()=>evaluate("location.pathname==='/app'"),'bare root redirects to fresh app entry');
  if(width<600){await until(()=>evaluate("Boolean(document.querySelector('button[aria-label=\"打开侧边栏\"]'))"),'mobile sidebar');await evaluate("document.querySelector('button[aria-label=\"打开侧边栏\"]').click()");}
  await click('战略图谱');
  const readyTabs="['任务推荐','团队','关系图谱','动态情报'].every(t=>[...document.querySelectorAll('button')].some(b=>b.textContent.trim()===t))";
  await until(()=>evaluate(readyTabs),'four integrated views from sidebar');
  assert.ok(await evaluate("location.pathname==='/workspace/strategic-map'"),'sidebar uses canonical route');
  assert.ok(await evaluate("Boolean(document.querySelector('a[href=\"/workspace/strategic-map\"][aria-current=\"page\"]'))"),'canonical route retains active sidebar');
  await until(()=>evaluate("document.querySelector('.strategic-map-header p')?.textContent.includes('116 支已核实团队')"),'verified catalogue loaded (summary is collapsed on mobile)');
  await click('团队');await until(()=>evaluate("Boolean(document.querySelector('#strategic-team-list'))"),'team view');
  await click('关系图谱');
  await until(()=>evaluate("Number(document.querySelector('.strategic-graph-workspace')?.dataset.nodeCount)>0"),'real graph from sidebar');
  const graph=await evaluate("({nodes:document.querySelector('.strategic-graph-workspace').dataset.nodeCount,edges:document.querySelector('.strategic-graph-workspace').dataset.edgeCount})");
  if(process.env.AI4S_FUSION_SCREENSHOT){const shot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});await writeFile(process.env.AI4S_FUSION_SCREENSHOT,Buffer.from(shot.data,'base64'));}
  await send('Page.navigate',{url:origin+'/?view=strategic-map&smDomain=domain_9484664194d24e0d86b23f9273eaab61&smSubdomain=all&smTeam=team_919b200d5516c62f2fb2d7cb'});
  await until(()=>evaluate(readyTabs),'legacy bookmark uses same integrated component');
  assert.ok(await evaluate("location.pathname==='/workspace/strategic-map'"),'legacy bookmark redirects');
  await send('Page.reload',{ignoreCache:false});
  await until(()=>evaluate(readyTabs),'ordinary reload, browser cache enabled');
  const cacheHeaders=documentHeaders.map(h=>Object.entries(h).find(([k])=>k.toLowerCase()==='cache-control')?.[1]);
  assert.ok(cacheHeaders.every(v=>v?.includes('no-store')),'all HTML entries must prevent stale caching');
  assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);
  console.log(JSON.stringify({width,legacyHtmlRecovered:seededLegacy,graph,checks:'bare root redirect, sidebar real link, canonical path, four views, real graph, legacy redirect, normal refresh, HTML cache headers',blockedWrites:blocked.length,paidCalls:0}));
} finally {
  socket?.close();chrome.kill();
  const target=await realpath(profile);assert.equal(path.dirname(target).toLowerCase(),(await realpath(os.tmpdir())).toLowerCase());
  assert.ok(path.basename(target).startsWith('ai4s-home-entry-'));
  await rm(target,{recursive:true,force:true,maxRetries:5,retryDelay:200});
}
