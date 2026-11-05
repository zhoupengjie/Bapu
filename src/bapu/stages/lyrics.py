"""07 lyrics: fuse dictation with the user's reference lyric file (if any).

See `bapu.lyrics.fuse` for the rules. Output `lyrics.json` holds lyric text and
stays in the (gitignored) work directory; `fusion.json` lists what dictation
changed, by line number and time only. Metrics carry counts only.

Languages (`langs`, shared with the dictation stage): any of `yue` (Cantonese),
`zh` (Mandarin), `en` (English) and `ja` (Japanese). Latin words are English
(in a Japanese song: unless the line reads as romaji, below). Chinese
characters are read as Cantonese if it is selected, otherwise
Mandarin; a `line_lang` correction switches single lines (e.g. the Mandarin
lines of a mixed song). Neither the speech model's language detection nor the
aligner's confidence can tell sung Cantonese from Mandarin (notes), so this is
the user's call, not guessed.

Japanese may only be combined with English: kanji are shared with Chinese, so
a character's language must follow from the song. In a Japanese song, kanji
and kana become hiragana morae, one token (and one syllable) each
(bapu.lyrics.japanese), keyed by their Hepburn letters. Lyrics written in
romaji are decided per line (bapu.lyrics.parse.romaji_plan): a romaji line's
words become hiragana morae the same way, the rest stay English words. Lines
decided on mixed evidence are counted (`romaji_uncertain_lines`) and listed in
the review report; `line_lang` `ja` / `en` settles a line.
"""

from __future__ import annotations

import json
from typing import Any

from bapu.lyrics import g2p
from bapu.lyrics.fuse import Tok, dictation_tokens, fuse, keys_for
from bapu.lyrics.parse import parse_file, tokenize, tokenize_line
from bapu.stages.base import Stage, StageContext, StageResult

SUPPORTED = ("yue", "zh", "en", "ja")


def check_langs(langs: Any) -> list[str]:
    if isinstance(langs, str):
        langs = [x.strip() for x in langs.split(",") if x.strip()]
    langs = [str(x) for x in langs]
    bad = [x for x in langs if x not in SUPPORTED]
    if bad or not langs:
        raise ValueError(f"不支持的语言 {', '.join(bad) or '（空）'}；"
                         "可选：yue（粤语）、zh（普通话）、en（英语）、ja（日语）")
    if "ja" in langs and any(x in ("yue", "zh") for x in langs):
        raise ValueError("日语不能和粤语、普通话一起选：汉字在这几种语言里写法相同，没法判断该按哪种语言读。"
                         "日语只能单独选，或者和英语（en）一起选")
    return langs


def chinese_of(langs: list[str]) -> list[str]:
    """The selected Chinese languages, in order; Cantonese if none was selected."""
    return [x for x in langs if x in ("yue", "zh")] or ["yue"]


def main_lang(langs: list[str]) -> str:
    """The language CJK characters are read in by default: Japanese if selected
    (it excludes Chinese), else the first Chinese language."""
    return "ja" if "ja" in langs else chinese_of(langs)[0]


def latin_uncertain(latin: dict[str, Any]) -> bool:
    """A line's romaji / English reading was decided by mixed votes (and not set by the user)."""
    return not latin["forced"] and 0 < latin["romaji_votes"] < latin["votes"]


def _tok_json(t: Tok, line_lang: dict[int, str]) -> dict[str, Any]:
    def one(x: Tok) -> dict[str, Any]:
        latin = g2p.is_latin(x.text)
        d = {"text": x.text, "letters": x.key,
             "lang": line_lang.get(x.line) or ("en" if latin else x.lang),
             "syll": g2p.english_syllables(x.text) if latin else 1}
        return d
    d = one(t)
    d.update(source=t.source, optional=t.optional, group=t.group, flags=t.flags,
             alt=one(t.alt) if t.alt else None)
    return d


def _run(ctx: StageContext) -> StageResult:
    langs = check_langs(ctx.params["langs"])
    default = main_lang(langs)
    # Chinese lines can switch between Cantonese and Mandarin; a Japanese song's
    # characters are read as Japanese throughout, and `ja` on a line reads its
    # Latin words as romaji. Any line can be set to English (in a Japanese song
    # its Latin words then stay English words).
    japanese = default == "ja"
    switchable = ("ja",) if japanese else ("yue", "zh")
    used: set[int] = set()
    dictation = json.loads(ctx.inputs["dictation"].read_text(encoding="utf-8"))["phrases"]
    text_fix = {int(c["line"]): c for c in ctx.corrections if c["type"] == "line_text"}
    forced = {int(c["line"]): str(c["lang"]) for c in ctx.corrections
              if c["type"] == "line_lang" and str(c["lang"]) in switchable + ("en",)}

    def tok(text: str) -> list[str]:
        return tokenize(text, langs)

    if "lyrics" in ctx.inputs:
        lines = [{"line": i, "start": ln.start, "end": ln.end, "text": ln.text, "tokens": ln.tokens, "latin": ln.latin}
                 for i, ln in enumerate(parse_file(ctx.inputs["lyrics"], langs), 1)]
        mode = "fused"
    else:
        lines = [{"line": i, "start": p["t0"], "end": p["t1"], "text": p["text"]}
                 for i, p in enumerate(dictation, 1)]
        for ln in lines:
            ln["tokens"], ln["latin"] = tokenize_line(ln["text"], langs)
        mode = "dictation_only"
    for ln in lines:
        if ln["line"] in text_fix:
            ln["text"] = str(text_fix[ln["line"]]["text"])
            used.add(text_fix[ln["line"]]["_id"])
        if ln["line"] in text_fix or japanese and ln["line"] in forced:
            romaji = {"ja": True, "en": False}.get(forced.get(ln["line"], "")) if japanese else None
            ln["tokens"], ln["latin"] = tokenize_line(ln["text"], langs, romaji)
    for c in ctx.corrections:
        if (c["type"] == "line_lang" and str(c["lang"]) in switchable + ("en",)
                and any(ln["line"] == int(c["line"]) for ln in lines)):
            used.add(c["_id"])

    l_langs = {ln["line"]: default for ln in lines}
    for line, lang in forced.items():
        if lang in switchable:
            l_langs[line] = lang
    ref: list[Tok] = []
    for ln in lines:
        src = "user" if ln["line"] in text_fix else "ref"
        lang = l_langs[ln["line"]]
        ref += [Tok(t, k, ln["line"], source=src, lang=lang) for t, k in zip(ln["tokens"], keys_for(ln["tokens"], lang))]
    timed = [(ln["start"], ln["end"], l_langs[ln["line"]]) for ln in lines
             if ln["start"] is not None and ln["end"] is not None and l_langs[ln["line"]] != default]

    def lang_at(t: float) -> str:
        return next((lang for a, b, lang in timed if a - 0.3 <= t <= b + 0.3), default)

    if mode == "fused":
        fused, events = fuse(ref, dictation_tokens(dictation, tok, lang_at),
                             locked_lines=set(text_fix), max_insert=int(ctx.params["max_insert"]))
    else:
        fused, events = ref, []
        for t in fused:
            if t.source == "ref":
                t.source = "dictation"
                t.flags.append("unverified")

    english_lines = {line for line, lang in forced.items() if lang == "en"}
    out_lines = []
    for ln in lines:
        toks = [_tok_json(t, {x: "en" for x in english_lines}) for t in fused if t.line == ln["line"]]
        out_lines.append({"line": ln["line"], "start": ln["start"], "end": ln["end"],
                          "lang": l_langs.get(ln["line"], default), "tokens": toks})
        if ln["latin"]:
            out_lines[-1]["latin"] = ln["latin"]  # counts only: how its Latin words were read
    out_lines = [ln for ln in out_lines if ln["tokens"]]
    out = ctx.out_dir / "lyrics.json"
    out.write_text(json.dumps({"lang": default, "langs": langs, "mode": mode, "lines": out_lines},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    (ctx.out_dir / "fusion.json").write_text(json.dumps(events, indent=1), encoding="utf-8")
    (ctx.out_dir / "corrections_used.json").write_text(json.dumps(sorted(used)), encoding="utf-8")
    toks = [t for ln in out_lines for t in ln["tokens"]]
    romaji_metrics = {}
    if japanese:
        latin = [ln["latin"] for ln in out_lines if ln.get("latin")]
        romaji_metrics = {"romaji_lines": sum(1 for x in latin if x["romaji_words"]),
                          "english_lines": sum(1 for x in latin if x["read_as"] == "en"),
                          "romaji_uncertain_lines": sum(1 for x in latin if latin_uncertain(x))}
    return StageResult(
        outputs={"lyrics_lines": out, "fusion": ctx.out_dir / "fusion.json"},
        metrics={
            "mode": mode, "langs": ",".join(langs), "lines": len(out_lines), "tokens": len(toks),
            "syllables": sum(t["syll"] for t in toks if not t["optional"]),
            "lines_zh": sum(1 for ln in out_lines if ln["lang"] == "zh"),
            "agreed": sum(1 for t in toks if t["source"] == "both"),
            "inserted_candidates": sum(1 for t in toks if t["optional"]),
            "alternatives": sum(1 for t in toks if t["alt"]),
            "gaps_kept_reference": sum(1 for e in events if e["type"] in ("differs", "extra_ignored")),
            "no_reading": sum(1 for t in toks if not t["letters"]),
            **romaji_metrics,
        },
    )


STAGE = Stage(
    name="lyrics",
    title="歌词（听写 + 参考）",
    index=7,
    version="12",
    inputs=("dictation", "lyrics"),
    optional_inputs=("lyrics",),
    outputs=("lyrics_lines", "fusion"),
    defaults={"langs": ["yue", "en"], "max_insert": 4},
    corrections=("line_text", "line_lang"),
    run=_run,
)
