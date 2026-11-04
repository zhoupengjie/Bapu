"""Align SV notes to timed subtitle lines and compare syllable vs note counts.

Usage: python -I align_lyrics.py <sv_notes_dump.txt> <ass_lines.json> <out.json>
Prints counts only (no lyric text).
"""

import json
import re
import sys

import numpy as np

QUARTER = 705600000
BPM = 123.0


def english_syllables(word: str) -> int:
    w = word.lower().strip("'")
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups)
    if n > 1 and w.endswith("e") and not w.endswith(("le", "ee")):
        n -= 1
    return max(n, 1)


def load_sv_notes(path: str):
    d = json.loads(open(path, encoding="utf8").read())
    g = d["groups"][0]
    cols, rows = g["notes"]["columns"], g["notes"]["rows"]
    ix = {c: i for i, c in enumerate(cols)}
    notes = [
        {"index": r[ix["noteIndex"]], "start": r[ix["absoluteOnsetSeconds"]],
         "dur": r[ix["absoluteDurationSeconds"]], "pitch": r[ix["pitch"]], "lyrics": r[ix["lyrics"]]}
        for r in rows
    ]
    return g, notes


def assign(notes, lines, delta):
    starts = np.array([ln["start"] for ln in lines]) + delta
    out = [[] for _ in lines]
    for n in notes:
        k = int(np.searchsorted(starts, n["start"], side="right") - 1)
        out[max(k, 0)].append(n["index"])
    return out


def main() -> None:
    dump, ass_json, out_json = sys.argv[1:4]
    g, notes = load_sv_notes(dump)
    lines = json.load(open(ass_json, encoding="utf8"))
    print(f"context={g['contextId']} noteCount={g['noteCount']} returned={len(notes)} "
          f"lyrics_now={sorted({n['lyrics'] for n in notes})}")
    for ln in lines:
        ln["syll"] = [1 if not t[0].isascii() else english_syllables(t) for t in ln["tokens"]]
        ln["n_syll"] = int(sum(ln["syll"]))

    best = None
    for delta in np.round(np.arange(-0.5, 0.501, 0.05), 2):
        a = assign(notes, lines, delta)
        match = sum(len(x) == ln["n_syll"] for x, ln in zip(a, lines))
        absdiff = sum(abs(len(x) - ln["n_syll"]) for x, ln in zip(a, lines))
        if best is None or (match, -absdiff) > (best[1], -best[2]):
            best = (float(delta), match, absdiff, a)
    delta, match, absdiff, a = best
    total_syll = sum(ln["n_syll"] for ln in lines)
    print(f"best offset={delta:+.2f} s  exact lines={match}/{len(lines)}  total |diff|={absdiff}  "
          f"syllables={total_syll} notes={len(notes)}")
    print("line  time            syll notes diff  note range")
    for i, (x, ln) in enumerate(zip(a, lines), 1):
        diff = len(x) - ln["n_syll"]
        flag = "" if diff == 0 else ("  <-- extra notes" if diff > 0 else "  <-- missing notes")
        rng = f"{x[0]}-{x[-1]}" if x else "-"
        print(f"L{i:02d}  {ln['start']:6.2f}-{ln['end']:6.2f}  {ln['n_syll']:4d} {len(x):5d} {diff:+4d}  {rng}{flag}")
    with open(out_json, "w", encoding="utf8") as f:
        json.dump({"offset": delta, "assign": a, "syll": [ln["syll"] for ln in lines]}, f)


if __name__ == "__main__":
    main()
