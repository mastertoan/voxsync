// Pure scheduling math (unit-tested). Media clock = video.currentTime; audio clock = AudioContext.currentTime.
// A dub source plays at 1x wall speed regardless of video.playbackRate, so only its START is aligned to media
// time; at rate != 1 the line simply ends earlier/later relative to the picture (Phase 4 could time-stretch).

export interface ClockPair { ctxT: number; mediaT: number; rate: number }

/** Media time at which a cue's dub should start: cue start + server-provided delay + user offset. */
export function targetMedia(cueStart: number, serverDelay: number, offsetMs: number): number {
  return cueStart + serverDelay + offsetMs / 1000;
}

/**
 * Context time to pass to `src.start(when, offset)`.
 * - leadWall = (target - mediaT) / rate: wall seconds until the picture reaches target.
 * - minus outputLatency + bias: the dub must enter the output pipeline that much earlier so it is *heard*
 *   when the frame is shown (video's own output latency is already compensated by the media pipeline).
 * Late join (target already passed): start ~immediately at the matching offset into the buffer, so the
 * remaining part of the line lines up; skip if too little is left.
 */
export function plan(
  clock: ClockPair,
  target: number,
  bufferDuration: number,
  outputLatency: number,
  biasMs: number,
  minRemaining = 0.15,
  joinPad = 0.03,
): { when: number; offset: number } | null {
  const comp = outputLatency + biasMs / 1000;
  const leadWall = (target - clock.mediaT) / clock.rate - comp;
  if (leadWall >= joinPad) return { when: clock.ctxT + leadWall, offset: 0 };
  // late: we can only start joinPad wall-seconds from now (heard comp later). The picture will then be
  // (joinPad - leadWall) wall-seconds = that × rate media-seconds past target; the line is spoken at 1x,
  // so start that many seconds into the buffer. At rate 1 this is simply the wall delay.
  const late = (joinPad - leadWall) * clock.rate;
  if (late >= bufferDuration - minRemaining) return null;
  return { when: clock.ctxT + joinPad, offset: late };
}

/** Signed drift (s) of a playing source vs the media clock at rate 1: + = dub ahead of picture. */
export function driftOf(clock: ClockPair, when: number, offset: number, target: number,
                        outputLatency: number, biasMs: number): number {
  const comp = outputLatency + biasMs / 1000;
  const audioMedia = target + (clock.ctxT - when + offset);        // media time the dub is at, as scheduled
  return audioMedia - (clock.mediaT + comp);                       // vs media time adjusted for output path
}

/** Cues whose dub should be scheduled now: target within [mediaT - maxBack, mediaT + lookahead]. */
export function inWindow(target: number, mediaT: number, lookahead: number, maxBack: number): boolean {
  return target <= mediaT + lookahead && target >= mediaT - maxBack;
}
