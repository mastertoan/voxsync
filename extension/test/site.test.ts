import { describe, expect, it } from "vitest";
import { isSiteHost, isWatchUrl } from "../src/shared/site";

describe("chophim site detection (domain changes)", () => {
  it("accepts any chophim domain, rejects other sites with a /xem/ path", () => {
    for (const h of ["chophim.lol", "chophim.xyz", "www.chophim.xyz", "chophim2.net", "chophim.com.vn"]) expect(isSiteHost(h)).toBe(true);
    for (const h of ["example.com", "notchophim.xyz", "chophim.xyz.evil.example", "chophim.evil.com", "analytics.chophim.app"]) expect(isSiteHost(h)).toBe(false);
    expect(isWatchUrl("https://chophim.xyz/xem/vision-truy-tim-ban-nga#tap=tap-1")).toBe(true);
    expect(isWatchUrl("https://chophim.xyz/phim/vision-truy-tim-ban-nga")).toBe(false);
    expect(isWatchUrl("http://chophim.xyz/xem/x")).toBe(false);
    expect(isWatchUrl("https://other.site/xem/x")).toBe(false);
  });
});
