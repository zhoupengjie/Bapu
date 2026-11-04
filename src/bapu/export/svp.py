"""Write a Synthesizer V Studio project (`.svp`, version 203 layout).

- Times in blicks (705 600 000 per quarter) from seconds at the score's tempo,
  not quantised.
- Every note carries its lyric (`-` / `+` included) and a `languageOverride`.
- The vocal track has no voice: the user picks a singer in SV.
- Optionally audio tracks (accompaniment, dry lead for comparison) that
  reference their files by absolute path; the audio is not copied.

The layout follows a project saved by SV 2 (version 203). Fields not listed
here are left for SV to fill with its defaults.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Sequence

import soundfile as sf

from bapu.svp import QUARTER

LANG = {"yue": "cantonese", "en": "english", "zh": "mandarin", "ja": "japanese", "ko": "korean", "es": "spanish"}
CURVES = ("pitchDelta", "vibratoEnv", "loudness", "tension", "breathiness", "voicing", "gender", "toneShift",
          "mouthOpening")


def _uuid() -> str:
    return str(uuid.uuid4())


def _takes() -> dict[str, Any]:
    return {"activeTakeId": 0, "takes": [{"id": 0, "seedDuration": 0, "seedPitch": 0, "seedTimbre": 0, "liked": False}]}


def _group(name: str, notes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": name, "uuid": _uuid(),
        "parameters": {c: {"mode": "cubic", "points": []} for c in CURVES},
        "vocalModes": {}, "pitchControls": [], "notes": notes,
        "musicalScale": {"type": "Major", "root": "C"},
    }


def _ref(group_id: str, *, language: str = "", instrumental: bool = False) -> dict[str, Any]:
    return {
        "uuid": _uuid(), "groupID": group_id,
        "blickAbsoluteBegin": 0, "blickAbsoluteEnd": -1, "blickOffset": 0, "pitchOffset": 0,
        "mute": False, "isInstrumental": instrumental,
        "database": {"name": "", "language": "", "languageOverride": language, "backendType": "", "version": "-2"},
        "dictionary": "",
        "voice": {"vocalModeInherited": True, "vocalModePreset": "", "vocalModeParams": {},
                  "choirSeatingSeparation": 0.699999988079071},
        "voicePresetName": "", "takes": _takes(), "timestampLMR": 0, "timestampLRSR": 0,
    }


def _mixer(fx: bool) -> dict[str, Any]:
    """SV 2.3 track defaults: effects on for vocal tracks, off for audio tracks."""
    eq = [{"freq": 117.69999694824219, "gain": 0.0, "q": 0.709999978542328},
          {"freq": 692.7999877929688, "gain": 0.0, "q": 0.709999978542328},
          {"freq": 4077.699951171875, "gain": 0.0, "q": 0.709999978542328}]
    return {
        "gainDecibel": 0.0, "pan": 0.0, "mute": False, "solo": False, "display": True, "fxPresetName": "",
        "fxParams": {
            "room": {"enabled": fx, "positionX": 0.0, "positionY": 0.0, "size": 15.0, "reflectionGain": 0.0},
            "postRoomEq": {"enabled": fx, "filters": eq, "lowShelf": {"freq": 25.0, "gain": 0.0}},
            "compressor": {"enabled": fx, "attack": 0.0, "ratio": 4.0, "threshold": -10.0},
            "reverb": {"enabled": fx, "type": "clean", "preDelay": 0.050000000745058, "decay": 1.0, "dryWetRatio": 0.5},
        },
    }


def _track(name: str, order: int, color: str, group: dict[str, Any], ref: dict[str, Any]) -> dict[str, Any]:
    fx = not ref["isInstrumental"]
    return {"name": name, "dispColor": color, "dispOrder": order, "renderEnabled": False, "mixer": _mixer(fx),
            "mainGroup": group, "mainRef": ref, "groups": []}


def blicks(notes: list[dict[str, Any]], bpm: float) -> list[tuple[int, int]]:
    """(onset, duration) per note; rounding never creates overlaps or empty notes."""
    k = bpm / 60.0 * QUARTER
    spans = [[round(n["start"] * k), round((n["start"] + n["dur"]) * k)] for n in notes]
    for i, (s, e) in enumerate(spans):
        if i + 1 < len(spans):
            e = min(e, spans[i + 1][0])
        spans[i][1] = max(e, s + 1)
    return [(s, e - s) for s, e in spans]


def _sv_notes(notes: list[dict[str, Any]], bpm: float, song_lang: str) -> list[dict[str, Any]]:
    out = []
    for n, (onset, dur) in zip(notes, blicks(notes, bpm)):
        out.append({
            "uuid": uuid.uuid4().hex[:16], "musicalType": "singing",
            "lyrics": n["lyric"], "phonemes": "", "accent": "",
            "onset": onset, "duration": dur, "pitch": int(n["pitch"]), "detune": 0,
            "attributes": {"muted": False, "evenSyllableDuration": True,
                           "languageOverride": LANG.get(n.get("lang", ""), song_lang)},
            "takes": _takes(),
        })
    return out


def build(score: dict[str, Any], *, audio_tracks: Sequence[tuple[str, Path]] = (), name: str = "Bapu",
          harmony: bool = True) -> dict[str, Any]:
    """`harmony`: add the harmony notes (score["harmony"]) as a second vocal track."""
    bpm = float(score["tempo"]["bpm"])
    num, den = (int(x) for x in str(score["tempo"].get("time_signature", "4/4")).split("/"))
    song_lang = LANG.get(score.get("lang", ""), "")
    vocal = _group("main", _sv_notes(score["notes"], bpm, song_lang))
    tracks = [_track(f"{name} vocal", 0, "ff7db235", vocal, _ref(vocal["uuid"], language=song_lang))]
    if harmony and score.get("harmony"):
        group = _group("main", _sv_notes(score["harmony"], bpm, song_lang))
        tracks.append(_track(f"{name} 和声", 1, "ffd16ba5", group, _ref(group["uuid"], language=song_lang)))
    colors = ("ff15a5c6", "ffe0913c", "ff9b7bd8")
    for i, (label, path) in enumerate(audio_tracks):
        empty = _group("main", [])
        ref = _ref(empty["uuid"], instrumental=True)
        ref["audio"] = {"filename": str(Path(path).resolve()), "duration": float(sf.info(str(path)).duration)}
        tracks.append(_track(f"{name} {label}", len(tracks), colors[i % len(colors)], empty, ref))
    return {
        "version": 203, "uuid": _uuid(),
        "time": {"meter": [{"index": 0, "numerator": num, "denominator": den}],
                 "tempo": [{"position": 0, "bpm": bpm}], "startTimeSeconds": 0.0},
        "library": [],
        "tracks": tracks,
        "renderConfig": {"destination": "", "filename": name, "numChannels": 1, "aspirationFormat": "noAspiration",
                         "bitDepth": 24, "sampleRate": 48000, "exportMixDown": True, "exportPitch": False,
                         "bypassPan": False, "bypassGain": False, "bypassEffects": False},
        "projectMixer": {"linkRoomSettings": True},
        "loopBegin": 0, "loopEnd": 0, "loopEnabled": False,
    }


def write(score: dict[str, Any], path: Path, *, audio_tracks: Sequence[tuple[str, Path]] = (),
          name: str = "Bapu", harmony: bool = True) -> Path:
    """`audio_tracks`: (label, file) pairs, each referenced by absolute path."""
    project = build(score, audio_tracks=audio_tracks, name=name, harmony=harmony)
    path.parent.mkdir(parents=True, exist_ok=True)
    # SV writes UTF-8 JSON followed by a NUL byte.
    path.write_bytes(json.dumps(project, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\x00")
    return path
