"""Find notes that SOME likely split out of one sung syllable.

Usage: python -I split_check.py <file.mid> <notes.json> <lead_dry.wav> <out.json>

For every pair of touching notes, measure syllable-onset evidence at the boundary
in the dry lead vocal: an energy dip, a spectral-flux peak, and a timbre (MFCC)
change between the two vowels. A boundary with little of all three is most
likely a pitch change inside one syllable (an extra note), not a new syllable.
"""

import json
import sys

import librosa
import mido
import numpy as np

NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def name(p: int) -> str:
    return f"{NAMES[p % 12]}{p // 12 - 1}"


def mmss(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:05.2f}"


def load_notes(path: str):
    mf = mido.MidiFile(path)
    tempo = next(m.tempo for t in mf.tracks for m in t if m.type == "set_tempo")
    sec_per_tick = tempo / 1e6 / mf.ticks_per_beat
    notes, active, t = [], {}, 0
    for m in mf.tracks[0]:
        t += m.time
        if m.type == "note_on" and m.velocity > 0:
            active[m.note] = t
        elif m.type in ("note_off", "note_on"):
            s = active.pop(m.note)
            notes.append((s * sec_per_tick, t * sec_per_tick, m.note))
    notes.sort()
    return notes


def main() -> None:
    mid, notes_json, wav, out_json = sys.argv[1:5]
    notes = load_notes(mid)
    float_pitch = [n["pitch"] for n in json.load(open(notes_json, encoding="utf8"))["notes"]]

    y, sr = librosa.load(wav, sr=44100, mono=True)
    hop = 220  # 5 ms
    rms_db = librosa.amplitude_to_db(librosa.feature.rms(y=y, frame_length=1024, hop_length=hop)[0], ref=1.0)
    flux = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop, n_mels=80)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=20, hop_length=hop, n_fft=2048)[1:]
    nf = len(rms_db)
    fr = lambda t: int(np.clip(round(t * sr / hop), 0, nf - 1))
    flux_ref = np.median(flux[flux > 0])

    # Phrases: split at gaps >= 300 ms.
    phrases, cur = [], [0]
    for i in range(1, len(notes)):
        if notes[i][0] - notes[i - 1][1] >= 0.3:
            phrases.append(cur)
            cur = []
        cur.append(i)
    phrases.append(cur)
    phrase_of = {i: k for k, ph in enumerate(phrases) for i in ph}

    rows = []
    for i in range(len(notes) - 1):
        (s0, e0, p0), (s1, e1, p1) = notes[i], notes[i + 1]
        if s1 - e0 > 0.001:
            continue  # separated by a rest: certainly a new syllable
        b = fr(e0)
        a0, a1 = fr(s0 + 0.25 * (e0 - s0)), fr(e0 - 0.03)
        c0, c1 = fr(s1 + 0.03), fr(s1 + 0.75 * (e1 - s1))
        interior = np.concatenate([rms_db[a0:max(a1, a0 + 1)], rms_db[c0:max(c1, c0 + 1)]])
        win = rms_db[max(b - 10, 0): b + 11]  # +-50 ms
        dip = float(np.median(interior) - win.min())
        fpk = float(flux[max(b - 8, 0): b + 9].max() / flux_ref)  # +-40 ms
        v0 = mfcc[:, fr(max(s0, e0 - 0.10)): fr(e0 - 0.02) + 1].mean(axis=1)
        v1 = mfcc[:, fr(s1 + 0.04): fr(min(e1, s1 + 0.14)) + 1].mean(axis=1)
        timbre = float(np.linalg.norm(v0 - v1))
        rows.append({
            "a": i + 1, "b": i + 2, "phrase": phrase_of[i] + 1, "time": e0,
            "p0": p0, "p1": p1, "d0": e0 - s0, "d1": e1 - s1,
            "fp0": float_pitch[i] if i < len(float_pitch) else None,
            "fp1": float_pitch[i + 1] if i + 1 < len(float_pitch) else None,
            "dip": dip, "flux": fpk, "timbre": timbre,
        })

    # Rank each cue across all touching boundaries; low on all = likely split.
    for key in ("dip", "flux", "timbre"):
        vals = np.array([r[key] for r in rows])
        ranks = vals.argsort().argsort() / (len(vals) - 1)
        for r, q in zip(rows, ranks):
            r[key + "_pct"] = float(q)
    for r in rows:
        r["onset_score"] = (r["dip_pct"] + r["flux_pct"] + r["timbre_pct"]) / 3

    rows.sort(key=lambda r: r["onset_score"])
    print(f"notes={len(notes)} phrases={len(phrases)} touching_boundaries={len(rows)}")
    print("\nphrases (gap >= 300 ms):")
    for k, ph in enumerate(phrases):
        s, e = notes[ph[0]][0], notes[ph[-1]][1]
        print(f"  P{k+1:02d} {mmss(s)}-{mmss(e)} notes {ph[0]+1}-{ph[-1]+1} ({len(ph)})")
    print("\nboundaries with the weakest syllable-onset evidence (likely extra notes):")
    print("  rank pair      phrase time      pitch        dur ms     dip dB flux  timbre score")
    for k, r in enumerate(rows[:25]):
        print(f"  {k+1:>3}  {r['a']:>3}-{r['b']:<3}  P{r['phrase']:02d}  {mmss(r['time'])}  "
              f"{name(r['p0']):>3}->{name(r['p1']):<3}  {r['d0']*1000:4.0f}/{r['d1']*1000:<4.0f}  "
              f"{r['dip']:5.1f}  {r['flux']:4.2f}  {r['timbre']:5.1f}  {r['onset_score']:.2f}")
    with open(out_json, "w", encoding="utf8") as f:
        json.dump({"phrases": [[i + 1 for i in ph] for ph in phrases], "boundaries": rows}, f)


if __name__ == "__main__":
    main()
