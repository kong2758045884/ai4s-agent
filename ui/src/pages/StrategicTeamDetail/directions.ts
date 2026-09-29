import type { StrategicTeam } from "@/services/strategicMap";

export type DirectionSection = { title: string; paragraphs: string[] };

const normalize = (value: string) => value.replace(/\s+/g, " ").trim();

function paragraphs(value: string): string[] {
  return value.split(/\n\s*\n/).map(normalize).filter(Boolean);
}

function explicitTopics(value: string, description: string): DirectionSection[] {
  const matches = [...value.matchAll(/(?:^|[。！？]\s+)([\p{Script=Han}A-Za-z0-9（）()·/+-]{3,24})\s+(?=[\p{Script=Han}A-Za-z])/gu)]
    .map((match) => ({
      title: match[1],
      start: (match.index ?? 0) + match[0].lastIndexOf(match[1])
    }))
    .filter((match) => description.includes(match.title));
  if (matches.length < 2 || matches[0].start !== 0) return [];
  return matches.map((match, index) => {
    const end = matches[index + 1]?.start ?? value.length;
    return {
      title: match.title,
      paragraphs: paragraphs(value.slice(match.start + match.title.length, end))
    };
  }).filter((section) => section.paragraphs.length);
}

/** Reorganize only headings already present in the published record. */
export function teamDirectionSections(team: StrategicTeam): DirectionSection[] {
  const listed = [...new Set((team.researchDirections || []).map(normalize).filter(Boolean))];
  const primary = normalize(team.coreDirection || team.focus || "");
  const source = primary || listed.join("、");
  if (!source) return [];
  const topics = explicitTopics(source, team.description || "");
  if (topics.length) return topics;

  const matchesList = listed.length > 1 && (
    normalize(listed.join("、")) === source || listed.every((value) => source.includes(value))
  );
  if (matchesList) return listed.map((value) => ({
    title: "",
    paragraphs: paragraphs(value)
  }));
  const sections: DirectionSection[] = [{
    title: "",
    paragraphs: paragraphs(source)
  }];
  for (const value of listed) {
    if (!source.includes(value) && !value.includes(source)) {
      sections.push({
        title: "",
        paragraphs: paragraphs(value)
      });
    }
  }
  return sections;
}
