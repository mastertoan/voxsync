// Pure matcher for the subset of DNR features our static ruleset uses (regexFilter, urlFilter with ||host and
// trailing path, requestDomains, initiatorDomains, resourceTypes). Real URLs captured on chophim (Phase 0/2).
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

interface Rule {
  condition: { regexFilter?: string; urlFilter?: string; requestDomains?: string[]; initiatorDomains?: string[];
               resourceTypes: string[] };
}
const rules: Rule[] = JSON.parse(readFileSync(new URL("../rules/chophim-ads.json", import.meta.url), "utf8"));

const domainMatch = (host: string, list: string[]) => list.some((d) => host === d || host.endsWith(`.${d}`));

function blocked(url: string, type: string, initiator = "chophim.lol"): boolean {
  const u = new URL(url);
  return rules.some(({ condition: c }) => {
    if (!c.resourceTypes.includes(type)) return false;
    if (c.initiatorDomains && type !== "main_frame" && !domainMatch(initiator, c.initiatorDomains)) return false;
    if (c.requestDomains && !domainMatch(u.host, c.requestDomains)) return false;
    if (c.regexFilter && !new RegExp(c.regexFilter).test(url)) return false;
    if (c.urlFilter) {
      const m = /^\|\|([^/^]+)(.*)$/.exec(c.urlFilter)!;
      if (!domainMatch(u.host, [m[1]!]) || !u.pathname.startsWith(m[2]!)) return false;
    }
    return true;
  });
}

describe("chophim ad ruleset", () => {
  it.each([
    ["https://chophim.lol/public/m3u8/3c8eabe624134233a22569ee4fe896dc.m3u8", "xmlhttprequest"],   // pre-roll manifest
    ["https://chophim.lol/ads/webp/s8-new.webp", "image"],
    ["https://chophim.lol/ads/webp/pop-789bet.webp", "image"],
    ["https://hpasoj.cc/landing", "main_frame"],
    ["https://789b668.me/", "sub_frame"],
  ])("blocks %s (%s)", (url, type) => expect(blocked(url, type)).toBe(true));

  it.each([
    ["https://chophim.lol/api/storage/subtitles?file=vision-truy-tim-ban-nga-tap-1-1791955231891-cce2ae46-tap_01_vietnamese.vtt", "xmlhttprequest"],
    ["https://chophim.lol/api/storage/webvtt?file=589ad0ed4b9b45c7b5ce24c24c783e68.vtt", "xmlhttprequest"],
    ["https://chophim.lol/api/playback/grant/p2.stringdeptrai.token", "xmlhttprequest"],
    ["https://chophim.lol/api/playback/manifest/p3.stringdeptrai.token", "xmlhttprequest"],
    ["https://a.kvp726.com/20261016/NsQtuo2V/index.m3u8", "xmlhttprequest"],
    ["https://node01.chophim-cdn.fun/abc.png", "xmlhttprequest"],
    ["https://p16-lp-sg.ibyteimg.com/origin/tos-alisg-i-375lmtcpo0-sg/thumb.jpg", "image"],   // seek thumbnails
    ["https://chophim.lol/xem/vision-truy-tim-ban-nga", "main_frame"],
    ["https://wsrv.nl/?url=https%3A%2F%2Fimage.tmdb.org%2Fposter.jpg", "image"],
  ])("never blocks %s (%s)", (url, type) => expect(blocked(url, type)).toBe(false));
});
