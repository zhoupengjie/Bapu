"""Tempo estimate on a synthetic click track."""

from __future__ import annotations

import numpy as np

from bapu.stages.tempo import estimate
from bapu.stages.transcribe import note_metrics


def click_track(bpm: float, seconds: float, sr: int = 22050, offset: float = 0.5) -> np.ndarray:
    y = np.zeros(int(seconds * sr), dtype=np.float32)
    click = (np.sin(2 * np.pi * 1000 * np.arange(int(0.02 * sr)) / sr) * np.hanning(int(0.02 * sr))).astype(np.float32)
    t = offset
    while t < seconds - 0.05:
        i = int(t * sr)
        y[i : i + len(click)] += click
        t += 60.0 / bpm
    return y


def test_steady_click_track_rounds_to_integer_bpm():
    info = estimate(click_track(120, 40), 22050)
    assert info["bpm"] == 120.0
    assert info["steady"]
    assert info["confidence"] == "high"
    assert abs(info["beat_phase_s"]) < 0.05  # 0.5 s is exactly on the 120 BPM grid


def test_note_metrics_counts_overlaps():
    notes = [
        {"start": 0.0, "dur": 0.5, "pitch": 60.1},
        {"start": 0.5, "dur": 0.5, "pitch": 62.0},  # touching is fine
        {"start": 0.9, "dur": 0.3, "pitch": 64.0},  # overlaps the previous one
    ]
    m = note_metrics(notes)
    assert m["notes"] == 3
    assert m["overlaps"] == 1
    assert m["range"] == "C4–E4"
