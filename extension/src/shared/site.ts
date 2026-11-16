// chophim changes its domain (chophim.lol → chophim.xyz, 2026-11). Chrome match patterns cannot wildcard a TLD,
// so the extension matches every https "/xem/*" page and this check keeps it to chophim mirrors.
export const WATCH_MATCH = "https://*/xem/*";

const HOST = /^(?:www\.)?chophim[a-z0-9-]*\.[a-z]{2,}(?:\.[a-z]{2})?$/i;   // .xyz, .lol, .com.vn

export function isSiteHost(host: string): boolean {
  return HOST.test(host);
}

export function isWatchUrl(url: string | undefined): boolean {
  if (!url) return false;
  try {
    const u = new URL(url);
    return u.protocol === "https:" && isSiteHost(u.hostname) && u.pathname.startsWith("/xem/");
  } catch {
    return false;
  }
}

/** Open chophim watch tabs, newest last. */
export async function watchTabs(): Promise<chrome.tabs.Tab[]> {
  return (await chrome.tabs.query({ url: WATCH_MATCH })).filter((t) => isWatchUrl(t.url));
}
