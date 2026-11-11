"""Multi-voice modes with a fake original-audio source: two speakers (100 Hz male, 200 Hz female) alternate."""
import pytest

from conftest import FakeSource, FakeTTS, wait_for

KEY = "ep_chars0001"
TITLE = "phim-thu"
MEDIA = "https://example.test/x.m3u8"
MALE, FEMALE = 100, 200


@pytest.fixture(autouse=True)
def small_pools(monkeypatch):
    from voxsync_server import speakers
    monkeypatch.setattr(speakers, "MALE_POOL", ["Quang Sơn"])
    monkeypatch.setattr(speakers, "FEMALE_POOL", ["Ngọc Huyền"])
    monkeypatch.setattr(speakers, "MALE_SHARED", "Hải Đăng")
    monkeypatch.setattr(speakers, "FEMALE_SHARED", "Thùy Dung")


def dialogue(n=8, gap=3.0):
    """Cue i is spoken by MALE when i is even, FEMALE when odd."""
    cues = [{"idx": i, "start": i * gap, "end": i * gap + 2, "text": f"Câu số {i}"} for i in range(n)]
    spk = [(c["start"] - 0.5, c["end"] + 0.5, MALE if c["idx"] % 2 == 0 else FEMALE) for c in cues]
    return cues, spk


def put(c, cues, mode="characters", key=KEY, **kw):
    return c.put(f"/episodes/{key}", json={"cues": cues, "mode": mode, "title": TITLE, "media": MEDIA, **kw})


def voices_used(tts):
    return {text: voice for text, voice in tts.calls}


def test_each_character_gets_its_own_voice(make_client):
    cues, spk = dialogue()
    c, tts = make_client(source=FakeSource(spk))
    assert put(c, cues).json()["mode"] == "characters"
    assert wait_for(lambda: c.get(f"/episodes/{KEY}/status").json()["ready"] == 8)
    v = voices_used(tts)
    # after 3 lines each speaker is a main character with a dedicated voice of the right gender
    assert {v[f"Câu số {i}"] for i in range(4, 8, 2)} == {"Quang Sơn"}
    assert {v[f"Câu số {i}"] for i in range(5, 8, 2)} == {"Ngọc Huyền"}
    chars = c.get(f"/episodes/{KEY}/characters").json()["characters"]
    assert sorted((ch["gender"], ch["voice"]) for ch in chars) == [("female", "Ngọc Huyền"), ("male", "Quang Sơn")]


def test_early_lines_are_revoiced_once_the_character_is_known(make_client):
    cues, spk = dialogue()
    c, tts = make_client(source=FakeSource(spk))
    put(c, cues)
    assert wait_for(lambda: c.get(f"/episodes/{KEY}/status").json()["ready"] == 8)
    # the first male line was voiced before "Quang Sơn" was assigned; it must end up with the main voice
    assert wait_for(lambda: ("Câu số 0", "Quang Sơn") in tts.calls)
    r = c.get(f"/episodes/{KEY}/cues/0")
    assert r.status_code == 200


def test_user_override_changes_only_that_character(make_client):
    cues, spk = dialogue()
    c, tts = make_client(source=FakeSource(spk))
    put(c, cues)
    assert wait_for(lambda: c.get(f"/episodes/{KEY}/status").json()["ready"] == 8)
    chars = c.get(f"/episodes/{KEY}/characters").json()["characters"]
    female = next(ch for ch in chars if ch["gender"] == "female")
    assert c.put(f"/titles/{TITLE}/characters/{female['id']}", json={"voice": "Thùy Dung"}).status_code == 200
    assert wait_for(lambda: c.get(f"/episodes/{KEY}/status").json()["ready"] == 8)
    assert wait_for(lambda: all(("Câu số %d" % i, "Thùy Dung") in tts.calls for i in (1, 3, 5, 7)))
    male_calls = [v for t, v in tts.calls if t in ("Câu số 6",)]
    assert male_calls[-1] == "Quang Sơn"                       # the other character is untouched
    assert c.put(f"/titles/{TITLE}/characters/999", json={"voice": "Thùy Dung"}).status_code == 404
    assert c.put(f"/titles/{TITLE}/characters/{female['id']}", json={"voice": "nope"}).status_code == 404


def test_cast_carries_over_to_the_next_episode(make_client):
    cues, spk = dialogue()
    src = FakeSource(spk)
    c, tts = make_client(source=src)
    put(c, cues)
    assert wait_for(lambda: c.get(f"/episodes/{KEY}/status").json()["ready"] == 8)
    female = next(ch for ch in c.get(f"/episodes/{KEY}/characters").json()["characters"] if ch["gender"] == "female")
    c.put(f"/titles/{TITLE}/characters/{female['id']}", json={"voice": "Thùy Dung"})
    # episode 2 (same title): the female speaker's very first line already uses the user's pick
    src.speakers = [(0.0, 10.0, FEMALE)]
    put(c, [{"idx": 0, "start": 1.0, "end": 3.0, "text": "Tập hai bắt đầu"}], key="ep_chars0002")
    assert wait_for(lambda: c.get("/episodes/ep_chars0002/status").json()["ready"] == 1)
    assert [v for t, v in tts.calls if t == "Tập hai bắt đầu"] == ["Thùy Dung"]


def test_source_failure_falls_back_to_one_voice(make_client):
    cues, spk = dialogue(4)
    c, tts = make_client(source=FakeSource(spk, fail=True))
    put(c, cues, voice="Hải Đăng")
    assert wait_for(lambda: c.get(f"/episodes/{KEY}/status").json()["ready"] == 4)
    st = c.get(f"/episodes/{KEY}/status").json()
    assert st["sourceError"] and {v for _, v in tts.calls} == {"Hải Đăng"}


def test_clone_requires_consent_and_clones_each_character_from_its_own_audio(make_client):
    cues, spk = dialogue(8)
    c, tts = make_client(source=FakeSource(spk))
    assert put(c, cues, mode="clone").status_code == 400
    assert put(c, cues, mode="clone", cloneConsent=True).json()["mode"] == "clone"
    assert wait_for(lambda: c.get(f"/episodes/{KEY}/status").json()["ready"] == 8)
    final = {}
    for text, v in tts.calls:                                 # last synthesis of each line wins
        final[text] = v
    male = {final[f"Câu số {i}"] for i in range(0, 8, 2)}
    female = {final[f"Câu số {i}"] for i in range(1, 8, 2)}
    assert len(male) == len(female) == 1                      # one cloned voice per character
    assert male != female and all(v.startswith("ref:") for v in male | female)


def test_multi_voice_mode_needs_media(make_client):
    cues, _ = dialogue(2)
    c, _ = make_client()
    r = c.put(f"/episodes/{KEY}", json={"cues": cues, "mode": "characters", "title": TITLE})
    assert r.json()["mode"] == "single"
    assert c.put(f"/episodes/{KEY}", json={"cues": cues, "mode": "characters", "title": TITLE,
                                           "media": "http://x/y.m3u8"}).status_code == 422
    assert c.put(f"/episodes/{KEY}", json={"cues": cues, "mode": "characters", "title": "../etc",
                                           "media": MEDIA}).status_code == 422
