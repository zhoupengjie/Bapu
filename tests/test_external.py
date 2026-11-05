"""Notes supplied by the user (SV project / MIDI) in place of the transcription (made-up lyrics)."""

from __future__ import annotations

import json

import pytest

from bapu import external, svp
from bapu.export import midi

Q = svp.QUARTER


def note(beat: float, beats: float, pitch: int, lyric: str = "la") -> dict:
    return {"onset": int(beat * Q), "duration": int(beats * Q), "pitch": pitch, "lyrics": lyric}


def track(name: str, notes: list[dict] | None = None, audio: str | None = None, offset: int = 0) -> dict:
    ref: dict = {"blickOffset": offset, "pitchOffset": 0}
    if audio:
        ref["audio"] = {"filename": audio, "duration": 10.0}
    return {"name": name, "mainGroup": {"notes": notes or []}, "mainRef": ref, "groups": []}


def project(*tracks: dict, bpm: float = 120.0) -> dict:
    return {"version": 203, "time": {"meter": [], "tempo": [{"position": 0, "bpm": bpm}]}, "library": [],
            "tracks": list(tracks)}


def write(tmp_path, p: dict, name: str = "x.svp"):
    f = tmp_path / name
    f.write_bytes(json.dumps(p, ensure_ascii=False).encode("utf-8") + b"\x00")
    return f


def test_svp_notes_move_back_by_the_dry_lead_offset(tmp_path):
    # SV2 extracted notes over a dry-lead track that sits 0.25 s late (one eighth at 120 BPM)
    p = project(track("t accompaniment", audio="D:/x/instrumental.wav", offset=Q // 2),
                track("t lead dry", audio="D:/x/lead_dry.wav", offset=Q // 2),
                track("extracted", [note(0.5, 1, 60, "天"), note(1.5, 0.5, 62, "空")]))
    out = external.notes_from_file(write(tmp_path, p), tmp_path / "inputs")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert out.name == "x.notes.json" and data["model"] == "external:svp"
    assert data["track"] == 3 and data["offset_s"] == pytest.approx(0.25)
    assert [(n["start"], n["dur"], n["pitch"]) for n in data["notes"]] == [(0.0, 0.5, 60), (0.5, 0.25, 62)]
    assert "lyrics" not in out.read_text(encoding="utf-8") and "天" not in out.read_text(encoding="utf-8")


def test_svp_without_audio_keeps_times_and_trims_overlaps(tmp_path):
    p = project(track("v", [note(0, 1, 60), note(0.5, 1, 64), note(2, 0.001, 65)]))
    notes, used, offset = external.notes_from_svp(p)
    assert used == 1 and offset == 0.0
    assert notes == [{"start": 0.0, "dur": 0.25, "pitch": 60}, {"start": 0.25, "dur": 0.5, "pitch": 64}]


def test_several_tracks_with_notes_need_a_choice(tmp_path):
    p = project(track("Bapu vocal", [note(0, 1, 60)]), track("lead dry", audio="lead_dry.wav"),
                track("extracted", [note(0, 1, 61), note(1, 1, 62)]))
    with pytest.raises(external.AmbiguousTrack) as e:
        external.notes_from_svp(p)
    assert [(t["index"], t["notes"], t["audio"]) for t in e.value.tracks] == [(1, 1, False), (2, 0, True), (3, 2, False)]
    notes, used, _ = external.notes_from_svp(p, track=3)
    assert used == 3 and [n["pitch"] for n in notes] == [61, 62]
    with pytest.raises(external.ExternalError):
        external.notes_from_svp(p, track=2)
    with pytest.raises(external.ExternalError):
        external.notes_from_svp(project(track("empty")))


def test_midi_notes(tmp_path):
    score = {"tempo": {"bpm": 125.0, "time_signature": "4/4"}, "lang": "yue", "notes": [
        {"start": 0.48, "dur": 0.4, "pitch": 60, "lyric": "天", "lang": "yue", "line": 1},
        {"start": 0.88, "dur": 0.4, "pitch": 62, "lyric": "-", "lang": "yue", "line": 1},
    ]}
    f = midi.write(score, tmp_path / "x.mid")
    out = external.notes_from_file(f, tmp_path / "inputs")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["kind"] == "midi" and [n["pitch"] for n in data["notes"]] == [60, 62]
    assert data["notes"][0]["start"] == pytest.approx(0.48, abs=0.002)
    assert external.describe(out) == {"from": "x.mid", "kind": "midi", "track": None, "offset_s": None, "bpm": None, "notes": 2}
    with pytest.raises(external.ExternalError):
        external.notes_from_file(tmp_path / "x.txt", tmp_path / "inputs")


def test_svp_notes_carry_the_project_tempo(tmp_path):
    p = project(track("v", [note(0, 1, 60)]), bpm=98.5)
    p["time"]["meter"] = [{"index": 0, "numerator": 3, "denominator": 4}]
    out = external.notes_from_file(write(tmp_path, p), tmp_path / "inputs")
    d = external.describe(out)
    assert d["bpm"] == 98.5
    assert json.loads(out.read_text(encoding="utf-8"))["time_signature"] == "3/4"


def test_tempo_file_and_audio_passthrough(tmp_path):
    t = external.tempo_file(tmp_path / "inputs", 125, time_signature="4/4", origin="user")
    data = json.loads(t.read_text(encoding="utf-8"))
    assert t.name == "tempo.tempo.json" and data["bpm"] == 125 and data["confidence"] == "user" and data["beat_phase_s"] == 0.0
    assert external.describe_tempo(t) == {"bpm": 125, "time_signature": "4/4", "origin": "user"}
    with pytest.raises(external.ExternalError):
        external.tempo_file(tmp_path / "inputs", 1000)
    # a WAV is used as it is; something unreadable is an error, not a silent copy
    import wave

    wav = tmp_path / "lead.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 800)
    assert external.audio_from_file(wav, tmp_path / "inputs") == wav
    bad = tmp_path / "x.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(external.ExternalError):
        external.audio_from_file(bad, tmp_path / "inputs")
