import { describe, expect, it } from "vitest";
import { isThumbnailVtt, parseSubtitles } from "../src/content/vtt";

describe("parseSubtitles", () => {
  it("parses chophim WebVTT with CRLF, multi-line cues, empties and duplicates", () => {
    const src = "\uFEFFWEBVTT\r\n\r\nNOTE ignored\r\n\r\n00:00:17.833 --> 00:00:18.917\r\nTôi là ai?\r\n\r\n" +
      "00:02:50.458 --> 00:02:52.416 align:start\r\n- Được rồi.\r\n- Nào. Ra ngoài.\r\n\r\n" +
      "00:03:00.000 --> 00:03:01.000\r\n\r\n\r\n" +                                     // empty cue
      "00:00:17.833 --> 00:00:18.917\r\nTôi là ai?\r\n";                                 // exact duplicate
    const cues = parseSubtitles(src);
    expect(cues).toEqual([
      { idx: 0, start: 17.833, end: 18.917, text: "Tôi là ai?" },
      { idx: 1, start: 170.458, end: 172.416, text: "- Được rồi.\n- Nào. Ra ngoài." },
    ]);
  });

  it("parses SRT (comma millis, numeric ids) and mm:ss timestamps; sorts by start", () => {
    const srt = "2\n00:00:05,000 --> 00:00:06,500\nHai\n\n1\n00:00:01,2 --> 00:00:02,000\nMột\n";
    expect(parseSubtitles(srt).map((c) => [c.idx, c.start, c.text])).toEqual([[0, 1.2, "Một"], [1, 5, "Hai"]]);
    expect(parseSubtitles("WEBVTT\n\n01:02.500 --> 01:04.000\nx")[0]!.start).toBe(62.5);
  });

  it("rejects cues whose end precedes start or timing is garbage", () => {
    expect(parseSubtitles("WEBVTT\n\n00:00:05.000 --> 00:00:01.000\nx\n\nfoo --> bar\ny")).toEqual([]);
  });
});

describe("isThumbnailVtt", () => {
  it("flags sprite VTTs, not dialogue", () => {
    const sprite = parseSubtitles("WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nhttps://p16.ibyteimg.com/a.jpg#xywh=0,0,160,90");
    expect(isThumbnailVtt(sprite)).toBe(true);
    expect(isThumbnailVtt(parseSubtitles("WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nXin chào"))).toBe(false);
  });
});
