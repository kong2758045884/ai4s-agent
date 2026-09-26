import React, { Suspense } from 'react';
import { createBrowserRouter, Navigate, useLocation } from 'react-router-dom';
import Layout from '@/layout/index';
import { Loading } from '@/components';
import { ROUTES } from './routes';
import { canonicalHomePath, canonicalStrategicMapPath, isStrategicMapHomeSearch } from './strategicMapNavigation';

// 使用 React.lazy 懒加载组件
const Home = React.lazy(() => import('@/pages/Home'));
const FeaturedConversations = React.lazy(
  () => import('@/pages/FeaturedConversations')
);
const FeaturedConversationDetail = React.lazy(
  () => import('@/pages/FeaturedConversationDetail')
);
const WorkspaceMRag = React.lazy(() => import('@/pages/WorkspaceMRag'));
const StrategicTeamDetail = React.lazy(() => import('@/pages/StrategicTeamDetail'));
const WorkspaceImageGeneration = React.lazy(() => import('@/pages/WorkspaceImageGeneration'));
const WorkspaceSop = React.lazy(() => import('@/pages/WorkspaceSop'));
const SubAgentAdmin = React.lazy(() => import('@/pages/SubAgentAdmin'));
const ModelAdmin = React.lazy(() => import('@/pages/ModelAdmin'));
const CapabilityLibrary = React.lazy(() => import('@/pages/CapabilityLibrary'));
const NotFound = React.lazy(() => import('@/components/NotFound'));

function HomeEntry({ root = false }: { root?: boolean }) {
  const { search } = useLocation();
  if (root) return <Navigate to={canonicalHomePath(search)} replace />;
  if (isStrategicMapHomeSearch(search)) return <Navigate to={canonicalStrategicMapPath(search)} replace />;
  return <Home />;
}

// 创建路由配置
const router = createBrowserRouter([
  {
    path: ROUTES.HOME,
    element: <Layout />,
    children: [
      {
        index: true,
        element: (
          <Suspense fallback={<Loading loading={true} className="h-full"/>}>
            <HomeEntry root />
          </Suspense>
        ),
      },
      {
        path: ROUTES.APP_HOME,
        element: <Suspense fallback={<Loading loading={true} className="h-full"/>}><HomeEntry /></Suspense>,
      },
      {
        path: ROUTES.FEATURED_CONVERSATIONS,
        element: (
          <Suspense fallback={<Loading loading={true} className="h-full"/>}>
            <FeaturedConversations />
          </Suspense>
        ),
      },
      {
        path: ROUTES.FEATURED_CONVERSATION_DETAIL,
        element: (
          <Suspense fallback={<Loading loading={true} className="h-full"/>}>
            <FeaturedConversationDetail />
          </Suspense>
        ),
      },
      {
        path: ROUTES.WORKSPACE,
        element: <Navigate to={ROUTES.WORKSPACE_MRAG} replace />,
      },
      {
        path: ROUTES.WORKSPACE_STRATEGIC_MAP,
        element: (
          <Suspense fallback={<Loading loading={true} className="h-full"/>}>
            <Home />
          </Suspense>
        ),
      },
      {
        path: ROUTES.WORKSPACE_IMPACT_TRIAGE,
        element: <Navigate to={`${ROUTES.WORKSPACE_STRATEGIC_MAP}?smMode=intelligence`} replace />,
      },
      {
        path: ROUTES.STRATEGIC_TEAM_DETAIL,
        element: (
          <Suspense fallback={<Loading loading={true} className="h-full"/>}>
            <StrategicTeamDetail />
          </Suspense>
        ),
      },
      {
        path: ROUTES.WORKSPACE_MRAG,
        element: (
          <Suspense fallback={<Loading loading={true} className="h-full"/>}>
            <WorkspaceMRag />
          </Suspense>
        ),
      },
      {
        path: ROUTES.WORKSPACE_IMAGE_GENERATION,
        element: (
          <Suspense fallback={<Loading loading={true} className="h-full"/>}>
            <WorkspaceImageGeneration />
          </Suspense>
        ),
      },
      {
        path: ROUTES.WORKSPACE_SOP,
        element: (
          <Suspense fallback={<Loading loading={true} className="h-full"/>}>
            <WorkspaceSop />
          </Suspense>
        ),
      },
      {
        path: ROUTES.WORKSPACE_SUB_AGENTS,
        element: (
          <Suspense fallback={<Loading loading={true} className="h-full"/>}>
            <SubAgentAdmin />
          </Suspense>
        ),
      },
      {
        path: ROUTES.WORKSPACE_MODELS,
        element: (
          <Suspense fallback={<Loading loading={true} className="h-full"/>}>
            <ModelAdmin />
          </Suspense>
        ),
      },
      {
        path: ROUTES.WORKSPACE_CAPABILITIES,
        element: (
          <Suspense fallback={<Loading loading={true} className="h-full"/>}>
            <CapabilityLibrary />
          </Suspense>
        ),
      },
      {
        path: ROUTES.NOT_FOUND,
        element: (
          <Suspense fallback={<Loading loading={true} className="h-full"/>}>
            <NotFound />
          </Suspense>
        ),
      },
    ],
  },
  // 重定向所有未匹配的路由到 404 页面
  {
    path: '*',
    element: <Navigate to={ROUTES.NOT_FOUND} replace />,
  },
]);

export default router;
