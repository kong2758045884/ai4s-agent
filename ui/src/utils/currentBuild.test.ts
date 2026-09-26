import { describe, expect, it, vi, afterEach } from 'vitest';
import { moduleEntry, refreshOutdatedApp } from './currentBuild';

afterEach(() => vi.unstubAllGlobals());

function browser(entry = 'https://ai4s.example/assets/index-old.js') {
  const assign = vi.fn();
  vi.stubGlobal('document', { querySelector: () => ({ src: entry }) });
  vi.stubGlobal('location', { origin: 'https://ai4s.example' });
  vi.stubGlobal('window', { location: { assign } });
  return assign;
}

describe('production build refresh on strategic-map navigation', () => {
  it('reads the module entry and ignores ordinary script tags', () => {
    expect(moduleEntry('<script src="old.js"></script><script crossorigin src="/assets/new.js" type="module">')).toBe('/assets/new.js');
    expect(moduleEntry('<html>502</html>')).toBeNull();
  });
  it('loads the new document while retaining the requested view and selection', async () => {
    const assign = browser();
    const fetcher = vi.fn().mockResolvedValue({ok:true, text:async () => '<script type="module" src="/assets/index-new.js">'});
    vi.stubGlobal('fetch', fetcher);
    expect(await refreshOutdatedApp('/?view=strategic-map&smDomain=quantum')).toBe(true);
    const target = new URL(assign.mock.calls[0][0]);
    expect(target.searchParams.get('view')).toBe('strategic-map');
    expect(target.searchParams.get('smDomain')).toBe('quantum');
    expect(target.searchParams.get('appBuild')).toBe('/assets/index-new.js');
    expect(fetcher.mock.calls[0][1].cache).toBe('no-store');
  });
  it('keeps navigation available for matching versions and network failures', async () => {
    const assign = browser();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ok:true, text:async () => '<script type="module" src="/assets/index-old.js">'}));
    expect(await refreshOutdatedApp('/?view=strategic-map')).toBe(false);
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    expect(await refreshOutdatedApp('/?view=strategic-map')).toBe(false);
    expect(assign).not.toHaveBeenCalled();
  });
});
