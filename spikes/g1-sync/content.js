// G1 spike (throwaway): sync pre-synthesized cues to <video> on chophim.lol.
// Clock = video.currentTime; playback = AudioBufferSourceNode.start(when). No setTimeout firing.
(() => {
  const LOOKAHEAD_S = 3;      // schedule cues starting within this window
  const PREFETCH_S = 25;      // fetch+decode cues starting within this window
  const DRIFT_MAX_S = 0.08;
  const DUCK = 0.3, RAMP_STEPS = 5, RAMP_MS = 50;

  const log = (ev, data = {}) => {
    const rec = { t: +performance.now().toFixed(1), ev, ...data };
    S.events.push(rec);
    console.log("VOXSYNC " + JSON.stringify(rec));
  };
  const S = {
    events: [], onsets: [], cues: null, ep: null, vttUrl: null, vttAt: -1, lastEmptiedAt: 0,
    video: null, ctx: null, buffers: new Map(), pending: new Set(), playing: new Map(),
    samples: [], av: [], drift: [], userVol: null, duckLevel: 1, armed: false,
  };
  window.__voxsync = S; // isolated world only; harness reads via console / dataset

  // ---- VTT capture: PerformanceObserver over same-origin resource timing ----
  const VTT_RE = /\/api\/storage\/subtitles\?file=.*-(tap-\d+)-.*_vietnamese\.vtt/;
  new PerformanceObserver((list) => {
    for (const e of list.getEntries()) {
      const m = VTT_RE.exec(e.name);
      if (m && e.name !== S.vttUrl) loadVtt(e.name, m[1]);
    }
  }).observe({ type: "resource", buffered: true });

  const ts = (s) => s.split(":").reduce((a, x) => a * 60 + parseFloat(x), 0);
  const normalize = (t) => t.replace(/<[^>]+>/g, "").replace(/\[[^\]]*\]|\([^)]*\)/g, "")
    .replace(/(^|\n)\s*-\s*/g, "$1").replace(/\.{3,}|…/g, ", ").replace(/\s*\n\s*/g, " ")
    .replace(/\s+/g, " ").replace(/^[\s,]+|[\s,]+$/g, "");

  async function loadVtt(url, ep) {
    S.vttUrl = url; S.vttAt = performance.now();
    const text = await (await fetch(url)).text(); // same-origin: no CORS involvement
    const cues = [];
    for (const block of text.split(/\r?\n\r?\n/)) {
      const m = /([\d:.]+) --> ([\d:.]+)[^\n]*\n([\s\S]+)/.exec(block);
      if (!m) continue;
      const t = normalize(m[3]);
      if (t) cues.push({ idx: cues.length, start: ts(m[1]), end: ts(m[2]), text: t });
    }
    S.cues = cues; S.ep = ep;
    S.buffers.clear(); S.pending.clear();
    log("vtt", { ep, cues: cues.length });
    rearm();
  }

  // ---- audio ----
  function ensureCtx() {
    if (!S.ctx) {
      S.ctx = new AudioContext({ latencyHint: "interactive" });
      S.ctx.onstatechange = () => log("ctx", { state: S.ctx.state });
      log("ctx-created", { state: S.ctx.state, baseLatency: S.ctx.baseLatency });
    }
    if (S.ctx.state !== "running") S.ctx.resume().then(() => resync("resume")).catch((e) => log("resume-fail", { e: String(e) }));
  }
  for (const type of ["pointerdown", "keydown"]) addEventListener(type, () => ensureCtx(), true);

  function fetchCue(c) {
    if (S.buffers.has(c.idx) || S.pending.has(c.idx)) return;
    S.pending.add(c.idx);
    const ep = S.ep;
    chrome.runtime.sendMessage({ op: "cue", ep, idx: c.idx }, async (r) => {
      S.pending.delete(c.idx);
      if (ep !== S.ep) return; // episode changed while in flight
      if (!r?.ok) return log("fetch-fail", { idx: c.idx, err: r?.err ?? chrome.runtime.lastError?.message });
      const bin = Uint8Array.from(atob(r.b64), (ch) => ch.charCodeAt(0));
      const buf = await S.ctx.decodeAudioData(bin.buffer);
      if (ep === S.ep) S.buffers.set(c.idx, buf);        // episode may have changed during decode
    });
  }

  function stopAll(reason) {
    for (const [, p] of S.playing) {
      if (S.ctx.currentTime < p.when) p.onset.cancelled = true;   // never reached the speaker
      try { p.src.onended = null; p.src.stop(); } catch {}
    }
    if (S.playing.size) log("stop", { reason, n: S.playing.size });
    S.playing.clear();
    setDuck(false);
  }

  // Snapshot (ctxTime, mediaTime) at one instant; media time advances at video.playbackRate.
  const clockPair = () => ({ ctxT: S.ctx.currentTime, mediaT: S.video.currentTime, rate: S.video.playbackRate });

  function schedule() {
    const v = S.video;
    if (!S.armed || !S.cues || !S.ctx || S.ctx.state !== "running" || v.paused || v.seeking || v.readyState < 3) return;
    const { ctxT, mediaT, rate } = clockPair();
    for (const c of S.cues) {
      if (c.start > mediaT + PREFETCH_S) break;
      if (c.end + 4 < mediaT) continue;
      fetchCue(c);
      const buf = S.buffers.get(c.idx);
      if (!buf || S.playing.has(c.idx) || c.start > mediaT + LOOKAHEAD_S) continue;
      const lead = (c.start - mediaT) / rate;          // seconds of wall time until cue start
      let when = ctxT + lead, offset = 0;
      if (lead < 0) {                                   // we are mid-cue (resume/seek): join late
        offset = -lead;
        if (offset >= buf.duration - 0.15) continue;   // nothing meaningful left
        when = ctxT + 0.02; offset += 0.02;
      }
      if (c.done) continue;
      const src = S.ctx.createBufferSource();
      src.buffer = buf; src.connect(S.ctx.destination);
      src.start(when, offset);
      const ot = S.ctx.getOutputTimestamp();
      const onset = { idx: c.idx, cueStart: c.start, when, offset, rate, outPerf: ot.performanceTime + (when - ot.contextTime) * 1000 };
      S.onsets.push(onset);
      S.playing.set(c.idx, { src, when, offset, cue: c, onset });
      src.onended = () => { S.playing.delete(c.idx); c.done = true; };
    }
  }

  // Mid-cue drift vs video.currentTime, every tick, any rate (logged, resync over threshold).
  function checkDrift() {
    if (!S.playing.size) return;
    const { ctxT, mediaT, rate } = clockPair();
    for (const [, p] of S.playing) {
      if (ctxT < p.when + 0.1) continue;
      // audio plays at 1x wall time; we aligned cue START, so measure only start alignment at rate 1
      if (rate !== 1) continue;
      const audioMedia = p.cue.start + (ctxT - p.when + p.offset);
      const drift = audioMedia - mediaT;
      S.drift.push([+drift.toFixed(4), rate]);
      if (Math.abs(drift) > DRIFT_MAX_S) { log("drift", { idx: p.cue.idx, drift: +drift.toFixed(3), rate }); return resync("drift"); }
    }
  }

  // Independent truth: presented video frame (rVFC mediaTime @ expectedDisplayTime) vs the
  // dub sample hitting the output at that same perf time (getOutputTimestamp, incl. outputLatency).
  function onFrame(_now, md) {
    if (S.ctx?.state === "running") {
      const ot = S.ctx.getOutputTimestamp();
      if (ot.performanceTime > 0) {
        const ctxAtDisplay = ot.contextTime + (md.expectedDisplayTime - ot.performanceTime) / 1000;
        const rate = S.video.playbackRate;
        for (const [, p] of S.playing) {
          const into = ctxAtDisplay - p.when;                 // wall seconds since this dub source hit the speaker
          if (into < 0.03 || into + p.offset > p.src.buffer.duration) continue;
          if (rate !== 1 && into > 0.25) continue;            // dub is not stretched: only onset alignment is meaningful at rate≠1
          // media time at which this dub source started sounding, per the presented frame clock
          const mediaAtOnset = md.mediaTime - into * rate;
          S.av.push([+(mediaAtOnset - (p.cue.start + p.offset * rate)).toFixed(4), rate, p.offset > 0 ? 1 : 0, p.cue.idx]); // + = dub late
        }
      }
    }
    S.video?.requestVideoFrameCallback(onFrame);
  }

  function resync(reason) {
    if (!S.ctx) return;
    stopAll(reason);
    // cues fully in the past stay done; anything at/after the playhead may replay
    const mediaT = S.video?.currentTime ?? 0;
    if (S.cues) for (const c of S.cues) c.done = c.end < mediaT - 0.05 ? c.done : false;
    schedule();
  }

  // ---- ducking via video.volume (stepped ramp; player does not override, observed) ----
  let rampTimer = null;
  function setDuck(on) {
    const target = on ? DUCK : 1;
    if (S.duckLevel === target || !S.video) return;
    const from = S.duckLevel; S.duckLevel = target;
    clearInterval(rampTimer);
    let i = 0;
    rampTimer = setInterval(() => {
      i++;
      const lvl = from + (target - from) * (i / RAMP_STEPS);
      S.lastSetVol = Math.max(0, Math.min(1, (S.userVol ?? 1) * lvl)); S.video.volume = S.lastSetVol;
      if (i >= RAMP_STEPS) { clearInterval(rampTimer); log("duck", { on, vol: +S.video.volume.toFixed(2), userVol: S.userVol }); }
    }, RAMP_MS / RAMP_STEPS);
  }

  // ---- arming: avoid pre-roll ads on the same element ----
  function rearm() {
    const v = S.video;
    const was = S.armed;
    S.armed = !!(v && S.cues && v.duration > 300 && S.vttAt > S.lastEmptiedAt);
    if (was !== S.armed) log("armed", { armed: S.armed, duration: v?.duration, ep: S.ep });
    if (S.armed) resync("arm");
  }

  function attach(v) {
    if (S.video === v) return;
    S.video = v; S.userVol = v.volume;
    v.requestVideoFrameCallback(onFrame);
    log("video", { src: v.currentSrc?.slice(0, 40), duration: v.duration });
    const on = (t, f) => v.addEventListener(t, f);
    // volumechange fires async: compare to the value we last wrote instead of a sync flag
    on("volumechange", () => { if (Math.abs(v.volume - (S.lastSetVol ?? -1)) > 0.005) S.userVol = v.volume; });
    for (const t of ["pause", "waiting", "seeking"]) on(t, () => { log(t, { mediaT: v.currentTime }); stopAll(t); });
    for (const t of ["playing", "seeked", "ratechange"]) on(t, () => { log(t, { mediaT: v.currentTime, rate: v.playbackRate }); resync(t); });
    on("play", () => ensureCtx());
    on("emptied", () => { S.lastEmptiedAt = performance.now(); S.armed = false; stopAll("emptied"); log("emptied"); });
    on("loadedmetadata", () => { log("loadedmetadata", { duration: v.duration }); rearm(); });
    on("durationchange", rearm);
  }

  // ---- loop: 250 ms tick for scheduling + 50 ms clock samples for measurement ----
  setInterval(() => {
    const v = document.querySelector("video");
    if (v) attach(v);
    if (!S.video) return;
    if (!S.armed) rearm();
    schedule(); checkDrift();
    document.documentElement.dataset.voxsync = JSON.stringify({ armed: S.armed, ep: S.ep, ctx: S.ctx?.state, onsets: S.onsets.length });
  }, 250);
  // Ducking: on only while some source is (about to be) audible; 100ms lead for the ramp,
  // 300ms hold after the last one ends so short inter-line gaps don't pump the music.
  let lastAudible = 0;
  setInterval(() => {
    if (!S.ctx || !S.video) return;
    const now = S.ctx.currentTime;
    for (const [, p] of S.playing) if (p.when - 0.1 <= now && now < p.when + p.src.buffer.duration - p.offset) lastAudible = now;
    setDuck(S.playing.size > 0 && now - lastAudible < 0.3);
  }, 50);
  setInterval(() => {
    if (S.video && !S.video.paused) S.samples.push([performance.now(), S.video.currentTime, S.video.playbackRate]);
    if (S.samples.length > 20000) S.samples.splice(0, 10000);
  }, 50);

  // Harness hook: a page-world script cannot read the isolated world, so dump on request via DOM event.
  addEventListener("voxsync-dump", () => {
    document.documentElement.dataset.voxsyncDump = JSON.stringify({
      onsets: S.onsets, samples: S.samples, av: S.av, drift: S.drift, events: S.events.slice(-400),
      outputLatency: S.ctx?.outputLatency, baseLatency: S.ctx?.baseLatency, ctx: S.ctx?.state,
    });
  });
  chrome.runtime.sendMessage({ op: "ping" }, (r) => log("localhost-ping", r ?? { err: chrome.runtime.lastError?.message }));
})();
