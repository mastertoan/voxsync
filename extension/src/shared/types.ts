export interface Cue {
  idx: number;
  start: number;
  end: number;
  text: string;
}

export interface Voice {
  id: string;
  name: string;
  gender: string;
  region: string;
  style: string;
  kind: "preset" | "user";
}

export interface Settings {
  enabled: boolean;
  endpoint: string;            // http://127.0.0.1:8765
  token: string;
  defaultVoice: string | null; // null → server default
  voiceByTitle: Record<string, string>;
  dubVolume: number;           // 0..1.5 GainNode
  duck: number;                // 0..1 original volume multiplier while dub speaks
  offsetMs: number;            // user sync offset, + = dub later
  biasMs: number;              // calibrated systematic offset (measured), subtracted from `when`
  blockAds: boolean;
  debug: boolean;
  voiceMode: VoiceMode;        // single: one voice; characters: one preset per character; clone: actor's own voice
  cloneConsent: boolean;       // user confirmed clone mode is for personal use only
}

export type VoiceMode = "single" | "characters" | "clone";

export interface Character {
  id: number;
  lines: number;
  gender: "male" | "female" | "";
  main: boolean;
  voice: string;
  override: string | null;
  sample: string;
}

export const DEFAULT_SETTINGS: Settings = {
  enabled: true,
  endpoint: "http://127.0.0.1:8765",
  token: "",
  defaultVoice: null,
  voiceByTitle: {},
  dubVolume: 1,
  duck: 0.3,
  offsetMs: 0,
  biasMs: 0,
  blockAds: false,
  debug: false,
  voiceMode: "characters",
  cloneConsent: false,
};

/** Content-script status shown in the popup. */
export interface TabStatus {
  phase: "idle" | "waiting-ad" | "no-subtitle" | "armed" | "suspended-audio" | "server-offline" | "unauthorized";
  title: string | null;        // chophim slug
  episode: string | null;      // "tap-1" | "movie"
  cues: number;
  ready: number;
  readyAhead: number;
  voice: string | null;
  lastError: string | null;
  mode: VoiceMode | null;      // what the server is actually doing (falls back to single without a stream)
  heard: number | null;        // cues whose speaker is known
}
