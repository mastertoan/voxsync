"""Content-addressed WAV cache on disk. Keys never depend on episode identity, so audio survives
server restarts and is shared across episodes/titles that repeat a line with the same voice."""
import hashlib
import os
import threading
from pathlib import Path

import numpy as np
import soundfile as sf

from . import config
from .normalize import NORM_VERSION


def raw_key(text_norm: str, voice_id: str, model_rev: str = config.TTS_REVISION) -> str:
    return hashlib.sha256(f"{text_norm}|{voice_id}|{model_rev}|{NORM_VERSION}".encode()).hexdigest()


def stretched_key(raw: str, rate: float) -> str:
    return raw if rate == 1.0 else hashlib.sha256(f"{raw}|atempo={rate:.3f}".encode()).hexdigest()


class DiskCache:
    def __init__(self, root: Path = config.CACHE_DIR, max_bytes: int = config.CACHE_MAX_BYTES):
        self.root, self.max_bytes = root, max_bytes
        self.root.mkdir(parents=True, exist_ok=True)
        for tmp in self.root.rglob("*.tmp"):        # leftovers from a crash mid-write
            tmp.unlink(missing_ok=True)
        self._lock = threading.Lock()
        self._bytes = sum(p.stat().st_size for p in self.root.rglob("*.wav"))

    def path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.wav"

    def has(self, key: str) -> bool:
        return self.path(key).is_file()

    def read_bytes(self, key: str) -> bytes | None:
        p = self.path(key)
        try:
            data = p.read_bytes()
            os.utime(p)                      # LRU by mtime
            return data
        except FileNotFoundError:
            return None

    def load(self, key: str) -> tuple[np.ndarray, int] | None:
        p = self.path(key)
        if not p.is_file():
            return None
        try:
            wav, sr = sf.read(p, dtype="float32")
            os.utime(p)
            return wav, sr
        except Exception:                    # corrupt file: drop and let the caller resynthesize
            self.discard(key)
            return None

    def duration(self, key: str) -> float | None:
        try:
            return sf.info(self.path(key)).duration
        except Exception:
            return None

    def store(self, key: str, wav: np.ndarray, sr: int) -> None:
        p = self.path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".{threading.get_ident()}.tmp")
        sf.write(tmp, wav, sr, subtype="PCM_16", format="WAV")
        size = tmp.stat().st_size
        with self._lock:
            try:
                old = p.stat().st_size                # overwrite: don't double count
            except FileNotFoundError:
                old = 0
            os.replace(tmp, p)                        # atomic: readers never see a partial file
            self._bytes += size - old
            if self._bytes > self.max_bytes:
                self._evict()

    def discard(self, key: str) -> None:
        p = self.path(key)
        try:
            size = p.stat().st_size
            p.unlink()
            with self._lock:
                self._bytes -= size
        except FileNotFoundError:
            pass

    def _evict(self) -> None:
        files = sorted(self.root.rglob("*.wav"), key=lambda p: p.stat().st_mtime)
        target = int(self.max_bytes * 0.9)
        for p in files:
            if self._bytes <= target:
                break
            try:
                size = p.stat().st_size
                p.unlink()
                self._bytes -= size
            except FileNotFoundError:
                pass

    @property
    def bytes(self) -> int:
        return self._bytes
