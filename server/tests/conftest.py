"""Fake engine only: deterministic audio whose length is proportional to text (0.08 s/char), so stretch /
overflow / drop decisions are exercised for real. Everything else (cache, registry, routes, auth) is real."""
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

SR = 48_000
SEC_PER_CHAR = 0.08


class FakeTTS:
    sample_rate = SR

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.calls: list[tuple[str, str]] = []
        emb = np.zeros(192, dtype=np.float32)
        self._preset_voices = {
            "Hải Đăng": {"description": "Nam · Bắc · Phong cách tự nhiên", "gender": "male", "speaker_emb": emb, "codes": None},
            "Thùy Dung": {"description": "Nữ · Nam · Phong cách tin tức", "gender": "female", "speaker_emb": emb, "codes": None},
            "Quang Sơn": {"description": "Nam · Trung · Phong cách tự nhiên", "gender": "male", "speaker_emb": emb, "codes": None},
            "Ngọc Huyền": {"description": "Nữ · Bắc · Giọng đọc tự nhiên", "gender": "female", "speaker_emb": emb, "codes": None},
        }
        self._default_voice = "Hải Đăng"

    def infer(self, text, voice=None, apply_watermark=True, ref_audio=None, **_):
        if ref_audio is None and voice not in self._preset_voices:
            raise ValueError(voice)
        self.calls.append((text, f"ref:{ref_audio}" if ref_audio else voice))
        time.sleep(self.delay)
        n = int(len(text) * SEC_PER_CHAR * SR)
        return (0.1 * np.sin(np.arange(n) / 20)).astype(np.float32)

    def encode_reference(self, path, denoise=True):
        return np.ones(192, dtype=np.float32), np.zeros((10, 16), dtype=np.int64)


class FakeSource:
    """Original audio where the speaker is encoded in the signal: a sine whose frequency the FakeEmbedder maps
    to a speaker. `speakers` = list of (start, end, freq_hz)."""

    def __init__(self, speakers, fail=False):
        self.speakers, self.fail, self.url = speakers, fail, "https://example.test/x.m3u8"

    def window(self, t0, t1):
        if self.fail:
            raise OSError("source down")
        f = next((hz for s, e, hz in self.speakers if s <= (t0 + t1) / 2 < e), 0)
        n = int((t1 - t0) * 16_000)
        return (0.3 * np.sin(2 * np.pi * f * np.arange(n) / 16_000)).astype(np.float32)


class FakeEmbedder:
    """Embedding = one-hot of the dominant frequency bucket (+ tiny noise): same speaker ≈ 1, different ≈ 0.
    P(female) = 1 above 150 Hz."""

    def embed(self, pcm):
        spec = np.abs(np.fft.rfft(pcm))
        hz = np.argmax(spec) * 16_000 / len(pcm)
        v = np.zeros(16, dtype=np.float32)
        v[min(15, int(hz // 50))] = 1.0
        v += np.random.default_rng(int(hz)).normal(0, 0.01, 16).astype(np.float32)
        return v / np.linalg.norm(v), (1.0 if hz > 150 else 0.0)


def wait_for(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def home(tmp_path, monkeypatch):
    from voxsync_server import config
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "VOICES_FILE", tmp_path / "voices.json")
    return tmp_path


@pytest.fixture
def make_client(home):
    from voxsync_server import auth, main
    from voxsync_server.cache import DiskCache
    from voxsync_server.voices import VoiceStore

    clients = []

    def _make(tts=None, source=None):
        from voxsync_server.speakers import CastStore
        tts = tts or FakeTTS()
        auth.set_token("t0ken")
        main.S.tts = None
        main.build(tts, DiskCache(home / "cache"), VoiceStore(tts, home / "voices.json"), CastStore(home / "cast"),
                   FakeEmbedder, (lambda url, duration=None: source) if source else None)
        main.S.registry.refs_dir = home / "refs"
        c = TestClient(main.asgi, headers={"X-VoxSync-Token": "t0ken"})
        c.__enter__()
        clients.append(c)
        return c, tts

    yield _make
    for c in clients:
        c.__exit__(None, None, None)
    main.S.tts = None
