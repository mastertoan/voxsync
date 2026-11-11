import io

import numpy as np
import soundfile as sf

from conftest import SR, FakeTTS, wait_for

KEY = "ep_abcdef12"


def cues(n=6, gap=3.0, text="Câu thoại số"):
    return [{"idx": i, "start": i * gap, "end": i * gap + 2, "text": f"{text} {i}"} for i in range(n)]


def wav_bytes(seconds):
    buf = io.BytesIO()
    sf.write(buf, np.zeros(int(SR * seconds), dtype=np.float32), SR, format="WAV")
    return buf.getvalue()


def get_ready(c, key, idx, timeout=10):
    r = None

    def ok():
        nonlocal r
        r = c.get(f"/episodes/{key}/cues/{idx}")
        return r.status_code != 202
    assert wait_for(ok, timeout), f"cue {idx} never ready"
    return r


def test_token_required(make_client):
    c, _ = make_client()
    assert c.get("/health", headers={"X-VoxSync-Token": "wrong"}).status_code == 401
    assert c.get("/health", headers={"X-VoxSync-Token": ""}).status_code == 401
    assert c.get("/health").status_code == 200
    from fastapi.testclient import TestClient

    from voxsync_server import main
    assert TestClient(main.asgi).get("/health").status_code == 401          # no header at all


def test_voices_listing_parses_upstream_metadata(make_client):
    c, _ = make_client()
    body = c.get("/voices").json()
    assert body["default"] == "Hải Đăng"
    dung = next(v for v in body["voices"] if v["id"] == "Thùy Dung")
    assert dung == {"id": "Thùy Dung", "name": "Thùy Dung", "gender": "female", "region": "Nam",
                    "style": "Phong cách tin tức", "kind": "preset"}


def test_episode_synthesizes_in_order_and_serves_wav(make_client):
    c, tts = make_client()
    assert c.put(f"/episodes/{KEY}", json={"cues": cues()}).json() == {"cues": 6, "voice": "Hải Đăng"}
    r = get_ready(c, KEY, 0)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert sf.info(io.BytesIO(r.content)).samplerate == SR
    assert wait_for(lambda: c.get(f"/episodes/{KEY}/status").json()["ready"] == 6)
    assert [t for t, _ in tts.calls] == [f"Câu thoại số {i}" for i in range(6)]


def test_playhead_moves_cursor_first(make_client):
    c, tts = make_client(FakeTTS(delay=0.05))
    c.put(f"/episodes/{KEY}", json={"cues": cues(n=40)})
    c.post(f"/episodes/{KEY}/playhead", json={"t": 90.5})      # cue 30 starts at 90
    get_ready(c, KEY, 30)
    later = [t for t, _ in tts.calls[1:]]
    assert any(t.endswith(" 30") for t in later[:3]), "seek target must be synthesized right after the in-flight cue"


def test_cues_before_playhead_are_filled_after_ahead(make_client):
    c, tts = make_client(FakeTTS(delay=0.01))
    c.put(f"/episodes/{KEY}", json={"cues": cues(n=12)})
    c.post(f"/episodes/{KEY}/playhead", json={"t": 27})        # cue 9 is current
    assert wait_for(lambda: c.get(f"/episodes/{KEY}/status").json()["ready"] == 12)
    order = [int(t.rsplit(" ", 1)[1]) for t, _ in tts.calls]
    after_seek = order[order.index(9):]
    assert after_seek[:3] == [9, 10, 11] and set(after_seek[3:]) <= set(range(9))


def test_unknown_episode_404_and_idempotent_reput(make_client):
    c, tts = make_client()
    assert c.get(f"/episodes/{KEY}/cues/0").json() == {"code": "unknown_episode"}
    assert c.get(f"/episodes/{KEY}/cues/0").status_code == 404
    c.put(f"/episodes/{KEY}", json={"cues": cues()})
    assert wait_for(lambda: c.get(f"/episodes/{KEY}/status").json()["ready"] == 6)
    c.post(f"/episodes/{KEY}/playhead", json={"t": 9})
    from voxsync_server import main
    before = main.S.registry.get(KEY)
    mtimes = {p: p.stat().st_mtime_ns for p in main.S.cache.root.rglob("*.wav")}
    n = len(tts.calls)
    c.put(f"/episodes/{KEY}", json={"cues": cues()})            # extension reconnect
    st = c.get(f"/episodes/{KEY}/status").json()
    assert st["playhead"] == 9 and st["ready"] == 6 and len(tts.calls) == n
    assert main.S.registry.get(KEY) is before                   # same episode state, no rebuild/re-finalize
    assert {p: p.stat().st_mtime_ns for p in main.S.cache.root.rglob("*.wav")} == mtimes


def test_restart_reuses_disk_cache(make_client):
    c, _ = make_client()
    c.put(f"/episodes/{KEY}", json={"cues": cues()})
    assert wait_for(lambda: c.get(f"/episodes/{KEY}/status").json()["ready"] == 6)
    c2, tts2 = make_client()                                    # fresh registry, same cache dir
    assert c2.get(f"/episodes/{KEY}/status").status_code == 404
    c2.put(f"/episodes/{KEY}", json={"cues": cues()})
    assert c2.get(f"/episodes/{KEY}/status").json()["ready"] == 6
    assert get_ready(c2, KEY, 3).status_code == 200 and tts2.calls == []


def test_restart_with_partial_cache_serves_cached_cues_immediately(make_client):
    """Only cues 0-1 and 5-9 were cached in a previous session (gap of 3 missing cues). After restart and
    re-PUT, cached cues past the gap must be served at once, without waiting for the gap to be synthesized."""
    import threading

    from voxsync_server import main
    from voxsync_server.cache import raw_key
    from voxsync_server.normalize import normalize
    gate = threading.Event()

    class Stalled(FakeTTS):                                    # worker blocks on any synthesis until released
        def infer(self, text, voice=None, **kw):
            gate.wait(5)
            return super().infer(text, voice, **kw)

    c, tts = make_client(Stalled())
    cs = cues(10)
    for i in [0, 1, 5, 6, 7, 8, 9]:
        text = normalize(cs[i]["text"])
        main.S.cache.store(raw_key(text, "Hải Đăng"), FakeTTS().infer(text, "Hải Đăng"), SR)
    c.put(f"/episodes/{KEY}", json={"cues": cs})
    c.post(f"/episodes/{KEY}/playhead", json={"t": 18})        # cue 6
    assert [c.get(f"/episodes/{KEY}/cues/{i}").status_code for i in (5, 6, 7, 8, 9)] == [200] * 5
    gate.set()



def test_voice_change_resynthesizes_from_playhead(make_client):
    c, tts = make_client()
    c.put(f"/episodes/{KEY}", json={"cues": cues()})
    assert wait_for(lambda: c.get(f"/episodes/{KEY}/status").json()["ready"] == 6)
    c.post(f"/episodes/{KEY}/playhead", json={"t": 7})          # cue 2 is playing
    c.put(f"/episodes/{KEY}", json={"voice": "Thùy Dung", "cues": cues()})
    assert wait_for(lambda: c.get(f"/episodes/{KEY}/status").json()["readyAhead"] == 4)
    redone = [t for t, v in tts.calls if v == "Thùy Dung"]
    assert redone[0] == "Câu thoại số 2"


def timeline(c, key, idxs):
    out = {}
    for i in idxs:
        r = get_ready(c, key, i)
        out[i] = (r.status_code, float(r.headers.get("X-Delay", 0)), r.headers.get("X-Stretch"))
    return out


def test_timeline_stretch_delay_absorbed_by_gaps(make_client):
    """0.08 s/char.
    cue0: 40 chars = 3.2 s in a 2 s slot → 1.25x → plays 0..2.56 → cue1 (start 2) delayed 0.56 → dropped.
    cue2: 25 chars = 2.0 s in a 1.5 s slot → 1.25x → plays 6..7.6 → cue3 (start 7.5) delayed 0.1, kept.
    cue4 (start 12) is past the end of cue3 → delay 0: the gap absorbs lateness."""
    c, _ = make_client()
    cs = [{"idx": 0, "start": 0, "end": 2, "text": "x" * 40},
          {"idx": 1, "start": 2, "end": 3, "text": "ngắn"},
          {"idx": 2, "start": 6, "end": 7, "text": "a" * 25},
          {"idx": 3, "start": 7.5, "end": 9, "text": "b" * 4},        # 0.32 s, too short to stretch
          {"idx": 4, "start": 12, "end": 13, "text": "c" * 10}]
    c.put(f"/episodes/{KEY}", json={"cues": cs})
    t = timeline(c, KEY, range(5))
    assert t[0][0] == 200 and t[0][2] == "1.250"
    assert t[1][0] == 204 and t[1][1] > 0.5                         # dropped whole, not truncated
    assert t[2] == (200, 0.0, "1.250")                              # dropped cue does not push the timeline
    assert t[3][0] == 200 and t[3][1] == __import__("pytest").approx(0.1, abs=0.01)
    assert t[4][1] == 0.0                                           # lateness never leaks past a gap


def test_retime_after_backward_fill(make_client):
    """Cue 1's audio is already cached (e.g. watched earlier), so it is prepared at PUT with delay 0 while cue 0
    is unknown. When cue 0 is synthesized (3.2 s → 1.25x → ends 2.56) cue 1 must be corrected to delay 0.56
    → dropped."""
    import threading

    from voxsync_server import main
    from voxsync_server.cache import raw_key
    gate = threading.Event()

    class Gated(FakeTTS):
        def infer(self, text, voice=None, **kw):
            if text.startswith("x"):
                gate.wait(5)
            return super().infer(text, voice, **kw)

    c, tts = make_client(Gated())
    main.S.cache.store(raw_key("y" * 10, "Hải Đăng"), tts.__class__.__mro__[1].infer(tts, "y" * 10, "Hải Đăng"), SR)
    tts.calls.clear()
    cs = [{"idx": 0, "start": 0, "end": 2, "text": "x" * 40},
          {"idx": 1, "start": 2, "end": 4, "text": "y" * 10}]
    c.put(f"/episodes/{KEY}", json={"cues": cs})
    r = c.get(f"/episodes/{KEY}/cues/1")
    assert r.status_code == 200 and r.headers["X-Delay"] == "0.000"   # predecessor unknown yet
    gate.set()
    assert wait_for(lambda: c.get(f"/episodes/{KEY}/cues/1").status_code == 204)
    assert float(c.get(f"/episodes/{KEY}/cues/1").headers["X-Delay"]) == __import__("pytest").approx(0.56, abs=0.01)


def test_sfx_only_cue_is_204_not_404(make_client):
    c, _ = make_client()
    c.put(f"/episodes/{KEY}", json={"cues": [{"idx": 0, "start": 0, "end": 1, "text": "[thở dài]"},
                                            {"idx": 1, "start": 2, "end": 3, "text": "Ừ"}]})
    r = c.get(f"/episodes/{KEY}/cues/0")
    assert r.status_code == 204 and r.headers["X-Reason"] == "empty"
    assert c.get(f"/episodes/{KEY}/cues/99").json() == {"code": "unknown_cue"}


def test_corrupt_cached_audio_fails_instead_of_spinning(make_client):
    from voxsync_server import main
    from voxsync_server.cache import raw_key
    c, tts = make_client()
    p = main.S.cache.path(raw_key("Hỏng", "Hải Đăng"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")                                             # truncated file from a crash
    c.put(f"/episodes/{KEY}", json={"cues": [{"idx": 0, "start": 0, "end": 1, "text": "Hỏng"}]})
    r = get_ready(c, KEY, 0)
    assert r.status_code == 200 and tts.calls == [("Hỏng", "Hải Đăng")]   # discarded and resynthesized


def test_synth_failure_is_410_after_retries(make_client):
    class Broken(FakeTTS):
        def infer(self, text, voice=None, **kw):
            if text == "Hỏng":
                raise RuntimeError("boom")
            return super().infer(text, voice, **kw)
    c, _ = make_client(Broken())
    c.put(f"/episodes/{KEY}", json={"cues": [{"idx": 0, "start": 0, "end": 1, "text": "Hỏng"},
                                            {"idx": 1, "start": 2, "end": 3, "text": "Tốt"}]})
    assert wait_for(lambda: c.get(f"/episodes/{KEY}/cues/0").status_code == 410)
    assert get_ready(c, KEY, 1).status_code == 200                  # one bad cue doesn't block the rest


def test_evicted_audio_is_resynthesized(make_client):
    from voxsync_server import main
    c, tts = make_client()
    c.put(f"/episodes/{KEY}", json={"cues": cues(1)})
    get_ready(c, KEY, 0)
    for p in main.S.cache.root.rglob("*.wav"):
        p.unlink()
    assert c.get(f"/episodes/{KEY}/cues/0").status_code == 202
    assert get_ready(c, KEY, 0).status_code == 200 and len(tts.calls) == 2


def test_deleting_voice_in_use_falls_back_to_default(make_client):
    c, tts = make_client()
    v = c.post("/voices", data={"name": "Bố", "consent": "true"},
               files={"clip": ("v.wav", wav_bytes(5), "audio/wav")}).json()
    tts._preset_voices[v["id"]]["description"] = ""
    c.put(f"/episodes/{KEY}", json={"voice": v["id"], "cues": cues(2)})
    get_ready(c, KEY, 1)
    assert c.delete(f"/voices/{v['id']}").status_code == 204
    assert c.get(f"/episodes/{KEY}/status").json()["voice"] == "Hải Đăng"
    assert get_ready(c, KEY, 1).status_code == 200


def test_clone_requires_consent_and_persists(make_client):
    c, tts = make_client()
    files = {"clip": ("v.wav", wav_bytes(5), "audio/wav")}
    assert c.post("/voices", data={"name": "Bố", "consent": "false"}, files=files).json()["code"] == "consent_required"
    assert c.post("/voices", data={"name": "Ngắn", "consent": "true"},
                  files={"clip": ("v.wav", wav_bytes(1), "audio/wav")}).json()["code"] == "clip_duration"
    v = c.post("/voices", data={"name": "Bố", "consent": "true"}, files=files).json()
    assert v["kind"] == "user" and v["id"].startswith("u_")
    assert c.patch(f"/voices/{v['id']}", json={"name": "Ba"}).json()["name"] == "Ba"
    assert c.patch("/voices/Hải Đăng", json={"name": "x"}).status_code == 403
    c2, _ = make_client()                                       # restart: user voice reloaded
    assert any(x["id"] == v["id"] and x["name"] == "Ba" for x in c2.get("/voices").json()["voices"])
    assert c2.delete(f"/voices/{v['id']}").status_code == 204
    assert c2.post(f"/voices/{v['id']}/preview").status_code == 404


def test_preview_returns_wav(make_client):
    c, _ = make_client()
    r = c.post("/voices/Thùy Dung/preview", json={"text": "Thử giọng"})
    assert r.status_code == 200 and sf.info(io.BytesIO(r.content)).duration > 0


def test_input_validation(make_client):
    c, _ = make_client()
    assert c.put("/episodes/bad", json={"cues": []}).status_code == 422          # key pattern
    dup = [{"idx": 0, "start": 0, "end": 1, "text": "a"}] * 2
    assert c.put(f"/episodes/{KEY}", json={"cues": dup}).status_code == 422
    assert c.put(f"/episodes/{KEY}", json={"cues": [{"idx": 0, "start": 5, "end": 1, "text": "a"}]}).status_code == 422
    assert c.put(f"/episodes/{KEY}", json={"voice": "không tồn tại", "cues": cues(1)}).json()["voice"] == "Hải Đăng"
    assert c.post(f"/episodes/{KEY}/playhead", content='{"t": Infinity}',
                  headers={"content-type": "application/json"}).status_code == 422


def test_body_cap_and_auth_before_body(make_client):
    c, _ = make_client()
    big = b"x" * (2 * 1024**2 + 10)
    assert c.put(f"/episodes/{KEY}", content=big, headers={"content-type": "application/json"}).status_code == 413

    def chunks():                                                   # no Content-Length: chunked transfer
        for _ in range(3):
            yield b"x" * (1024**2)
    assert c.put(f"/episodes/{KEY}", content=chunks(), headers={"content-type": "application/json"}).status_code == 413
    assert c.put(f"/episodes/{KEY}", content=chunks(),
                 headers={"content-type": "application/json", "X-VoxSync-Token": "nope"}).status_code == 401
    assert c.get("/health", headers={"content-length": "abc"}).status_code == 413
    assert c.get("/health", headers={"X-VoxSync-Token": "tóken".encode()}).status_code == 401   # non-ASCII: 401 not 500
