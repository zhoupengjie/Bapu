"""Lining the export up with SV's grid: beats on beat lines, notes snapped, audio moved with them."""

from __future__ import annotations

import numpy as np
import soundfile as sf

from bapu.export import grid, midi
from bapu.export import svp as svp_export
from bapu.svp import QUARTER


def score(notes, bpm=120.0, phase=0.1):
    return {"tempo": {"bpm": bpm, "time_signature": "4/4", "beat_phase_s": phase}, "lang": "yue",
            "notes": [{"start": s, "dur": d, "pitch": 60, "lyric": "a", "lang": "yue", "line": 1} for s, d in notes]}


def test_shift_puts_the_beats_on_beat_lines():
    assert abs(grid.shift(score([], phase=0.1)) - 0.4) < 1e-9  # 120 BPM: beats at 0.1, 0.6 … -> 0.5, 1.0 …
    assert abs(grid.shift(score([], phase=-0.2)) - 0.2) < 1e-9
    assert grid.shift(score([], phase=0.0)) == 0.0


def test_notes_on_beats_land_on_beat_lines_and_snap():
    # 120 BPM, beats at 0.1 + k*0.5; a 1/16 note is 0.125 s
    sc = score([(0.1, 0.5), (0.62, 0.11), (1.1, 0.24), (1.6, 0.9)])
    out, off = grid.align(sc, 16)
    assert abs(off - 0.4) < 1e-9
    starts = [n["start"] for n in out["notes"]]
    assert abs(starts[0] - 0.5) < 1e-9 and abs(starts[2] - 1.5) < 1e-9 and abs(starts[3] - 2.0) < 1e-9
    assert abs(starts[1] - 1.0) < 1e-9  # 0.62 + 0.4 = 1.02 -> 1.0
    for n in out["notes"]:
        steps = n["start"] / 0.125, n["dur"] / 0.125
        assert all(abs(x - round(x)) < 1e-9 for x in steps) and n["dur"] > 0


def test_snapping_keeps_notes_apart():
    # three notes inside one 1/16 step
    out, _ = grid.align(score([(1.00, 0.03), (1.03, 0.03), (1.06, 0.03)], phase=0.0), 16)
    spans = [(round(n["start"] / 0.125), round((n["start"] + n["dur"]) / 0.125)) for n in out["notes"]]
    assert spans == [(8, 9), (9, 10), (10, 11)]
    for (a0, a1), (b0, _) in zip(spans, spans[1:]):
        assert a1 <= b0 and a1 > a0


def test_svp_audio_moves_with_the_notes(tmp_path):
    wav = tmp_path / "acc.wav"
    sf.write(str(wav), np.zeros(4410), 44100)
    sc = score([(0.1, 0.5), (0.6, 0.5)])
    p = svp_export.build(sc, audio_tracks=[("accompaniment", wav)], grid=16)
    notes = p["tracks"][0]["mainGroup"]["notes"]
    assert [n["onset"] for n in notes] == [QUARTER, 2 * QUARTER]  # on beats 2 and 3 (0-based 1 and 2)
    assert p["tracks"][1]["mainRef"]["blickOffset"] == round(0.4 * 2 * QUARTER)  # 0.4 s at 120 BPM
    plain = svp_export.build(sc, audio_tracks=[("accompaniment", wav)])
    assert plain["tracks"][1]["mainRef"]["blickOffset"] == 0
    assert [n["onset"] for n in plain["tracks"][0]["mainGroup"]["notes"]] == [round(0.2 * QUARTER), round(1.2 * QUARTER)]


def test_midi_snaps_to_the_grid(tmp_path):
    p = midi.write(score([(0.1, 0.5), (0.62, 0.11)]), tmp_path / "a.mid", grid=16)
    back = midi.read_back(p)
    step = midi.TPB // 4
    assert all(n["start"] % step == 0 and n["end"] % step == 0 for n in back)
    assert back[0]["start"] == midi.TPB
