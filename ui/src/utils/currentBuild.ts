/** Check the HTML entry, which must be revalidated independently of hashed assets. */
export function moduleEntry(html: string): string | null {
  for (const tag of html.match(/<script\b[^>]*>/gi) ?? []) {
    if (!/\btype\s*=\s*['"]module['"]/i.test(tag)) continue;
    const src = tag.match(/\bsrc\s*=\s*['"]([^'"]+)['"]/i)?.[1];
    if (src) return src;
  }
  return null;
}

export async function refreshOutdatedApp(destination: string): Promise<boolean> {
  const current = document.querySelector<HTMLScriptElement>('script[type="module"][src]')?.src;
  // Vite dev has no immutable production entry to compare.
  if (!current || !new URL(current).pathname.startsWith('/assets/')) return false;
  try {
    const response = await fetch('/index.html', {
      cache: 'no-store', signal: AbortSignal.timeout(2000),
    });
    if (!response.ok) return false;
    const entry = moduleEntry(await response.text());
    if (!entry || new URL(entry, location.origin).href === current) return false;
    const target = new URL(destination, location.origin);
    if (target.origin !== location.origin) return false;
    // A unique document URL also bypasses an older server's heuristic HTML cache.
    target.searchParams.set('appBuild', new URL(entry, location.origin).pathname);
    window.location.assign(target.href);
    return true;
  } catch {
    // A failed version probe should not prevent opening the current workspace.
    return false;
  }
}
