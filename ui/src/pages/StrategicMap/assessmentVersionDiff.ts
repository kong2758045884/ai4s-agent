import type { AssessmentRun, CombinationRole } from "@/services/strategicAssessments";

type TeamChange = { teamId: string; teamName: string; kind: "added" | "removed" | "updated";
  addedEvidence: string[]; removedEvidence: string[]; changedEvidence: string[];
  previousRole?: CombinationRole; currentRole?: CombinationRole; roleChanged: boolean };

function citations(run: AssessmentRun, teamId: string) {
  const item = run.items.find(value => value.teamId === teamId);
  return new Map((item?.citations || []).map(value => [value.id || `${value.url}|${value.quote}`,
    JSON.stringify([value.text, value.quote, value.url, value.provenance?.contentHash, value.provenance?.quoteHash])]));
}

export function compareVersions(before: AssessmentRun, after: AssessmentRun): TeamChange[] {
  const oldItems = new Map(before.items.map(item => [item.teamId, item]));
  const newItems = new Map(after.items.map(item => [item.teamId, item]));
  const oldRoles = new Map((before.selection?.combination || []).map(role => [role.teamId, role]));
  const newRoles = new Map((after.selection?.combination || []).map(role => [role.teamId, role]));
  return [...new Set([...oldItems.keys(), ...newItems.keys(), ...oldRoles.keys(), ...newRoles.keys()])]
    .sort((a, b) => (newItems.get(a)?.teamName || oldItems.get(a)?.teamName || a).localeCompare(newItems.get(b)?.teamName || oldItems.get(b)?.teamName || b, "zh-CN"))
    .flatMap(teamId => {
      const previous = oldItems.get(teamId), current = newItems.get(teamId);
      const oldCitations = citations(before, teamId), newCitations = citations(after, teamId);
      const addedEvidence = [...newCitations.keys()].filter(key => !oldCitations.has(key));
      const removedEvidence = [...oldCitations.keys()].filter(key => !newCitations.has(key));
      const changedEvidence = [...newCitations.keys()].filter(key => oldCitations.has(key) && oldCitations.get(key) !== newCitations.get(key));
      const previousRole = oldRoles.get(teamId), currentRole = newRoles.get(teamId);
      const roleChanged = JSON.stringify(previousRole || null) !== JSON.stringify(currentRole || null);
      const kind = !previous ? "added" : !current ? "removed" : "updated";
      if (kind === "updated" && !addedEvidence.length && !removedEvidence.length && !changedEvidence.length && !roleChanged) return [];
      return [{
        teamId,
        teamName: current?.teamName || previous?.teamName || teamId,
        kind,
        addedEvidence,
        removedEvidence,
        changedEvidence,
        previousRole,
        currentRole,
        roleChanged
      }];
    });
}
