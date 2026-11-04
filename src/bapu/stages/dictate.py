"""06 dictate: lyrics dictated from the dry lead by a local speech model.

The song is cut into phrases at rests between notes (no lyric file needed);
each phrase is transcribed on its own. Output `dictation.json` holds lyric text
and stays in the work directory. Metrics carry counts only.

Measured on the validation song (pipeline notes): Cantonese is usable (about
17 % sound errors on confirmed lines) and finds words a subtitle missed;
English is poor. The lyrics stage therefore fuses dictation with a reference
lyric file when one is given.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from bapu.lyrics.parse import tokenize
from bapu.stages.base import Stage, StageContext, StageResult


def phrases(notes: list[dict[str, Any]], rest: float, pad: float) -> list[tuple[float, float, int, int]]:
    """(t0, t1, first note, last note) per phrase; notes are 1-based."""
    out, start = [], 0
    for i in range(1, len(notes) + 1):
        if i == len(notes) or notes[i]["start"] - (notes[i - 1]["start"] + notes[i - 1]["dur"]) >= rest:
            a, b = notes[start], notes[i - 1]
            out.append((max(0.0, a["start"] - pad), b["start"] + b["dur"] + pad, start + 1, i))
            start = i
    return out


def _run(ctx: StageContext) -> StageResult:
    spec = ctx.models["model"]
    assert spec is not None
    model_dir = ctx.registry.ensure_ready(spec).parent
    notes = json.loads(ctx.inputs["merged_notes"].read_text(encoding="utf-8"))["notes"]
    segs = phrases(notes, float(ctx.params["rest"]), float(ctx.params["pad"]))

    raw = ctx.out_dir / "asr_raw.json"
    args = ctx.out_dir / "asr_args.json"
    lang = ctx.params.get("language") or None
    args.write_text(json.dumps({"model_dir": str(model_dir), "wav": str(ctx.inputs["lead_dry"]),
                                "segments": [[a, b] for a, b, _, _ in segs], "language": lang,
                                "out_json": str(raw)}), encoding="utf-8")
    log = ctx.out_dir / "asr.log"
    ctx.progress(f"载入 {spec.name}", 0.0)
    with open(log, "w", encoding="utf-8") as errf:
        proc = subprocess.Popen([sys.executable, "-I", "-m", "bapu.backends.qwen_asr_worker", str(args)],
                                stdout=subprocess.PIPE, stderr=errf, text=True, encoding="utf-8")
        assert proc.stdout is not None
        for line in proc.stdout:
            parts = line.split()
            if parts[:2] == ["BAPU", "progress"]:
                ctx.progress(f"乐句 {parts[2]} / {parts[3]}", int(parts[2]) / int(parts[3]))
        code = proc.wait()
    if code != 0:
        raise RuntimeError(f"听写失败（退出码 {code}），见 {log}")
    res = json.loads(raw.read_text(encoding="utf-8"))["segments"]
    raw.unlink()

    out_phrases = []
    for (t0, t1, n0, n1), r in zip(segs, res):
        out_phrases.append({"t0": round(t0, 3), "t1": round(t1, 3), "notes": [n0, n1],
                            "text": r["text"], "language": r["language"]})
    out = ctx.out_dir / "dictation.json"
    out.write_text(json.dumps({"model": spec.id, "phrases": out_phrases}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    toks = [t for p in out_phrases for t in tokenize(p["text"])]
    return StageResult(outputs={"dictation": out}, metrics={
        "phrases": len(out_phrases), "tokens": len(toks),
        "latin_words": sum(1 for t in toks if t[0].isascii()),
        "empty_phrases": sum(1 for p in out_phrases if not tokenize(p["text"])),
    })


STAGE = Stage(
    name="dictate",
    title="听写",
    index=6,
    version="1",
    inputs=("lead_dry", "merged_notes"),
    outputs=("dictation",),
    defaults={"model": "qwen3-asr-1.7b", "rest": 0.3, "pad": 0.2, "language": "Cantonese"},
    model_params={"model": "dictate"},
    run=_run,
)
