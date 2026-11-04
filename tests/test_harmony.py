"""Harmony lyrics and exports (made-up lyrics)."""

from __future__ import annotations

import numpy as np

from bapu.export import midi
from bapu.export import svp as svp_export
from bapu import svp
from bapu.stages.backing import note_levels
from bapu.stages.harmony import assign_lyrics, word_ids

LEAD = [  # 天 - 空 | hello + | 海
    {"start": 0.0, "dur": 0.5, "pitch": 64, "kind": "word", "lyric": "天", "lang": "yue", "line": 1},
    {"start": 0.5, "dur": 0.5, "pitch": 62, "kind": "sustain", "lyric": "-", "lang": "yue", "line": 1},
    {"start": 1.0, "dur": 0.5, "pitch": 64, "kind": "word", "lyric": "空", "lang": "yue", "line": 1},
    {"start": 2.0, "dur": 0.4, "pitch": 67, "kind": "word", "lyric": "hello", "lang": "en", "line": 2},
    {"start": 2.4, "dur": 0.4, "pitch": 65, "kind": "cont", "lyric": "+", "lang": "en", "line": 2},
    {"start": 4.0, "dur": 0.5, "pitch": 60, "kind": "word", "lyric": "海", "lang": "yue", "line": 3},
]
KW = dict(tolerance=0.06, join_gap=0.1, alone_lyric="啊", default_lang="yue")


def b(start, dur, pitch):
    return {"start": start, "dur": dur, "pitch": float(pitch)}


def test_word_ids():
    assert word_ids(LEAD) == [0, 0, 1, 2, 2, 3]


def test_copies_the_lead_syllable_and_keeps_its_language():
    out = assign_lyrics([b(0.0, 0.5, 60), b(0.5, 0.5, 59), b(1.02, 0.4, 60), b(2.0, 0.4, 64), b(2.4, 0.4, 62)], LEAD, **KW)
    assert [n["lyric"] for n in out] == ["天", "-", "空", "hello", "+"]
    assert [n["lang"] for n in out] == ["yue", "yue", "yue", "en", "en"]
    assert out[0]["interval"] == -4 and not any(n["flags"] for n in out)


def test_melisma_late_onset_alone_and_octave():
    out = assign_lyrics([
        b(1.0, 0.2, 60), b(1.2, 0.3, 62),   # second note starts inside 空, right after: "-"
        b(4.2, 0.3, 57),                     # starts late in 海 after a gap: the word, flagged
        b(3.0, 0.3, 70)][::1], LEAD, **KW)
    out = sorted(out, key=lambda n: n["start"])
    assert [n["lyric"] for n in out[:2]] == ["空", "-"]
    alone = next(n for n in out if n["start"] == 3.0)
    assert alone["lyric"] == "啊" and "alone" in alone["flags"]
    late = next(n for n in out if n["start"] == 4.2)
    assert late["lyric"] == "海" and "late_onset" in late["flags"]
    far = assign_lyrics([b(0.0, 0.5, 40)], LEAD, **KW)[0]
    assert "octave" in far["flags"]


def test_a_dash_without_its_word_gets_the_word():
    # The harmony enters on the lead's "-" (0.5 s) without having sung 天.
    out = assign_lyrics([b(0.5, 0.5, 58)], LEAD, **KW)
    assert out[0]["lyric"] == "天" and "partial_word" in out[0]["flags"]


def test_note_levels():
    sr = 1000
    y = np.concatenate([np.zeros(500), 0.1 * np.ones(500)]).astype(np.float32)
    quiet, loud = note_levels([{"start": 0.0, "dur": 0.4}, {"start": 0.6, "dur": 0.3}], y, sr)
    assert quiet < -100 and abs(loud - (-20.0)) < 0.1


def test_exports_carry_the_harmony_track(tmp_path):
    score = {"tempo": {"bpm": 120.0, "time_signature": "4/4"}, "lang": "yue",
             "notes": [dict(n) for n in LEAD], "harmony": assign_lyrics([b(0.0, 0.5, 60), b(1.0, 0.5, 60)], LEAD, **KW)}
    p = svp_export.write(score, tmp_path / "x.svp", name="t")
    proj = svp.load_project(p)
    assert [t["name"] for t in proj["tracks"]] == ["t vocal", "t 和声"]
    assert [n.lyric for n in svp.vocal_notes(proj, 2)] == ["天", "空"]
    m = midi.write(score, tmp_path / "x.mid")
    assert len(midi.read_back(m, 0)) == 6 and [n["lyric"] for n in midi.read_back(m, 1)] == ["天", "空"]
    plain = svp_export.write(score, tmp_path / "y.svp", name="t", harmony=False)
    assert len(svp.load_project(plain)["tracks"]) == 1
