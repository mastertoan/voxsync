import type { Cue } from "../shared/types";

const TS = /(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})/;
const ARROW = /^\s*(\S+)\s+-->\s+(\S+)/;

function seconds(ts: string): number | null {
  const m = TS.exec(ts);
  if (!m) return null;
  const [, h, mi, s, ms] = m;
  return (Number(h ?? 0) * 3600) + Number(mi) * 60 + Number(s) + Number(ms!.padEnd(3, "0")) / 1000;
}

/** Parse WebVTT or SRT into cues (raw text, normalization is the server's job). Skips cues with empty text,
 * NOTE/STYLE/REGION blocks and cues with unparseable timing; collapses exact duplicates (same timing+text);
 * sorts by start. `idx` is the position in the returned list. */
export function parseSubtitles(src: string): Cue[] {
  const text = src.replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n");
  const out: Omit<Cue, "idx">[] = [];
  const seen = new Set<string>();
  for (const block of text.split(/\n{2,}/)) {
    const lines = block.split("\n");
    const at = lines.findIndex((l) => ARROW.test(l));
    if (at < 0) continue;
    const m = ARROW.exec(lines[at]!)!;
    const start = seconds(m[1]!);
    const end = seconds(m[2]!);
    if (start === null || end === null || end < start) continue;
    const body = lines.slice(at + 1).join("\n").trim();
    if (!body) continue;
    const key = `${start}|${end}|${body}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ start, end, text: body });
  }
  out.sort((a, b) => a.start - b.start || a.end - b.end);
  return out.map((c, idx) => ({ idx, ...c }));
}

/** True if the payload looks like a thumbnail-sprite VTT (cues are image URLs), not dialogue. */
export function isThumbnailVtt(cues: Cue[]): boolean {
  const sample = cues.slice(0, 10);
  return sample.length > 0 && sample.every((c) => /^https?:\/\/\S+(#xywh=|\.(jpe?g|png|webp))/i.test(c.text));
}
