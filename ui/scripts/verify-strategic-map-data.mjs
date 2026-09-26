import assert from "node:assert/strict";

// Read-only audit of the currently served taxonomy, team list, and graph.
// The UI assigns the two foundation domains to 高原 and the remaining roots
// to 高峰; a mismatch makes the selected domain appear to have an empty graph.
const base = (
  process.env.STRATEGIC_MAP_API_BASE_URL ||
  "http://127.0.0.1:3000/tool/v1/strategic-map"
).replace(/\/$/, "");

async function getData(url) {
  const response = await fetch(url, { signal: AbortSignal.timeout(30_000) });
  assert.ok(response.ok, `${response.status} ${url}`);
  const body = await response.json();
  return body.data ?? body;
}

function graphUrl(scope, cluster, domainId, subdomainId) {
  const url = new URL(`${base}/graph/data`);
  url.searchParams.set("scope", scope);
  url.searchParams.set("cluster", cluster);
  url.searchParams.set("domain_id", domainId);
  if (subdomainId) url.searchParams.set("subdomain_id", subdomainId);
  return url;
}

function teamDomainId(team) {
  return team.domainId ?? team.domain_id;
}

function teamSubdomainId(team) {
  return team.subdomainId ?? team.subdomain_id;
}

function teamIsDomestic(team) {
  return team.isDomestic ?? team.is_domestic ?? true;
}

function teamIds(nodes) {
  return new Set(
    nodes
      .filter((node) => typeof node.id === "string" && node.id.startsWith("team:"))
      .map((node) => node.id.slice(5)),
  );
}

function assertSameSet(actual, expected, label) {
  const extra = [...actual].filter((id) => !expected.has(id));
  const missing = [...expected].filter((id) => !actual.has(id));
  assert.deepEqual({ extra, missing }, { extra: [], missing: [] }, label);
}

const snapshot = await getData(base);
assert.ok(Array.isArray(snapshot.domains) && snapshot.domains.length > 0);
assert.ok(Array.isArray(snapshot.teams));

let checkedSubdomains = 0;
let checkedGraphs = 0;
for (const scope of ["domestic", "international"]) {
  for (const domain of snapshot.domains) {
    const cluster =
      domain.name === "科学通用底座" || domain.name === "通用 AI"
        ? "高原"
        : "高峰";
    const domainGraph = await getData(graphUrl(scope, cluster, domain.id));
    assert.ok(Array.isArray(domainGraph.nodes), `${domain.name}: graph has no nodes array`);
    const domainNodeIds = new Set(domainGraph.nodes.map((node) => node.id));
    const expectedDomainTeams = new Set(
      snapshot.teams
        .filter(
          (team) =>
            teamDomainId(team) === domain.id &&
            teamIsDomestic(team) === (scope === "domestic"),
        )
        .map((team) => team.id),
    );
    assertSameSet(
      teamIds(domainGraph.nodes),
      expectedDomainTeams,
      `${scope}/${domain.name}: domain team nodes differ from the team list`,
    );
    checkedGraphs += 1;

    for (const subdomain of domain.subdomains ?? []) {
      const graph = await getData(graphUrl(scope, cluster, domain.id, subdomain.id));
      assert.ok(Array.isArray(graph.nodes), `${subdomain.name}: graph has no nodes array`);
      const outside = graph.nodes
        .filter((node) => !domainNodeIds.has(node.id))
        .map((node) => node.id);
      assert.deepEqual(
        outside,
        [],
        `${scope}/${domain.name}/${subdomain.name}: nodes outside the parent domain`,
      );
      const wrongTags = graph.nodes
        .filter((node) => {
          const raw = node.data?.raw ?? {};
          const tag = raw.subdomainId ?? raw.subdomain_id;
          return tag && tag !== subdomain.id;
        })
        .map((node) => node.id);
      assert.deepEqual(
        wrongTags,
        [],
        `${scope}/${domain.name}/${subdomain.name}: nodes tagged to another subdomain`,
      );
      const expectedTeams = new Set(
        snapshot.teams
          .filter(
            (team) =>
              teamDomainId(team) === domain.id &&
              teamSubdomainId(team) === subdomain.id &&
              teamIsDomestic(team) === (scope === "domestic"),
          )
          .map((team) => team.id),
      );
      assertSameSet(
        teamIds(graph.nodes),
        expectedTeams,
        `${scope}/${domain.name}/${subdomain.name}: subdomain team nodes differ from the team list`,
      );
      checkedGraphs += 1;
      checkedSubdomains += 1;
    }
  }
}

console.log(
  `PASS: ${snapshot.domains.length} domains, ${checkedSubdomains / 2} subdomains, ${snapshot.teams.length} teams; ${checkedGraphs} scoped graphs audited read-only.`,
);
