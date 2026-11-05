"""03 tempo: global tempo and first beat from the instrumental stem.

The score keeps times in seconds; the tempo is only used to convert them to
ticks / blicks at export, so it must match the SV project's tempo.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from bapu.audio import read_mono
from bapu.stages.base import Stage, StageContext, StageResult


def estimate(
    y: np.ndarray, sr: int, *, hop_length: int = 512, round_tolerance: float = 0.15, steady_spread: float = 1.0,
) -> dict[str, Any]:
    import librosa

    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
    tempo, beats = librosa.beat.beat_track(onset_envelope=onset, sr=sr, hop_length=hop_length, units="time")
    raw = float(np.atleast_1d(tempo)[0])
    beats = np.asarray(beats, dtype=float)
    if len(beats) < 8:  # too short or too quiet to fit a grid: beat_track's own estimate, flagged
        return {"bpm": round(raw, 2) or 120.0, "bpm_raw": raw, "bpm_beat_track": raw,
                "first_beat": float(beats[0]) if len(beats) else 0.0,
                "beats": beats.round(4).tolist(), "confidence": "low", "steady": False,
                "per_quarter_bpm": [], "ibi_std_ms": None, "fit_residual_ms": None, "beat_phase_s": 0.0}

    ibi = np.diff(beats)
    # beat_track's tempo and its beat spacing are whole onset frames (at hop 512
    # one beat of 125 BPM is ~20.7 frames, so the beats alternate 20/21 frames
    # and the median reads 123.05). Fit a line through the beats instead and
    # take the slope. Beats are numbered gap by gap (a skipped beat adds 2);
    # numbering by distance from the first beat would drift.
    period = float(np.median(ibi))
    index = np.concatenate([[0.0], np.cumsum(np.maximum(1.0, np.round(ibi / period)))])
    slope, intercept = np.polyfit(index, beats, 1)
    est = float(60 / slope)
    residual_ms = float(np.std(beats - (slope * index + intercept)) * 1000)
    quarters = [
        float(60 / np.polyfit(i, b, 1)[0])
        for i, b in zip(np.array_split(index, 4), np.array_split(beats, 4))
        if len(i) >= 4
    ]
    spread = max(quarters) - min(quarters)
    steady = spread <= steady_spread
    near_int = abs(est - round(est)) <= round_tolerance
    bpm = float(round(est)) if steady and near_int else round(est, 2)

    beat_len = 60.0 / bpm
    first = float(beats[0])
    phase = float(intercept) % beat_len
    if phase > beat_len / 2:
        phase -= beat_len  # signed offset of the fitted beat grid from 0 s

    return {
        "bpm": bpm,
        "bpm_raw": round(est, 3),
        "bpm_beat_track": round(raw, 3),
        "first_beat": round(first, 4),
        "beat_phase_s": round(phase, 4),
        "beats": beats.round(4).tolist(),
        "per_quarter_bpm": [round(q, 2) for q in quarters],
        "ibi_std_ms": round(float(np.std(ibi) * 1000), 1),
        "fit_residual_ms": round(residual_ms, 1),
        "steady": steady,
        "confidence": "high" if steady and residual_ms < 30 else "low",
    }


def _run(ctx: StageContext) -> StageResult:
    sr = int(ctx.params["sample_rate"])
    y, sr = read_mono(ctx.inputs["instrumental"], sr)
    ctx.progress("librosa beat_track")
    info = estimate(y, sr, hop_length=int(ctx.params["hop_length"]))
    info["time_signature"] = ctx.params["time_signature"]
    out = ctx.out_dir / "tempo.json"
    Path(out).write_text(json.dumps(info, indent=2), encoding="utf-8")
    metrics = {k: info[k] for k in ("bpm", "bpm_raw", "beat_phase_s", "fit_residual_ms", "confidence")}
    metrics["per_quarter_bpm"] = info["per_quarter_bpm"]
    return StageResult(outputs={"tempo": out}, metrics=metrics)


STAGE = Stage(
    name="tempo",
    title="拍速",
    index=3,
    version="2",
    inputs=("instrumental",),
    outputs=("tempo",),
    defaults={"model": "librosa-beat-track", "sample_rate": 22050, "hop_length": 512, "time_signature": "4/4"},
    model_params={"model": "tempo"},
    run=_run,
)
