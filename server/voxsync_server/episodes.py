"""Episode registry (in RAM) + the synthesis worker + the listener.

One synthesis worker: the ONNX engine serializes calls behind an RLock, so more threads add no throughput
(measured Phase 0). The worker walks cues in order from the cursor (= first unfinished cue at/after the
playhead of the most recently active episode) and fills the disk cache. Seeking just moves the cursor;
an in-flight synthesis is never cancelled (worst case ~0.85 s, Phase 0).

Modes:
- `single`: every cue uses the episode voice.
- `characters`: a listener thread reads the original audio of each cue (HLS source), embeds the speaker and
  adds it to the title's cast; each cue is voiced with its character's voice. The listener runs ahead of the
  worker (same cursor order); a cue is synthesized only once it has been heard, so it is never voiced twice.
  If the source fails, the episode falls back to `single` for the cues not heard yet.
- `clone`: like `characters`, but each cue is voiced by cloning the actor from that cue's own original audio
  (or the character's longest line when the cue is too short). Personal use, explicit consent required.

Alignment has two parts:
- per-cue audio (rate from stretch.decide_rate; depends only on the cue and the next cue's start) → `prepared`
- timeline (delay/dropped) chained from the previous played cue. It is recomputed forward from any cue
  that changes until the values stop changing or a not-yet-prepared cue breaks the chain. A cue whose
  predecessor is not prepared (right after a seek) is timed as if nothing precedes it and is corrected
  once the predecessor is ready.
"""
import hashlib
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf

from . import stretch
from .cache import DiskCache, DiskFull, raw_key, stretched_key
from .normalize import normalize

log = logging.getLogger("voxsync.worker")

MODES = ("single", "characters", "clone")
LISTEN_AHEAD = 30            # cues heard ahead of the playhead before the listener idles
MIN_REF_SECONDS = 1.2        # clone: lines shorter than this never go into a reference clip
REF_LINES = 3                # clone reference = the character's REF_LINES longest lines, concatenated (~8 s)
SOURCE_FAILS = 3


@dataclass
class Cue:
    idx: int
    start: float
    end: float
    text: str            # normalized
    raw: str = ""        # raw cache key (text|voice|model|norm)
    rate: float = 1.0
    play_dur: float = 0.0
    delay: float = 0.0
    dropped: bool = False
    prepared: bool = False  # stretched audio on disk, rate/play_dur known
    ready: bool = False     # prepared and timeline decided
    failed: int = 0
    pos: int = 0         # position in Episode.cues (sorted by start)
    voice: str = ""      # voice id used for `raw` (single/characters); "" in clone mode
    ref: str = ""        # clone mode: path of the reference clip used for `raw`
    heard: bool = False  # listener has embedded this cue (multi-voice modes)
    char: int | None = None

    @property
    def audio_key(self) -> str:
        return stretched_key(self.raw, self.rate)


@dataclass
class Episode:
    key: str
    voice: str
    cues: list[Cue]
    fingerprint: str
    playhead: float = 0.0
    touched: float = field(default_factory=time.monotonic)
    by_idx: dict[int, Cue] = field(default_factory=dict)
    empty_idx: frozenset[int] = frozenset()   # cues whose text normalized to nothing (SFX-only)
    mode: str = "single"
    title: str | None = None
    source: object | None = None              # HlsSource (multi-voice modes)
    source_fails: int = 0
    source_error: str | None = None
    cast_version: int = -1                    # cast version the cue voices were last checked against
    refs: dict[int, Path] = field(default_factory=dict)   # clone mode: frozen reference clip per character

    def __post_init__(self) -> None:
        for i, c in enumerate(self.cues):
            c.pos = i
        self.by_idx = {c.idx: c for c in self.cues}

    def first_at(self, t: float) -> int:
        """Position of the first cue still relevant at media time t (ends after t)."""
        lo, hi = 0, len(self.cues)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.cues[mid].end <= t:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def before(self, c: Cue) -> Cue | None:
        return self.cues[c.pos - 1] if c.pos > 0 else None

    def after(self, c: Cue) -> Cue | None:
        return self.cues[c.pos + 1] if c.pos + 1 < len(self.cues) else None


def fingerprint(cues: list[dict], voice: str, mode: str = "single", title: str | None = None,
                media: str | None = None, duration: float | None = None) -> str:
    # duration rounded: the player reports slightly different values across reloads
    h = hashlib.sha256(f"{voice}|{mode}|{title}|{media}|{round(duration or 0)}".encode())
    for c in cues:
        h.update(f"{c['idx']}|{float(c['start']):.3f}|{float(c['end']):.3f}|{c['text']}\n".encode())
    return h.hexdigest()


class Registry:
    MAX_EPISODES = 8
    MAX_FAILS = 2

    def __init__(self, tts, cache: DiskCache, voices, casts=None, embedder_factory=None, source_factory=None,
                 refs_dir: Path | None = None):
        self.tts, self.cache, self.voices = tts, cache, voices
        self.casts = casts
        self._embedder_factory, self._source_factory = embedder_factory, source_factory
        self._embedder = None
        self._saved_at = 0.0
        self.refs_dir = refs_dir
        self._ref_hash: dict[str, str] = {}     # clone ref path → content hash (refs are written once)
        self._eps: dict[str, Episode] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._hear = threading.Event()
        self._stop = threading.Event()
        self._rtf: deque[float] = deque(maxlen=20)
        self.disk_full_until = 0.0              # monotonic; while in the future, synthesis is paused
        self._thread = threading.Thread(target=self._run, name="voxsync-synth", daemon=True)
        self._listener = threading.Thread(target=self._listen, name="voxsync-listen", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self._listener.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._hear.set()
        self._thread.join(timeout=5)
        self._listener.join(timeout=5)
        if self.casts:
            self.casts.save_all()

    # ---- API surface ----
    def put(self, key: str, voice: str | None, cues: list[dict], mode: str = "single", title: str | None = None,
            media: str | None = None, duration: float | None = None) -> Episode:
        """mode != single needs title + media (the HLS URL) and a cast store; otherwise it degrades to single."""
        voice = self.voices.resolve(voice)
        if mode not in MODES or not (title and media and self.casts and self._source_factory):
            mode = "single"
        fp = fingerprint(cues, voice, mode, title, media, duration)
        with self._lock:
            old = self._eps.get(key)
            if old and old.fingerprint == fp:        # idempotent re-put (extension reconnect)
                old.touched = time.monotonic()
                return old
            built, empty = [], set()
            for c in cues:
                text = normalize(c["text"])
                if text:
                    built.append(Cue(int(c["idx"]), float(c["start"]), float(c["end"]), text))
                else:
                    empty.add(int(c["idx"]))
            built.sort(key=lambda c: (c.start, c.idx))
            # voice/cue change keeps the playhead, so resynthesis starts where the viewer is
            ep = Episode(key, voice, built, fp, old.playhead if old else 0.0, empty_idx=frozenset(empty),
                         mode=mode, title=title if mode != "single" else None)
            if mode != "single":
                ep.source = self._source_factory(media, duration)
                cast = self.casts.get(title)
                for c in ep.cues:                    # lines heard in an earlier session/episode
                    if cast.has(self._line(ep, c)):
                        c.heard, c.char = True, cast.char_of(self._line(ep, c))
                ep.cast_version = cast.version
            for c in ep.cues:
                if mode == "single" or c.heard:
                    self._apply_voice(ep, c)
            self._eps[key] = ep
            self._evict_episodes()
            self._retime(ep, 0)
        self._wake.set()
        self._hear.set()
        return ep

    def get(self, key: str) -> Episode | None:
        ep = self._eps.get(key)
        if ep:
            ep.touched = time.monotonic()
        return ep

    def set_playhead(self, key: str, t: float) -> Episode | None:
        ep = self.get(key)
        if ep:
            ep.playhead = max(0.0, t)
            self._wake.set()
        return ep

    def status(self, ep: Episode) -> dict:
        i = ep.first_at(ep.playhead)
        ready_ahead = 0
        for c in ep.cues[i:]:
            if not c.ready:
                break
            ready_ahead += 1
        return {"total": len(ep.cues), "ready": sum(c.ready for c in ep.cues), "readyAhead": ready_ahead,
                "cursorIdx": next((c.idx for c in ep.cues[i:] if not c.ready), None), "voice": ep.voice,
                "playhead": ep.playhead, "rtf": self.rtf(), "mode": ep.mode,
                "heard": sum(c.heard for c in ep.cues) if ep.mode != "single" else None,
                "sourceError": ep.source_error}

    def characters(self, ep: Episode) -> list[dict]:
        """The title's characters seen in this episode, biggest first, with a sample line each."""
        if ep.mode == "single":
            return []
        cast = self.casts.get(ep.title)
        sample: dict[int, str] = {}
        for c in ep.cues:
            if c.char is not None and len(c.text) > len(sample.get(c.char, "")):
                sample[c.char] = c.text
        out = cast.public(lambda cid: cast.voice_for(cid, ep.voice, self.voices.resolve))
        for ch in out:
            ch["sample"] = sample.get(ch["id"], "")
        return [ch for ch in out if ch["sample"]]

    def set_character_voice(self, title: str, cid: int, voice: str | None) -> None:
        cast = self.casts.get(title)
        if cid not in cast.chars:
            raise KeyError(cid)
        cast.set_voice(cid, self.voices.resolve(voice) if voice else None)
        cast.save()
        self._refresh_title(title)

    def rtf(self) -> float | None:
        return round(sum(self._rtf) / len(self._rtf), 3) if self._rtf else None

    def episode_count(self) -> int:
        return len(self._eps)

    # ---- worker ----
    def _evict_episodes(self) -> None:
        while len(self._eps) > self.MAX_EPISODES:
            oldest = min(self._eps.values(), key=lambda e: e.touched)
            del self._eps[oldest.key]

    @staticmethod
    def _line(ep: Episode, c: Cue) -> str:
        return f"{ep.key}:{c.idx}"

    def _ref_path(self, ep: Episode, c: Cue) -> Path:
        return self.refs_dir / ep.title / "lines" / f"{ep.key}_{c.idx}.wav"

    def _clone_ref(self, ep: Episode, c: Cue) -> Path | None:
        """The character's reference clip: its first REF_LINES heard lines of ≥ MIN_REF_SECONDS in this episode,
        concatenated, then frozen so all its lines share one cloned voice. Measured (tập 2, 6 short lines):
        dub↔actor similarity 0.44-0.60 vs 0.13-0.47 when cloning each line from its own audio.
        None → no usable audio yet (the preset voice is used meanwhile)."""
        if c.char is None:
            return None
        frozen = ep.refs.get(c.char)
        if frozen is not None:
            return frozen
        lines = [o for o in ep.cues if o.char == c.char and o.end - o.start >= MIN_REF_SECONDS
                 and self._ref_path(ep, o).is_file()][:REF_LINES]
        if len(lines) < REF_LINES:
            return None
        sig = hashlib.sha1(f"{ep.key}|{[o.idx for o in lines]}".encode()).hexdigest()[:16]
        out = self.refs_dir / ep.title / f"char{c.char}_{sig}.wav"
        if not out.is_file():
            pcm = np.concatenate([sf.read(self._ref_path(ep, o), dtype="float32")[0] for o in lines])
            tmp = out.with_suffix(".tmp.wav")
            sf.write(tmp, pcm, 16_000, subtype="PCM_16")
            tmp.replace(out)
        ep.refs[c.char] = out
        for o in ep.cues:                             # lines voiced with the preset meanwhile switch to the clone
            if o is not c and o.heard and o.char == c.char:
                self._apply_voice(ep, o)
        return out

    def _apply_voice(self, ep: Episode, c: Cue) -> bool:
        """Decide the cue's voice (and clone reference) and its cache key. Caller holds the lock. Returns True if
        the cue's audio changed (it is then unprepared, or prepared straight from cache)."""
        voice, ref = ep.voice, ""
        if ep.mode != "single":
            cast = self.casts.get(ep.title)
            ch = cast.chars.get(c.char) if c.char is not None else None
            voice = cast.voice_for(c.char, ep.voice, self.voices.resolve)
            if ep.mode == "clone" and not (ch and ch.voice):          # a user-picked voice beats cloning
                p = self._clone_ref(ep, c)
                if p is not None:
                    ref = str(p)
                    if ref not in self._ref_hash:
                        self._ref_hash[ref] = hashlib.sha256(p.read_bytes()).hexdigest()[:24]
                    voice = "clone:" + self._ref_hash[ref]
        raw = raw_key(c.text, voice)
        if raw == c.raw:
            return False
        c.raw, c.voice, c.ref = raw, voice, ref
        c.prepared = c.ready = False
        c.failed = 0
        if self.cache.has(raw):
            try:
                self._prepare(ep, c)
            except Exception:
                log.warning("cached cue %s unusable; will resynthesize", c.idx)
        return True

    def _refresh_title(self, title: str) -> None:
        """The title's cast changed: re-read each heard cue's character and re-decide its voice."""
        cast = self.casts.get(title)
        with self._lock:
            for ep in self._eps.values():
                if ep.title != title:
                    continue
                ep.cast_version = cast.version
                changed = False
                for c in ep.cues:
                    if not c.heard:
                        continue
                    c.char = cast.char_of(self._line(ep, c))
                    changed |= self._apply_voice(ep, c)
                if changed:                  # several independent changes: _retime's early stop needs a full walk
                    self._retime_all(ep)
        self._wake.set()

    def _next_job(self) -> tuple[Episode, Cue] | None:
        """Ahead of the playhead first (most recently active episode first), then wrap to the cues before
        it so a backward seek is a cache hit and the whole episode ends up cached. In multi-voice modes a cue
        waits until the listener has heard it (so it is voiced once, with the right character)."""
        with self._lock:
            eps = sorted(self._eps.values(), key=lambda e: e.touched, reverse=True)
        for ep in eps:
            i = ep.first_at(ep.playhead)
            for c in ep.cues[i:] + ep.cues[:i]:
                if not c.prepared and c.failed < self.MAX_FAILS and c.raw:
                    return ep, c
        return None

    # ---- listener (multi-voice modes) ----
    def _next_unheard(self) -> tuple[Episode, Cue] | None:
        with self._lock:
            eps = sorted(self._eps.values(), key=lambda e: e.touched, reverse=True)
        for ep in eps:
            if ep.mode == "single" or ep.source_error:
                continue
            i = ep.first_at(ep.playhead)
            for c in ep.cues[i:i + LISTEN_AHEAD]:
                if not c.heard:
                    return ep, c
        return None

    def _listen(self) -> None:
        while not self._stop.is_set():
            job = self._next_unheard()
            if job is None:
                self._hear.wait(timeout=1.0)
                self._hear.clear()
                continue
            ep, c = job
            try:
                self._hear_cue(ep, c)
                ep.source_fails = 0
            except Exception as e:
                ep.source_fails += 1
                log.warning("listening to cue %s failed (%d/%d): %s", c.idx, ep.source_fails, SOURCE_FAILS, e)
                if ep.source_fails >= SOURCE_FAILS:
                    self._give_up_source(ep, f"{type(e).__name__}: {e}"[:200])

    def _hear_cue(self, ep: Episode, c: Cue) -> None:
        from .speakers import EMBED_SECONDS, SR
        if self._embedder is None:
            self._embedder = self._embedder_factory()
        s, e = c.start, c.end
        if e - s < EMBED_SECONDS:                    # too short to embed: widen around the middle
            m = (s + e) / 2
            s, e = max(0.0, m - EMBED_SECONDS / 2), m + EMBED_SECONDS / 2
        pcm = ep.source.window(s, e)
        cast = self.casts.get(ep.title)
        line = self._line(ep, c)
        if len(pcm) >= SR // 5:
            cast.add(line, *self._embedder.embed(pcm))
            if ep.mode == "clone":
                p = self._ref_path(ep, c)
                p.parent.mkdir(parents=True, exist_ok=True)
                own = ep.source.window(max(0.0, c.start - 0.1), c.end + 0.1)
                sf.write(p, own, SR, subtype="PCM_16")
            # spike: reclustering per line kept the 2-person scene at 88% (69-81% when batched); cost grows with n²
            if len(cast.lines) <= 200 or len(cast.lines) % 10 == 0:
                cast.recluster()
            if time.monotonic() - self._saved_at > 10:      # survive a crash; save() is a no-op when clean
                cast.save()
                self._saved_at = time.monotonic()
        with self._lock:
            c.heard, c.char = True, cast.char_of(line)
        if cast.version != ep.cast_version:
            self._refresh_title(ep.title)            # also applies this cue's voice
        else:
            with self._lock:
                refs = len(ep.refs)
                if self._apply_voice(ep, c):
                    if len(ep.refs) != refs:          # a clone ref froze: the character's earlier lines changed too
                        self._retime_all(ep)
                    else:
                        self._retime(ep, c.pos)
            self._wake.set()

    def _give_up_source(self, ep: Episode, why: str) -> None:
        """Original audio unavailable: voice the cues not heard yet with the episode voice."""
        log.error("source failed for %s, falling back to one voice: %s", ep.key, why)
        with self._lock:
            ep.source_error = why
            for c in ep.cues:
                if not c.heard:
                    c.heard = True
                    self._apply_voice(ep, c)
            self._retime_all(ep)
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            if time.monotonic() < self.disk_full_until:
                self._wake.wait(timeout=5.0)
                self._wake.clear()
                continue
            job = self._next_job()
            if job is None:
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue
            ep, c = job
            try:
                self._synth(ep, c)
            except DiskFull:
                # not the cue's fault: don't burn its retries; pause and retry later
                self.disk_full_until = time.monotonic() + 30
                log.error("disk full: synthesis paused 30 s")
            except Exception:
                c.failed += 1
                log.exception("synthesis failed for cue %s (%d/%d)", c.idx, c.failed, self.MAX_FAILS)

    def _synth(self, ep: Episode, c: Cue) -> None:
        raw, voice, ref = c.raw, c.voice, c.ref       # snapshot: the listener may re-voice the cue meanwhile
        if not self.cache.has(raw):
            t0 = time.perf_counter()
            if ref:
                wav = self.tts.infer(c.text, ref_audio=ref, apply_watermark=False)
            else:
                wav = self.tts.infer(c.text, voice=voice, apply_watermark=False)
            dt = time.perf_counter() - t0
            if len(wav):
                self._rtf.append(dt / (len(wav) / self.tts.sample_rate))
            self.cache.store(raw, wav, self.tts.sample_rate)
        if c.raw != raw:                              # re-voiced while synthesizing: the worker picks it up again
            return
        self._ensure_stretched(ep, c)            # ffmpeg, outside the registry lock
        with self._lock:
            if self._eps.get(ep.key) is not ep or c.raw != raw:
                return                            # replaced meanwhile: audio stays cached
            self._prepare(ep, c)
            self._retime(ep, c.pos)

    def _rate_for(self, ep: Episode, c: Cue) -> tuple[float, float]:
        """(raw duration, rate). Raises and drops the file if the cached raw audio is unusable, so the worker
        counts a failure instead of spinning."""
        dur = self.cache.duration(c.raw)
        if not dur:
            self.cache.discard(c.raw)
            raise ValueError(f"unusable cached audio for cue {c.idx}")
        nxt = ep.after(c)
        return dur, stretch.decide_rate(dur, c.start, nxt.start if nxt else None)

    def _ensure_stretched(self, ep: Episode, c: Cue) -> None:
        _, rate = self._rate_for(ep, c)
        key = stretched_key(c.raw, rate)
        if rate != 1.0 and not self.cache.has(key):
            loaded = self.cache.load(c.raw)
            if loaded is None:
                raise ValueError(f"unreadable cached audio for cue {c.idx}")
            wav, sr = loaded
            self.cache.store(key, stretch.atempo(wav, sr, rate), sr)

    def _prepare(self, ep: Episode, c: Cue) -> None:
        """Mark a cue prepared if its final audio is on disk. Never runs ffmpeg (callers hold the lock); a
        missing stretched file leaves the cue unprepared for the worker."""
        dur, rate = self._rate_for(ep, c)
        if rate != 1.0 and not self.cache.has(stretched_key(c.raw, rate)):
            return
        c.rate, c.play_dur, c.prepared = rate, dur / rate, True

    def _retime(self, ep: Episode, pos: int, full: bool = False) -> None:
        """Recompute delay/dropped from cue `pos` forward. An unprepared cue breaks the chain: the next prepared
        cue is timed as if nothing precedes it (corrected when the gap fills). Stops once a prepared cue that
        was already ready keeps its exact timing: everything after it depends only on it, so it is consistent.
        Unprepared cues never stop the walk (prepared-but-unready cues after them must still be timed).
        `full` disables the early stop: needed when several cues changed independently (re-voicing)."""
        prev_end = self._played_end_before(ep, pos)
        for c in ep.cues[pos:]:
            if not c.prepared:
                prev_end = None
                continue
            delay = stretch.delay_after(prev_end, c.start)
            dropped = stretch.should_drop(delay)
            unchanged = c.ready and c.delay == delay and c.dropped == dropped
            c.delay, c.dropped, c.ready = delay, dropped, True
            if not dropped:
                prev_end = c.start + delay + c.play_dur
            if unchanged and c.pos != pos and not full:
                return

    def _retime_all(self, ep: Episode) -> None:
        self._retime(ep, 0, full=True)

    def invalidate(self, ep: Episode, c: Cue) -> None:
        """Cue audio vanished from disk (evicted/corrupt): resynthesize it."""
        with self._lock:
            c.prepared = c.ready = False
            c.failed = 0
        self._wake.set()

    def disk_full(self) -> bool:
        return time.monotonic() < self.disk_full_until

    def is_failed(self, c: Cue) -> bool:
        return not c.prepared and c.failed >= self.MAX_FAILS

    def reassign_voice(self, removed: str) -> None:
        """A user voice was deleted: episodes using it fall back to the default voice (resynth from playhead);
        characters overridden to it lose the override."""
        if self.casts:
            for title in {e.title for e in self._eps.values() if e.title}:
                cast = self.casts.get(title)
                for ch in list(cast.chars.values()):
                    if ch.voice == removed:
                        cast.set_voice(ch.id, None)
                self._refresh_title(title)
        with self._lock:
            affected = [e for e in self._eps.values() if e.voice == removed]
        for e in affected:
            self.put(e.key, None, [{"idx": c.idx, "start": c.start, "end": c.end, "text": c.text} for c in e.cues],
                     e.mode, e.title, getattr(e.source, "url", None), getattr(e.source, "duration", None))

    @staticmethod
    def _played_end_before(ep: Episode, pos: int) -> float | None:
        """Media time at which the last played cue before `pos` ends; None if unknown or nothing plays."""
        for c in reversed(ep.cues[:pos]):
            if not c.prepared:
                return None
            if not c.dropped:
                return c.start + c.delay + c.play_dur
        return None
