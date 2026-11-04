"""11 harmony ("和声歌词"): give the harmony notes their lyrics.

Backing singers mostly sing the lead's words in the lead's rhythm, so each
harmony note takes the syllable the lead is singing at that moment:

- starts with a lead syllable (within `tolerance`): copy it (word, `-` or `+`);
- starts later, right after the previous harmony note: `-` (a melisma);
- starts later, after a gap: the lead's current word, flagged `late_onset`;
- no lead note at that time (the harmony sings alone): `-` if it continues
  the previous harmony note, otherwise `alone_lyric`, flagged `alone`.

A `-` or `+` is only kept when the previous harmony note carries the same
word; otherwise the word itself is written. Notes more than 16 semitones
from the lead are flagged `octave` (SOME sometimes jumps an octave).
"""

from __future__ import annotations

import json
from typing import Any

from bapu.stages.base import Stage, StageContext, StageResult


def word_ids(lead: list[dict[str, Any]]) -> list[int]:
    """For each lead note, the index of the word it belongs to."""
    out, w = [], -1
    for n in lead:
        if n["kind"] == "word" or w < 0:
            w += 1
        out.append(w)
    return out


def assign_lyrics(backing: list[dict[str, Any]], lead: list[dict[str, Any]], *, tolerance: float, join_gap: float,
                  alone_lyric: str, default_lang: str) -> list[dict[str, Any]]:
    wid = word_ids(lead)
    words: dict[int, str] = {}
    for n, w in zip(lead, wid):
        if n["kind"] == "word":
            words.setdefault(w, n["lyric"])

    def lead_at(t: float) -> int | None:
        for k, n in enumerate(lead):
            if n["start"] <= t < n["start"] + n["dur"]:
                return k
        return None

    backing = sorted(backing, key=lambda n: n["start"])
    out: list[dict[str, Any]] = []
    prev_word: int | None = None
    prev_end = -1.0
    lang, line = default_lang, (lead[0]["line"] if lead else 1)
    for i, b in enumerate(backing):
        k = lead_at(b["start"] + b["dur"] / 2)
        joined = out and b["start"] - prev_end <= join_gap
        flags: list[str] = []
        interval = None
        if k is not None:
            ln = lead[k]
            lang, line = ln["lang"], ln["line"]
            interval = round(b["pitch"]) - ln["pitch"]
            if abs(interval) > 16:
                flags.append("octave")
            if abs(b["start"] - ln["start"]) <= tolerance:
                word = wid[k]
                lyric = ln["lyric"]
                if ln["kind"] != "word" and prev_word != word:
                    lyric = words.get(word, alone_lyric)  # a "-"/"+" needs its own word before it
                    flags.append("partial_word")
            elif joined:
                word, lyric = prev_word, "-"
            else:
                word = wid[k]
                lyric = words.get(word, alone_lyric)
                flags.append("late_onset")
        else:
            flags.append("alone")
            if joined:
                word, lyric = prev_word, "-"
            else:
                word, lyric = None, alone_lyric
        kind = "sustain" if lyric == "-" else "cont" if lyric == "+" else "word"
        out.append({
            "index": i + 1, "start": b["start"], "dur": b["dur"],
            "pitch": int(round(b["pitch"])), "cents": int(round((b["pitch"] - round(b["pitch"])) * 100)),
            "kind": kind, "lyric": lyric, "lang": lang, "line": line, "interval": interval,
            "level_db": b.get("level_db"), "flags": flags,
        })
        prev_word, prev_end = word, b["start"] + b["dur"]
    return out


def _run(ctx: StageContext) -> StageResult:
    p = ctx.params
    backing = json.loads(ctx.inputs["backing_notes"].read_text(encoding="utf-8"))["notes"]
    score = json.loads(ctx.inputs["score"].read_text(encoding="utf-8"))
    notes = assign_lyrics(backing, score["notes"], tolerance=float(p["tolerance"]), join_gap=float(p["join_gap"]),
                          alone_lyric=str(p["alone_lyric"]), default_lang=score.get("lang", "yue"))
    out = ctx.out_dir / "harmony.json"
    out.write_text(json.dumps({"notes": notes}, ensure_ascii=False, indent=1), encoding="utf-8")
    count = lambda f: sum(1 for n in notes if f in n["flags"])  # noqa: E731
    return StageResult(outputs={"harmony": out}, metrics={
        "notes": len(notes),
        "words": sum(1 for n in notes if n["kind"] == "word"),
        "sustain": sum(1 for n in notes if n["kind"] == "sustain"),
        "alone": count("alone"), "late_onset": count("late_onset"), "octave": count("octave"),
    })


STAGE = Stage(
    name="harmony",
    title="和声歌词",
    index=11,
    version="1",
    inputs=("backing_notes", "score"),
    outputs=("harmony",),
    defaults={"tolerance": 0.06, "join_gap": 0.1, "alone_lyric": "啊"},
    run=_run,
)
