import numpy as np
import pytest

from voxsync_server import stretch
from voxsync_server.cache import DiskCache, raw_key, stretched_key
from voxsync_server.normalize import normalize


@pytest.mark.parametrize("raw,want", [
    ("Tôi là ai?", "Tôi là ai?"),
    ("<i>Hòa bình</i> cho thời đại chúng ta.", "Hòa bình cho thời đại chúng ta."),
    ("[thở dài]", ""),                                          # SFX-only cue disappears
    ("Nghe hay quá đi [cười]. Tiếp nhé", "Nghe hay quá đi . Tiếp nhé"),
    ("- Được rồi. Chúng tôi đi đây.\n- Nào. Ra ngoài.", "Được rồi. Chúng tôi đi đây. Nào. Ra ngoài."),
    ("Tôi sinh ra từ xung đột...", "Tôi sinh ra từ xung đột"),
    ("...kẻ nhân tạo\nvà đầy bệnh hoạn.", "kẻ nhân tạo và đầy bệnh hoạn."),
    ("cho tới 50-50 nhé", "cho tới 50-50 nhé"),                 # a dash inside a line is not a dialogue marker
])
def test_normalize_real_cues(raw, want):
    assert normalize(raw) == want


def test_normalize_nfc():
    decomposed = "Hoa\u0300 bi\u0300nh"                       # combining grave accents
    assert normalize(decomposed) == "Hoà bình"


@pytest.mark.parametrize("dur,start,nxt,rate", [
    (1.0, 0.0, 2.0, 1.0),         # fits
    (2.0, 0.0, 2.0, 1.0),         # exactly fits: no stretch
    (2.4, 0.0, 2.0, 1.2),         # stretch within cap
    (2.5, 0.0, 2.0, 1.25),        # exactly at cap
    (3.0, 0.0, 2.0, 1.25),        # capped; the rest runs long
    (0.35, 0.0, 0.2, 1.0),        # too short to stretch
    (5.0, 0.0, None, 1.0),        # last cue never stretches
])
def test_decide_rate(dur, start, nxt, rate):
    assert stretch.decide_rate(dur, start, nxt) == pytest.approx(rate)


def test_delay_and_drop():
    assert stretch.delay_after(None, 5.0) == 0.0
    assert stretch.delay_after(4.0, 5.0) == 0.0                # gap absorbs: previous ended before we start
    assert stretch.delay_after(5.3, 5.0) == pytest.approx(0.3)
    assert not stretch.should_drop(0.5)
    assert stretch.should_drop(0.51)


def test_atempo_shortens_by_rate():
    sr = 48_000
    wav = (0.1 * np.sin(np.arange(sr * 2) / 20)).astype(np.float32)
    out = stretch.atempo(wav, sr, 1.25)
    assert len(out) / sr == pytest.approx(2 / 1.25, abs=0.03)


def test_cache_keys_separate_voice_model_and_rate():
    k = raw_key("xin chào", "Hải Đăng")
    assert k != raw_key("xin chào", "Thùy Dung")
    assert k != raw_key("xin chào", "Hải Đăng", model_rev="other")
    assert stretched_key(k, 1.0) == k
    assert stretched_key(k, 1.2) not in (k, stretched_key(k, 1.25))


def test_cache_roundtrip_corrupt_and_eviction(tmp_path):
    c = DiskCache(tmp_path, max_bytes=400_000)
    wav = np.zeros(48_000, dtype=np.float32)        # ~96 KB PCM16 each
    for i in range(6):
        c.store(f"{i:064x}", wav, 48_000)
    assert c.bytes <= 400_000                         # LRU eviction kept the budget
    assert c.has(f"{5:064x}") and not c.has(f"{0:064x}")
    c.path(f"{5:064x}").write_bytes(b"garbage")
    assert c.load(f"{5:064x}") is None and not c.has(f"{5:064x}")   # corrupt file removed for resynthesis
