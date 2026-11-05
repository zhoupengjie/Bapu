"""Files the user supplies in place of a stage's output.

- Notes: a Synthesizer V project (`.svp`, for example one where SV 2 extracted
  the pitch from the dry lead) or a MIDI file replaces the transcription
  (stage 4). The file is converted once into the stage's own JSON (`notes.json`
  layout: start, dur, pitch in seconds and MIDI numbers) so the pipeline sees an
  ordinary artifact; the stage that would have produced it is skipped and
  everything after it reruns. SV times are read back on Bapu's timeline: when
  the project has an audio track for the dry lead that was moved (the grid
  alignment on export, or the user placing it), the notes move back by the same
  offset. Lyrics in the file are ignored and never copied.
- Audio (`lead_dry`, `instrumental`): used as given when soundfile can read it,
  otherwise decoded to WAV once.
- Tempo: a number the user typed, or the first tempo mark of the notes' SV
  project, written in the tempo stage's layout.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bapu import svp
from bapu.audio import decode_to_wav, readable

VERSION = "1"  # bump when the conversion changes its output
NOTE_FILES = (".svp", ".mid", ".midi")
MIN_DUR = 0.01
BPM_RANGE = (20.0, 400.0)


class ExternalError(ValueError):
    pass


class AmbiguousTrack(ExternalError):
    """Several tracks carry notes: the caller must pick one (`tracks` lists them)."""

    def __init__(self, tracks: list[dict[str, Any]]) -> None:
        super().__init__("这个工程里有几条音轨都有音符，选一条")
        self.tracks = tracks


def svp_tracks(project: dict[str, Any]) -> list[dict[str, Any]]:
    """Every track: 1-based index, name, note count and whether it is an audio track."""
    out = []
    for i, tr in enumerate(project.get("tracks", []), 1):
        ref = tr.get("mainRef", {})
        n = len(svp.vocal_notes(project, i))
        out.append({"index": i, "name": str(tr.get("name", "")), "notes": n, "audio": "audio" in ref})
    return out


def _audio_offset(project: dict[str, Any]) -> float:
    """Seconds the dry-lead audio track was moved by (0 when there is none)."""
    tmap = svp.TempoMap(project["time"]["tempo"])
    offsets: list[tuple[str, float]] = []
    for tr in project.get("tracks", []):
        ref = tr.get("mainRef", {})
        if "audio" not in ref:
            continue
        name = Path(str(ref["audio"].get("filename", ""))).stem.lower()
        offsets.append((name, tmap.seconds(ref.get("blickOffset", 0))))
    if not offsets:
        return 0.0
    for name, off in offsets:
        if "lead_dry" in name:
            return off
    if len({round(off, 6) for _, off in offsets}) == 1:
        return offsets[0][1]
    return 0.0


def _clean(notes: list[dict[str, Any]], offset: float) -> list[dict[str, Any]]:
    """Shift by `offset`, drop what ends before 0, trim overlaps (later note wins)."""
    out = []
    for n in sorted(notes, key=lambda x: x["start"]):
        start, dur = n["start"] - offset, n["dur"]
        if start < 0:
            dur += start
            start = 0.0
        if dur < MIN_DUR:
            continue
        if out and start < out[-1]["start"] + out[-1]["dur"]:
            out[-1]["dur"] = round(start - out[-1]["start"], 4)
            if out[-1]["dur"] < MIN_DUR:
                out.pop()
        out.append({"start": round(start, 4), "dur": round(dur, 4), "pitch": int(n["pitch"])})
    return out


def notes_from_svp(project: dict[str, Any], track: int | None = None) -> tuple[list[dict[str, Any]], int, float]:
    """Notes of one track on Bapu's timeline; returns (notes, track index, offset applied)."""
    tracks = svp_tracks(project)
    if track is None:
        with_notes = [t for t in tracks if t["notes"]]
        if not with_notes:
            raise ExternalError("这个工程里没有音符")
        if len(with_notes) > 1:
            raise AmbiguousTrack(tracks)
        track = with_notes[0]["index"]
    if not 1 <= track <= len(tracks):
        raise ExternalError(f"没有第 {track} 条音轨（共 {len(tracks)} 条）")
    raw = [{"start": n.start, "dur": n.dur, "pitch": n.pitch} for n in svp.vocal_notes(project, track)]
    if not raw:
        raise ExternalError(f"第 {track} 条音轨里没有音符")
    offset = _audio_offset(project)
    return _clean(raw, offset), track, offset


def notes_from_midi(path: Path) -> list[dict[str, Any]]:
    """All note events of the file (tracks merged), in seconds via its tempo map."""
    import mido

    mf = mido.MidiFile(str(path))
    t, active, notes = 0.0, {}, []
    for msg in mf:  # iterating the file merges tracks and gives delta times in seconds
        t += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            active[msg.note] = t
        elif msg.type in ("note_off", "note_on") and msg.note in active:
            s = active.pop(msg.note)
            notes.append({"start": s, "dur": t - s, "pitch": msg.note})
    if not notes:
        raise ExternalError("这个 MIDI 里没有音符")
    return _clean(notes, 0.0)


def notes_from_file(path: Path, out_dir: Path, track: int | None = None) -> Path:
    """Convert `.svp` / `.mid` into `<out_dir>/<stem>.notes.json`; returns that file.
    The JSON is written deterministically, so its hash is the pipeline's cache key."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".svp":
        try:
            project = svp.load_project(path)
        except (ValueError, KeyError) as e:
            raise ExternalError(f"读不了这个 SV 工程：{e}") from None
        notes, used_track, offset = notes_from_svp(project, track)
        meta: dict[str, Any] = {"kind": "svp", "track": used_track, "offset_s": round(offset, 4),
                                "bpm": svp_bpm(project), "time_signature": svp_meter(project)}
    elif suffix in NOTE_FILES:
        notes = notes_from_midi(path)
        meta = {"kind": "midi"}
    else:
        raise ExternalError(f"音符文件要是 .svp 或 .mid：{path.name}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{path.stem}.notes.json"
    data = {"model": f"external:{meta['kind']}", "version": VERSION, "from": path.name, **meta,
            "chunks": 0, "notes": notes}
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    return out


def svp_bpm(project: dict[str, Any]) -> float | None:
    """The project's tempo at the start (SV projects usually have one tempo)."""
    marks = sorted(project.get("time", {}).get("tempo", []), key=lambda m: m.get("position", 0))
    try:
        return float(marks[0]["bpm"]) if marks else None
    except (KeyError, TypeError, ValueError):
        return None


def svp_meter(project: dict[str, Any]) -> str:
    meters = project.get("time", {}).get("meter", [])
    try:
        return f"{int(meters[0]['numerator'])}/{int(meters[0]['denominator'])}" if meters else "4/4"
    except (KeyError, TypeError, ValueError):
        return "4/4"


def describe(json_path: Path) -> dict[str, Any]:
    """What the UI shows for a converted notes file: the original name, track, count (no lyrics)."""
    try:
        d = json.loads(Path(json_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"from": Path(json_path).name, "notes": None}
    return {"from": d.get("from", Path(json_path).name), "kind": d.get("kind"), "track": d.get("track"),
            "offset_s": d.get("offset_s"), "bpm": d.get("bpm"), "notes": len(d.get("notes", []))}


def audio_from_file(path: Path, out_dir: Path) -> Path:
    """An audio input as the stages can read it: the file itself when soundfile
    reads it, else its first audio track decoded to `<out_dir>/<stem>.wav`."""
    path = Path(path)
    if readable(path):
        return path
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        return decode_to_wav(path, out_dir / f"{path.stem}.wav")
    except ValueError as e:
        raise ExternalError(str(e)) from None


def tempo_file(out_dir: Path, bpm: float, *, time_signature: str = "4/4", origin: str = "user",
               name: str = "tempo") -> Path:
    """A tempo the user typed (or the notes file carried), in the tempo stage's layout.
    The beat grid starts at 0 s: nothing is known about the song's first beat."""
    bpm = float(bpm)
    if not BPM_RANGE[0] <= bpm <= BPM_RANGE[1]:
        raise ExternalError(f"拍速要在 {BPM_RANGE[0]:g} 到 {BPM_RANGE[1]:g} BPM 之间")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{name}.tempo.json"
    data = {"bpm": round(bpm, 3), "bpm_raw": bpm, "bpm_beat_track": None, "first_beat": 0.0, "beat_phase_s": 0.0,
            "beats": [], "per_quarter_bpm": [], "ibi_std_ms": None, "fit_residual_ms": None, "steady": True,
            "confidence": "user", "time_signature": time_signature, "origin": origin}
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return out


def describe_tempo(json_path: Path) -> dict[str, Any]:
    try:
        d = json.loads(Path(json_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"bpm": None, "origin": None}
    return {"bpm": d.get("bpm"), "time_signature": d.get("time_signature"), "origin": d.get("origin", "user")}
