"""08 assign: put every syllable on its own note; build the score.

Monotonic dynamic programming assigns each syllable (aligned start time) to a
distinct note. Cost: `min(|syllable start - note onset|, cap)`. Notes left
without a syllable become `-` (sustain the previous word); doing that to a note
right after a rest costs `gap_pen`, and leading notes before the first syllable
cost `lead_pen` each. Later syllables of an English word become `+`.

Corrections enter as constraints or replacements:
- `kind`: a note forced to `sustain` cannot take a syllable; `word`/`cont`
  must take one.
- `line_version`: the user's own version of a line is used instead. Switching
  between the automatic and the manual version only happens where both have
  used the same number of syllables, so nothing is duplicated or dropped.
- `pitch`, `tempo`: replace the value in the score.
- `split`: `reject` keeps a note whole where it would be split; `confirm`
  splits it there.

Several syllables on one transcribed note: Japanese is often sung with several
morae on one pitch, and the transcriber gives them one note. So (always for
Japanese, otherwise only when there are more syllables than notes) a note is
split at the aligned start of a further syllable inside it, if the aligner was
confident about that syllable (`split_conf`) and both pieces are at least
`split_min_s` long. The pieces keep the pitch; every split is listed in the
review report.

A syllable may also stay without a note, at a cost that grows with how well
the aligner supports it (`drop_cost + drop_conf_cost * support`; support is the
syllable's confidence or its line's mean confidence, whichever is higher). So
lines the lead does not sing (backing vocals, say) are left out instead of
failing the run or pushing every later syllable off its note; they are listed
in the review report with their times. A well-aligned syllable costs more to
leave out than the furthest placement (`cap`).

The result's word sequence is checked against the lyric file token by token
(the placed tokens).
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from bapu.corrections import match_time
from bapu.lyrics import g2p
from bapu.stages.base import Stage, StageContext, StageResult

BIG = 1e6
WORD, SUSTAIN, CONT = "word", "sustain", "cont"
MARK = {SUSTAIN: "-", CONT: "+"}


def dp_assign(
    syl_t: np.ndarray, onsets: np.ndarray, ends: np.ndarray, *,
    gap_pen: float, lead_pen: float, cap: float, rest_gap: float,
    forbid: np.ndarray | None = None, require: np.ndarray | None = None,
) -> list[int]:
    """Note index for each syllable (strictly increasing)."""
    n_syl, m = len(syl_t), len(onsets)
    if n_syl > m:
        raise ValueError(f"音节（{n_syl}）比音符（{m}）多，无法一字一音")
    gaps = np.r_[np.inf, onsets[1:] - ends[:-1]]
    skip = np.where(gaps > rest_gap, gap_pen, 0.0)
    if require is not None:
        skip = skip + np.where(require, BIG, 0.0)
    s_cum = np.cumsum(skip)  # s_cum[j] = skip cost of notes 0..j
    cost = np.minimum(np.abs(syl_t[:, None] - onsets[None, :]), cap)
    if forbid is not None:
        cost[:, forbid] = BIG
    req_cost = np.where(require, BIG, 0.0) if require is not None else np.zeros(m)
    lead = lead_pen * np.arange(m) + np.r_[0.0, np.cumsum(req_cost)[:-1]]  # skipping notes 0..j-1

    dp = np.full((n_syl, m), np.inf)
    back = np.full((n_syl, m), -1, dtype=int)
    dp[0] = cost[0] + lead
    before = np.r_[0.0, s_cum[:-1]]  # skip cost of notes 0..j-1
    for i in range(1, n_syl):
        prev = dp[i - 1] - s_cum  # dp[i-1][j'] - skip(0..j')
        best, arg = np.inf, -1
        for j in range(m):
            if arg >= 0:
                dp[i, j] = cost[i, j] + before[j] + best
                back[i, j] = arg
            if prev[j] < best:
                best, arg = prev[j], j
    final = dp[-1] + (s_cum[-1] - s_cum)
    j = int(np.argmin(final))
    if not np.isfinite(final[j]):
        raise ValueError("找不到可行的分配")
    path = [j]
    for i in range(n_syl - 1, 0, -1):
        j = int(back[i, j])
        path.append(j)
    return path[::-1]


def dp_assign_dropping(
    syl_t: np.ndarray, onsets: np.ndarray, ends: np.ndarray, drop: np.ndarray, *,
    gap_pen: float, lead_pen: float, cap: float, rest_gap: float,
    forbid: np.ndarray | None = None, require: np.ndarray | None = None,
) -> list[int]:
    """Like `dp_assign`, but syllable i may also stay without a note at cost
    `drop[i]`; its entry in the result is then -1."""
    n_syl, m = len(syl_t), len(onsets)
    gaps = np.r_[np.inf, onsets[1:] - ends[:-1]]
    skip = np.where(gaps > rest_gap, gap_pen, 0.0)
    if require is not None:
        skip = skip + np.where(require, BIG, 0.0)
    s_cum = np.cumsum(skip)
    before = np.r_[0.0, s_cum[:-1]]
    cost = np.minimum(np.abs(syl_t[:, None] - onsets[None, :]), cap)
    if forbid is not None:
        cost[:, forbid] = BIG
    req_cost = np.where(require, BIG, 0.0) if require is not None else np.zeros(m)
    lead = lead_pen * np.arange(m) + np.r_[0.0, np.cumsum(req_cost)[:-1]]

    # dp[i, j]: syllables 0..i settled, the last placed one on note j.
    # back: -2 = syllable i dropped, -1 = first placed syllable, else previous note.
    dp = np.full((n_syl, m), np.inf)
    back = np.full((n_syl, m), -3, dtype=int)
    none = 0.0  # all syllables so far dropped
    for i in range(n_syl):
        prev = dp[i - 1] - s_cum if i else np.full(m, np.inf)
        best, arg = np.inf, -1
        for j in range(m):
            via_prev = best + before[j] if arg >= 0 else np.inf
            first = none + lead[j]
            place, src = (via_prev, arg) if via_prev <= first else (first, -1)
            place += cost[i, j]
            dropped = dp[i - 1, j] + drop[i] if i else np.inf
            if dropped < place:
                dp[i, j], back[i, j] = dropped, -2
            else:
                dp[i, j], back[i, j] = place, src
            if i and prev[j] < best:
                best, arg = prev[j], j
        none += drop[i]
    final = dp[-1] + (s_cum[-1] - s_cum)
    j = int(np.argmin(final))
    if not np.isfinite(final[j]):
        raise ValueError("找不到可行的分配")
    path = [-1] * n_syl
    for i in range(n_syl - 1, -1, -1):
        b = back[i, j]
        if b == -2:
            continue  # dropped; the last placed note stays j
        path[i] = j
        if b == -1:
            break  # earlier syllables were all dropped
        j = int(b)
    return path


def split_notes(notes: list[dict[str, Any]], syl: list[tuple[float, float]], *, min_s: float, min_conf: float,
                rejected: list[float], confirmed: list[float], tol: float
                ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split notes that carry several syllables. `syl`: (start, aligner
    confidence) per syllable. A note is cut at the start of every further
    syllable inside it that the aligner is confident about, keeping pieces of
    at least `min_s`; `rejected` / `confirmed` cut times (user corrections)
    override. Returns the new notes and one record per cut."""
    times = sorted(syl)
    out: list[dict[str, Any]] = []
    cuts: list[dict[str, Any]] = []
    for idx, n in enumerate(notes):
        a, b = n["start"], n["start"] + n["dur"]
        inside = [(t, c) for t, c in times if a - tol <= t < b]
        want = [(t, c, "auto") for t, c in inside[1:] if c >= min_conf]
        want += [(t, None, "user") for t in confirmed if a + min_s <= t <= b - min_s]
        points: list[tuple[float, float | None, str]] = []
        for t, c, src in sorted(want):
            if src == "auto" and match_time(sorted(confirmed), t, tol) is not None:
                continue  # the user's own cut time is used instead
            if src == "auto" and match_time(sorted(rejected), t, tol) is not None:
                continue
            last = points[-1][0] if points else a
            if t - last >= min_s and b - t >= min_s:
                points.append((t, c, src))
        if not points:
            out.append(n)
            continue
        edges = [a] + [t for t, _, _ in points] + [b]
        for k, (s, e) in enumerate(zip(edges, edges[1:])):
            piece = dict(n, start=round(s, 4), dur=round(e - s, 4))
            if k:
                piece["merges"] = []
                piece["split"] = {"source": points[k - 1][2], "conf": points[k - 1][1], "of": idx + 1}
            out.append(piece)
        cuts += [{"time": round(t, 3), "of": idx + 1, "conf": c, "source": src} for t, c, src in points]
    return out, cuts


def crowded_cuts(notes: list[dict[str, Any]], left: list[tuple[float, float]], *, min_s: float,
                 rejected: list[float], tol: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Cut notes for syllables the assignment left out where several crowd one
    note. `left`: (start, support) of those syllables. The note holding a
    syllable's start (or starting just after it) is cut there, or at its middle
    if that would leave a piece shorter than `min_s`; at most one cut per note."""
    out = [dict(n) for n in notes]
    cuts: list[dict[str, Any]] = []
    done: set[int] = set()
    for t, c in sorted(left):
        k = next((i for i, n in enumerate(out) if n["start"] - tol <= t < n["start"] + n["dur"]), None)
        if k is None or k in done:
            continue
        n = out[k]
        a, b = n["start"], n["start"] + n["dur"]
        cut = t if t - a >= min_s and b - t >= min_s else (a + b) / 2 if b - a >= 2 * min_s else None
        if cut is None or match_time(sorted(rejected), cut, tol) is not None:
            continue
        first = dict(n, dur=round(cut - a, 4))
        second = dict(n, start=round(cut, 4), dur=round(b - cut, 4), merges=[],
                      split={"source": "auto", "conf": c, "of": n.get("split", {}).get("of"), "crowded": True})
        out[k:k + 1] = [first, second]
        done.update({k, k + 1})  # later syllables lie in later notes: no index shift needed
        cuts.append({"time": round(cut, 3), "of": None, "conf": c, "source": "auto"})
    return out, cuts


def safe_merge(auto: list[str], manual: dict[int, str], want_manual: set[int]) -> tuple[list[str], int]:
    """Per-note lyrics: manual where wanted, switching sources only where both
    streams have consumed the same number of syllables. Returns (lyrics, late)."""
    consumes = lambda t: t != "-"  # noqa: E731
    out, source, pre_a, pre_m, late = [], "auto", 0, 0, 0
    for j, a in enumerate(auto):
        m = manual.get(j, a)
        want = "manual" if j in want_manual else "auto"
        if want != source:
            if pre_a == pre_m:
                source = want
            else:
                late += 1
        out.append(m if source == "manual" else a)
        pre_a += consumes(a)
        pre_m += consumes(m)
    return out, late


def kind_of(lyric: str) -> str:
    return SUSTAIN if lyric == "-" else CONT if lyric == "+" else WORD


def _run(ctx: StageContext) -> StageResult:
    p = ctx.params
    tol = float(p["tolerance"])
    notes = json.loads(ctx.inputs["merged_notes"].read_text(encoding="utf-8"))["notes"]
    ali = json.loads(ctx.inputs["alignment"].read_text(encoding="utf-8"))
    tempo = json.loads(ctx.inputs["tempo"].read_text(encoding="utf-8"))
    boundaries = json.loads(ctx.inputs["boundaries"].read_text(encoding="utf-8"))
    used: set[int] = set()
    conflicts: list[dict[str, Any]] = []

    # The aligner's output carries the final tokens (after dictation fusion).
    tokens = ali["tokens"]
    line_info = {ln["line"]: ln for ln in ali["lines"]}
    sub_lines: dict[int, dict[str, Any]] = {}
    for t in tokens:
        sub_lines.setdefault(t["line"], {"tokens": []})["tokens"].append(t)

    # Syllable start times. A later syllable of an English word starts where the
    # aligner put the first letter of that syllable; without letter times it is
    # spread evenly over the word.
    syl: list[tuple[int, bool, float]] = []  # (token index, first syllable?, time)
    for k, t in enumerate(tokens):
        starts = g2p.syllable_starts(t["text"]) if t["syll"] > 1 and g2p.is_latin(t["text"]) else None
        letter_t = t.get("letter_t")
        for s in range(t["syll"]):
            if s and starts and letter_t and len(starts) == t["syll"]:
                when = letter_t[starts[s]]
            else:
                when = t["t0"] + s * (t["t1"] - t["t0"]) / t["syll"]
            syl.append((k, s == 0, when))
    # How well the aligner supports each syllable: its own confidence, or its
    # line's mean if higher (a weak syllable between well-aligned neighbours
    # still sits about right; a line the lead does not sing is weak throughout).
    line_mean = {ln["line"]: float(ln.get("conf_mean", ln.get("conf") or 0.0)) for ln in ali["lines"]}
    syl_conf = np.array([max(float(tokens[k].get("conf", 1.0)), line_mean.get(tokens[k]["line"], 0.0))
                         for k, _, _ in syl])

    # Several syllables on one transcribed note: split it (see the module notes).
    split_fix = [c for c in ctx.corrections if c["type"] == "split"]
    japanese = any(t.get("lang") == "ja" for t in tokens)
    mode = str(p["split"])  # auto | on | off
    auto_split = mode == "on" or mode == "auto" and (japanese or len(syl) > len(notes))
    cuts: list[dict[str, Any]] = []
    if auto_split or split_fix:
        notes, cuts = split_notes(
            notes, [(w, float(c)) for (_, _, w), c in zip(syl, syl_conf)],
            min_s=float(p["split_min_s"]), min_conf=float(p["split_conf"]) if auto_split else float("inf"),
            rejected=[float(c["at"]) for c in split_fix if c["action"] == "reject"],
            confirmed=[float(c["at"]) for c in split_fix if c["action"] == "confirm"], tol=tol)
    rejected = [float(c["at"]) for c in split_fix if c["action"] == "reject"]

    def place(notes: list[dict[str, Any]]):
        onsets = np.array([n["start"] for n in notes])
        ends = onsets + np.array([n["dur"] for n in notes])
        forbid = np.zeros(len(notes), bool)
        require = np.zeros(len(notes), bool)
        for c in (c for c in ctx.corrections if c["type"] == "kind"):
            j = match_time(onsets.tolist(), float(c["at"]), tol)
            if j is not None:
                (forbid if c["kind"] == SUSTAIN else require)[j] = True
        # A syllable may stay without a note (see the module notes). On the
        # Cantonese validation song this changes nothing: no syllable is left out.
        drop = float(p["drop_cost"]) + float(p["drop_conf_cost"]) * syl_conf
        path = dp_assign_dropping(np.array([s[2] for s in syl]), onsets, ends, drop, gap_pen=float(p["gap_pen"]),
                                  lead_pen=float(p["lead_pen"]), cap=float(p["cap"]), rest_gap=float(p["rest_gap"]),
                                  forbid=forbid, require=require)
        return onsets, ends, forbid, require, path

    onsets, ends, forbid, require, path = place(notes)
    # Well-supported syllables left without a note where several crowd one:
    # cut the note that holds them, and assign again.
    for _ in range(3 if auto_split else 0):
        left = [(syl[i][2], float(syl_conf[i])) for i, j in enumerate(path) if j < 0 and syl_conf[i] >= float(p["split_conf"])]
        notes, more = crowded_cuts(notes, left, min_s=float(p["split_min_s"]), rejected=rejected, tol=tol)
        if not more:
            break
        cuts = sorted(cuts + more, key=lambda c: c["time"])
        onsets, ends, forbid, require, path = place(notes)
    on_list = onsets.tolist()
    for c in split_fix:
        near = any(abs(x["time"] - float(c["at"])) <= tol for x in cuts)
        inside = any(n["start"] - tol <= float(c["at"]) < n["start"] + n["dur"] for n in notes)
        if (c["action"] == "confirm" and near) or (c["action"] == "reject" and inside):
            used.add(c["_id"])
    for c in (c for c in ctx.corrections if c["type"] == "kind"):
        if match_time(on_list, float(c["at"]), tol) is not None:
            used.add(c["_id"])

    # A token is placed if any of its syllables got a note; its first placed
    # syllable carries the word, later ones `+`.
    auto = ["-"] * len(notes)
    placed = [False] * len(tokens)
    for (k, _, _), j in zip(syl, path):
        if j < 0:
            continue
        auto[j] = "+" if placed[k] else tokens[k]["text"]
        placed[k] = True
    unplaced = [{"line": t["line"], "time": round(float(t["t0"]), 3), "syllables": int(t["syll"]),
                 "conf": t.get("conf")} for t, ok in zip(tokens, placed) if not ok]

    # Manual line versions.
    manual: dict[int, str] = {}
    want_manual: set[int] = set()
    locked_lines: set[int] = set()
    for c in (c for c in ctx.corrections if c["type"] == "line_version"):
        idx = [match_time(on_list, float(e["at"]), tol) for e in c["notes"]]
        words = [str(e["lyric"]) for e in c["notes"] if str(e["lyric"]) not in ("-", "+")]
        expected = [t["text"] for t in sub_lines.get(int(c["line"]), {"tokens": []})["tokens"]]
        if any(j is None for j in idx) or len(set(idx)) != len(idx):
            continue  # stale: notes no longer there
        used.add(c["_id"])
        if words != expected:
            conflicts.append({"id": c["_id"], "line": int(c["line"]), "reason": "字序和歌词文件这一句不一致"})
            continue
        for j, e in zip(idx, c["notes"]):
            manual[j] = str(e["lyric"])
            want_manual.add(j)
        locked_lines.add(int(c["line"]))
    final, late = safe_merge(auto, manual, want_manual)

    # Tokens in the score: the placed ones, and every token of a manual line.
    seq = [k for k, t in enumerate(tokens) if placed[k] or t["line"] in locked_lines]
    unplaced = [u for u in unplaced if u["line"] not in locked_lines]
    words = [x for x in final if x not in ("-", "+")]
    sequence_ok = words == [tokens[k]["text"] for k in seq]
    if not sequence_ok:
        raise RuntimeError("分配后的字序和歌词文件不一致")

    # With the sequence verified, the w-th word note carries token seq[w]. A `+`
    # belongs to the word before it, a `-` sustains it; leading notes go to
    # the first token.
    token_of_note: list[int] = []
    w = -1
    for lyric in final:
        if lyric not in ("-", "+"):
            w += 1
        token_of_note.append(seq[max(w, 0)])
    line_of_note = [tokens[k]["line"] for k in token_of_note]

    # Pitch / tempo overrides.
    pitch_fix: dict[int, int] = {}
    for c in (c for c in ctx.corrections if c["type"] == "pitch"):
        j = match_time(on_list, float(c["at"]), tol)
        if j is not None:
            pitch_fix[j] = int(c["pitch"])
            used.add(c["_id"])
    tempo_fix = [c for c in ctx.corrections if c["type"] == "tempo"]
    for c in tempo_fix:
        used.add(c["_id"])

    b_times = [b["time"] for b in boundaries]  # boundaries are in time order
    threshold = float(p["line_conf_threshold"])
    score_notes = []
    for j, n in enumerate(notes):
        lyric = final[j]
        kind = kind_of(lyric)
        k = token_of_note[j]
        lang = tokens[k]["lang"]
        flags = []
        for mg in n["merges"]:
            flags.append("auto_merged" if mg["source"] == "auto" else "user_merged")
        if j in want_manual:
            flags.append("manual_line")
        if forbid[j] or require[j]:
            flags.append("kind_fixed")
        if kind == SUSTAIN and j > 0 and onsets[j] - ends[j - 1] > float(p["rest_gap"]):
            flags.append("sustain_after_rest")
        if line_info.get(line_of_note[j], {}).get("conf", 1.0) < threshold and line_of_note[j] not in locked_lines:
            flags.append("low_conf_line")
        if n.get("split"):
            flags.append("auto_split" if n["split"]["source"] == "auto" else "user_split")
        bi = match_time(b_times, n["start"], 0.002)
        b = boundaries[bi] if bi is not None else None
        raw_pitch = float(pitch_fix.get(j, n["pitch"]))
        score_notes.append({
            "index": j + 1,
            "start": n["start"],
            "dur": n["dur"],
            "pitch": int(round(raw_pitch)),
            "cents": 0 if j in pitch_fix else int(round((raw_pitch - round(raw_pitch)) * 100)),
            "kind": kind,
            "lyric": lyric,
            "auto_lyric": auto[j],
            "lang": lang,
            "line": line_of_note[j],
            "conf": {
                "align": tokens[k]["conf"] if kind != SUSTAIN else None,
                "onset": b["score"] if b else None,  # weak (low) = looks like no new syllable
            },
            "merged_from": n["merged_from"],
            "flags": flags,
        })
    split_log = []
    for c in cuts:
        j = match_time(on_list, c["time"], 0.002)
        split_log.append({**c, "note": j + 1 if j is not None else None,
                          "line": line_of_note[j] if j is not None else None})
    for u in unplaced:
        u["unplaced"] = True

    lines = []
    for ln in ali["lines"]:
        li = ln
        ln_tokens = sub_lines.get(ln["line"], {"tokens": []})["tokens"]
        mine = [s for s in score_notes if s["line"] == ln["line"]]
        lines.append({
            "line": ln["line"], "start": ln["start"], "end": ln["end"],
            "t0": li.get("t0"), "t1": li.get("t1"), "conf": li.get("conf"),
            "lang": sorted({t["lang"] for t in ln_tokens}),
            "version": "manual" if ln["line"] in locked_lines else "auto",
            "syllables": sum(t["syll"] for t in ln_tokens),
            "unplaced": sum(u["syllables"] for u in unplaced if u["line"] == ln["line"]),
            "from_dictation": sum(1 for t in ln_tokens if t.get("source") == "dictation"),
            "notes": len(mine),
            "sustain": sum(1 for s in mine if s["kind"] == SUSTAIN),
            "first_note": mine[0]["index"] if mine else None,
            "last_note": mine[-1]["index"] if mine else None,
        })

    bpm = float(tempo_fix[-1]["bpm"]) if tempo_fix else tempo["bpm"]
    score = {
        "format": "bapu.score/1",
        "tempo": {"bpm": bpm, "source": "user" if tempo_fix else "auto", "first_beat": tempo["first_beat"],
                  "beat_phase_s": tempo["beat_phase_s"], "confidence": tempo["confidence"],
                  "time_signature": tempo["time_signature"]},
        "lang": ali.get("lang", "yue"),
        "lines": lines,
        "notes": score_notes,
        "checks": {"sequence_matches_lyrics": sequence_ok, "late_switches": late},
        "splits": split_log,  # notes cut where several syllables shared one (times, no text)
        "unplaced": unplaced,  # tokens left without a note (times, no text)
    }
    out = ctx.out_dir / "score.json"
    out.write_text(json.dumps(score, ensure_ascii=False, indent=1), encoding="utf-8")
    (ctx.out_dir / "corrections_used.json").write_text(json.dumps(sorted(used)), encoding="utf-8")
    (ctx.out_dir / "conflicts.json").write_text(json.dumps(conflicts, ensure_ascii=False), encoding="utf-8")

    dev = np.array([abs(s[2] - onsets[j]) for s, j in zip(syl, path) if j >= 0])
    return StageResult(
        outputs={"score": out},
        metrics={
            "notes": len(score_notes), "syllables": len(syl),
            "sustain": sum(1 for s in score_notes if s["kind"] == SUSTAIN),
            "cont": sum(1 for s in score_notes if s["kind"] == CONT),
            "manual_lines": len(locked_lines),
            "onset_dev_median_ms": round(float(np.median(dev)) * 1000) if len(dev) else None,
            "onset_dev_p90_ms": round(float(np.percentile(dev, 90)) * 1000) if len(dev) else None,
            "sequence_ok": sequence_ok,
            "splits": len(cuts),
            "unplaced_syllables": sum(u["syllables"] for u in unplaced),
        },
    )


STAGE = Stage(
    name="assign",
    title="分配字到音符",
    index=9,
    version="5",
    inputs=("merged_notes", "boundaries", "alignment", "tempo"),
    outputs=("score",),
    defaults={"gap_pen": 0.3, "lead_pen": 2.0, "cap": 1.5, "rest_gap": 0.25, "tolerance": 0.03,
              "line_conf_threshold": 0.15,
              "split": "auto", "split_min_s": 0.07, "split_conf": 0.1,
              "drop_cost": 1.5, "drop_conf_cost": 3.0},
    corrections=("kind", "line_version", "pitch", "tempo", "split"),
    run=_run,
)
