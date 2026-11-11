// Arm gate: never dub over the pre-roll ad, which plays on the SAME <video> element (Phase 0 probe), and never
// play one episode's lines over another. Armed only when the current media is long (a real episode) AND the
// Vietnamese subtitle we hold belongs to the media now loaded: either it was captured after the most recent
// `emptied`, or it was captured for the same (episode, server) the page now shows (the player re-uses its cached
// VTT without refetching when switching back to a server).

export const MIN_EPISODE_SECONDS = 300;

export interface ArmInput {
  duration: number;              // video.duration (NaN while unknown)
  lastEmptiedAt: number;         // performance.now() of the last `emptied`, 0 if none seen
  subtitleAt: number | null;     // performance.now() when the current Vietnamese subtitle was captured
  subtitleMatchesPage: boolean;  // subtitle's (episode, server) binding equals what the page shows now
  skipAdVisible: boolean;        // the player's "Bỏ qua" (skip ad) button is on screen
}

export function subtitleIsCurrent(s: Pick<ArmInput, "lastEmptiedAt" | "subtitleAt" | "subtitleMatchesPage">): boolean {
  return s.subtitleAt !== null && (s.subtitleAt >= s.lastEmptiedAt || s.subtitleMatchesPage);
}

export function shouldArm(s: ArmInput): boolean {
  return Number.isFinite(s.duration) && s.duration > MIN_EPISODE_SECONDS && subtitleIsCurrent(s) && !s.skipAdVisible;
}

/** (episode, server) the chophim page shows, from its state-mirroring hash: "#tap=tap-02&sv=...&channel=1".
 * Episode numbers are normalized (tap-02 → 2). Null when the hash carries no episode. */
export function pageMediaKey(hash: string): string | null {
  const p = new URLSearchParams(hash.replace(/^#/, ""));
  const tap = /(\d+)/.exec(p.get("tap") ?? "");
  if (!tap) return null;
  return `${Number(tap[1])}|${p.get("sv") ?? ""}|${p.get("channel") ?? ""}`;
}
