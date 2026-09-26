import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import tailwindcss from '@tailwindcss/vite';
import { createToolProxyConfig } from './toolProxy';

export default defineConfig(({ command, mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // Production is served by Nginx, which proxies the API and Tool paths on
  // the same origin. Never bake the developer's loopback addresses into a
  // public bundle; keep them only for the Vite development server.
  const serviceBaseUrl = command === 'build' ? '' : (env.SERVICE_BASE_URL || 'http://127.0.0.1:8100');
  const ai4sToolBaseUrl = command === 'build' ? '/tool' : (env.AI4S_TOOL_BASE_URL || '');
  const serviceProxy = {
    target: serviceBaseUrl || 'http://127.0.0.1:8100',
    changeOrigin: true,
    configure(proxy: { on: (event: string, listener: (proxyReq: { removeHeader?: (name: string) => void }) => void) => void }) {
      proxy.on('proxyReq', (proxyReq) => {
        // The browser talks to Vite same-origin; do not forward a loopback
        // Origin that the Java CORS allow-list does not need for proxy calls.
        proxyReq.removeHeader?.('origin');
      });
    },
  };
  return {
    plugins: [
      react(),
      tailwindcss()
    ],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, 'src'),
        crypto: 'crypto-browserify',
        'use-sync-external-store/shim': path.resolve(
          __dirname,
          'src/shims/use-sync-external-store/shim.ts'
        ),
        'use-sync-external-store/shim/with-selector': path.resolve(
          __dirname,
          'src/shims/use-sync-external-store/with-selector.ts'
        ),
      },
    },
    css: {preprocessorOptions: {less: {javascriptEnabled: true},},},
    optimizeDeps: {
      exclude: [
        'clsx',
        'nanoid',
        'radix-ui',
        'lucide-react',
        'tailwind-merge',
      ],
    },
    server: {
      // 修改为监听所有接口，而不是特定主机名
      host: '0.0.0.0',
      port: 3000,
      allowedHosts: true,
      proxy: {
        '/web': serviceProxy,
        '/api': serviceProxy,
        '/data': serviceProxy,
        '/tool': createToolProxyConfig(env.AI4S_TOOL_BASE_URL),
      },
    },
    define: {
      // 一定要序列化，否则打包时会报错
      SERVICE_BASE_URL: JSON.stringify(serviceBaseUrl),
      AI4S_TOOL_BASE_URL: JSON.stringify(ai4sToolBaseUrl),
    },
    build: {
      outDir: 'dist',
      sourcemap: false,
      // The full workspace bundle exceeds Terser's worker heap on Windows.
      minify: 'esbuild' as const,
    },
  }
});
