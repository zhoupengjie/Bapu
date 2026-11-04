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
                        "conf": round(float(np.mean([s.score for s in sps])), 4), "aligned": True,
                        # start of every aligned letter, used to place English syllables
                        "letter_t": [round(s.start * FRAME, 3) for s in sps]
                        if len(sps) == len(t["letters"]) else None})
        else:
            out.append({"t0": None, "t1": None, "conf": 0.0, "aligned": False, "letter_t": None})
    # Tokens without letters (no reading): place them between their neighbours.
    for k, o in enumerate(out):
        if o["t0"] is None:
            prev = next((out[j]["t1"] for j in range(k - 1, -1, -1) if out[j]["t1"] is not None), 0.0)
            nxt = next((out[j]["t0"] for j in range(k + 1, len(out)) if out[j]["t0"] is not None), prev)
            o["t0"], o["t1"] = prev, max(prev, nxt)
    return out


def resolve(em, tokens: list[dict[str, Any]], dictionary: dict[str, int], *, keep_conf: float, alt_margin: float,
            alt_min: float = 0.3) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Settle tokens the lyrics stage left open, by how well the recording supports them.

    Pass A aligns the primary sequence (inserted words kept, reference words
    where there is an alternative); pass B drops the inserted words and uses
    the alternatives. Words inserted together stay if their mean pass-A
    confidence is at least `keep_conf`; an alternative replaces the reference word only if its
    confidence is at least `alt_min` and higher by `alt_margin` (on a confirmed
    line the aligner gave a correct reference word 0.005 and a wrong dictated
    one 0.20). Returns (final tokens, decisions).
    """
    open_k = [k for k, t in enumerate(tokens) if t.get("optional") or t.get("alt")]
    if not open_k:
        return [dict(t) for t in tokens], []
    a = align_tokens(em, tokens, dictionary)
    seq_b, map_b = [], {}
    for k, t in enumerate(tokens):
        if t.get("optional"):
            continue
        map_b[k] = len(seq_b)
        seq_b.append(dict(t, **{x: t["alt"][x] for x in ("text", "letters", "lang", "syll")}) if t.get("alt") else t)
    b = align_tokens(em, seq_b, dictionary) if seq_b else []

    # Words dictation inserted together are judged together (a short syllable
    # between others can get a near-zero confidence on its own).
    group_conf: dict[Any, list[float]] = {}
    for k, t in enumerate(tokens):
        if t.get("optional"):
            group_conf.setdefault(t.get("group") or ("k", k), []).append(a[k]["conf"])
    final, decisions = [], []
    for k, t in enumerate(tokens):
        t = dict(t)
        if t.get("optional"):
            g = group_conf[t.get("group") or ("k", k)]
            keep = float(np.mean(g)) >= keep_conf
            decisions.append({"type": "insert", "line": t["line"], "time": a[k]["t0"], "conf": a[k]["conf"],
                              "group_conf": round(float(np.mean(g)), 4), "kept": keep})
            if not keep:
                continue
            t["flags"] = list(t.get("flags", [])) + ["added_from_dictation"]
        elif t.get("alt"):
            ca, cb = a[k]["conf"], b[map_b[k]]["conf"]
            use_alt = cb >= ca + alt_margin and cb >= alt_min
            decisions.append({"type": "alternative", "line": t["line"], "time": a[k]["t0"],
                              "conf_reference": ca, "conf_dictation": cb, "replaced": use_alt})
            if use_alt:
                t.update({x: t["alt"][x] for x in ("text", "letters", "lang", "syll")})
                t["source"] = "dictation"
                t["flags"] = list(t.get("flags", [])) + ["replaced_by_dictation"]
        t.pop("alt", None)
        t["optional"] = False
        final.append(t)
    return final, decisions


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
    dictionary = MMS_FA.get_dict(star=None)
    tokens, decisions = resolve(em, tokens, dictionary, keep_conf=float(ctx.params["keep_conf"]),
                                alt_margin=float(ctx.params["alt_margin"]), alt_min=float(ctx.params["alt_min"]))
    timing = align_tokens(em, tokens, dictionary)
    for t, x in zip(tokens, timing):
        t.update(x)

    by_line: dict[int, list[int]] = {}
    for k, t in enumerate(tokens):
        by_line.setdefault(t["line"], []).append(k)
    lines_out = []
    for ln in data["lines"]:
        ks = by_line.get(ln["line"])
        if not ks:
            continue
        confs = [tokens[k]["conf"] for k in ks]
        t0, t1 = tokens[ks[0]]["t0"], tokens[ks[-1]]["t1"]
        lines_out.append({
            "line": ln["line"], "start": ln["start"], "end": ln["end"], "t0": t0, "t1": t1,
            "conf": round(min(confs), 4), "conf_mean": round(float(np.mean(confs)), 4),
            "start_offset": round(t0 - ln["start"], 3) if ln["start"] is not None else None,
        })
    out = ctx.out_dir / "alignment.json"
    out.write_text(json.dumps({"model": spec.id, "lang": data["lang"], "mode": data.get("mode"),
                               "frames": int(em.shape[0]), "tokens": tokens, "lines": lines_out,
                               "decisions": decisions}, ensure_ascii=False, indent=1), encoding="utf-8")

    confs = np.array([t["conf"] for t in tokens])
    offsets = [abs(ln["start_offset"]) for ln in lines_out if ln["start_offset"] is not None]
    return StageResult(
        outputs={"alignment": out},
        metrics={
            "tokens": len(tokens),
            "conf_median": round(float(np.median(confs)), 3),
            "tokens_conf_below_0.2": int(np.sum(confs < 0.2)),
            "lines_conf_below_threshold": sum(1 for ln in lines_out if ln["conf"] < float(ctx.params["line_conf_threshold"])),
            "max_line_offset_s": round(max(offsets), 3) if offsets else None,
            "inserted_kept": sum(1 for d in decisions if d["type"] == "insert" and d["kept"]),
            "inserted_dropped": sum(1 for d in decisions if d["type"] == "insert" and not d["kept"]),
            "replaced_by_dictation": sum(1 for d in decisions if d["type"] == "alternative" and d["replaced"]),
            "alternatives_rejected": sum(1 for d in decisions if d["type"] == "alternative" and not d["replaced"]),
        },
    )


STAGE = Stage(
    name="align",
    title="对齐",
    index=8,
    version="5",
    inputs=("lead_dry", "lyrics_lines"),
    outputs=("alignment",),
    defaults={"model": "mms-fa", "chunk_s": 20.0, "context_s": 2.0, "line_conf_threshold": 0.15,
              "keep_conf": 0.15, "alt_margin": 0.15, "alt_min": 0.3},
    model_params={"model": "align"},
    run=_run,
)
