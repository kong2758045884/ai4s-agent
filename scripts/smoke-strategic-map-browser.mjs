import assert from "node:assert/strict";
import { setTimeout as delay } from "node:timers/promises";

const cdpUrl = process.argv[2] || "http://127.0.0.1:9333";
const appUrl = process.argv[3] || "http://localhost:3000/workspace/strategic-map";
const toolUrl = "http://127.0.0.1:1601";

async function json(url) {
  const response = await fetch(url);
  assert.equal(response.status, 200, `${url} returned ${response.status}`);
  return response.json();
}

const snapshot = (await json(`${toolUrl}/v1/strategic-map`)).data;
const domain = snapshot.domains[0];
const child = domain?.subdomains?.[0];
assert.ok(domain && child, "a domain with a subdomain is required");

const cluster = ["科学通用底座", "通用 AI"].includes(domain.name)
  ? "高原"
  : "高峰";
async function expectedNodes(subdomainId) {
  const url = new URL(`${toolUrl}/v1/strategic-map/graph/data`);
  url.searchParams.set("scope", "domestic");
  url.searchParams.set("cluster", cluster);
  url.searchParams.set("domain_id", domain.id);
  if (subdomainId) url.searchParams.set("subdomain_id", subdomainId);
  return (await json(url)).data.nodes.length;
}
const rootNodes = await expectedNodes("");
const childNodes = await expectedNodes(child.id);
assert.notEqual(rootNodes, childNodes, "the filter fixture must change the graph");

const targets = await json(`${cdpUrl}/json`);
const page = targets.find((target) => target.type === "page");
assert.ok(page?.webSocketDebuggerUrl, "Chrome CDP page not found");
const socket = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener("open", resolve, { once: true });
  socket.addEventListener("error", reject, { once: true });
});

let nextId = 0;
const pending = new Map();
socket.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (!message.id) return;
  const task = pending.get(message.id);
  if (!task) return;
  pending.delete(message.id);
  if (message.error) task.reject(new Error(JSON.stringify(message.error)));
  else task.resolve(message.result);
});

function command(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++nextId;
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

async function evaluate(expression) {
  const reply = await command("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (reply.exceptionDetails) {
    throw new Error(reply.exceptionDetails.text || "browser evaluation failed");
  }
  return reply.result?.value;
}

async function waitFor(read, predicate, label, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let last;
  while (Date.now() < deadline) {
    last = await read();
    if (predicate(last)) return last;
    await delay(250);
  }
  throw new Error(`${label} timed out; last=${JSON.stringify(last)}`);
}

async function click(label, selector = "button") {
  const found = await evaluate(`(() => {
    const button = [...document.querySelectorAll(${JSON.stringify(selector)})]
      .find((item) => item.textContent.trim() === ${JSON.stringify(label)});
    if (!button) return false;
    button.click();
    return true;
  })()`);
  assert.ok(found, `button not found: ${label}`);
}

const readView = () => evaluate(`(() => ({
  title: document.querySelector('h1')?.textContent?.trim() || '',
  mode: document.querySelector('.strategic-graph-workspace') ? 'graph' : 'teams',
  nodes: Number(document.querySelector('.strategic-graph-stats .grid > div:first-child > div:first-child')?.textContent?.trim() || 0)
}))()`);

try {
  await command("Page.enable");
  await command("Runtime.enable");
  await command("Page.navigate", { url: appUrl });
  const initial = await waitFor(
    readView,
    (view) => view.title.includes("AI4S战略力量图谱"),
    "strategic map load",
  );
  assert.equal(initial.mode, "teams");
  await waitFor(
    () => evaluate(`[...document.querySelectorAll('aside button')]
      .map((item) => item.textContent.trim())`),
    (labels) => labels.includes(child.name),
    "subdomain navigation",
  );

  // A child click changes only the shared scope, not the active workspace.
  await click(child.name, "aside button");
  assert.equal((await readView()).mode, "teams");

  await click("关系图谱", "button[aria-pressed]");
  await waitFor(
    readView,
    (view) => view.mode === "graph" && view.nodes === childNodes,
    "child graph",
  );

  await click("全部子领域", "aside button");
  await waitFor(
    readView,
    (view) => view.mode === "graph" && view.nodes === rootNodes,
    "root graph",
  );

  await click(child.name, "aside button");
  await waitFor(
    readView,
    (view) => view.mode === "graph" && view.nodes === childNodes,
    "child graph after root",
  );

  await click("团队", "button[aria-pressed]");
  assert.equal((await readView()).mode, "teams");
  console.log(JSON.stringify({
    result: "PASS",
    domain: domain.name,
    subdomain: child.name,
    rootNodes,
    childNodes,
    checks: ["team filter keeps mode", "child graph", "root graph", "return to teams"],
  }));
} finally {
  socket.close();
}
