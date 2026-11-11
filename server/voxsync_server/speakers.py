"""Who is speaking: per-cue speaker embedding (3D-Speaker ERes2Net via sherpa-onnx) + a per-title cast.

Spike (Phase 6, tập 1, 163 cues, `phase-06-multi-voice-emotion.md`):
- gender: audeering wav2vec2 age/gender (6 layers, ONNX): 22/22 hand-labelled lines right. Median pitch called
  the male lead "female" (his excited lines sit at 180-210 Hz), so pitch is not used;
- average-linkage cosine clustering at distance 0.75: 15 clusters, top-4 cover 85%, the 2-person scene 15/16;
- online nearest-centroid assignment fragmented the same people into 40 clusters, so it is NOT used;
- reclustering a 60% prefix vs everything: matching centroids 0.95-1.0, others ≤ 0.61 → MATCH 0.8.
- WeSpeaker ResNet34 collapsed everything into one cluster: not an option.

Characters are stable ids over time: each recluster maps new clusters to existing characters by centroid
similarity (greedy, ≥ MATCH); unmatched clusters become new characters. The top MAX_MAIN characters of each
episode (≥ MIN_MAIN lines) become "main" and keep their own preset voice for good; everyone else shares one male
or female voice. User overrides win. Casts persist per title, so later episodes reuse earlier characters.
"""
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import config

log = logging.getLogger("voxsync.speakers")

SR = 16_000
MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
             "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx")
MODEL_SHA256 = "1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b"
# audeering w2v2-L-robust-6-age-gender (CC BY-NC-SA 4.0: personal, non-commercial use), ONNX export
GENDER_URL = "https://huggingface.co/ykk648/audio_lib/resolve/main/age_gender/w2v2-L-robust-6-age-gender.onnx"
GENDER_SHA256 = "75c5cc3debc2013215cee5f331a66b59bd7205da170fb61abab46fc4507df7be"
CLUSTER_DIST = 0.75
MATCH = 0.8              # cluster centroid ↔ character centroid (same person 0.95-1.0, others ≤ 0.61)
MIN_MAIN = 3
LINE_MATCH = 0.5         # single line ↔ character centroid (own p10 0.49, best other p90 0.43)
LINE_MARGIN = 0.05
MAX_MAIN = 4
MAX_LINES = 3000          # embeddings kept per title (oldest dropped); recluster is O(n²) memory
GENDER_CONF = 0.6         # |P(female)-0.5|*2 above which two lines of opposite gender may not share a cluster
EMBED_SECONDS = 0.8       # shorter lines are padded around their middle to this length
# Natural-style presets first; pool order decides who gets which voice.
MALE_POOL = ["Hải Đăng", "Quang Sơn", "Phạm Tuyên", "Xuân Vĩnh", "Quốc Tuấn"]
FEMALE_POOL = ["Ngọc Huyền", "Trúc Ly", "Đoan Trang", "Ngọc Trân", "Ngọc Linh"]
MALE_SHARED, FEMALE_SHARED = "Adam bựa", "Mai Anh"
TITLE_RE = re.compile(r"^[a-z0-9-]{1,120}$")


def _ensure(name: str, url: str, sha256: str, max_bytes: int, models_dir: Path) -> Path:
    p = models_dir / "speaker" / name
    if p.is_file():
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    log.info("downloading %s → %s", url, p)
    h, tmp = hashlib.sha256(), p.with_suffix(".tmp")
    with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
        n = 0
        while chunk := r.read(1 << 20):
            n += len(chunk)
            if n > max_bytes:
                raise RuntimeError(f"{name}: download too large")
            h.update(chunk)
            f.write(chunk)
    if h.hexdigest() != sha256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"{name}: checksum mismatch")
    os.replace(tmp, p)
    return p


def ensure_model(models_dir: Path = config.MODELS_DIR) -> Path:
    return _ensure("eres2net_base_16k.onnx", MODEL_URL, MODEL_SHA256, 64 * 1024**2, models_dir)


def ensure_gender_model(models_dir: Path = config.MODELS_DIR) -> Path:
    return _ensure("w2v2-L-robust-6-age-gender.onnx", GENDER_URL, GENDER_SHA256, 400 * 1024**2, models_dir)


class Embedder:
    """Per line: speaker embedding (who) + P(female) (which voice pool). Child voices count as female-range."""

    def __init__(self, model: Path | None = None, gender_model: Path | None = None):
        import onnxruntime as ort
        import sherpa_onnx
        self._ex = sherpa_onnx.SpeakerEmbeddingExtractor(
            sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model or ensure_model()), num_threads=2))
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        self._g = ort.InferenceSession(str(gender_model or ensure_gender_model()), opts,
                                       providers=["CPUExecutionProvider"])
        self._lock = threading.Lock()

    def embed(self, pcm: np.ndarray) -> tuple[np.ndarray, float]:
        with self._lock:
            st = self._ex.create_stream()
            st.accept_waveform(SR, pcm)
            st.input_finished()
            v = np.asarray(self._ex.compute(st), dtype=np.float32)
            x = ((pcm - pcm.mean()) / (pcm.std() + 1e-7)).astype(np.float32)
            logits = self._g.run(["logits_gender"], {"signal": x[None]})[0][0]
        p = np.exp(logits - logits.max())
        p /= p.sum()
        return v / (np.linalg.norm(v) or 1.0), float(p[0] + p[2])


def _distances(X: np.ndarray, lines: list) -> np.ndarray:
    """Condensed cosine distances; two lines both confidently of opposite gender are pushed apart (+1) so a male
    and a female voice never share a character."""
    from scipy.spatial.distance import pdist, squareform
    D = squareform(pdist(X, "cosine"))
    pf = np.array([0.5 if l.pf is None else l.pf for l in lines])
    conf = np.abs(pf - 0.5) * 2
    apart = ((pf[:, None] >= 0.5) != (pf[None, :] >= 0.5)) & (np.minimum(conf[:, None], conf[None, :]) > GENDER_CONF)
    D = D + apart
    np.fill_diagonal(D, 0.0)
    return squareform(D, checks=False)


@dataclass
class Character:
    id: int
    centroid: np.ndarray
    n: int = 0
    pf: float | None = None      # mean P(female) of its lines
    auto: str | None = None      # dedicated preset (main characters only); frozen once set
    voice: str | None = None     # user override

    @property
    def gender(self) -> str:
        return "" if self.pf is None else ("female" if self.pf >= 0.5 else "male")


@dataclass
class Line:
    key: str                     # "<episode key>:<cue idx>"
    emb: np.ndarray
    pf: float | None             # P(female)
    char: int = -1


class Cast:
    """One title's characters. Thread-safe; save() is atomic."""

    def __init__(self, title: str, root: Path):
        self.title, self.dir = title, root / title
        self.chars: dict[int, Character] = {}
        self.lines: dict[str, Line] = {}
        self.next_id = 0
        self.version = 0          # bumps whenever any line's character may have changed
        self._lock = threading.RLock()
        self._dirty = False
        self._load()

    # ---- persistence ----
    def _load(self) -> None:
        p = self.dir / "cast.json"
        if not p.is_file():
            return
        try:
            d = json.loads(p.read_text("utf-8"))
            self.next_id = d["next_id"]
            for c in d["chars"]:
                self.chars[c["id"]] = Character(c["id"], np.asarray(c["centroid"], dtype=np.float32), c["n"],
                                                c.get("pf"), c.get("auto"), c.get("voice"))
            emb = np.load(self.dir / "lines.npy") if (self.dir / "lines.npy").is_file() else None
            for i, l in enumerate(d.get("lines", [])):
                if emb is not None and i < len(emb):
                    self.lines[l["k"]] = Line(l["k"], emb[i], l.get("pf"), l.get("c", -1))
        except Exception:
            log.exception("cast for %s unreadable; starting fresh", self.title)
            self.chars, self.lines, self.next_id = {}, {}, 0

    def save(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            self.dir.mkdir(parents=True, exist_ok=True)
            lines = list(self.lines.values())
            if lines:
                fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".npy.tmp")
                with os.fdopen(fd, "wb") as f:
                    np.save(f, np.stack([l.emb for l in lines]).astype(np.float16))
                os.replace(tmp, self.dir / "lines.npy")
            d = {"next_id": self.next_id,
                 "chars": [{"id": c.id, "centroid": c.centroid.round(4).tolist(), "n": c.n, "pf": c.pf, "auto": c.auto,
                            "voice": c.voice} for c in self.chars.values()],
                 "lines": [{"k": l.key, "pf": l.pf, "c": l.char} for l in lines]}
            fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".json.tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(d, f)
            os.replace(tmp, self.dir / "cast.json")
            self._dirty = False

    # ---- lines ----
    def has(self, key: str) -> bool:
        return key in self.lines

    def char_of(self, key: str) -> int | None:
        l = self.lines.get(key)
        return l.char if l and l.char >= 0 else None

    def add(self, key: str, emb: np.ndarray, pf: float | None) -> None:
        """Record one line. Assigns it to the closest existing character (≥ MATCH) right away so it can be voiced
        before the next recluster."""
        with self._lock:
            line = Line(key, emb.astype(np.float32), pf)
            sims = sorted(((float(line.emb @ c.centroid), c.id) for c in self.chars.values() if c.n), reverse=True)
            if sims and sims[0][0] >= LINE_MATCH and (len(sims) == 1 or sims[0][0] - sims[1][0] >= LINE_MARGIN):
                line.char = sims[0][1]
            self.lines[key] = line
            while len(self.lines) > MAX_LINES:
                self.lines.pop(next(iter(self.lines)))
            self._dirty = True

    def recluster(self) -> set[int]:
        """Cluster all lines, map clusters to stable character ids, promote main characters.
        Returns the characters whose membership changed."""
        from scipy.cluster.hierarchy import fcluster, linkage
        with self._lock:
            lines = list(self.lines.values())
            if len(lines) < 2:
                return set()
            X = np.stack([l.emb for l in lines])
            lab = fcluster(linkage(_distances(X, lines), "average"), t=CLUSTER_DIST, criterion="distance")
            groups: dict[int, list[int]] = {}
            for i, k in enumerate(lab):
                groups.setdefault(int(k), []).append(i)
            cents = {k: X[ix].mean(0) for k, ix in groups.items()}
            cents = {k: v / (np.linalg.norm(v) or 1.0) for k, v in cents.items()}
            # greedy match: biggest clusters claim their best existing character first
            mapping, taken = {}, set()
            for k in sorted(groups, key=lambda k: -len(groups[k])):
                best, sim = None, MATCH
                for c in self.chars.values():
                    if c.id in taken:
                        continue
                    s = float(cents[k] @ c.centroid)
                    if s >= sim:
                        best, sim = c.id, s
                if best is None:
                    best = self.next_id
                    self.next_id += 1
                    self.chars[best] = Character(best, cents[k])
                mapping[k] = best
                taken.add(best)
            changed = set()
            for k, ix in groups.items():
                c = self.chars[mapping[k]]
                c.centroid, c.n = cents[k], len(ix)
                pfs = [lines[i].pf for i in ix if lines[i].pf is not None]
                c.pf = round(float(np.mean(pfs)), 3) if pfs else None
                for i in ix:
                    if lines[i].char != c.id:
                        changed.add(c.id)
                        if lines[i].char >= 0:
                            changed.add(lines[i].char)
                        lines[i].char = c.id
            for c in self.chars.values():
                if c.id not in taken:
                    c.n = 0
                    c.auto = None          # merged away: free its main slot (the override, if any, stays)
            # mains: the top MAX_MAIN characters (≥ MIN_MAIN lines) of *each episode* (line keys are
            # "<episode>:<cue>"), so a series' later leads get their own voice too. Once main, always main with the
            # same voice (only a gender flip re-picks it); dedicated voices are bounded by the pools.
            per_ep: dict[str, dict[int, int]] = {}
            for l in lines:
                if l.char >= 0:
                    counts = per_ep.setdefault(l.key.rsplit(":", 1)[0], {})
                    counts[l.char] = counts.get(l.char, 0) + 1
            leads = {cid for counts in per_ep.values()
                     for cid, n in sorted(counts.items(), key=lambda kv: -kv[1])[:MAX_MAIN] if n >= MIN_MAIN}
            for c in sorted(self.chars.values(), key=lambda c: -c.n):
                if c.auto is None and c.id not in leads:
                    continue
                pool = FEMALE_POOL if c.gender == "female" else MALE_POOL
                if c.auto is None or c.auto not in pool:
                    used = {x.auto for x in self.chars.values() if x is not c} | {x.voice for x in self.chars.values()}
                    free = [v for v in pool if v not in used]
                    if not free and c.auto is None:
                        continue                   # pool exhausted: stays on the shared voice
                    c.auto = free[0] if free else pool[0]
                    changed.add(c.id)
            if changed:
                self.version += 1
            self._dirty = True
            return changed

    # ---- voices ----
    def voice_for(self, cid: int | None, default: str, resolve) -> str:
        """Character → voice id. Override > dedicated main voice > shared voice of the character's gender >
        the episode default (unknown character or unknown gender)."""
        c = self.chars.get(cid) if cid is not None else None
        if c is None:
            return default
        for v in (c.voice, c.auto):
            if v and resolve(v) == v:
                return v
        if c.gender:
            v = FEMALE_SHARED if c.gender == "female" else MALE_SHARED
            if resolve(v) == v:
                return v
        return default

    def set_voice(self, cid: int, voice: str | None) -> None:
        with self._lock:
            self.chars[cid].voice = voice
            self.version += 1
            self._dirty = True

    def public(self, voice_of) -> list[dict]:
        with self._lock:
            return [{"id": c.id, "lines": c.n, "gender": c.gender, "main": c.auto is not None,
                     "voice": voice_of(c.id), "override": c.voice}
                    for c in sorted(self.chars.values(), key=lambda c: -c.n) if c.n > 0]


class CastStore:
    def __init__(self, root: Path | None = None):
        self.root = root or config.HOME / "cast"
        self._casts: dict[str, Cast] = {}
        self._lock = threading.Lock()

    def get(self, title: str) -> Cast:
        if not TITLE_RE.match(title):
            raise ValueError("bad title")
        with self._lock:
            if title not in self._casts:
                self._casts[title] = Cast(title, self.root)
            return self._casts[title]

    def save_all(self) -> None:
        with self._lock:
            casts = list(self._casts.values())
        for c in casts:
            try:
                c.save()
            except Exception:
                log.exception("saving cast %s failed", c.title)
