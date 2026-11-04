"""04 transcribe: dry lead -> notes (seconds, fractional MIDI pitch) with SOME."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from bapu import paths
from bapu.stages.base import Stage, StageContext, StageResult

NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(p: int) -> str:
    return f"{NAMES[p % 12]}{p // 12 - 1}"


def note_metrics(notes: list[dict[str, Any]], tolerance: float = 1e-3) -> dict[str, Any]:
    """Counts for the report. Times are stored rounded to 0.1 ms, so touching
    notes can look like they overlap by that much; `tolerance` absorbs it."""
    if not notes:
        return {"notes": 0, "overlaps": 0}
    ordered = sorted(notes, key=lambda n: n["start"])
    overlaps = sum(
        1 for a, b in zip(ordered, ordered[1:]) if b["start"] < a["start"] + a["dur"] - tolerance
    )
    pitches = [round(n["pitch"]) for n in ordered]
    durs = sorted(n["dur"] for n in ordered)
    return {
        "notes": len(ordered),
        "overlaps": overlaps,
        "range": f"{note_name(min(pitches))}–{note_name(max(pitches))}",
        "dur_min_ms": round(durs[0] * 1000),
        "dur_median_ms": round(durs[len(durs) // 2] * 1000),
    }


def _run(ctx: StageContext) -> StageResult:
    spec = ctx.models["model"]
    assert spec is not None
    ckpt = ctx.registry.ensure_ready(spec)
    some_src = Path(ctx.params.get("some_src") or paths.some_src_dir())
    if not (some_src / "inference").is_dir():
        raise FileNotFoundError(f"找不到 SOME 代码：{some_src}（设置 BAPU_SOME_SRC）")

    raw = ctx.out_dir / "some_raw.json"
    args = ctx.out_dir / "some_args.json"
    args.write_text(json.dumps({
        "some_src": str(some_src), "ckpt": str(ckpt), "wav": str(ctx.inputs["lead_dry"]), "out_json": str(raw),
    }), encoding="utf-8")

    log = ctx.out_dir / "some.log"
    ctx.progress(f"载入 {spec.name}", 0.0)
    with open(log, "w", encoding="utf-8") as errf:
        proc = subprocess.Popen(
            [sys.executable, "-I", "-m", "bapu.backends.some_worker", str(args)],
            stdout=subprocess.PIPE, stderr=errf, text=True, encoding="utf-8",
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            parts = line.split()
            if parts[:2] == ["BAPU", "progress"]:
                i, n = int(parts[2]), int(parts[3])
                ctx.progress(f"段 {i} / {n}", i / n)
        code = proc.wait()
    if code != 0:
        raise RuntimeError(f"SOME 失败（退出码 {code}），见 {log}")

    data = json.loads(raw.read_text(encoding="utf-8"))
    notes = sorted(data["notes"], key=lambda n: n["start"])
    out = ctx.out_dir / "notes.json"
    out.write_text(json.dumps({"model": spec.id, "chunks": data["chunks"], "notes": notes}, indent=1), encoding="utf-8")
    raw.unlink()
    metrics = note_metrics(notes)
    metrics["chunks"] = data["chunks"]
    return StageResult(outputs={"notes": out}, metrics=metrics)


STAGE = Stage(
    name="transcribe",
    title="转谱",
    index=4,
    version="2",
    inputs=("lead_dry",),
    outputs=("notes",),
    defaults={"model": "some-0119-continuous256-5spk", "some_src": None},
    model_params={"model": "transcribe"},
    run=_run,
)
