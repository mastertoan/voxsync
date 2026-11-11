"""Cast: stable character ids across reclusters, main-voice assignment, persistence."""
import numpy as np

from voxsync_server import speakers
from voxsync_server.speakers import Cast


def emb(axis, jitter=0.0, seed=0):
    v = np.zeros(8, dtype=np.float32)
    v[axis] = 1.0
    v += np.random.default_rng(seed).normal(0, jitter, 8).astype(np.float32)
    return v / np.linalg.norm(v)


def test_ids_survive_recluster_and_reload(tmp_path):
    cast = Cast("t", tmp_path)
    for i in range(4):
        cast.add(f"e:{i}", emb(0, 0.05, i), 0.02)           # male
        cast.add(f"e:{10 + i}", emb(1, 0.05, i), 0.98)      # female
    cast.recluster()
    a, b = cast.char_of("e:0"), cast.char_of("e:10")
    assert a is not None and b is not None and a != b
    assert cast.chars[a].gender == "male" and cast.chars[b].gender == "female"
    assert cast.chars[a].auto in speakers.MALE_POOL and cast.chars[b].auto in speakers.FEMALE_POOL
    # a third speaker appears: existing characters keep their ids and voices
    voices = {a: cast.chars[a].auto, b: cast.chars[b].auto}
    for i in range(3):
        cast.add(f"e:{20 + i}", emb(2, 0.05, i), 0.03)
    cast.recluster()
    assert cast.char_of("e:0") == a and cast.char_of("e:10") == b
    assert {a: cast.chars[a].auto, b: cast.chars[b].auto} == voices
    c = cast.char_of("e:20")
    assert c not in (a, b)
    # a new line is matched to its character immediately, before any recluster
    cast.add("e:99", emb(1, 0.05, 9), 0.97)
    assert cast.char_of("e:99") == b
    cast.set_voice(b, "Thùy Dung")
    cast.save()
    again = Cast("t", tmp_path)
    assert again.char_of("e:10") == b and again.chars[b].voice == "Thùy Dung" and again.chars[a].auto == voices[a]


def test_opposite_genders_never_share_a_character(tmp_path):
    # same speaker embedding, confident opposite genders → two characters
    cast = Cast("t", tmp_path)
    for i in range(3):
        cast.add(f"m:{i}", emb(0, 0.02, i), 0.02)
        cast.add(f"f:{i}", emb(0, 0.02, 10 + i), 0.98)
    cast.recluster()
    assert cast.char_of("m:0") != cast.char_of("f:0")
    assert {cast.chars[cast.char_of("m:0")].gender, cast.chars[cast.char_of("f:0")].gender} == {"male", "female"}


def test_voice_resolution_order(tmp_path):
    cast = Cast("t", tmp_path)
    for i in range(3):
        cast.add(f"e:{i}", emb(0, 0.05, i), 0.02)
    cast.add("e:9", emb(3, 0.05, 1), 0.99)                   # one-line female extra
    cast.recluster()
    main_, extra = cast.char_of("e:0"), cast.char_of("e:9")
    ok = lambda v: v                                          # every voice exists
    assert cast.voice_for(main_, "DEF", ok) == cast.chars[main_].auto
    assert cast.voice_for(extra, "DEF", ok) == speakers.FEMALE_SHARED
    assert cast.voice_for(None, "DEF", ok) == "DEF"
    cast.set_voice(extra, "Mine")
    assert cast.voice_for(extra, "DEF", ok) == "Mine"
    assert cast.voice_for(extra, "DEF", lambda v: "DEF" if v == "Mine" else v) == speakers.FEMALE_SHARED  # deleted


def test_each_episode_gets_its_own_leads_and_earlier_voices_stay(tmp_path):
    cast = Cast("t", tmp_path)
    for spk in range(4):                                      # episode A: 4 male leads fill MAX_MAIN
        for i in range(4):
            cast.add(f"A:{spk * 10 + i}", emb(spk, 0.02, spk * 10 + i), 0.02)
    cast.recluster()
    a_voices = {cast.char_of(f"A:{spk * 10}"): cast.chars[cast.char_of(f"A:{spk * 10}")].auto for spk in range(4)}
    assert all(a_voices.values())
    for i in range(5):                                        # episode B: a new female lead
        cast.add(f"B:{i}", emb(5, 0.02, 100 + i), 0.98)
    cast.recluster()
    lead = cast.chars[cast.char_of("B:0")]
    assert lead.auto in speakers.FEMALE_POOL
    assert {cid: cast.chars[cid].auto for cid in a_voices} == a_voices
