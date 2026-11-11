// Content script on chophim.lol/xem/*: capture the Vietnamese subtitle, keep the server's episode registry in
// sync, and play pre-synthesized lines on an AudioContext scheduled against video.currentTime.
import { send, type Reply } from "../shared/messages";
import { DEFAULT_SETTINGS, type Cue, type Settings, type TabStatus } from "../shared/types";
import { pageMediaKey, shouldArm, subtitleIsCurrent } from "./arm";
import { CHOPHIM_SUB_RE, episodeKey, episodeOf, isVietnamese, titleOf } from "./subtitle-detect";
import { setAdBlocking } from "./cosmetic";
import { driftOf, inWindow, plan, targetMedia } from "./timing";
import { isThumbnailVtt, parseSubtitles } from "./vtt";


// One live instance per page. Instances live in separate isolated worlds (and an orphaned one cannot be
// reached), so coordinate through a DOM heartbeat: a fresh instance only starts if the beat is stale.
const BEAT_ATTR = "data-voxsync-beat";
const beat = Number(document.documentElement.getAttribute(BEAT_ATTR) ?? 0);
if (Date.now() - beat > 1500) start();

function start(): void {
  const TICK_MS = 250;
  const LOOKAHEAD = 3;          // seconds of media ahead to schedule on the audio clock
  const PREFETCH = 25;          // seconds of media ahead to fetch + decode
  const KEEP_BACK = 30;         // decoded buffers kept behind the playhead
  const DRIFT_MAX = 0.08;
  const NO_SUB_AFTER_MS = 10_000;
  const OFFLINE_RETRY_MS = 2_000;

  interface Episode {
    key: string;
    url: string;
    episode: string;
    cues: Cue[];
    byIdx: Map<number, Cue>;
    capturedAt: number;
    mediaKey: string | null;   // page (episode, server) when captured; lets a server switch-back re-arm
    put: boolean;              // server knows this episode (re-put on reconnect / 404)
  }

  interface Loaded { buf: AudioBuffer; delay: number }
  interface Playing { src: AudioBufferSourceNode; when: number; offset: number; target: number; cue: Cue }

  const S = {
    settings: { ...DEFAULT_SETTINGS } as Settings,
    video: null as HTMLVideoElement | null,
    ctx: null as AudioContext | null,
    gain: null as GainNode | null,
    ep: null as Episode | null,
    lastEmptiedAt: 0,
    metaAt: 0,
    armed: false,
    loaded: new Map<number, Loaded>(),
    inflight: new Set<number>(),
    retryAt: new Map<number, number>(),
    silent: new Set<number>(),          // 204/410: nothing to play
    playing: new Map<number, Playing>(),
    done: new Set<number>(),            // played to the end in this pass; cleared on resync
    offlineUntil: 0,
    status: { phase: "idle", title: null, episode: null, cues: 0, ready: 0, readyAhead: 0, voice: null,
              lastError: null } as TabStatus,
    // duck state
    userVol: null as number | null,
    lastSetVol: -1,
    duckLevel: 1,
    lastAudible: 0,
    rampTimer: 0,
    // debug metrics
    av: [] as [number, number, number][],     // [error s (+ = dub late), rate, lateJoin]
    drift: [] as number[],
    orphaned: false,
  };

  const log = (...a: unknown[]) => { if (S.settings.debug) console.debug("[voxsync]", ...a); };

  chrome.storage.local.get(DEFAULT_SETTINGS).then((s) => {
    S.settings = s as Settings;
    applyGain();
    setAdBlocking(S.settings.blockAds);
  });
  chrome.storage.onChanged.addListener((ch, area) => {
    if (area !== "local") return;
    const prevVoice = voiceForTitle();
    for (const [k, v] of Object.entries(ch)) (S.settings as unknown as Record<string, unknown>)[k] = v.newValue;
    applyGain();
    if ("blockAds" in ch) setAdBlocking(S.settings.blockAds);
    if (!S.settings.enabled) { stopAll("disabled"); setDuck(false); }
    if ("offsetMs" in ch || "biasMs" in ch) resync("settings");
    if (voiceForTitle() !== prevVoice && S.ep) { S.ep.put = false; dropAudio(); void ensurePut(); }
  });

  function voiceForTitle(): string | null {
    const t = titleOf(location.href);
    return (t && S.settings.voiceByTitle[t]) || S.settings.defaultVoice || null;
  }

  // ---------------- subtitle capture (F2) ----------------
  new PerformanceObserver((list) => {
    for (const e of list.getEntries()) if (CHOPHIM_SUB_RE.test(e.name)) void onSubtitleUrl(e.name);
  }).observe({ type: "resource", buffered: true });

  const tried = new Set<string>();
  async function onSubtitleUrl(url: string, text?: string): Promise<void> {
    if (!text && (tried.has(url) || S.ep?.url === url)) return;
    tried.add(url);
    const capturedAt = performance.now();
    let body = text;
    if (body === undefined) {
      try {
        const r = await fetch(url, { credentials: "include" });     // same-origin
        if (!r.ok) return;
        body = await r.text();
      } catch { return; }
    }
    const cues = parseSubtitles(body);
    if (!cues.length || isThumbnailVtt(cues) || (text === undefined && !isVietnamese(url, cues))) return;
    const ep: Episode = { key: await episodeKey(url), url, episode: episodeOf(url), cues,
                          byIdx: new Map(cues.map((c) => [c.idx, c])), capturedAt,
                          mediaKey: text === undefined ? pageMediaKey(location.hash) : null, put: false };
    log("subtitle", ep.episode, cues.length, url);
    S.ep = ep;
    dropAudio();
    S.status.episode = ep.episode;
    S.status.cues = cues.length;
    await ensurePut();
    rearm();
  }

  /** Manual fallback from the popup: subtitle text or an https URL. */
  async function manualSubtitle(req: { url?: string; text?: string; name?: string }): Promise<string | null> {
    let text = req.text;
    const label = req.url ?? `manual:${req.name ?? "file"}:${Date.now()}`;
    if (!text && req.url) {
      const r = await send<string>({ op: "subtitle.fetch", url: req.url });
      if (!r.ok) return r.code;
      text = r.data;
    }
    if (!text || !parseSubtitles(text).length) return "no_cues";
    S.lastEmptiedAt = Math.min(S.lastEmptiedAt, performance.now() - 1);
    await onSubtitleUrl(label, text);
    return null;
  }

  // ---------------- server sync ----------------
  async function ensurePut(): Promise<boolean> {
    const ep = S.ep;
    if (!ep || ep.put || !S.settings.enabled) return !!ep?.put;
    if (performance.now() < S.offlineUntil) return false;
    const r = await send<{ cues: number; voice: string }>({ op: "episode.put", key: ep.key, voice: voiceForTitle(),
      cues: ep.cues.map(({ idx, start, end, text }) => ({ idx, start, end, text })) });
    if (!handleTransport(r)) return false;
    if (!r.ok) { S.status.lastError = r.code; return false; }
    if (S.ep !== ep) return false;
    ep.put = true;
    S.status.voice = r.data.voice;
    S.status.lastError = null;
    if (S.status.phase === "server-offline" || S.status.phase === "unauthorized") S.status.phase = "idle";
    S.offlineUntil = 0;
    S.silent.clear();
    S.retryAt.clear();
    if (S.video) await sendPlayhead();
    resync("reconnected");
    return true;
  }

  async function sendPlayhead(): Promise<void> {
    const ep = S.ep;
    if (!ep?.put || !S.video) return;
    const r = await send({ op: "episode.playhead", key: ep.key, t: S.video.currentTime });
    if (r.ok === false && r.code === "unknown_episode") { ep.put = false; void ensurePut(); }
    else handleTransport(r);
  }

  /** Track server reachability. Returns false when the call didn't reach the server. */
  function handleTransport(r: Reply): boolean {
    if (r.ok === false && r.code === "orphaned") { orphan(); return false; }
    if (r.ok === false && (r.status === 0 || r.code === "offline")) {
      if (S.status.phase !== "server-offline") log("server offline");
      S.status.phase = "server-offline";
      S.offlineUntil = performance.now() + OFFLINE_RETRY_MS;
      if (S.ep) S.ep.put = false;               // server may have restarted: re-put when it is back
      stopAll("offline");
      return false;
    }
    if (r.ok === false && (r.status === 401 || r.code === "no_token")) {
      S.status.phase = "unauthorized";
      S.offlineUntil = performance.now() + OFFLINE_RETRY_MS;
      return false;
    }
    return true;
  }

  // ---------------- audio ----------------
  function ensureCtx(): void {
    if (!S.ctx) {
      S.ctx = new AudioContext({ latencyHint: "interactive" });
      S.gain = S.ctx.createGain();
      S.gain.connect(S.ctx.destination);
      applyGain();
      S.ctx.onstatechange = () => { if (S.ctx?.state === "running") resync("ctx-running"); };
      // output device change (e.g. AirPods connected): the AudioContext follows the default sink and
      // outputLatency jumps; reschedule so queued lines pick up the new compensation
      navigator.mediaDevices?.addEventListener?.("devicechange", () => setTimeout(() => resync("devicechange"), 500));
    }
    if (S.ctx.state === "suspended") void S.ctx.resume().catch(() => {});
  }
  for (const t of ["pointerdown", "keydown"]) addEventListener(t, ensureCtx, { capture: true });

  function applyGain(): void {
    if (S.gain) S.gain.gain.value = S.settings.dubVolume;
  }

  async function fetchCue(c: Cue): Promise<void> {
    const ep = S.ep;
    if (!ep?.put || !S.ctx || S.inflight.has(c.idx) || S.loaded.has(c.idx) || S.silent.has(c.idx)) return;
    if ((S.retryAt.get(c.idx) ?? 0) > performance.now()) return;
    S.inflight.add(c.idx);
    try {
      const r = await send<string | null>({ op: "cue.get", key: ep.key, idx: c.idx });
      if (S.ep !== ep) return;
      if (!handleTransport(r)) return;
      if (r.ok === false) {
        if (r.code === "unknown_episode") { ep.put = false; void ensurePut(); }
        else if (r.code === "unknown_cue" || r.code === "synth_failed") S.silent.add(c.idx);
        else if (r.code === "disk_full") {
          S.status.lastError = "Ổ đĩa đầy — server tạm dừng sinh tiếng. Giải phóng dung lượng hoặc giảm giới hạn cache.";
          S.retryAt.set(c.idx, performance.now() + 5000);
        }
        return;
      }
      if (r.status === 202) {
        S.retryAt.set(c.idx, performance.now() + Number(r.headers?.retryMs ?? 250));
        return;
      }
      if (r.status === 204 || !r.data) { S.silent.add(c.idx); return; }
      const bytes = Uint8Array.from(atob(r.data), (ch) => ch.charCodeAt(0));
      const buf = await S.ctx.decodeAudioData(bytes.buffer);
      if (S.ep === ep) S.loaded.set(c.idx, { buf, delay: Number(r.headers?.delay ?? 0) });
    } catch (e) {
      S.retryAt.set(c.idx, performance.now() + 1000);
      log("cue fetch failed", c.idx, e);
    } finally {
      S.inflight.delete(c.idx);
    }
  }

  function stopAll(reason: string): void {
    if (S.playing.size) log("stop", reason, S.playing.size);
    for (const p of S.playing.values()) {
      p.src.onended = null;
      try { p.src.stop(); } catch { /* not started */ }
    }
    S.playing.clear();
  }

  function dropAudio(): void {
    stopAll("drop");
    S.loaded.clear();
    S.inflight.clear();
    S.retryAt.clear();
    S.silent.clear();
    S.done.clear();
  }

  function resync(reason: string): void {
    stopAll(reason);
    S.done.clear();
    schedule();
  }

  function clock() {
    return { ctxT: S.ctx!.currentTime, mediaT: S.video!.currentTime, rate: S.video!.playbackRate };
  }

  function outputLatency(): number {
    return S.ctx?.outputLatency || S.ctx?.baseLatency || 0;
  }

  function schedule(): void {
    const v = S.video, ep = S.ep, ctx = S.ctx;
    if (!v || !ep || !ctx || !S.armed || !S.settings.enabled) return;
    // server unreachable: stay silent (spec: "dừng phát") until a re-put succeeds, then resume from cache
    if (S.status.phase === "server-offline" || !ep.put) return;
    const mediaT = v.currentTime;
    // prefetch window regardless of play state (so resume/seek have audio ready)
    for (const c of ep.cues) {
      if (c.start > mediaT + PREFETCH) break;
      if (c.end + 5 < mediaT) continue;
      void fetchCue(c);
    }
    if (ctx.state !== "running") { S.status.phase = "suspended-audio"; return; }
    if (v.paused || v.seeking || v.readyState < 3) return;
    const ck = clock(), lat = outputLatency();
    for (const c of ep.cues) {
      if (c.start > mediaT + LOOKAHEAD + 1) break;
      const l = S.loaded.get(c.idx);
      if (!l || S.playing.has(c.idx) || S.done.has(c.idx)) continue;
      const target = targetMedia(c.start, l.delay, S.settings.offsetMs);
      if (!inWindow(target, mediaT, LOOKAHEAD, l.buf.duration)) continue;
      const p = plan(ck, target, l.buf.duration, lat, S.settings.biasMs);
      if (!p) { S.done.add(c.idx); continue; }
      const src = ctx.createBufferSource();
      src.buffer = l.buf;
      src.connect(S.gain!);
      src.start(p.when, p.offset);
      S.playing.set(c.idx, { src, when: p.when, offset: p.offset, target, cue: c });
      src.onended = () => { S.playing.delete(c.idx); S.done.add(c.idx); };
    }
    // free decoded audio far behind the playhead
    for (const [idx] of S.loaded) {
      const c = ep.byIdx.get(idx);
      if (c && c.end < mediaT - KEEP_BACK) S.loaded.delete(idx);
    }
  }

  let lastLatency = -1;
  function checkDrift(): void {
    const v = S.video;
    const lat = outputLatency();
    if (lastLatency >= 0 && Math.abs(lat - lastLatency) > 0.02) { lastLatency = lat; resync("latency-change"); return; }
    lastLatency = lat;
    if (!S.ctx || !v || v.paused || !S.playing.size) return;
    const ck = clock();
    if (ck.rate !== 1) return;                 // dub isn't time-stretched; only onset is aligned at rate ≠ 1
    for (const p of S.playing.values()) {
      if (ck.ctxT < p.when + 0.1) continue;
      const d = driftOf(ck, p.when, p.offset, p.target, lat, S.settings.biasMs);
      if (S.settings.debug) S.drift.push(d);
      if (Math.abs(d) > DRIFT_MAX) { log("drift", d.toFixed(3)); resync("drift"); return; }
    }
  }

  // ---------------- ducking (video.volume) ----------------
  function setDuck(on: boolean): void {
    const v = S.video;
    const target = on ? S.settings.duck : 1;
    if (!v || S.duckLevel === target) return;
    if (S.userVol === null) S.userVol = v.volume;
    const from = S.duckLevel;
    S.duckLevel = target;
    clearInterval(S.rampTimer);
    let i = 0;
    S.rampTimer = window.setInterval(() => {
      i++;
      const lvl = from + (target - from) * (i / 5);
      S.lastSetVol = Math.min(1, Math.max(0, (S.userVol ?? 1) * lvl));
      // Chrome kills a disabled/reloaded extension's content script without any callback; leave the true
      // volume on the element so the next instance (or reload) can restore it instead of staying ducked.
      if (lvl < 1) v.dataset.voxsyncUserVol = String(S.userVol ?? 1); else delete v.dataset.voxsyncUserVol;
      v.volume = S.lastSetVol;
      if (i >= 5) clearInterval(S.rampTimer);
    }, 10);
  }

  function duckTick(): void {
    if (!S.ctx || !S.video) return;
    const now = S.ctx.currentTime, lat = outputLatency();
    for (const p of S.playing.values()) {
      const end = p.when + p.src.buffer!.duration - p.offset;
      if (p.when + lat - 0.1 <= now && now < end + lat) S.lastAudible = now;
    }
    setDuck(S.settings.enabled && S.armed && S.playing.size > 0 && now - S.lastAudible < 0.3);
  }

  // ---------------- arming / video lifecycle ----------------
  function skipAdVisible(): boolean {
    for (const b of document.querySelectorAll("button")) {
      if (/Bỏ qua/i.test(b.textContent ?? "") && (b as HTMLElement).offsetParent !== null) return true;
    }
    return false;
  }

  function rearm(): void {
    const v = S.video;
    const was = S.armed;
    S.armed = !!v && shouldArm({ duration: v.duration, ...subtitleBinding(), skipAdVisible: skipAdVisible() });
    if (was !== S.armed) {
      log("armed", S.armed);
      if (!S.armed) { stopAll("disarm"); setDuck(false); }
      else { void sendPlayhead(); resync("arm"); }
    }
  }

  function attach(v: HTMLVideoElement): void {
    if (S.video === v) return;
    S.video = v;
    const stale = v.dataset.voxsyncUserVol;           // a previous (killed) instance left the movie ducked
    if (stale !== undefined) {
      v.volume = Math.min(1, Math.max(0, Number(stale) || 1));
      delete v.dataset.voxsyncUserVol;
    }
    S.userVol = v.volume;
    const on = (t: string, f: () => void) => v.addEventListener(t, f);
    on("volumechange", () => {
      // A disabled/reloaded instance keeps its DOM listeners alive but loses chrome.runtime: it must stop
      // touching the element (otherwise it re-ducks the volume the next instance just restored).
      if (!chrome.runtime?.id) return orphan();
      // a change we didn't make = the user's new base volume; re-apply the current duck on top of it
      if (Math.abs(v.volume - S.lastSetVol) <= 0.005) return;
      S.userVol = v.volume;
      if (S.duckLevel !== 1) {
        S.lastSetVol = Math.min(1, Math.max(0, S.userVol * S.duckLevel));
        v.volume = S.lastSetVol;
      }
    });
    for (const t of ["pause", "waiting"]) on(t, () => stopAll(t));
    on("seeking", () => { stopAll("seeking"); void sendPlayhead(); });
    for (const t of ["playing", "seeked", "ratechange"]) on(t, () => resync(t));
    on("play", ensureCtx);
    on("emptied", () => {
      S.lastEmptiedAt = performance.now();
      S.armed = false;
      stopAll("emptied");
      setDuck(false);
    });
    on("loadedmetadata", () => { S.metaAt = performance.now(); rearm(); });
    on("durationchange", rearm);
    v.requestVideoFrameCallback(onFrame);   // records only when debug is on (settings load async)
  }

  /** Debug metric: when the dub of a cue is actually *heard*, which media time is the picture showing, vs the
   * intended target. Independent of the scheduler's own clock pairing: uses rVFC's presented-frame mediaTime and
   * AudioContext.getOutputTimestamp() (whose contextTime is already the sample at the speaker).
   * err = mediaTime_shown_when_heard - (target + offset*rate); + = dub late. */
  function onFrame(_now: number, md: VideoFrameCallbackMetadata): void {
    const v = S.video, ctx = S.ctx;
    if (v && ctx && ctx.state === "running" && S.settings.debug) {
      const ot = ctx.getOutputTimestamp();
      if (ot.performanceTime && ot.contextTime !== undefined) {
        // context time of the sample at the speaker when this frame is on screen
        const heardCtx = ot.contextTime + (md.expectedDisplayTime - ot.performanceTime) / 1000;
        for (const p of S.playing.values()) {
          const into = heardCtx - p.when;                 // wall s of this source already heard
          if (into < 0.03 || into > 0.25) continue;
          // the sample heard at onset is `offset` seconds into the line, which should coincide with media time
          // target + offset (the line is spoken at 1x wall speed; at rate r the picture moves r× faster)
          const shownAtOnset = md.mediaTime - into * v.playbackRate;
          // reference = where the line belongs on the timeline (cue start + server delay), NOT incl. the user
          // offset, so a deliberate +100 ms offset shows up as +100 ms here
          const intended = p.target - S.settings.offsetMs / 1000;
          S.av.push([shownAtOnset - (intended + p.offset), v.playbackRate, p.offset > 0 ? 1 : 0]);
        }
        if (S.av.length > 20000) S.av.splice(0, 10000);
      }
    }
    S.video?.requestVideoFrameCallback(onFrame);
  }

  // ---------------- main loop ----------------
  function orphan(): void {
    if (S.orphaned) return;
    S.orphaned = true;
    stopAll("orphaned");
    // undo our duck only if the volume is still exactly what we set (never override a newer instance/user)
    if (S.video && S.userVol !== null && S.duckLevel !== 1 && Math.abs(S.video.volume - S.lastSetVol) <= 0.005) {
      S.video.volume = S.userVol;
      delete S.video.dataset.voxsyncUserVol;
    }
    clearInterval(S.rampTimer);
    clearInterval(statusTimer);
    void S.ctx?.close();
    clearInterval(mainTimer);
    clearInterval(duckTimer);
  }

  const mainTimer = window.setInterval(async () => {
    if (!chrome.runtime?.id) return orphan();
    document.documentElement.setAttribute(BEAT_ATTR, String(Date.now()));
    const v = document.querySelector("video");
    if (v) attach(v);
    S.status.title = titleOf(location.href);
    if (!S.settings.enabled) { S.status.phase = "idle"; return; }
    if (!S.video) return;
    if (S.ep && !S.ep.put) await ensurePut();
    if (!S.armed) rearm();
    updatePhase();
    schedule();
    checkDrift();
  }, TICK_MS);
  const duckTimer = window.setInterval(duckTick, 50);
  const statusTimer = window.setInterval(async () => {
    const ep = S.ep;
    if (!ep?.put || !S.settings.enabled) return;
    const r = await send<{ ready: number; readyAhead: number; voice: string }>({ op: "episode.status", key: ep.key });
    if (r.ok) { S.status.ready = r.data.ready; S.status.readyAhead = r.data.readyAhead; S.status.voice = r.data.voice; }
    else if (r.code === "unknown_episode") { ep.put = false; }
    else handleTransport(r);
  }, 2000);
  void statusTimer;

  function updatePhase(): void {
    const v = S.video!;
    // offline/unauthorized stays until a re-put succeeds (ensurePut clears it); retries are rate-limited there
    if ((S.status.phase === "server-offline" || S.status.phase === "unauthorized") && !S.ep?.put) return;
    if (S.armed) S.status.phase = S.ctx?.state === "running" ? "armed" : "suspended-audio";
    else if (skipAdVisible() || (Number.isFinite(v.duration) && v.duration <= 300)) S.status.phase = "waiting-ad";
    else if (!subtitleIsCurrent(subtitleBinding()) && S.metaAt > S.lastEmptiedAt &&
             performance.now() - S.metaAt > NO_SUB_AFTER_MS) S.status.phase = "no-subtitle";
    else S.status.phase = "idle";
  }

  function subtitleBinding() {
    const key = pageMediaKey(location.hash);
    return { lastEmptiedAt: S.lastEmptiedAt, subtitleAt: S.ep?.capturedAt ?? null,
             subtitleMatchesPage: !!S.ep?.mediaKey && S.ep.mediaKey === key };
  }

  // ---------------- popup bridge ----------------
  chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
    if (msg?.op === "tab.status") { reply(S.status); return false; }
    if (msg?.op === "tab.manualSubtitle") { void manualSubtitle(msg).then((err) => reply({ error: err })); return true; }
    if (msg?.op === "tab.metrics") {
      reply({ av: S.av, drift: S.drift, outputLatency: S.ctx?.outputLatency ?? null,
              baseLatency: S.ctx?.baseLatency ?? null });
      return false;
    }
    if (msg?.op === "tab.resetMetrics") { S.av = []; S.drift = []; reply(true); return false; }
    return false;
  });

}
