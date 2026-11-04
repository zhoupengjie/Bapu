"""05 splits: merge notes that SOME split out of one sung note.

SOME cuts a scoop into a pitch into "short note + long note a semitone away",
and sometimes cuts one long same-pitch note in two (pipeline notes §5). For
every pair of touching notes this stage measures syllable-onset evidence at the
boundary in the dry lead: an energy dip, a spectral-flux peak and a timbre
(MFCC) change. Each cue is ranked across the song; a boundary low on all three
is most likely not a new syllable. Boundaries scoring below `threshold` are
merged automatically and flagged for review; corrections can confirm or reject
any boundary by time.

Merge: the first note is extended to the end of the second, takes the pitch of
the longer of the two, and the second note is removed.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from bapu.audio import read_mono
from bapu.corrections import match_time
from bapu.stages.base import Stage, StageContext, StageResult

TOUCH = 1e-3  # notes closer than this are "touching"


def boundary_evidence(notes: list[dict[str, Any]], y: np.ndarray, sr: int) -> list[dict[str, Any]]:
    import librosa

    hop = int(round(sr * 0.005))  # 5 ms
    rms_db = librosa.amplitude_to_db(librosa.feature.rms(y=y, frame_length=1024, hop_length=hop)[0], ref=1.0)
    flux = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop, n_mels=80)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=20, hop_length=hop, n_fft=2048)[1:]
    nf = len(rms_db)

    def fr(t: float) -> int:
        return int(np.clip(round(t * sr / hop), 0, nf - 1))

    flux_ref = float(np.median(flux[flux > 0])) if np.any(flux > 0) else 1.0
    rows = []
    for i in range(len(notes) - 1):
        a, b = notes[i], notes[i + 1]
        s0, e0, s1 = a["start"], a["start"] + a["dur"], b["start"]
        e1 = s1 + b["dur"]
        if s1 - e0 > TOUCH:
            continue  # a rest in between: certainly a new syllable
        k = fr(e0)
        interior = np.concatenate([
            rms_db[fr(s0 + 0.25 * (e0 - s0)): max(fr(e0 - 0.03), fr(s0 + 0.25 * (e0 - s0)) + 1)],
            rms_db[fr(s1 + 0.03): max(fr(s1 + 0.75 * (e1 - s1)), fr(s1 + 0.03) + 1)],
        ])
        win = rms_db[max(k - 10, 0): k + 11]  # +-50 ms
        dip = float(np.median(interior) - win.min())
        fpk = float(flux[max(k - 8, 0): k + 9].max() / flux_ref)  # +-40 ms
        v0 = mfcc[:, fr(max(s0, e0 - 0.10)): fr(e0 - 0.02) + 1].mean(axis=1)
        v1 = mfcc[:, fr(s1 + 0.04): fr(min(e1, s1 + 0.14)) + 1].mean(axis=1)
        rows.append({"first": i, "time": round(e0, 4), "dip_db": round(dip, 2), "flux": round(fpk, 3),
                     "timbre": round(float(np.linalg.norm(v0 - v1)), 2)})

    # Rank each cue across all touching boundaries; low on all three = likely split.
    if len(rows) > 1:
        for key in ("dip_db", "flux", "timbre"):
            vals = np.array([r[key] for r in rows])
            ranks = vals.argsort().argsort() / (len(vals) - 1)
            for r, q in zip(rows, ranks):
                r[key + "_pct"] = float(q)
        for r in rows:
            r["score"] = round((r["dip_db_pct"] + r["flux_pct"] + r["timbre_pct"]) / 3, 4)
    else:
        for r in rows:
            r["score"] = 1.0
    return rows


def decide(rows: list[dict[str, Any]], threshold: float, corrections: list[dict[str, Any]],
           tolerance: float) -> tuple[list[dict[str, Any]], set[int]]:
    """Mark each boundary auto / confirmed / rejected / kept; returns used correction ids."""
    used: set[int] = set()
    times = [r["time"] for r in rows]
    for r in rows:
        r["merge"] = r["score"] < threshold
        r["source"] = "auto" if r["merge"] else "none"
    for c in corrections:
        j = match_time(times, float(c["at"]), tolerance)
        if j is None:
            continue
        used.add(c["_id"])
        rows[j]["merge"] = c["action"] == "confirm"
        rows[j]["source"] = "user"
    return rows, used


def apply_merges(notes: list[dict[str, Any]], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merge_after = {r["first"]: r for r in rows if r["merge"]}
    out: list[dict[str, Any]] = []
    for i, n in enumerate(notes):
        if i - 1 in merge_after and out:
            prev = out[-1]
            if n["dur"] > prev["_longest"]:  # pitch of the longest piece wins
                prev["pitch"] = n["pitch"]
                prev["_longest"] = n["dur"]
            prev["dur"] = round(n["start"] + n["dur"] - prev["start"], 4)
            prev["merged_from"].append(i + 1)
            r = merge_after[i - 1]
            prev["merges"].append({"at": r["time"], "score": r["score"], "source": r["source"]})
            continue
        out.append({"start": n["start"], "dur": n["dur"], "pitch": n["pitch"], "merged_from": [i + 1],
                    "merges": [], "_longest": n["dur"]})
    for n in out:
        del n["_longest"]
    return out


def _run(ctx: StageContext) -> StageResult:
    notes = json.loads(ctx.inputs["notes"].read_text(encoding="utf-8"))["notes"]
    y, sr = read_mono(ctx.inputs["lead_dry"], int(ctx.params["sample_rate"]))
    ctx.progress("测量交界处的起音证据")
    rows = boundary_evidence(notes, y, sr)
    rows, used = decide(rows, float(ctx.params["threshold"]), ctx.corrections, float(ctx.params["tolerance"]))
    merged = apply_merges(notes, rows)

    (ctx.out_dir / "boundaries.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    out = ctx.out_dir / "notes.json"
    out.write_text(json.dumps({"notes": merged}, indent=1), encoding="utf-8")
    (ctx.out_dir / "corrections_used.json").write_text(json.dumps(sorted(used)), encoding="utf-8")
    auto = sum(1 for r in rows if r["merge"] and r["source"] == "auto")
    user = sum(1 for r in rows if r["source"] == "user")
    return StageResult(
        outputs={"merged_notes": out, "boundaries": ctx.out_dir / "boundaries.json"},
        metrics={"notes_in": len(notes), "notes": len(merged), "touching": len(rows),
                 "auto_merged": auto, "user_decided": user},
    )


STAGE = Stage(
    name="splits",
    title="合并切开的音符",
    index=5,
    version="1",
    inputs=("notes", "lead_dry"),
    outputs=("merged_notes", "boundaries"),
    # threshold: calibrated on the validation song. The 8 boundaries merged by
    # ear score 0.028-0.078; the 2 the user kept score 0.054 and 0.065; the next
    # boundary up scores 0.080 (notes §5). Below 0.078 every auto merge is still
    # flagged for review; corrections confirm or reject single boundaries.
    defaults={"threshold": 0.078, "sample_rate": 44100, "tolerance": 0.03},
    corrections=("merge",),
    run=_run,
)
