import { describe, expect, it } from "vitest";
import { pageMediaKey, shouldArm } from "../src/content/arm";
import { driftOf, inWindow, plan, targetMedia } from "../src/content/timing";

describe("plan", () => {
  const clock = { ctxT: 100, mediaT: 50, rate: 1 };

  it("schedules ahead, compensating output latency and bias", () => {
    // target 52 at rate 1 → 2 s ahead; minus 16 ms output latency and 14 ms bias
    expect(plan(clock, 52, 3, 0.016, 14)).toEqual({ when: 100 + 2 - 0.03, offset: 0 });
  });

  it("scales the lead by playback rate", () => {
    const p = plan({ ...clock, rate: 1.5 }, 53, 3, 0, 0)!;
    expect(p.when).toBeCloseTo(102, 9);          // 3 media seconds at 1.5x = 2 wall seconds
    expect(plan({ ...clock, rate: 0.5 }, 51, 3, 0, 0)!.when).toBeCloseTo(102, 9);
  });

  it("late join starts at the matching offset into the line", () => {
    // picture is already 1 s into the line → start almost now, 1 s + pad in
    const p = plan(clock, 49, 3, 0, 0)!;
    expect(p.when).toBeCloseTo(100.03, 9);
    expect(p.offset).toBeCloseTo(1.03, 9);
  });

  it("late join accounts for output latency (heard later than started)", () => {
    const p = plan(clock, 49, 3, 0.2, 0)!;
    expect(p.offset).toBeCloseTo(1.23, 9);
  });

  it("late join at 1.5x jumps 1.5 media-seconds into the line per wall second late", () => {
    // target 1 media-s in the past → 0.667 wall-s late; +pad 0.03 wall-s → offset (0.667+0.03)*1.5
    const p = plan({ ...clock, rate: 1.5 }, 49, 3, 0, 0)!;
    expect(p.offset).toBeCloseTo((1 / 1.5 + 0.03) * 1.5, 9);
  });

  it("skips a line that is essentially over (< minRemaining left)", () => {
    expect(plan(clock, 47.16, 3, 0, 0)).toBeNull();   // 2.87 s into a 3 s buffer: 0.13 s left
    expect(plan(clock, 47.2, 3, 0, 0)).not.toBeNull(); // 2.83 s in: 0.17 s left, still worth playing
  });
});

describe("driftOf", () => {
  it("is ~0 for a source started by plan() as the clocks advance together", () => {
    const comp = 0.016;
    const p = plan({ ctxT: 100, mediaT: 50, rate: 1 }, 52, 3, comp, 0)!;
    const later = { ctxT: p.when + 0.7, mediaT: 50 + (p.when + 0.7 - 100), rate: 1 };
    expect(driftOf(later, p.when, p.offset, 52, comp, 0)).toBeCloseTo(0, 9);
  });
  it("is positive when the video stalls while audio keeps playing", () => {
    const p = plan({ ctxT: 100, mediaT: 50, rate: 1 }, 50.5, 3, 0, 0)!;
    expect(driftOf({ ctxT: p.when + 1, mediaT: 50.5, rate: 1 }, p.when, p.offset, 50.5, 0, 0)).toBeCloseTo(1, 6);
  });
});

it("targetMedia adds server delay and user offset", () => {
  expect(targetMedia(10, 0.12, 50)).toBeCloseTo(10.17, 9);
  expect(targetMedia(10, 0, -100)).toBeCloseTo(9.9, 9);
});

it("inWindow", () => {
  expect(inWindow(53, 50, 3, 5)).toBe(true);
  expect(inWindow(53.1, 50, 3, 5)).toBe(false);
  expect(inWindow(44.9, 50, 3, 5)).toBe(false);
});

describe("shouldArm (pre-roll / wrong-media guard)", () => {
  const base = { duration: 3201, lastEmptiedAt: 1000, subtitleAt: 1500, subtitleMatchesPage: false, skipAdVisible: false };
  it("arms for an episode whose subtitle arrived after the last source swap", () => expect(shouldArm(base)).toBe(true));
  it("never arms during the 62 s pre-roll", () => expect(shouldArm({ ...base, duration: 62.3 })).toBe(false));
  it("not while duration is unknown", () => expect(shouldArm({ ...base, duration: NaN })).toBe(false));
  it("not with a stale subtitle from the previous episode", () =>
    expect(shouldArm({ ...base, subtitleAt: 900 })).toBe(false));
  it("re-arms when switching back to the server the subtitle came from (player reuses its cached VTT)", () =>
    expect(shouldArm({ ...base, subtitleAt: 900, subtitleMatchesPage: true })).toBe(true));
  it("not while the skip-ad button is visible", () => expect(shouldArm({ ...base, skipAdVisible: true })).toBe(false));
  it("not without a subtitle", () => expect(shouldArm({ ...base, subtitleAt: null, subtitleMatchesPage: true })).toBe(false));
});

describe("pageMediaKey", () => {
  it("normalizes tap-02 and distinguishes servers", () => {
    const a = pageMediaKey("#tap=tap-1&sv=Song+Ng%E1%BB%AF&channel=0&server_id=0");
    expect(pageMediaKey("#tap=tap-01&sv=Song+Ng%E1%BB%AF&channel=0&server_id=0")).toBe(a);
    expect(pageMediaKey("#tap=tap-01&sv=Vietsub+%28KK%29&channel=1&server_id=0")).not.toBe(a);
    expect(pageMediaKey("#tap=tap-2&sv=Song+Ng%E1%BB%AF&channel=0")).not.toBe(a);
    expect(pageMediaKey("")).toBeNull();
  });
});
