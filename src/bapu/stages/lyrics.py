"""07 lyrics: fuse dictation with the user's reference lyric file (if any).

See `bapu.lyrics.fuse` for the rules. Output `lyrics.json` holds lyric text and
stays in the (gitignored) work directory; `fusion.json` lists what dictation
changed, by line number and time only. Metrics carry counts only.
"""

from __future__ import annotations

import json
from typing import Any

from bapu.lyrics import g2p
from bapu.lyrics.fuse import Tok, dictation_tokens, fuse, keys_for
from bapu.lyrics.parse import parse_file, tokenize
from bapu.stages.base import Stage, StageContext, StageResult

SUPPORTED = {"yue"}


def _tok_json(t: Tok, lang: str, line_lang: dict[int, str]) -> dict[str, Any]:
    def one(x: Tok) -> dict[str, Any]:
        latin = g2p.is_latin(x.text)
        return {"text": x.text, "letters": x.key,
                "lang": line_lang.get(x.line) or ("en" if latin else lang),
                "syll": g2p.english_syllables(x.text) if latin else 1}
    d = one(t)
    d.update(source=t.source, optional=t.optional, group=t.group, flags=t.flags,
             alt=one(t.alt) if t.alt else None)
    return d


def _run(ctx: StageContext) -> StageResult:
    lang = str(ctx.params["lang"])
    if lang not in SUPPORTED:
        raise ValueError(f"暂不支持语言 {lang}；目前支持：{', '.join(sorted(SUPPORTED))}")
    used: set[int] = set()
    dictation = json.loads(ctx.inputs["dictation"].read_text(encoding="utf-8"))["phrases"]
    text_fix = {int(c["line"]): c for c in ctx.corrections if c["type"] == "line_text"}
    line_lang = {int(c["line"]): str(c["lang"]) for c in ctx.corrections if c["type"] == "line_lang"}

    if "lyrics" in ctx.inputs:
        lines = [{"line": i, "start": ln.start, "end": ln.end, "tokens": ln.tokens}
                 for i, ln in enumerate(parse_file(ctx.inputs["lyrics"]), 1)]
        mode = "fused"
    else:
        lines = [{"line": i, "start": p["t0"], "end": p["t1"], "tokens": tokenize(p["text"])}
                 for i, p in enumerate(dictation, 1)]
        mode = "dictation_only"
    for ln in lines:
        if ln["line"] in text_fix:
            ln["tokens"] = tokenize(str(text_fix[ln["line"]]["text"]))
            used.add(text_fix[ln["line"]]["_id"])
    for c in ctx.corrections:
        if c["type"] == "line_lang" and any(ln["line"] == int(c["line"]) for ln in lines):
            used.add(c["_id"])

    ref: list[Tok] = []
    for ln in lines:
        src = "user" if ln["line"] in text_fix else "ref"
        ref += [Tok(t, k, ln["line"], source=src) for t, k in zip(ln["tokens"], keys_for(ln["tokens"]))]
    if mode == "fused":
        fused, events = fuse(ref, dictation_tokens(dictation, tokenize), locked_lines=set(text_fix),
                             max_insert=int(ctx.params["max_insert"]))
    else:
        fused, events = ref, []
        for t in fused:
            if t.source == "ref":
                t.source = "dictation"
                t.flags.append("unverified")

    out_lines = []
    for ln in lines:
        toks = [_tok_json(t, lang, line_lang) for t in fused if t.line == ln["line"]]
        out_lines.append({"line": ln["line"], "start": ln["start"], "end": ln["end"], "tokens": toks})
    out_lines = [ln for ln in out_lines if ln["tokens"]]
    out = ctx.out_dir / "lyrics.json"
    out.write_text(json.dumps({"lang": lang, "mode": mode, "lines": out_lines}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    (ctx.out_dir / "fusion.json").write_text(json.dumps(events, indent=1), encoding="utf-8")
    (ctx.out_dir / "corrections_used.json").write_text(json.dumps(sorted(used)), encoding="utf-8")
    toks = [t for ln in out_lines for t in ln["tokens"]]
    return StageResult(
        outputs={"lyrics_lines": out, "fusion": ctx.out_dir / "fusion.json"},
        metrics={
            "mode": mode, "lines": len(out_lines), "tokens": len(toks),
            "syllables": sum(t["syll"] for t in toks if not t["optional"]),
            "agreed": sum(1 for t in toks if t["source"] == "both"),
            "inserted_candidates": sum(1 for t in toks if t["optional"]),
            "alternatives": sum(1 for t in toks if t["alt"]),
            "gaps_kept_reference": sum(1 for e in events if e["type"] in ("differs", "extra_ignored")),
            "no_reading": sum(1 for t in toks if not t["letters"]),
        },
    )


STAGE = Stage(
    name="lyrics",
    title="歌词（听写 + 参考）",
    index=7,
    version="9",
    inputs=("dictation", "lyrics"),
    optional_inputs=("lyrics",),
    outputs=("lyrics_lines", "fusion"),
    defaults={"lang": "yue", "max_insert": 4},
    corrections=("line_text", "line_lang"),
    run=_run,
)
