export const ROUTES = {
  HOME: "/",
  FEATURED_CONVERSATIONS: "/featured-conversations",
  FEATURED_CONVERSATION_DETAIL: "/featured-conversations/:featuredId",
  WORKSPACE: "/workspace",
  WORKSPACE_STRATEGIC_MAP: "/workspace/strategic-map",
  STRATEGIC_TEAM_DETAIL: "/strategic-map/team/:teamId",
  WORKSPACE_MRAG: "/workspace/mrag",
  WORKSPACE_IMAGE_GENERATION: "/workspace/image-generation",
  WORKSPACE_SOP: "/workspace/sop",
  WORKSPACE_SUB_AGENTS: "/workspace/sub-agents",
  WORKSPACE_MODELS: "/workspace/models",
  WORKSPACE_CAPABILITIES: "/workspace/capabilities",
  NOT_FOUND: "*",
} as const;

export function buildFeaturedConversationDetailPath(featuredId: string) {
  return ROUTES.FEATURED_CONVERSATION_DETAIL.replace(
    ":featuredId",
    encodeURIComponent(featuredId)
  );
}

export function buildStrategicTeamDetailPath(teamId: string) {
  return ROUTES.STRATEGIC_TEAM_DETAIL.replace(":teamId", encodeURIComponent(teamId));
}
