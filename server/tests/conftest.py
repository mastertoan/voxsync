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
        }
        self._default_voice = "Hải Đăng"

    def infer(self, text, voice=None, apply_watermark=True, **_):
        if voice not in self._preset_voices:
            raise ValueError(voice)
        self.calls.append((text, voice))
        time.sleep(self.delay)
        n = int(len(text) * SEC_PER_CHAR * SR)
        return (0.1 * np.sin(np.arange(n) / 20)).astype(np.float32)

    def encode_reference(self, path, denoise=True):
        return np.ones(192, dtype=np.float32), np.zeros((10, 16), dtype=np.int64)


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

    def _make(tts=None):
        tts = tts or FakeTTS()
        auth.set_token("t0ken")
        main.S.tts = None
        main.build(tts, DiskCache(home / "cache"), VoiceStore(tts, home / "voices.json"))
        c = TestClient(main.asgi, headers={"X-VoxSync-Token": "t0ken"})
        c.__enter__()
        clients.append(c)
        return c, tts

    yield _make
    for c in clients:
        c.__exit__(None, None, None)
    main.S.tts = None
