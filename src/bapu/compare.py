"""Compare a run with a Synthesizer V project the user saved.

- `from_svp`: turn what the user changed in SV into correction entries.
- `evaluate`: how many notes per line the automatic version gets right.

Results carry counts, times and indices only; lyric text stays in the
correction entries, which live in the (gitignored) work directory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bapu import svp
from bapu.corrections import match_time
from bapu.workdir import WorkDir

TOL = 0.03


def kind_of(lyric: str) -> str:
    return "sustain" if lyric == "-" else "cont" if lyric == "+" else "word"


def _load(wd: WorkDir) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    score_p = wd.root / "score.json"
    if not score_p.exists():
        raise FileNotFoundError("还没有 score.json：先用 --lyrics 跑完整个流程")
    score = json.loads(score_p.read_text(encoding="utf-8"))
    rec = wd.get("splits")
    boundaries = json.loads((wd.root / rec.outputs["boundaries"]).read_text(encoding="utf-8")) if rec else []
    return score, boundaries


@dataclass
class Match:
    sv: list[svp.SvpNote]
    sv_to_ours: list[int | None]  # index into score notes
    sv_line: list[int]


def match(score: dict[str, Any], sv_notes: list[svp.SvpNote]) -> Match:
    ours = score["notes"]
    onsets = [n["start"] for n in ours]
    to_ours = [match_time(onsets, n.start, TOL) for n in sv_notes]
    lines, cur = [], ours[0]["line"] if ours else 1
    for j in to_ours:
        if j is not None:
            cur = ours[j]["line"]
        lines.append(cur)
    return Match(sv_notes, to_ours, lines)


def from_svp(wd: WorkDir, svp_path: Path, track: int = 1) -> tuple[list[dict[str, Any]], dict[str, int]]:
    score, boundaries = _load(wd)
    sv_notes = svp.vocal_notes(svp.load_project(svp_path), track)
    sv_onsets = [n.start for n in sv_notes]
    entries: list[dict[str, Any]] = []
    stats = {"merge_confirm": 0, "merge_reject": 0, "line_version": 0, "pitch": 0}

    # Merges: is there a note boundary at this time in SV?
    for b in boundaries:
        t = b["time"]
        split_in_sv = match_time(sv_onsets, t, TOL) is not None
        covered = any(n.start < t - TOL and n.start + n.dur > t + TOL for n in sv_notes)
        if covered and not split_in_sv and not (b["merge"] and b["source"] == "user"):
            entries.append({"type": "merge", "at": round(t, 3), "action": "confirm"})
            stats["merge_confirm"] += 1
        elif split_in_sv and b["merge"]:
            entries.append({"type": "merge", "at": round(t, 3), "action": "reject"})
            stats["merge_reject"] += 1
    # Notes the assign stage cut in two: kept apart in SV (confirm) or joined
    # again (reject: the note stays whole on the next run).
    kept_splits = []
    for n in score["notes"]:
        if "auto_split" not in n.get("flags", []):
            continue
        t = n["start"]
        if match_time(sv_onsets, t, TOL) is not None:
            kept_splits.append({"type": "split", "at": round(t, 3), "action": "confirm"})
            stats["split_confirm"] = stats.get("split_confirm", 0) + 1
        elif any(x.start < t - TOL and x.start + x.dur > t + TOL for x in sv_notes):
            entries.append({"type": "split", "at": round(t, 3), "action": "reject"})
            stats["split_reject"] = stats.get("split_reject", 0) + 1

    if entries:
        # Merge and split decisions change which notes exist. Lyric differences
        # are only meaningful once the notes line up, so they wait for the next pass.
        stats["needs_rerun"] = 1
        return entries + kept_splits, stats
    entries += kept_splits

    m = match(score, sv_notes)
    ours = score["notes"]
    # Pitch: only where the note itself is the same note.
    for k, j in enumerate(m.sv_to_ours):
        if j is not None and abs(sv_notes[k].dur - ours[j]["dur"]) < TOL and sv_notes[k].pitch != ours[j]["pitch"]:
            entries.append({"type": "pitch", "at": round(sv_notes[k].start, 3), "pitch": sv_notes[k].pitch})
            stats["pitch"] += 1

    # Lines whose lyrics differ from the current plan become manual versions.
    # If the words themselves differ from the lyric file (a word the subtitle
    # missed or got wrong), the line's text is corrected first.
    subtitle = _subtitle_tokens(wd)
    by_line: dict[int, list[int]] = {}
    for k, li in enumerate(m.sv_line):
        by_line.setdefault(li, []).append(k)
    for li in sorted(by_line):
        ks = by_line[li]
        same = all(m.sv_to_ours[k] is not None and ours[m.sv_to_ours[k]]["lyric"] == sv_notes[k].lyric for k in ks)
        mine = [n for n in ours if n["line"] == li]
        if same and len(mine) == len(ks):
            continue
        words = [sv_notes[k].lyric for k in ks if sv_notes[k].lyric not in ("-", "+")]
        if li in subtitle and words != subtitle[li]:
            entries.append({"type": "line_text", "line": li, "text": join_tokens(words)})
            stats["line_text"] = stats.get("line_text", 0) + 1
        entries.append({"type": "line_version", "line": li,
                        "notes": [{"at": round(sv_notes[k].start, 3), "lyric": sv_notes[k].lyric} for k in ks]})
        stats["line_version"] += 1
    return entries, stats


def join_tokens(words: list[str]) -> str:
    """Words back into line text that tokenizes to the same words. A Japanese
    mora that starts with っ only starts a phrase, so it gets a space before it."""
    out = ""
    for w in words:
        if out and (w[0].isascii() or out[-1].isascii() or w[0] == "っ"):
            out += " "
        out += w
    return out


def _subtitle_tokens(wd: WorkDir) -> dict[int, list[str]]:
    """The run's final words per line (reference fused with dictation)."""
    rec = wd.get("align")
    if rec is None:
        return {}
    data = json.loads((wd.root / rec.outputs["alignment"]).read_text(encoding="utf-8"))
    out: dict[int, list[str]] = {}
    for t in data["tokens"]:
        out.setdefault(t["line"], []).append(t["text"])
    return out


def evaluate(wd: WorkDir, svp_path: Path, track: int = 1, use: str = "auto_lyric") -> dict[str, Any]:
    """Per line: notes where the plan's lyric (or only its type) equals SV."""
    score, _ = _load(wd)
    sv_notes = svp.vocal_notes(svp.load_project(svp_path), track)
    m = match(score, sv_notes)
    ours = score["notes"]
    rows: dict[int, dict[str, int]] = {}
    for k, j in enumerate(m.sv_to_ours):
        r = rows.setdefault(m.sv_line[k], {"notes": 0, "matched": 0, "same_lyric": 0, "same_kind": 0})
        r["notes"] += 1
        if j is None:
            continue
        r["matched"] += 1
        a, b = ours[j][use], sv_notes[k].lyric
        r["same_lyric"] += a == b
        r["same_kind"] += kind_of(a) == kind_of(b)
    total = {key: sum(r[key] for r in rows.values()) for key in ("notes", "matched", "same_lyric", "same_kind")}
    return {"sv_notes": len(sv_notes), "plan_notes": len(ours), "lines": rows, "total": total}
