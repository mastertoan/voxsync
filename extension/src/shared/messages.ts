// Fixed op set between content/popup/options and the service worker. The SW never fetches a caller-supplied
// URL except `subtitle.fetch` (https only, size-capped) for the manual-subtitle fallback.
import type { Cue, Voice, VoiceMode } from "./types";

export type Request =
  | { op: "health" }
  | { op: "voices.list" }
  | { op: "voices.preview"; id: string; text?: string }
  | { op: "voices.create"; name: string; clipB64: string; mime: string; consent: boolean }
  | { op: "voices.rename"; id: string; name: string }
  | { op: "voices.delete"; id: string }
  | { op: "episode.put"; key: string; voice: string | null; cues: Cue[]; mode: VoiceMode; title: string | null;
      media: string | null; duration: number | null; cloneConsent: boolean }
  | { op: "episode.playhead"; key: string; t: number }
  | { op: "episode.status"; key: string }
  | { op: "episode.characters"; key: string }
  | { op: "character.voice"; title: string; id: number; voice: string | null }
  | { op: "cue.get"; key: string; idx: number }
  | { op: "subtitle.fetch"; url: string };

export interface HealthOk { ok: true; voices: number; defaultVoice: string; rtf: number | null; cacheBytes: number }

/** Normalized SW reply. `status` mirrors the server HTTP status; 0 = network error (server down). */
export type Reply<T = unknown> =
  | { ok: true; status: number; data: T; headers?: Record<string, string> }
  | { ok: false; status: number; code: string; message?: string };

export type CueReply =
  | { kind: "audio"; b64: string; delay: number; stretch: number }
  | { kind: "pending"; retryMs: number }
  | { kind: "silent"; reason: string }
  | { kind: "failed" }
  | { kind: "unknown-episode" }
  | { kind: "unknown-cue" }
  | { kind: "offline" }
  | { kind: "unauthorized" };

export interface VoicesList { default: string; voices: Voice[] }

export function send<T = unknown>(req: Request): Promise<Reply<T>> {
  const { promise, resolve } = Promise.withResolvers<Reply<T>>();
  try {
    chrome.runtime.sendMessage(req, (res: Reply<T> | undefined) => {
      const err = chrome.runtime.lastError;
      resolve(res ?? { ok: false, status: 0, code: "no_reply", message: err?.message });
    });
  } catch (e) {
    // extension reloaded/updated: this content script is orphaned
    resolve({ ok: false, status: 0, code: "orphaned", message: String(e) });
  }
  return promise;
}
