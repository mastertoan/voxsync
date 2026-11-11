import { describe, expect, it } from "vitest";
import { episodeKey, episodeOf, isVietnamese, langFromName, titleOf } from "../src/content/subtitle-detect";
import { parseSubtitles } from "../src/content/vtt";

// Real file names captured on chophim.lol during Phase 0.
const base = "https://chophim.lol/api/storage/subtitles?file=";
const urls = {
  seriesVi: base + "vision-truy-tim-ban-nga-tap-1-1791955231891-cce2ae46-tap_01_vietnamese.vtt",
  seriesEn: base + "vision-truy-tim-ban-nga-tap-1-1791955230678-4a7aaff5-tap_01_english.vtt",
  seriesKo: base + "100-ngay-doi-tra-tap-1-1791642872834-254b87e2-tap_01_korean.vtt",
  seriesZh: base + "bac-si-x-mafia-ao-trang-tap-1-1791556898526-d162c75b-tap_01_chinese_simplified.vtt",
  movieVi: base + "cuoc-phieu-luu-1-1794051447164-b859bd59-the_odyssey_vie.vtt",
  movieEn: base + "cuoc-phieu-luu-1-1794051446239-7b199913-the_odyssey_eng.vtt",
  ep2: base + "vision-truy-tim-ban-nga-tap-2-1791955232932-b480e5af-tap_02_vietnamese.vtt",
};

describe("language from file name", () => {
  it.each([
    ["seriesVi", "vi"], ["movieVi", "vi"], ["seriesEn", "other"], ["movieEn", "other"],
    ["seriesKo", "other"], ["seriesZh", "other"],
  ] as const)("%s → %s", (k, want) => expect(langFromName(urls[k])).toBe(want));

  it("does not read 'vi' out of words like 'vision' or 'movie'", () => {
    expect(langFromName(base + "vision-truy-tim-tap-1-abc-tap_01.vtt")).toBe("unknown");
  });
});

describe("content fallback when the name is ambiguous", () => {
  const vi = parseSubtitles("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nTôi là ai?\n\n00:00:03.000 --> 00:00:04.000\nHòa bình");
  const en = parseSubtitles("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nWho am I?\n\n00:00:03.000 --> 00:00:04.000\nPeace");
  const ambiguous = base + "some-title-1-123-abcd-sub.vtt";
  it("accepts Vietnamese text, rejects English text", () => {
    expect(isVietnamese(ambiguous, vi)).toBe(true);
    expect(isVietnamese(ambiguous, en)).toBe(false);
  });
  it("a name token wins over content", () => expect(isVietnamese(urls.seriesEn, vi)).toBe(false));
});

describe("episode / title / key", () => {
  it("episode from tap-N, leading zero dropped; movies are 'movie'", () => {
    expect(episodeOf(urls.seriesVi)).toBe("tap-1");
    expect(episodeOf(urls.ep2)).toBe("tap-2");
    expect(episodeOf(base + "x-tap-02-1-a-tap_02_vietnamese.vtt")).toBe("tap-2");
    expect(episodeOf(urls.movieVi)).toBe("movie");
  });
  it("title slug from watch URL", () => {
    expect(titleOf("https://chophim.lol/xem/vision-truy-tim-ban-nga#tap=tap-1&sv=Song")).toBe("vision-truy-tim-ban-nga");
    expect(titleOf("https://chophim.lol/")).toBeNull();
  });
  it("key is a stable 40-hex digest that differs per subtitle", async () => {
    const a = await episodeKey(urls.seriesVi);
    expect(a).toMatch(/^[0-9a-f]{40}$/);
    expect(await episodeKey(urls.seriesVi)).toBe(a);
    expect(await episodeKey(urls.ep2)).not.toBe(a);
  });
});

describe("stream links in page data", () => {
  it("maps episode numbers to direct https m3u8 links, skipping opaque player tokens", async () => {
    const { streamLinks } = await import("../src/content/subtitle-detect");
    const page = String.raw`{\"server_name\":\"Song Ngữ\",\"server_data\":[{\"name\":\"01\",\"slug\":\"tap-1\",\"link_m3u8\":\"/api/playback/manifest/p2.x\"}]},` +
      String.raw`{\"server_name\":\"Vietsub (KK)\",\"server_data\":[{\"name\":\"01\",\"slug\":\"tap-01\",\"link_embed\":\"https:\/\/player.phimapi.com\/player\/?url=https:\/\/a.kvp726.com\/x\/index.m3u8\",\"link_m3u8\":\"https:\/\/a.kvp726.com\/x\/index.m3u8\"},` +
      String.raw`{\"name\":\"02\",\"slug\":\"tap-02\",\"link_m3u8\":\"https:\/\/a.kvp726.com\/y\/index.m3u8\"}]}` +
      String.raw`{"slug":"full","link_m3u8":"https://a.kvp726.com/m/index.m3u8"}`;
    expect([...streamLinks(page)]).toEqual([
      [1, "https://a.kvp726.com/x/index.m3u8"], [2, "https://a.kvp726.com/y/index.m3u8"], [0, "https://a.kvp726.com/m/index.m3u8"]]);
  });
});
