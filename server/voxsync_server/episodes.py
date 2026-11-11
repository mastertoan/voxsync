"""Episode registry (in RAM) + the single synthesis worker.

One worker thread: the ONNX engine serializes calls behind an RLock, so more threads add no throughput
(measured Phase 0). The worker walks cues in order from the cursor (= first unfinished cue at/after the
playhead of the most recently active episode) and fills the disk cache. Seeking just moves the cursor;
an in-flight synthesis is never cancelled (worst case ~0.85 s, Phase 0).

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

from . import stretch
from .cache import DiskCache, DiskFull, raw_key, stretched_key
from .normalize import normalize

log = logging.getLogger("voxsync.worker")


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


def fingerprint(cues: list[dict], voice: str) -> str:
    h = hashlib.sha256(voice.encode())
    for c in cues:
        h.update(f"{c['idx']}|{float(c['start']):.3f}|{float(c['end']):.3f}|{c['text']}\n".encode())
    return h.hexdigest()


class Registry:
    MAX_EPISODES = 8
    MAX_FAILS = 2

    def __init__(self, tts, cache: DiskCache, voices):
        self.tts, self.cache, self.voices = tts, cache, voices
        self._eps: dict[str, Episode] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._rtf: deque[float] = deque(maxlen=20)
        self.disk_full_until = 0.0              # monotonic; while in the future, synthesis is paused
        self._thread = threading.Thread(target=self._run, name="voxsync-synth", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=5)

    # ---- API surface ----
    def put(self, key: str, voice: str | None, cues: list[dict]) -> Episode:
        voice = self.voices.resolve(voice)
        fp = fingerprint(cues, voice)
        with self._lock:
            old = self._eps.get(key)
            if old and old.fingerprint == fp:        # idempotent re-put (extension reconnect)
                old.touched = time.monotonic()
                return old
            built, empty = [], set()
            for c in cues:
                text = normalize(c["text"])
                if text:
                    built.append(Cue(int(c["idx"]), float(c["start"]), float(c["end"]), text,
                                     raw=raw_key(text, voice)))
                else:
                    empty.add(int(c["idx"]))
            built.sort(key=lambda c: (c.start, c.idx))
            # voice/cue change keeps the playhead, so resynthesis starts where the viewer is
            ep = Episode(key, voice, built, fp, old.playhead if old else 0.0, empty_idx=frozenset(empty))
            self._eps[key] = ep
            self._evict_episodes()
            self._prime_from_cache(ep)
        self._wake.set()
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
                "playhead": ep.playhead, "rtf": self.rtf()}

    def rtf(self) -> float | None:
        return round(sum(self._rtf) / len(self._rtf), 3) if self._rtf else None

    def episode_count(self) -> int:
        return len(self._eps)

    # ---- worker ----
    def _evict_episodes(self) -> None:
        while len(self._eps) > self.MAX_EPISODES:
            oldest = min(self._eps.values(), key=lambda e: e.touched)
            del self._eps[oldest.key]

    def _prime_from_cache(self, ep: Episode) -> None:
        """After a (re)put, prepare every cue whose final audio is already on disk. Caller holds the lock, so
        this does header reads only; cues that still need an atempo pass are left to the worker."""
        for c in ep.cues:
            if self.cache.has(c.raw):
                try:
                    self._prepare(ep, c)
                except Exception:
                    log.warning("cached cue %s unusable; will resynthesize", c.idx)
        self._retime(ep, 0)

    def _next_job(self) -> tuple[Episode, Cue] | None:
        """Ahead of the playhead first (most recently active episode first), then wrap to the cues before
        it so a backward seek is a cache hit and the whole episode ends up cached."""
        with self._lock:
            eps = sorted(self._eps.values(), key=lambda e: e.touched, reverse=True)
        for ep in eps:
            i = ep.first_at(ep.playhead)
            for c in ep.cues[i:] + ep.cues[:i]:
                if not c.prepared and c.failed < self.MAX_FAILS:
                    return ep, c
        return None

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
        if not self.cache.has(c.raw):
            t0 = time.perf_counter()
            wav = self.tts.infer(c.text, voice=ep.voice, apply_watermark=False)
            dt = time.perf_counter() - t0
            if len(wav):
                self._rtf.append(dt / (len(wav) / self.tts.sample_rate))
            self.cache.store(c.raw, wav, self.tts.sample_rate)
        self._ensure_stretched(ep, c)            # ffmpeg, outside the registry lock
        with self._lock:
            if self._eps.get(ep.key) is not ep:   # episode replaced meanwhile: audio stays cached for the new one
                return
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

    def _retime(self, ep: Episode, pos: int) -> None:
        """Recompute delay/dropped from cue `pos` forward. An unprepared cue breaks the chain: the next prepared
        cue is timed as if nothing precedes it (corrected when the gap fills). Stops once a prepared cue that
        was already ready keeps its exact timing: everything after it depends only on it, so it is consistent.
        Unprepared cues never stop the walk (prepared-but-unready cues after them must still be timed)."""
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
            if unchanged and c.pos != pos:
                return

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
        """A user voice was deleted: episodes using it fall back to the default voice (resynth from playhead)."""
        with self._lock:
            affected = [(e.key, e) for e in self._eps.values() if e.voice == removed]
        for key, e in affected:
            self.put(key, None, [{"idx": c.idx, "start": c.start, "end": c.end, "text": c.text} for c in e.cues])

    @staticmethod
    def _played_end_before(ep: Episode, pos: int) -> float | None:
        """Media time at which the last played cue before `pos` ends; None if unknown or nothing plays."""
        for c in reversed(ep.cues[:pos]):
            if not c.prepared:
                return None
            if not c.dropped:
                return c.start + c.delay + c.play_dur
        return None
