"""07 align: CTC forced alignment of the lyric letters to the dry lead.

Backend `mms`: torchaudio MMS_FA (16 kHz, 20 ms frames). Letter posteriors are
computed in 20 s chunks with 2 s of context on each side, concatenated, and the
whole song is aligned in one Viterbi pass. Each token gets a start, an end and
a confidence (mean posterior of its letters); a line's confidence is its
lowest token confidence.

MMS was trained on speech; long sung vowels and large pitch jumps lower its
confidence (pipeline notes §6). Other aligners (e.g. SOFA) can be added as
backends with the same output.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from bapu.audio import read_mono
from bapu.stages.base import Stage, StageContext, StageResult

SR = 16000
HOP = 320
FRAME = HOP / SR


def emissions(model, wav: np.ndarray, device: str, chunk_s: float, ctx_s: float, progress=None):
    import torch

    chunk, ctx = int(chunk_s * SR), int(ctx_s * SR)
    x = torch.from_numpy(wav).unsqueeze(0)
    n = x.shape[1]
    starts = list(range(0, n, chunk))
    outs = []
    with torch.inference_mode():
        for k, s in enumerate(starts):
            a, b = max(0, s - ctx), min(n, s + chunk + ctx)
            em, _ = model(x[:, a:b].to(device))
            em = torch.log_softmax(em, dim=-1)[0].cpu()
            f0 = round((s - a) / HOP)
            f1 = f0 + round((min(s + chunk, n) - s) / HOP)
            outs.append(em[f0:f1])
            if progress:
                progress(f"块 {k + 1} / {len(starts)}", (k + 1) / len(starts) * 0.9)
    return torch.cat(outs)


def align_tokens(em, tokens: list[dict[str, Any]], dictionary: dict[str, int]) -> list[dict[str, Any]]:
    import torch
    import torchaudio.functional as AF

    targets, owner = [], []
    for k, t in enumerate(tokens):
        for ch in t["letters"]:
            if ch in dictionary:
                targets.append(dictionary[ch])
                owner.append(k)
    if not targets:
        raise ValueError("歌词里没有可对齐的字母")
    tgt = torch.tensor([targets], dtype=torch.int32)
    ali, scores = AF.forced_align(em.unsqueeze(0), tgt, blank=0)
    spans = AF.merge_tokens(ali[0], scores[0].exp())
    if len(spans) != len(targets):
        raise RuntimeError(f"对齐结果数量不对：{len(spans)} != {len(targets)}")
    per: list[list[Any]] = [[] for _ in tokens]
    for sp, k in zip(spans, owner):
        per[k].append(sp)

    out: list[dict[str, Any]] = []
    for t, sps in zip(tokens, per):
        if sps:
            out.append({"t0": round(sps[0].start * FRAME, 3), "t1": round(sps[-1].end * FRAME, 3),
                        "conf": round(float(np.mean([s.score for s in sps])), 4), "aligned": True})
        else:
            out.append({"t0": None, "t1": None, "conf": 0.0, "aligned": False})
    # Tokens without letters (no reading): place them between their neighbours.
    for k, o in enumerate(out):
        if o["t0"] is None:
            prev = next((out[j]["t1"] for j in range(k - 1, -1, -1) if out[j]["t1"] is not None), 0.0)
            nxt = next((out[j]["t0"] for j in range(k + 1, len(out)) if out[j]["t0"] is not None), prev)
            o["t0"], o["t1"] = prev, max(prev, nxt)
    return out


def _run(ctx: StageContext) -> StageResult:
    import torch
    from torchaudio.pipelines import MMS_FA

    spec = ctx.models["model"]
    assert spec is not None
    ctx.registry.ensure_ready(spec)
    torch.hub.set_dir(str(ctx.registry.model_dir(spec).parent))  # <cache>/torch/hub

    data = json.loads(ctx.inputs["lyrics_lines"].read_text(encoding="utf-8"))
    tokens = [dict(t, line=ln["line"]) for ln in data["lines"] for t in ln["tokens"]]
    wav, _ = read_mono(ctx.inputs["lead_dry"], SR, quality="VHQ")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    ctx.progress(f"载入 {spec.name}（{device}）", 0.0)
    model = MMS_FA.get_model(with_star=False).to(device).eval()
    try:
        em = emissions(model, wav, device, float(ctx.params["chunk_s"]), float(ctx.params["context_s"]), ctx.progress)
    finally:
        del model
        if device == "cuda":
            torch.cuda.empty_cache()
    ctx.progress("维特比对齐", 0.95)
    timing = align_tokens(em, tokens, MMS_FA.get_dict(star=None))

    by_line: dict[int, list[int]] = {}
    for k, t in enumerate(tokens):
        by_line.setdefault(t["line"], []).append(k)
    lines_out = []
    for ln in data["lines"]:
        ks = by_line[ln["line"]]
        confs = [timing[k]["conf"] for k in ks]
        t0, t1 = timing[ks[0]]["t0"], timing[ks[-1]]["t1"]
        lines_out.append({
            "line": ln["line"], "t0": t0, "t1": t1,
            "conf": round(min(confs), 4), "conf_mean": round(float(np.mean(confs)), 4),
            "start_offset": round(t0 - ln["start"], 3) if ln["start"] is not None else None,
        })
    out = ctx.out_dir / "alignment.json"
    out.write_text(json.dumps({"model": spec.id, "frames": int(em.shape[0]), "tokens": timing, "lines": lines_out},
                              indent=1), encoding="utf-8")

    confs = np.array([t["conf"] for t in timing])
    offsets = [abs(ln["start_offset"]) for ln in lines_out if ln["start_offset"] is not None]
    return StageResult(
        outputs={"alignment": out},
        metrics={
            "tokens": len(timing),
            "conf_median": round(float(np.median(confs)), 3),
            "tokens_conf_below_0.2": int(np.sum(confs < 0.2)),
            "lines_conf_below_threshold": sum(1 for ln in lines_out if ln["conf"] < float(ctx.params["line_conf_threshold"])),
            "max_line_offset_s": round(max(offsets), 3) if offsets else None,
        },
    )


STAGE = Stage(
    name="align",
    title="对齐",
    index=7,
    version="1",
    inputs=("lead_dry", "lyrics_lines"),
    outputs=("alignment",),
    defaults={"model": "mms-fa", "chunk_s": 20.0, "context_s": 2.0, "line_conf_threshold": 0.15},
    model_params={"model": "align"},
    run=_run,
)
