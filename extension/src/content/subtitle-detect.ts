// F2: decide whether a captured subtitle URL / payload is the Vietnamese track and which episode it belongs to.
import type { Cue } from "../shared/types";

export const CHOPHIM_SUB_RE = /\/api\/storage\/subtitles\?file=/;

const VI_TOKEN = /(?:^|[_\-.])(vietnamese|vie|vi|viet|vn)(?:[_\-.]|\.vtt|\.srt|$)/i;
const OTHER_TOKEN = /(?:^|[_\-.])(english|eng|en|korean|kor|ko|japanese|jpn|ja|chinese|chi|zh|french|fre|fr|thai|tha|th|spanish|spa|es)(?:[_\-.]|\.vtt|\.srt|$)/i;

export function fileOf(url: string): string {
  try {
    const u = new URL(url);
    return decodeURIComponent(u.searchParams.get("file") ?? u.pathname.split("/").pop() ?? "");
  } catch {
    return url;
  }
}

/** "vi" | "other" | "unknown" from the file name alone. */
export function langFromName(url: string): "vi" | "other" | "unknown" {
  const f = fileOf(url).toLowerCase();
  if (VI_TOKEN.test(f)) return "vi";
  if (OTHER_TOKEN.test(f)) return "other";
  return "unknown";
}

// Letters that only occur in Vietnamese (with tone/vowel marks), NFC.
const VI_CHARS = /[ăâđêôơưàảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíịòỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵ]/i;

/** Fraction of the first n cues containing a Vietnamese-only letter. */
export function viScore(cues: Cue[], n = 20): number {
  const sample = cues.slice(0, n);
  if (!sample.length) return 0;
  return sample.filter((c) => VI_CHARS.test(c.text.normalize("NFC"))).length / sample.length;
}

export function isVietnamese(url: string, cues: Cue[]): boolean {
  const byName = langFromName(url);
  if (byName === "vi") return true;
  if (byName === "other") return false;
  return viScore(cues) >= 0.3;
}

/** "tap-3" (series) or "movie" (no episode token). Leading zeros dropped: tap-02 → tap-2. */
export function episodeOf(url: string): string {
  const m = /(?:^|[-_])tap[-_](\d+)(?:[-_]|$)/i.exec(fileOf(url));
  return m ? `tap-${Number(m[1])}` : "movie";
}

/** Title slug from a chophim watch URL: /xem/<slug>. */
export function titleOf(pageUrl: string): string | null {
  const m = /\/xem\/([^/?#]+)/.exec(pageUrl);
  return m ? m[1]! : null;
}

/** Stable server episode key from the subtitle URL (sha-1 hex, 40 chars, matches the server key pattern). */
export async function episodeKey(subtitleUrl: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-1", new TextEncoder().encode(subtitleUrl));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
