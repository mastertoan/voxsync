"""Original-audio reader for an HLS VOD stream of the movie the user is watching.

Used to tell characters apart (speaker embedding per cue) and, in clone mode, as the voice reference.

The stream is the site's other server (KK, `link_m3u8` in the page data), not the one playing, so its timeline
must be mapped onto `video.currentTime`. Measured on vision tập 1: KK = 3236.18 s, playing video = 3201.38 s,
and KK has one discontinuity-bounded insert of 34.7 s at 15:00 (a black/sponsor block). Banner segments earlier
in the stream are the movie re-encoded with an overlay and keep their place. So: if the durations differ, drop
the run of discontinuity groups whose length matches the difference; if none matches, the stream is unusable.
"""
import ipaddress
import logging
import socket
import subprocess
import threading
import urllib.parse
import urllib.request
from bisect import bisect_right
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np

log = logging.getLogger("voxsync.source")

SR = 16_000
MAX_PLAYLIST_BYTES = 2 * 1024**2
MAX_SEGMENT_BYTES = 16 * 1024**2
TIMEOUT = 15
SEG_CACHE = 48                       # decoded segments kept (~6 min of audio, ~18 MB)
PREFETCH = 6                         # segments fetched ahead of the last window (~25 s)
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150 Safari/537.36"


class SourceError(Exception):
    pass


def _check_url(url: str) -> None:
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or not u.hostname:
        raise SourceError("https_only")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or 443, proto=socket.IPPROTO_TCP)
    except OSError as e:
        raise SourceError(f"dns: {e}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise SourceError("non_public_address")


def _get(url: str, limit: int) -> bytes:
    _check_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://chophim.lol/"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        _check_url(r.geturl())                       # redirects must stay public too
        data = r.read(limit + 1)
    if len(data) > limit:
        raise SourceError("too_large")
    return data


@dataclass
class Segment:
    start: float          # on the playing video's timeline (after removing inserts)
    dur: float
    url: str
    group: int = 0        # discontinuity group (raw playlist)


def parse_playlist(text: str, base: str) -> tuple[list[Segment], str | None]:
    """Media playlist → segments; master playlist → (no segments, URL of the highest-bandwidth variant)."""
    lines = [l.strip() for l in text.splitlines()]
    if not lines or lines[0] != "#EXTM3U":
        raise SourceError("not_m3u8")
    best, best_bw = None, -1
    segs, t, dur, group = [], 0.0, None, 0
    for i, l in enumerate(lines):
        if l.startswith("#EXT-X-STREAM-INF:"):
            bw = int(next((p.split("=")[1] for p in l.split(":", 1)[1].split(",") if p.startswith("BANDWIDTH=")), 0))
            nxt = next((x for x in lines[i + 1:] if x and not x.startswith("#")), None)
            if nxt and bw > best_bw:
                best, best_bw = urllib.parse.urljoin(base, nxt), bw
        elif l.startswith("#EXT-X-DISCONTINUITY") and segs:
            group += 1
        elif l.startswith("#EXTINF:"):
            dur = float(l[8:].split(",")[0])
        elif l.startswith("#EXT-X-KEY:") and "METHOD=NONE" not in l:
            raise SourceError("encrypted")
        elif l and not l.startswith("#") and dur is not None:
            segs.append(Segment(t, dur, urllib.parse.urljoin(base, l), group))
            t += dur
            dur = None
    if segs:
        return segs, None
    if best:
        return [], best
    raise SourceError("empty_playlist")


ALIGN_TOL = 0.6           # s; EXTINF rounding over a whole episode


def align(segs: list[Segment], duration: float | None) -> list[Segment]:
    """Map the stream onto the playing video's timeline (see module doc). Raises when it cannot be done."""
    total = sum(s.dur for s in segs)
    if duration is None or abs(total - duration) <= ALIGN_TOL:
        return segs
    diff = total - duration
    if diff < 0:
        raise SourceError(f"stream shorter than video by {-diff:.1f}s")
    groups: dict[int, float] = {}
    for s in segs:
        groups[s.group] = groups.get(s.group, 0.0) + s.dur
    ids = sorted(groups)
    hits = []
    for i in range(1, len(ids)):                     # never the first group (the movie starts there)
        acc = 0.0
        for j in range(i, min(i + 12, len(ids))):   # a run of up to 12 consecutive groups
            acc += groups[ids[j]]
            if abs(acc - diff) <= ALIGN_TOL:
                hits.append(set(ids[i:j + 1]))
            if acc > diff + ALIGN_TOL:
                break
    if len(hits) != 1:
        raise SourceError(f"cannot align stream ({len(hits)} candidate inserts for {diff:.1f}s)")
    out, t = [], 0.0
    for s in segs:
        if s.group in hits[0]:
            continue
        out.append(Segment(t, s.dur, s.url, s.group))
        t += s.dur
    return out


def decode(ts: bytes) -> np.ndarray:
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le",
                        "pipe:1"], input=ts, capture_output=True, timeout=30, check=True)
    return np.frombuffer(p.stdout, dtype=np.float32)


class HlsSource:
    """Lazy: the playlist is fetched on first use. Thread-safe. `duration` = the playing video's duration."""

    def __init__(self, url: str, duration: float | None = None, fetch=_get):
        self.url, self.duration, self._fetch = url, duration, fetch
        self._segs: list[Segment] | None = None
        self._starts: list[float] = []
        self._pcm: OrderedDict[int, np.ndarray] = OrderedDict()
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="voxsync-hls")
        self._pending: dict[int, Future] = {}

    def _segments(self) -> list[Segment]:
        with self._lock:
            if self._segs is None:
                url = self.url
                for _ in range(3):                   # master → variant (one hop expected)
                    segs, variant = parse_playlist(self._fetch(url, MAX_PLAYLIST_BYTES).decode("utf-8", "replace"), url)
                    if segs:
                        break
                    url = variant
                else:
                    raise SourceError("playlist_loop")
                self._segs = align(segs, self.duration)
                self._starts = [s.start for s in self._segs]
            return self._segs

    def _segment_pcm(self, i: int) -> np.ndarray:
        with self._lock:
            if i in self._pcm:
                self._pcm.move_to_end(i)
                return self._pcm[i]
            fut = self._pending.get(i)
            if fut is None:
                fut = self._pending[i] = self._pool.submit(self._load, i)
        return fut.result()

    def _load(self, i: int) -> np.ndarray:
        try:
            pcm = decode(self._fetch(self._segs[i].url, MAX_SEGMENT_BYTES))
            with self._lock:
                self._pcm[i] = pcm
                while len(self._pcm) > SEG_CACHE:
                    self._pcm.popitem(last=False)
            return pcm
        finally:
            with self._lock:
                self._pending.pop(i, None)

    def window(self, t0: float, t1: float) -> np.ndarray:
        """16 kHz mono float32 of the movie between t0 and t1 (playing video's time). Also starts fetching the
        next PREFETCH segments in the background: the listener walks forward through the episode."""
        segs = self._segments()
        if t1 <= t0 or t0 >= segs[-1].start + segs[-1].dur:
            return np.zeros(0, dtype=np.float32)
        first = max(0, bisect_right(self._starts, t0) - 1)
        last = first
        while last + 1 < len(segs) and segs[last + 1].start < t1:
            last += 1
        with self._lock:
            for i in range(last + 1, min(len(segs), last + 1 + PREFETCH)):
                if i not in self._pcm and i not in self._pending:
                    self._pending[i] = self._pool.submit(self._load, i)
        out = []
        for i in range(first, last + 1):
            s = segs[i]
            pcm = self._segment_pcm(i)
            a = max(0, int((t0 - s.start) * SR))
            b = min(len(pcm), int((t1 - s.start) * SR))
            if b > a:
                out.append(pcm[a:b])
        return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)
