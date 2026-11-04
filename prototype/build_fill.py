"""Build a per-note lyric plan from aligned subtitle lines.

Usage: python -I build_fill.py <sv_notes.json> <ass_lines.json> <align.json> <lead_dry.wav> <plan.json>

Within each line, the first note always starts a syllable. If a line has k more
notes than syllables, the k internal boundaries with the weakest syllable-onset
evidence (energy dip, spectral flux, timbre change) get "-" (sustain previous
syllable). Multi-syllable English words put the word on the first note and "+"
on the following syllable notes. Prints counts and note indices only.
"""

import json
import sys

import librosa
import numpy as np


def main() -> None:
    sv_json, ass_json, align_json, wav, plan_out = sys.argv[1:6]
    d = json.loads(open(sv_json, encoding="utf8").read())
    g = d["groups"][0]
    ix = {c: i for i, c in enumerate(g["notes"]["columns"])}
    notes = {r[ix["noteIndex"]]: (r[ix["absoluteOnsetSeconds"]], r[ix["absoluteDurationSeconds"]], r[ix["pitch"]])
             for r in g["notes"]["rows"]}
    lines = json.load(open(ass_json, encoding="utf8"))
    al = json.load(open(align_json, encoding="utf8"))

    y, sr = librosa.load(wav, sr=44100, mono=True)
    hop = 220
    rms_db = librosa.amplitude_to_db(librosa.feature.rms(y=y, frame_length=1024, hop_length=hop)[0])
    flux = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop, n_mels=80)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=20, hop_length=hop, n_fft=2048)[1:]
    nf = len(rms_db)
    fr = lambda t: int(np.clip(round(t * sr / hop), 0, nf - 1))
    flux_ref = np.median(flux[flux > 0])

    # Onset evidence for the boundary *before* note n (n >= 2).
    raw = {}
    for n in range(2, len(notes) + 1):
        s0, d0, _ = notes[n - 1]
        s1, d1, _ = notes[n]
        e0 = s0 + d0
        gap = s1 - e0
        b = fr(s1)
        interior = np.concatenate([rms_db[fr(s0 + 0.25 * d0): max(fr(e0 - 0.03), fr(s0 + 0.25 * d0) + 1)],
                                   rms_db[fr(s1 + 0.03): max(fr(s1 + 0.75 * d1), fr(s1 + 0.03) + 1)]])
        dip = float(np.median(interior) - rms_db[max(b - 10, 0): b + 11].min())
        fpk = float(flux[max(b - 8, 0): b + 9].max() / flux_ref)
        v0 = mfcc[:, fr(max(s0, e0 - 0.10)): fr(e0 - 0.02) + 1].mean(axis=1)
        v1 = mfcc[:, fr(s1 + 0.04): fr(min(s1 + d1, s1 + 0.14)) + 1].mean(axis=1)
        raw[n] = (dip, fpk, float(np.linalg.norm(v0 - v1)), gap)
    keys = sorted(raw)
    score = {}
    for j in range(3):
        vals = np.array([raw[k][j] for k in keys])
        pct = vals.argsort().argsort() / (len(vals) - 1)
        for k, p in zip(keys, pct):
            score[k] = score.get(k, 0.0) + p / 3
    for k in keys:
        if raw[k][3] > 0.03:  # separated by a gap: a new syllable is very likely
            score[k] += 1.0

    plan, report = [], []
    for li, (idxs, ln, syll) in enumerate(zip(al["assign"], lines, al["syll"]), 1):
        slots = []
        for tok, s in zip(ln["tokens"], syll):
            slots.append(tok)
            slots.extend(["+"] * (s - 1))
        extra = len(idxs) - len(slots)
        if extra < 0:
            report.append((li, len(idxs), len(slots), "MISSING", []))
            continue
        internal = sorted(idxs[1:], key=lambda n: score[n])[:extra]
        dash = set(internal)
        it = iter(slots)
        for n in idxs:
            plan.append({"noteIndex": n, "lyric": "-" if n in dash else next(it)})
        plus = sum(1 for s in slots if s == "+")
        report.append((li, len(idxs), len(slots), plus, sorted(dash)))

    plan.sort(key=lambda p: p["noteIndex"])
    assert [p["noteIndex"] for p in plan] == list(range(1, len(notes) + 1)), "plan must cover every note once"
    with open(plan_out, "w", encoding="utf8") as f:
        json.dump(plan, f, ensure_ascii=False)

    total_dash = sum(len(r[4]) for r in report)
    print(f"notes={len(plan)} dash_notes={total_dash} plus_notes={sum(r[3] for r in report if r[3] != 'MISSING')}")
    for li, nn, ns, plus, dash in report:
        if dash or plus:
            ds = ", ".join(f"{n}@{notes[n][0]:.2f}s(score {score[n]:.2f})" for n in dash)
            print(f"L{li:02d} notes={nn} syllables={ns} plus={plus} dash=[{ds}]")


if __name__ == "__main__":
    main()
