"""Line the score up with SV's piano-roll grid.

The tempo stage finds the song's beat grid: `bpm` and `beat_phase_s`, the
signed offset of its beats from 0 s. SV's grid starts at 0, so everything —
the notes and the audio tracks alike — moves later by one `shift` that puts
the song's beats on SV's beat lines. Then each note's start and end snap to
the chosen grid (1/16 note by default). The audio is only moved, never
stretched, so it stays with the notes it was transcribed from.

Snapping never makes two notes start together, never makes a note empty and
never makes notes overlap: a note that would land on the previous note's start
goes one grid step later, and the previous note ends where the next starts.
"""

from __future__ import annotations

from typing import Any

GRIDS = (8, 16, 32)  # 1/8, 1/16, 1/32 notes


def shift(score: dict[str, Any]) -> float:
    """Seconds to move the song so its beats fall on the grid's beat lines (0 ≤ shift < one beat)."""
    bpm = float(score["tempo"]["bpm"])
    beat = 60.0 / bpm
    phase = float(score["tempo"].get("beat_phase_s") or 0.0)
    s = (-phase) % beat
    return 0.0 if s < 1e-6 or beat - s < 1e-6 else s


def align(score: dict[str, Any], grid: int) -> tuple[dict[str, Any], float]:
    """A copy of the score with notes moved and snapped (times still in seconds), and the shift.
    `grid` 0 leaves the score as it is."""
    if not grid:
        return score, 0.0
    if grid not in GRIDS:
        raise ValueError(f"网格只能是 1/{', 1/'.join(map(str, GRIDS))}")
    bpm = float(score["tempo"]["bpm"])
    sec_per_quarter = 60.0 / bpm
    unit = 4.0 / grid  # grid step in quarter notes
    off = shift(score)

    def snap(t: float) -> int:  # seconds -> grid steps
        return round((t + off) / sec_per_quarter / unit)

    spans = [[snap(n["start"]), snap(n["start"] + n["dur"])] for n in score["notes"]]
    for i, sp in enumerate(spans):
        if i and sp[0] <= spans[i - 1][0]:
            sp[0] = spans[i - 1][0] + 1
        sp[1] = max(sp[1], sp[0] + 1)
    for i in range(len(spans) - 1):
        spans[i][1] = min(spans[i][1], spans[i + 1][0])
    notes = []
    for n, (a, b) in zip(score["notes"], spans):
        notes.append({**n, "start": a * unit * sec_per_quarter, "dur": (b - a) * unit * sec_per_quarter})
    return {**score, "notes": notes}, off
