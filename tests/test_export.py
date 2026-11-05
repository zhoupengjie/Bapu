"""Exporters round-trip (made-up lyrics)."""

from __future__ import annotations

import wave

import pytest

from bapu import svp
from bapu.export import midi, paste
from bapu.export import svp as svp_export

SCORE = {
    "tempo": {"bpm": 125.0, "time_signature": "4/4"},
    "lang": "yue",
    "notes": [
        {"start": 0.48, "dur": 0.4, "pitch": 60, "lyric": "天", "lang": "yue", "line": 1},
        {"start": 0.88, "dur": 0.40004, "pitch": 62, "lyric": "-", "lang": "yue", "line": 1},  # ends 0.04 ms late
        {"start": 1.28, "dur": 0.2, "pitch": 64, "lyric": "hello", "lang": "en", "line": 2},
        {"start": 1.48, "dur": 0.3, "pitch": 65, "lyric": "+", "lang": "en", "line": 2},
        {"start": 2.0, "dur": 0.5, "pitch": 67, "lyric": "海", "lang": "yue", "line": 3},
    ],
}


def test_midi_round_trip(tmp_path):
    p = midi.write(SCORE, tmp_path / "x.mid")
    back = midi.read_back(p)
    assert [n["lyric"] for n in back] == ["天", "-", "hello", "+", "海"]
    assert [n["pitch"] for n in back] == [60, 62, 64, 65, 67]
    k = 125 / 60 * midi.TPB
    assert all(abs(b["start"] / k - n["start"]) < 0.001 for b, n in zip(back, SCORE["notes"]))
    assert all(a["end"] <= b["start"] for a, b in zip(back, back[1:]))  # touching, never overlapping


def test_svp_round_trip_with_audio(tmp_path):
    wav = tmp_path / "inst.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 8000)
    p = svp_export.write(SCORE, tmp_path / "x.svp", audio_tracks=[("accompaniment", wav), ("lead dry", wav)], name="t")
    assert p.read_bytes().endswith(b"\x00")
    proj = svp.load_project(p)
    assert proj["version"] == 203 and proj["time"]["tempo"] == [{"position": 0, "bpm": 125.0}]
    notes = svp.vocal_notes(proj)
    assert [n.lyric for n in notes] == ["天", "-", "hello", "+", "海"]
    assert all(abs(a.start - b["start"]) < 1e-6 for a, b in zip(notes, SCORE["notes"]))
    assert all(a.start + a.dur <= b.start + 1e-9 for a, b in zip(notes, notes[1:]))
    langs = [n["attributes"]["languageOverride"] for n in proj["tracks"][0]["mainGroup"]["notes"]]
    assert langs == ["cantonese", "cantonese", "english", "english", "cantonese"]
    audio = proj["tracks"][1]["mainRef"]
    assert audio["isInstrumental"] and audio["audio"]["duration"] == pytest.approx(1.0)
    assert proj["tracks"][0]["mixer"]["fxParams"]["reverb"]["enabled"]
    assert not proj["tracks"][1]["mixer"]["fxParams"]["reverb"]["enabled"]
    assert [t["name"] for t in proj["tracks"]] == ["t vocal", "t accompaniment", "t lead dry"]


def test_paste_file(tmp_path):
    p = paste.write(SCORE, tmp_path / "x.txt")
    raw = p.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw
    assert raw.decode("utf-8-sig").splitlines() == ["天 -", "hello +", "海"]


def test_stage_exports_before_the_lyrics(tmp_path):
    """Audio-only and notes-only .svp and the stems come from the stage outputs alone."""
    import json

    from bapu import jobs
    from bapu.workdir import StageRecord, WorkDir

    wd = WorkDir(tmp_path / "work")
    wav = tmp_path / "work" / "02_lead" / "lead_dry.wav"
    wav.parent.mkdir(parents=True)
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 8000)
    (tmp_path / "work" / "01_separate").mkdir()
    inst = tmp_path / "work" / "01_separate" / "instrumental.wav"
    inst.write_bytes(wav.read_bytes())
    (tmp_path / "work" / "03_tempo").mkdir()
    (tmp_path / "work" / "03_tempo" / "tempo.json").write_text(json.dumps(
        {"bpm": 125.0, "first_beat": 0.5, "beat_phase_s": 0.02, "confidence": "high", "time_signature": "4/4"}))
    (tmp_path / "work" / "05_splits").mkdir()
    (tmp_path / "work" / "05_splits" / "notes.json").write_text(json.dumps(
        {"notes": [{"start": 0.5, "dur": 0.4, "pitch": 60.3}, {"start": 0.9, "dur": 0.5, "pitch": 62.0}]}))
    wd.set_sources({"source": (tmp_path / "demo.wav", "x")})
    wd.record("separate", StageRecord(key="k", dir="01_separate", outputs={"instrumental": "01_separate/instrumental.wav"}))
    wd.record("lead", StageRecord(key="k", dir="02_lead", outputs={"lead_dry": "02_lead/lead_dry.wav"}))
    wd.record("tempo", StageRecord(key="k", dir="03_tempo", outputs={"tempo": "03_tempo/tempo.json"}))

    with pytest.raises(jobs.JobError):  # no score yet: the full export is refused
        jobs.export(wd, ["svp"])
    with pytest.raises(jobs.JobError):  # no merged notes yet either
        jobs.export(wd, ["svp-notes"])
    written = jobs.export(wd, ["svp-audio", "wav"])
    assert [(f, p.name) for f, p in written] == [("svp-audio", "demo 音轨.svp"), ("wav", "demo 主唱干声.wav"), ("wav", "demo 伴奏.wav")]
    proj = svp.load_project(written[0][1])
    assert proj["time"]["tempo"] == [{"position": 0, "bpm": 125.0}]
    assert [t["name"] for t in proj["tracks"]] == ["demo vocal", "demo accompaniment", "demo lead dry"]
    assert proj["tracks"][0]["mainGroup"]["notes"] == [] and proj["tracks"][2]["mainRef"]["audio"]["filename"].endswith("lead_dry.wav")
    assert written[1][1].read_bytes() == wav.read_bytes()

    wd.record("splits", StageRecord(key="k", dir="05_splits", outputs={"merged_notes": "05_splits/notes.json"}))
    (f, p), = jobs.export(wd, ["svp-notes"], grid=16)
    notes = svp.vocal_notes(svp.load_project(p))
    assert [(n.pitch, n.lyric) for n in notes] == [(60, "la"), (62, "la")]
    assert svp.load_project(p)["tracks"][1]["mainRef"]["blickOffset"] > 0  # the audio moved with the grid
