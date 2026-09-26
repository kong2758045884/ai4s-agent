import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdtemp, readFile, realpath, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

// Run against the local UI and tool service. All strategic-map writes are
// intercepted in a disposable Chrome profile, so this does not alter MySQL.
const chromePath =
  process.env.CHROME_PATH ||
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
assert.ok(existsSync(chromePath), `Chrome not found: ${chromePath}`);

const profile = await mkdtemp(path.join(os.tmpdir(), "ai4s-strategic-smoke-"));
const chrome = spawn(
  chromePath,
  [
    "--headless=new",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    "--remote-debugging-port=0",
    `--user-data-dir=${profile}`,
    "about:blank",
  ],
  { stdio: "ignore", windowsHide: true },
);

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(check, label, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const value = await check();
    if (value) return value;
    await delay(150);
  }
  throw new Error(`Timed out: ${label}`);
}

let socket;
try {
  const portFile = path.join(profile, "DevToolsActivePort");
  const port = await until(async () => {
    if (!existsSync(portFile)) return null;
    return Number((await readFile(portFile, "utf8")).split(/\r?\n/)[0]);
  }, "Chrome debugging port");
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const target = targets.find((item) => item.type === "page");
  assert.ok(target, "Chrome has no page target");

  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  const pending = new Map();
  let commandId = 0;
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    if (!message.id) return;
    const promise = pending.get(message.id);
    if (!promise) return;
    pending.delete(message.id);
    if (message.error) promise.reject(new Error(message.error.message));
    else promise.resolve(message.result);
  });
  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const id = ++commandId;
      pending.set(id, { resolve, reject });
      socket.send(JSON.stringify({ id, method, params }));
    });
  const evaluate = async (expression) => {
    const result = await send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.text);
    }
    return result.result.value;
  };

  await send("Page.enable");
  await send("Runtime.enable");
  await send("Page.addScriptToEvaluateOnNewDocument", {
    source: `(() => {
      const originalFetch = window.fetch.bind(window);
      const calls = { creates: [], refreshes: [], blockedWrites: [], graphLoads: [], snapshotSettled: false };
      window.__strategicSmoke = calls;
      window.fetch = async (input, init = {}) => {
        const url = new URL(typeof input === 'string' ? input : input.url, location.href);
        const method = String(init.method || (typeof input === 'string' ? 'GET' : input.method)).toUpperCase();
        if (method === 'POST' && /\\/v1\\/strategic-map\\/domains\\/[^/]+\\/subdomains$/.test(url.pathname)) {
          const body = JSON.parse(init.body);
          calls.creates.push({ path: url.pathname, name: body.name });
          return new Response(JSON.stringify({ id: 'smoke-subdomain-no-write', name: body.name, description: body.description }), {
            status: 200, headers: { 'Content-Type': 'application/json' }
          });
        }
        if (method !== 'GET' && url.pathname.includes('/v1/strategic-map')) {
          const bucket = url.pathname.includes('refreshes') ? calls.refreshes : calls.blockedWrites;
          bucket.push({ method, path: url.pathname });
          return new Response(JSON.stringify({ detail: 'blocked by browser smoke test' }), {
            status: 503, headers: { 'Content-Type': 'application/json' }
          });
        }
        const response = await originalFetch(input, init);
        if (method === 'GET' && url.pathname.endsWith('/v1/strategic-map')) {
          calls.snapshotSettled = true;
        }
        if (method === 'GET' && url.pathname.endsWith('/v1/strategic-map/graph/data')) {
          calls.graphLoads.push({
            domainId: url.searchParams.get('domain_id'),
            subdomainId: url.searchParams.get('subdomain_id'),
            cluster: url.searchParams.get('cluster'),
            status: response.status,
          });
        }
        return response;
      };
    })();`,
  });

  await send("Page.navigate", {
    url: "http://localhost:3000/workspace/strategic-map",
  });
  await until(
    () => evaluate(`window.__strategicSmoke?.snapshotSettled === true`),
    "initial strategic-map snapshot",
  );
  try {
    await until(
      () => evaluate(`Boolean(document.querySelector('button[aria-label="新增子领域"]:not(:disabled)'))`),
      "enabled add-subdomain button",
    );
  } catch (error) {
    const page = await evaluate(`({ url: location.href, text: document.body?.innerText.slice(0, 500), button: document.querySelector('button[aria-label="新增子领域"]')?.disabled })`);
    throw new Error(`${error.message}; page=${JSON.stringify(page)}`);
  }
  await delay(500);
  const before = await evaluate(`({
    teamView: Boolean(document.querySelector('#strategic-team-list')),
    graphView: Boolean(document.querySelector('.strategic-graph-workspace'))
  })`);
  assert.ok(before.teamView && !before.graphView, "Domain selection should start in team view");

  await evaluate(`document.querySelector('button[aria-label="新增子领域"]').click()`);
  await until(
    () => evaluate(`Boolean(document.querySelector('form.strategic-map-editor input'))`),
    "add-subdomain editor",
  );
  await evaluate(`(() => {
    const input = document.querySelector('form.strategic-map-editor input');
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '浏览器验收临时子领域');
    input.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  await until(
    () => evaluate(`document.querySelector('form.strategic-map-editor input')?.value === '浏览器验收临时子领域'`),
    "editor input value",
  );
  await evaluate(`document.querySelector('form.strategic-map-editor button[type="submit"]').click()`);
  await until(
    () => evaluate(`window.__strategicSmoke.creates.length === 1 && !document.querySelector('form.strategic-map-editor')`),
    "subdomain save",
  );
  await delay(1_000);
  const after = await evaluate(`({
    calls: window.__strategicSmoke,
    url: location.href,
    selected: location.search.includes('smSubdomain=smoke-subdomain-no-write'),
    teamView: Boolean(document.querySelector('#strategic-team-list')),
    graphView: Boolean(document.querySelector('.strategic-graph-workspace')),
    newNameVisible: document.body.innerText.includes('浏览器验收临时子领域')
  })`);
  assert.equal(after.calls.creates.length, 1, "Create request was not intercepted");
  assert.deepEqual(after.calls.refreshes, [], "Creating a subdomain started a scan");
  assert.deepEqual(after.calls.blockedWrites, [], "Unexpected strategic-map write");
  assert.ok(after.selected, "New subdomain was not selected");
  assert.ok(after.teamView && !after.graphView, "Creating a subdomain switched workspaces");
  const subdomainButtons = `Array.from(document.querySelectorAll('.strategic-map-domain-nav div'))
    .filter((item) => item.className?.includes?.('group/sub'))
    .map((item) => item.querySelector('button'))
    .filter(Boolean)`;
  const existingSubdomainCount = await evaluate(`(${subdomainButtons}).filter((button) => !button.textContent.includes('浏览器验收临时子领域')).length`);
  assert.ok(existingSubdomainCount > 0, "No existing subdomain available to test filtering");
  await evaluate(`(${subdomainButtons}).find((button) => !button.textContent.includes('浏览器验收临时子领域')).click()`);
  await until(
    () => evaluate(`location.search.includes('smSubdomain=') && !location.search.includes('smSubdomain=smoke-subdomain-no-write')`),
    "existing subdomain filter in team view",
  );
  assert.equal(await evaluate(`Boolean(document.querySelector('#strategic-team-list'))`), true);

  await evaluate(`Array.from(document.querySelectorAll('button')).find((button) => button.textContent.trim() === '关系图谱').click()`);
  await until(
    () => evaluate(`Boolean(document.querySelector('.strategic-graph-workspace'))`),
    "graph workspace",
  );
  const previousDomain = await evaluate(`new URLSearchParams(location.search).get('smDomain')`);
  await evaluate(`Array.from(document.querySelectorAll('.strategic-map-domain-nav button[aria-pressed]')).find((button) => !['科学通用底座', '通用 AI'].includes(button.textContent.trim())).click()`);
  await until(
    () => evaluate(`new URLSearchParams(location.search).get('smDomain') !== ${JSON.stringify(previousDomain)}`),
    "domain filter in graph view",
  );
  assert.equal(await evaluate(`Boolean(document.querySelector('.strategic-graph-workspace'))`), true);
  const peakDomain = await evaluate(`new URLSearchParams(location.search).get('smDomain')`);
  await until(
    () => evaluate(`window.__strategicSmoke.graphLoads.some((load) => load.domainId === ${JSON.stringify(peakDomain)} && load.cluster === '高峰' && load.status === 200)`),
    "peak-domain graph request",
  );
  const graphSubdomainCount = await evaluate(`(${subdomainButtons}).length`);
  if (graphSubdomainCount > 0) {
    await evaluate(`(${subdomainButtons})[0].click()`);
    await until(
      () => evaluate(`new URLSearchParams(location.search).get('smSubdomain') !== 'all'`),
      "subdomain filter in graph view",
    );
    assert.equal(await evaluate(`Boolean(document.querySelector('.strategic-graph-workspace'))`), true);
    const selectedSubdomain = await evaluate(`new URLSearchParams(location.search).get('smSubdomain')`);
    await until(
      () => evaluate(`window.__strategicSmoke.graphLoads.some((load) => load.domainId === ${JSON.stringify(peakDomain)} && load.subdomainId === ${JSON.stringify(selectedSubdomain)} && load.cluster === '高峰' && load.status === 200)`),
      "peak-subdomain graph request",
    );
  }
  const finalCalls = await evaluate(`window.__strategicSmoke`);
  assert.deepEqual(finalCalls.refreshes, [], "Changing filters started a scan");
  assert.deepEqual(finalCalls.blockedWrites, [], "Changing filters made an unexpected write");
  console.log("PASS: taxonomy creation and filtering preserve the selected workspace, peak graph requests use 高峰, no scan starts, and no database write occurs.");
} finally {
  socket?.close();
  chrome.kill();
  await delay(500);
  const resolvedProfile = await realpath(profile).catch(() => profile);
  const tempRoot = await realpath(os.tmpdir());
  if (
    path.dirname(resolvedProfile).toLowerCase() === tempRoot.toLowerCase() &&
    path.basename(resolvedProfile).startsWith("ai4s-strategic-smoke-")
  ) {
    await rm(resolvedProfile, { recursive: true, force: true }).catch(() => {});
  }
}
