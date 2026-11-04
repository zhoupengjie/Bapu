"""06 lyrics: parse the user's lyric file into lines and tokens.

Output `lyrics.json` holds lyric text and stays in the work directory (which is
gitignored). Metrics and logs carry counts only.
"""

from __future__ import annotations

import json
from typing import Any

from bapu.lyrics import g2p
from bapu.lyrics.parse import parse_file, tokenize
from bapu.stages.base import Stage, StageContext, StageResult

SUPPORTED = {"yue"}


def build(lines, lang: str, corrections: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], set[int]]:
    used: set[int] = set()
    text_fix = {int(c["line"]): c for c in corrections if c["type"] == "line_text"}
    lang_fix = {int(c["line"]): c for c in corrections if c["type"] == "line_lang"}
    out = []
    for no, line in enumerate(lines, 1):
        tokens = line.tokens
        if no in text_fix:
            tokens = tokenize(str(text_fix[no]["text"]))
            used.add(text_fix[no]["_id"])
        line_lang = None
        if no in lang_fix:
            line_lang = str(lang_fix[no]["lang"])
            used.add(lang_fix[no]["_id"])
        cjk = [t for t in tokens if not g2p.is_latin(t)]
        letters = iter(g2p.jyutping_letters(cjk))
        toks = []
        for t in tokens:
            if g2p.is_latin(t):
                toks.append({"text": t, "lang": line_lang or "en", "syll": g2p.english_syllables(t),
                             "letters": g2p.english_letters(t)})
            else:
                toks.append({"text": t, "lang": line_lang or lang, "syll": 1, "letters": next(letters)})
        out.append({"line": no, "start": line.start, "end": line.end, "tokens": toks})
    return out, used


def _run(ctx: StageContext) -> StageResult:
    lang = str(ctx.params["lang"])
    if lang not in SUPPORTED:
        raise ValueError(f"暂不支持语言 {lang}；目前支持：{', '.join(sorted(SUPPORTED))}")
    lines = parse_file(ctx.inputs["lyrics"])
    data, used = build(lines, lang, ctx.corrections)
    out = ctx.out_dir / "lyrics.json"
    out.write_text(json.dumps({"lang": lang, "lines": data}, ensure_ascii=False, indent=1), encoding="utf-8")
    (ctx.out_dir / "corrections_used.json").write_text(json.dumps(sorted(used)), encoding="utf-8")
    toks = [t for ln in data for t in ln["tokens"]]
    return StageResult(
        outputs={"lyrics_lines": out},
        metrics={
            "lines": len(data),
            "tokens": len(toks),
            "syllables": sum(t["syll"] for t in toks),
            "latin_words": sum(1 for t in toks if g2p.is_latin(t["text"])),
            "no_reading": sum(1 for t in toks if not t["letters"]),
            "timed": all(ln["start"] is not None for ln in data),
        },
    )


STAGE = Stage(
    name="lyrics",
    title="解析歌词",
    index=6,
    version="2",
    inputs=("lyrics",),
    outputs=("lyrics_lines",),
    defaults={"lang": "yue"},
    corrections=("line_text", "line_lang"),
    run=_run,
)
