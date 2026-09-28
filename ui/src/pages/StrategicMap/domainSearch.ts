import type { StrategicDomain } from "@/services/strategicMap";

export type DomainMatch = {
  domainId: string;
  subdomainId: string | null;
  domainName: string;
  subdomainName: string;
  matchedBy: string;
};

// These words only suggest a scope. The user must select one; they never
// silently classify a team or override a saved task's confirmed scope.
const ROOT_WORDS: Record<string, string[]> = {
  "科学通用底座": ["科学计算", "算力", "高性能计算", "hpc", "知识图谱"],
  "通用 AI": ["人工智能", "大模型", "具身智能", "机器人", "多模态", "ai"],
  "高能物理与量子科技": ["量子", "粒子物理", "高能物理", "量子计算"],
  "化学与材料": ["材料", "合金", "催化", "电池", "化学"],
  "生命科学与医学": ["蛋白", "生物", "基因", "医学", "药物", "肿瘤"],
  "地球科学": ["气候", "天气", "遥感", "海洋", "地震", "地球"],
};

const normalize = (value: string) => value.normalize("NFKC").toLocaleLowerCase().replace(/[\s·_/-]+/g, "");
const matches = (query: string, word: string) => {
  const value = normalize(word);
  return value.length >= 2 && (value.includes(query) || query.includes(value));
};

export function findDomainMatches(domains: StrategicDomain[], rawQuery: string): DomainMatch[] {
  const query = normalize(rawQuery.trim());
  if (query.length < 2) return [];
  const found: DomainMatch[] = [];
  for (const domain of domains) {
    for (const child of domain.subdomains || []) {
      if (matches(query, child.name)) {
        found.push({ domainId: domain.id, subdomainId: child.id, domainName: domain.name,
          subdomainName: child.name, matchedBy: "子领域名称" });
      }
    }
    const word = (ROOT_WORDS[domain.name] || []).find(value => matches(query, value));
    if (matches(query, domain.name) || word) {
      found.push({ domainId: domain.id, subdomainId: null, domainName: domain.name,
        subdomainName: "", matchedBy: word && !matches(query, domain.name) ? "同义词：" + word : "领域名称" });
    }
  }
  return found.slice(0, 10);
}
