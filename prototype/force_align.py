"""CTC forced alignment of subtitle lyrics to the dry lead vocal, then note assignment.

Usage:
  python -I force_align.py <lead_dry.wav> <ass_lines.json> <sv_notes.json> <sv_now.json>
                           <old_plan.json> <out_plan.json> <out_paste.txt>

Prints timings, counts and note indices only -- never lyric text.
"""

import json
import re
import sys

import numpy as np
import torch
import torchaudio
import torchaudio.functional as AF
import ToJyutping
from opencc import OpenCC
from torchaudio.pipelines import MMS_FA

FRAME = 320 / 16000  # MMS emission hop in seconds


def english_syllables(word: str) -> int:
    w = word.lower().strip("'")
    n = len(re.findall(r"[aeiouy]+", w))
    if n > 1 and w.endswith("e") and not w.endswith(("le", "ee")):
        n -= 1
    return max(n, 1)


def build_units(lines):
    cc = OpenCC("s2hk")
    units = []  # one per subtitle token
    for li, ln in enumerate(lines):
        cjk = [t for t in ln["tokens"] if not t[0].isascii()]
        jy = ToJyutping.get_jyutping_list(cc.convert("".join(cjk))) if cjk else []
        it = iter(jy)
        for t in ln["tokens"]:
            if t[0].isascii():
                letters = re.sub(r"[^a-z']", "", t.lower())
                units.append({"line": li, "tok": t, "eng": True, "syll": english_syllables(t), "letters": letters})
            else:
                ch, reading = next(it)
                letters = re.sub(r"[^a-z]", "", (reading or "").split()[0] if reading else "")
                units.append({"line": li, "tok": t, "eng": False, "syll": 1, "letters": letters})
    return units


def emissions(wav_path: str, device: str):
    import soundfile as sf
    import soxr

    data, sr = sf.read(wav_path, always_2d=True, dtype="float32")
    mono = soxr.resample(data.mean(axis=1), sr, 16000, quality="VHQ").astype(np.float32)
    wav = torch.from_numpy(mono).unsqueeze(0)
    model = MMS_FA.get_model(with_star=False).to(device).eval()
    chunk, ctx = 20 * 16000, 2 * 16000
    n = wav.shape[1]
    outs = []
    with torch.inference_mode():
        for s in range(0, n, chunk):
            a, b = max(0, s - ctx), min(n, s + chunk + ctx)
            em, _ = model(wav[:, a:b].to(device))
            em = torch.log_softmax(em, dim=-1)[0].cpu()
            f0 = round((s - a) / 320)
            f1 = f0 + round((min(s + chunk, n) - s) / 320)
            outs.append(em[f0:f1])
    return torch.cat(outs)  # (T, C)


def load_notes(path):
    g = json.loads(open(path, encoding="utf8").read())["groups"][0]
    ix = {c: i for i, c in enumerate(g["notes"]["columns"])}
    rows = sorted(g["notes"]["rows"], key=lambda r: r[ix["noteIndex"]])
    return [{"index": r[ix["noteIndex"]], "start": r[ix["absoluteOnsetSeconds"]],
             "dur": r[ix["absoluteDurationSeconds"]], "lyric": r[ix["lyrics"]]} for r in rows]


def assign(syl_t, notes, gap_pen=0.3, lead_pen=2.0, cap=1.5):
    """Monotonic DP: each syllable to a distinct note; skipped notes become '-'."""
    N, M = len(syl_t), len(notes)
    on = np.array([n["start"] for n in notes])
    gaps = np.r_[np.inf, on[1:] - (on[:-1] + np.array([n["dur"] for n in notes[:-1]]))]
    skip = np.where(gaps > 0.25, gap_pen, 0.0)  # penalty if this note is a '-' after a rest
    S = np.cumsum(skip)  # S[j] = sum skip[0..j]
    cost = np.minimum(np.abs(np.array(syl_t)[:, None] - on[None, :]), cap)
    INF = 1e18
    dp = np.full((N, M), INF)
    back = np.zeros((N, M), dtype=int)
    dp[0] = cost[0] + lead_pen * np.arange(M)
    for i in range(1, N):
        prev = dp[i - 1] - S  # dp[i-1][j'] - S[j']
        best, arg = INF, -1
        run_min = np.full(M, INF)
        run_arg = np.zeros(M, dtype=int)
        for j in range(M):
            run_min[j], run_arg[j] = best, arg
            if prev[j] < best:
                best, arg = prev[j], j
        valid = run_arg >= 0
        dp[i, valid] = cost[i, valid] + np.r_[0, S[:-1]][valid] + run_min[valid]
        back[i] = run_arg
    final = dp[N - 1] + (S[-1] - S)
    j = int(np.argmin(final))
    path = [j]
    for i in range(N - 1, 0, -1):
        j = back[i, j]
        path.append(j)
    return path[::-1]


def main() -> None:
    wav, ass_json, sv_notes, sv_now, old_plan, out_plan, out_paste = sys.argv[1:8]
    lines = json.load(open(ass_json, encoding="utf8"))
    units = build_units(lines)
    dictionary = MMS_FA.get_dict(star=None)
    empty = [k for k, u in enumerate(units) if not u["letters"]]
    print(f"units={len(units)} cjk={sum(not u['eng'] for u in units)} eng={sum(u['eng'] for u in units)} "
          f"units_without_letters={len(empty)}")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    em = emissions(wav, device)
    print(f"emission frames={em.shape[0]} ({em.shape[0]*FRAME:.1f} s) classes={em.shape[1]} device={device}")

    targets, owner = [], []
    for k, u in enumerate(units):
        for ch in u["letters"]:
            targets.append(dictionary[ch])
            owner.append(k)
    tgt = torch.tensor([targets], dtype=torch.int32)
    ali, scores = AF.forced_align(em.unsqueeze(0), tgt, blank=0)
    spans = AF.merge_tokens(ali[0], scores[0].exp())
    assert len(spans) == len(targets), (len(spans), len(targets))
    for k in range(len(units)):
        units[k]["frames"] = []
    for sp, k in zip(spans, owner):
        units[k]["frames"].append(sp)
    for u in units:
        u["t0"] = u["frames"][0].start * FRAME
        u["t1"] = u["frames"][-1].end * FRAME
        u["conf"] = float(np.mean([sp.score for sp in u["frames"]]))

    # Syllable start times (English multi-syllable words spread over the word span).
    syl = []
    for k, u in enumerate(units):
        for s in range(u["syll"]):
            syl.append({"unit": k, "t": u["t0"] + s * (u["t1"] - u["t0"]) / u["syll"], "first": s == 0})
    notes = load_notes(sv_notes)
    path = assign([s["t"] for s in syl], notes)

    plan = {n["index"]: "-" for n in notes}
    line_of_note = {}
    for s, j in zip(syl, path):
        u = units[s["unit"]]
        plan[notes[j]["index"]] = u["tok"] if s["first"] else "+"
        line_of_note[notes[j]["index"]] = u["line"]
    cur = 0
    for n in notes:  # '-' notes belong to the line of the syllable they sustain
        cur = line_of_note.get(n["index"], cur)
        line_of_note[n["index"]] = cur

    dev = np.array([abs(s["t"] - notes[j]["start"]) for s, j in zip(syl, path)])
    print(f"syllables={len(syl)} notes={len(notes)} dash={sum(v == '-' for v in plan.values())} "
          f"plus={sum(v == '+' for v in plan.values())}")
    print(f"|syllable start - note onset|: median {np.median(dev)*1000:.0f} ms, p90 {np.percentile(dev,90)*1000:.0f} ms, "
          f">300 ms: {int(np.sum(dev > 0.3))}")
    confs = np.array([u["conf"] for u in units])
    print(f"alignment confidence per token: median {np.median(confs):.2f}, <0.2: {int(np.sum(confs < 0.2))}")

    # Compare with the lyrics currently in SV (user's manual fixes) and the old plan.
    now = {n["index"]: n["lyric"] for n in load_notes(sv_now)}
    old = {p["noteIndex"]: p["lyric"] for p in json.load(open(old_plan, encoding="utf8"))}
    for name, lo, hi in (("L02 (your fix)", 9, 28), ("L10 (your fix)", 62, 71)):
        rng = range(lo, hi + 1)
        print(f"{name}: new plan equals current SV on {sum(plan[n] == now[n] for n in rng)}/{len(rng)} notes; "
              f"old plan did {sum(old[n] == now[n] for n in rng)}/{len(rng)}")
    print(f"whole song: new plan equals current SV on {sum(plan[n] == now[n] for n in plan)}/{len(plan)}")

    print("lines where the new plan differs from current SV:")
    by_line = {}
    for n in notes:
        if plan[n["index"]] != now[n["index"]]:
            by_line.setdefault(line_of_note[n["index"]], []).append(n["index"])
    for li in sorted(by_line):
        lu = [u for u in units if u["line"] == li]
        print(f"  L{li+1:02d} notes {by_line[li][0]}-{by_line[li][-1]} ({len(by_line[li])} differ)  "
              f"aligned {lu[0]['t0']:.2f}-{lu[-1]['t1']:.2f}s vs subtitle {lines[li]['start']:.2f}-{lines[li]['end']:.2f}s  "
              f"min conf {min(u['conf'] for u in lu):.2f}")

    with open(out_plan, "w", encoding="utf8") as f:
        json.dump([{"noteIndex": k, "lyric": v, "line": line_of_note[k]} for k, v in sorted(plan.items())], f,
                  ensure_ascii=False)
    rows = {}
    for k in sorted(plan):
        rows.setdefault(line_of_note[k], []).append(plan[k])
    with open(out_paste, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write("\n".join(" ".join(rows[li]) for li in sorted(rows)) + "\n")
    print(f"paste tokens={sum(len(r) for r in rows.values())}")


if __name__ == "__main__":
    main()
