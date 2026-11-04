"""Export the score as a single-track SMF with lyric events.

Times are converted from seconds with the score's tempo, without quantising.
Every note gets a lyric meta event at its onset, including `-` and `+`.
Lyric text is written as UTF-8.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import mido

TPB = 960  # ticks per quarter: ~0.5 ms at 125 BPM


def to_ticks(notes: list[dict[str, Any]], bpm: float, tpb: int = TPB) -> list[tuple[int, int]]:
    """(start, end) ticks per note; rounding never creates overlaps or empty notes."""
    k = bpm / 60.0 * tpb
    out = [[round(n["start"] * k), round((n["start"] + n["dur"]) * k)] for n in notes]
    for i, (s, e) in enumerate(out):
        if i + 1 < len(out):
            e = min(e, out[i + 1][0])
        out[i][1] = max(e, s + 1)
    return [(s, e) for s, e in out]


def write(score: dict[str, Any], path: Path, *, track_name: str = "Bapu vocal", tpb: int = TPB) -> Path:
    bpm = float(score["tempo"]["bpm"])
    num, den = (int(x) for x in str(score["tempo"].get("time_signature", "4/4")).split("/"))
    mf = mido.MidiFile(type=0, ticks_per_beat=tpb, charset="utf-8")
    tr = mido.MidiTrack()
    mf.tracks.append(tr)
    tr.append(mido.MetaMessage("track_name", name=track_name, time=0))
    tr.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))
    tr.append(mido.MetaMessage("time_signature", numerator=num, denominator=den, time=0))

    events: list[tuple[int, int, mido.Message | mido.MetaMessage]] = []
    for n, (s, e) in zip(score["notes"], to_ticks(score["notes"], bpm, tpb)):
        pitch = max(0, min(127, int(n["pitch"])))
        # Order at equal ticks: note_off (0) before lyric (1) before note_on (2).
        events.append((s, 1, mido.MetaMessage("lyrics", text=n["lyric"])))
        events.append((s, 2, mido.Message("note_on", note=pitch, velocity=100)))
        events.append((e, 0, mido.Message("note_off", note=pitch, velocity=0)))
    events.sort(key=lambda x: (x[0], x[1]))
    now = 0
    for tick, _, msg in events:
        tr.append(msg.copy(time=tick - now))
        now = tick
    tr.append(mido.MetaMessage("end_of_track", time=0))
    path.parent.mkdir(parents=True, exist_ok=True)
    mf.save(str(path))
    return path


def read_back(path: Path) -> list[dict[str, Any]]:
    """Notes with lyric, pitch and tick span, for checks."""
    mf = mido.MidiFile(str(path), charset="utf-8")
    notes, lyric, active, t = [], None, {}, 0
    for msg in mf.tracks[0]:
        t += msg.time
        if msg.type == "lyrics":
            lyric = msg.text
        elif msg.type == "note_on" and msg.velocity > 0:
            active[msg.note] = (t, lyric)
            lyric = None
        elif msg.type in ("note_off", "note_on") and msg.note in active:
            s, ly = active.pop(msg.note)
            notes.append({"start": s, "end": t, "pitch": msg.note, "lyric": ly})
    return sorted(notes, key=lambda n: n["start"])
