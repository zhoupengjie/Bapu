"""10 backing ("和声转谱"): transcribe the backing-vocal stem with SOME.

The karaoke step already splits vocals into lead and backing. On the
validation song the backing is audible in 55 % of the song, almost always
together with the lead (77 % of the lead's time, alone only 4 %), about 11 dB
quieter, mostly a third below the lead and on the lead's syllables (75 % of
onsets within 60 ms of a lead onset). SOME follows one line, so where several
backing voices stack only the loudest is kept.

Notes whose backing audio is quieter than `min_db` (dBFS) are dropped: they
are mostly bleed or breath, not a harmony line.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from bapu.audio import read_mono
from bapu.stages.base import Stage, StageContext, StageResult
from bapu.stages.transcribe import note_metrics, run_some


def note_levels(notes: list[dict[str, Any]], y: np.ndarray, sr: int) -> list[float]:
    """RMS level (dBFS) of the audio under each note."""
    out = []
    for n in notes:
        a, b = int(n["start"] * sr), int((n["start"] + n["dur"]) * sr)
        seg = y[a:max(b, a + 1)]
        out.append(round(float(20 * np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-12)), 1))
    return out


def _run(ctx: StageContext) -> StageResult:
    spec = ctx.models["model"]
    assert spec is not None
    chunks, notes = run_some(ctx, ctx.inputs["backing"])
    y, sr = read_mono(ctx.inputs["backing"])
    levels = note_levels(notes, y, sr)
    min_db = float(ctx.params["min_db"])
    kept = [dict(n, level_db=lv) for n, lv in zip(notes, levels) if lv >= min_db]
    out = ctx.out_dir / "notes.json"
    out.write_text(json.dumps({"model": spec.id, "chunks": chunks, "notes": kept}, indent=1), encoding="utf-8")
    metrics = note_metrics(kept)
    metrics.update(chunks=chunks, dropped_quiet=len(notes) - len(kept))
    return StageResult(outputs={"backing_notes": out}, metrics=metrics)


STAGE = Stage(
    name="backing",
    title="和声转谱",
    index=10,
    version="1",
    inputs=("backing",),
    outputs=("backing_notes",),
    defaults={"model": "some-0119-continuous256-5spk", "min_db": -45.0},
    model_params={"model": "transcribe"},
    run=_run,
)
