"""HLS playlist parsing and window slicing (no network: fetch is injected)."""
import numpy as np
import pytest

from voxsync_server import source
from voxsync_server.source import HlsSource, SourceError, parse_playlist

MASTER = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000\nlow/index.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=3500000\nhi/index.m3u8\n"
MEDIA = ("#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.0,\na.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:2.5,\n"
         "/abs/b.ts\n#EXTINF:4.0,\nhttps://cdn.test/c.ts\n#EXT-X-ENDLIST\n")


def test_master_picks_highest_bandwidth_and_media_resolves_urls():
    assert parse_playlist(MASTER, "https://h.test/v/index.m3u8") == ([], "https://h.test/v/hi/index.m3u8")
    segs, _ = parse_playlist(MEDIA, "https://h.test/v/hi/index.m3u8")
    assert [(s.start, s.dur, s.url) for s in segs] == [
        (0.0, 4.0, "https://h.test/v/hi/a.ts"), (4.0, 2.5, "https://h.test/abs/b.ts"), (6.5, 4.0, "https://cdn.test/c.ts")]


def test_rejects_encrypted_and_non_playlists():
    with pytest.raises(SourceError):
        parse_playlist("#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=\"k\"\n#EXTINF:4,\na.ts\n", "https://h.test/")
    with pytest.raises(SourceError):
        parse_playlist("<html>", "https://h.test/")


def test_window_spans_segment_boundaries(monkeypatch):
    # each segment decodes to a constant = its index, so the slice shows exactly which samples were taken
    urls = {"https://h.test/v/index.m3u8": MASTER.encode(), "https://h.test/v/hi/index.m3u8": MEDIA.encode()}
    order = ["https://h.test/v/hi/a.ts", "https://h.test/abs/b.ts", "https://cdn.test/c.ts"]
    durs = [4.0, 2.5, 4.0]
    for i, u in enumerate(order):
        urls[u] = str(i).encode()
    monkeypatch.setattr(source, "decode", lambda b: np.full(int(durs[int(b)] * source.SR), float(b), np.float32))
    fetched = []
    s = HlsSource("https://h.test/v/index.m3u8", fetch=lambda u, limit: fetched.append(u) or urls[u])
    w = s.window(3.0, 7.0)
    assert len(w) == 4 * source.SR
    assert (w[: source.SR] == 0).all() and (w[source.SR: int(3.5 * source.SR)] == 1).all() and (w[-source.SR // 2:] == 2).all()
    n = len(fetched)
    s.window(3.5, 6.0)                                    # cached segments: no refetch
    assert len(fetched) == n
    assert len(s.window(20.0, 21.0)) == 0                 # past the end


def test_rejects_non_https_and_private_hosts():
    for url in ("http://h.test/x.m3u8", "https://127.0.0.1/x.m3u8", "https://10.0.0.1/x.m3u8"):
        with pytest.raises(SourceError):
            source._check_url(url)


def test_align_drops_the_insert_that_explains_the_length_difference():
    from voxsync_server.source import Segment, align
    # movie: groups 0,2 (60 s + 40 s); group 1 is a 10 s insert; group 3 a 4 s banner segment (part of the movie)
    segs = [Segment(0, 30, "a", 0), Segment(30, 30, "b", 0), Segment(60, 10, "ad", 1), Segment(70, 20, "c", 2),
            Segment(90, 20, "d", 2), Segment(110, 4, "banner", 3)]
    out = align(segs, 104.0)
    assert [s.url for s in out] == ["a", "b", "c", "d", "banner"]
    assert [s.start for s in out] == [0, 30, 60, 80, 100]
    assert align(segs, 114.2) == segs                         # same length: untouched
    with pytest.raises(SourceError):
        align(segs, 120.0)                                    # stream shorter than the video
    with pytest.raises(SourceError):
        align(segs, 107.0)                                    # 7 s matches no run of inserts
    twin = segs + [Segment(114, 10, "ad2", 4), Segment(124, 5, "e", 5)]
    with pytest.raises(SourceError):
        align(twin, 109.0)                                    # two 10 s candidates: ambiguous → refuse
