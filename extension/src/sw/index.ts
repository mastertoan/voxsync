// Service worker: the only component that talks to the local server (host_permissions exempt it from page
// CORS and Local Network Access). Stateless apart from settings in chrome.storage; safe to be suspended.
import type { Reply, Request } from "../shared/messages";
import { DEFAULT_SETTINGS, type Settings } from "../shared/types";
import { reinjectOpenTabs } from "./reinject";

const MAX_SUBTITLE_BYTES = 2 * 1024 * 1024;
const TIMEOUT_MS = 15_000;
const VOICE_ID = /^[\p{L}\p{N} _.'-]{1,80}$/u;
const EP_KEY = /^[A-Za-z0-9_-]{8,64}$/;
const TITLE = /^[a-z0-9-]{1,120}$/;

async function settings(): Promise<Settings> {
  return (await chrome.storage.local.get(DEFAULT_SETTINGS)) as Settings;
}

function b64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

async function call(method: string, path: string, body?: BodyInit, json = true): Promise<Response | Reply> {
  const s = await settings();
  if (!s.token) return { ok: false, status: 401, code: "no_token" };
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
  try {
    const headers: Record<string, string> = { "X-VoxSync-Token": s.token };
    if (body !== undefined && json) headers["content-type"] = "application/json";
    return await fetch(s.endpoint.replace(/\/+$/, "") + path, { method, body, headers, signal: ctl.signal });
  } catch (e) {
    return { ok: false, status: 0, code: "offline", message: String(e) };
  } finally {
    clearTimeout(timer);
  }
}

async function jsonReply(r: Response | Reply): Promise<Reply> {
  if (!(r instanceof Response)) return r;
  const text = await r.text();
  let data: unknown = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* non-JSON */ }
  if (r.ok) return { ok: true, status: r.status, data };
  const code = (data as { code?: string } | null)?.code ?? `http_${r.status}`;
  return { ok: false, status: r.status, code };
}

async function audioReply(r: Response | Reply): Promise<Reply> {
  if (!(r instanceof Response)) return r;
  if (r.status === 200) {
    return { ok: true, status: 200, data: b64(await r.arrayBuffer()),
             headers: { delay: r.headers.get("X-Delay") ?? "0", stretch: r.headers.get("X-Stretch") ?? "1" } };
  }
  if (r.status === 202 || r.status === 204) {
    return { ok: true, status: r.status, data: null,
             headers: { retryMs: r.headers.get("X-Retry-Ms") ?? "250", reason: r.headers.get("X-Reason") ?? "" } };
  }
  return jsonReply(r);
}

const enc = encodeURIComponent;
const bad = (code: string): Reply => ({ ok: false, status: 400, code });

async function handle(req: Request): Promise<Reply> {
  switch (req.op) {
    case "health":
      return jsonReply(await call("GET", "/health"));
    case "voices.list":
      return jsonReply(await call("GET", "/voices"));
    case "voices.preview":
      if (!VOICE_ID.test(req.id)) return bad("bad_voice");
      return audioReply(await call("POST", `/voices/${enc(req.id)}/preview`,
                                   JSON.stringify({ text: req.text ?? null })));
    case "voices.create": {
      const fd = new FormData();
      fd.set("name", req.name);
      fd.set("consent", String(req.consent));
      const clip = Uint8Array.from(atob(req.clipB64), (c) => c.charCodeAt(0));
      fd.set("clip", new Blob([clip], { type: req.mime || "audio/wav" }), "clip");
      return jsonReply(await call("POST", "/voices", fd, false));
    }
    case "voices.rename":
      if (!VOICE_ID.test(req.id)) return bad("bad_voice");
      return jsonReply(await call("PATCH", `/voices/${enc(req.id)}`, JSON.stringify({ name: req.name })));
    case "voices.delete":
      if (!VOICE_ID.test(req.id)) return bad("bad_voice");
      return jsonReply(await call("DELETE", `/voices/${enc(req.id)}`));
    case "episode.put":
      if (!EP_KEY.test(req.key)) return bad("bad_key");
      return jsonReply(await call("PUT", `/episodes/${req.key}`, JSON.stringify({
        voice: req.voice, cues: req.cues, mode: req.mode, title: req.title, media: req.media, duration: req.duration,
        cloneConsent: req.cloneConsent })));
    case "episode.characters":
      if (!EP_KEY.test(req.key)) return bad("bad_key");
      return jsonReply(await call("GET", `/episodes/${req.key}/characters`));
    case "character.voice":
      if (!TITLE.test(req.title) || !Number.isInteger(req.id) || req.id < 0) return bad("bad_character");
      if (req.voice !== null && !VOICE_ID.test(req.voice)) return bad("bad_voice");
      return jsonReply(await call("PUT", `/titles/${req.title}/characters/${req.id}`, JSON.stringify({ voice: req.voice })));
    case "episode.playhead":
      if (!EP_KEY.test(req.key)) return bad("bad_key");
      return jsonReply(await call("POST", `/episodes/${req.key}/playhead`, JSON.stringify({ t: req.t })));
    case "episode.status":
      if (!EP_KEY.test(req.key)) return bad("bad_key");
      return jsonReply(await call("GET", `/episodes/${req.key}/status`));
    case "cue.get":
      if (!EP_KEY.test(req.key) || !Number.isInteger(req.idx) || req.idx < 0) return bad("bad_cue");
      return audioReply(await call("GET", `/episodes/${req.key}/cues/${req.idx}`));
    case "subtitle.fetch":
      return fetchSubtitle(req.url);
  }
}

/** Manual-subtitle fallback: https only, size-capped; returns text. */
async function fetchSubtitle(url: string): Promise<Reply> {
  let u: URL;
  try { u = new URL(url); } catch { return bad("bad_url"); }
  if (u.protocol !== "https:") return bad("https_only");
  try {
    const ctl = new AbortController();
    const r = await fetch(u, { credentials: "omit", signal: ctl.signal });
    if (!r.ok || !r.body) return { ok: false, status: r.status, code: `http_${r.status}` };
    if (Number(r.headers.get("content-length") ?? 0) > MAX_SUBTITLE_BYTES) {
      ctl.abort();
      return { ok: false, status: 413, code: "too_large" };
    }
    const chunks: Uint8Array[] = [];
    let total = 0;
    for await (const chunk of r.body as unknown as AsyncIterable<Uint8Array>) {
      total += chunk.byteLength;
      if (total > MAX_SUBTITLE_BYTES) { ctl.abort(); return { ok: false, status: 413, code: "too_large" }; }
      chunks.push(chunk);
    }
    const dec = new TextDecoder();
    return { ok: true, status: 200, data: chunks.map((c, i) => dec.decode(c, { stream: i < chunks.length - 1 })).join("") };
  } catch (e) {
    return { ok: false, status: 0, code: "fetch_failed", message: String(e) };
  }
}

chrome.runtime.onMessage.addListener((req: Request, sender, reply) => {
  if (sender.id !== chrome.runtime.id) return false;
  handle(req).then(reply, (e) => reply({ ok: false, status: 0, code: "sw_error", message: String(e) }));
  return true;
});

// Ad blocking (F3): static ruleset toggled from settings; off by default.
async function syncRulesets(): Promise<void> {
  const { blockAds } = await settings();
  await chrome.declarativeNetRequest.updateEnabledRulesets(
    blockAds ? { enableRulesetIds: ["chophim_ads"] } : { disableRulesetIds: ["chophim_ads"] });
}
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && "blockAds" in changes) void syncRulesets();
});
chrome.runtime.onInstalled.addListener(() => { void syncRulesets(); void reinjectOpenTabs(); });
chrome.runtime.onStartup.addListener(() => void syncRulesets());
// Re-enable fires neither onInstalled nor onStartup, but it does start a fresh SW. Content scripts dedupe via a
// DOM marker, so injecting on every SW start is harmless (no-op when an instance is alive).
void reinjectOpenTabs();
