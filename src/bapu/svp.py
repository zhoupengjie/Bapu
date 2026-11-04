"""Read Synthesizer V `.svp` projects (version 203 layout).

Times in a project are blicks: one quarter note = 705 600 000 blicks. Notes sit
in note groups (`library`) referenced by tracks with a blick/pitch offset; the
tempo map converts blicks to seconds.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

QUARTER = 705_600_000


@dataclass
class SvpNote:
    start: float  # seconds
    dur: float
    pitch: int
    lyric: str


def load_project(path: Path) -> dict[str, Any]:
    raw = Path(path).read_bytes().rstrip(b"\x00")
    return json.loads(raw.decode("utf-8"))


class TempoMap:
    def __init__(self, marks: list[dict[str, Any]]) -> None:
        marks = sorted(marks, key=lambda m: m["position"])
        if not marks or marks[0]["position"] != 0:
            marks = [{"position": 0, "bpm": marks[0]["bpm"] if marks else 120.0}] + marks
        self.marks = marks
        self.starts = [0.0]  # seconds at each mark
        for a, b in zip(marks, marks[1:]):
            self.starts.append(self.starts[-1] + (b["position"] - a["position"]) / QUARTER * 60 / a["bpm"])

    def seconds(self, blick: float) -> float:
        k = max(i for i, m in enumerate(self.marks) if m["position"] <= blick) if blick >= 0 else 0
        m = self.marks[k]
        return self.starts[k] + (blick - m["position"]) / QUARTER * 60 / m["bpm"]


def vocal_notes(project: dict[str, Any], track: int = 1) -> list[SvpNote]:
    """Notes of one track (1-based), main group plus referenced groups, by time."""
    tmap = TempoMap(project["time"]["tempo"])
    tr = project["tracks"][track - 1]
    library = {g["uuid"]: g for g in project.get("library", [])}
    parts = [(tr["mainGroup"], tr.get("mainRef", {}))]
    for ref in tr.get("groups", []):
        if ref.get("groupID") in library:
            parts.append((library[ref["groupID"]], ref))
    out = []
    for group, ref in parts:
        boff, poff = ref.get("blickOffset", 0), ref.get("pitchOffset", 0)
        for n in group.get("notes", []):
            on = n["onset"] + boff
            t0 = tmap.seconds(on)
            out.append(SvpNote(t0, tmap.seconds(on + n["duration"]) - t0, int(n["pitch"] + poff), n.get("lyrics", "")))
    out.sort(key=lambda n: n.start)
    return out
