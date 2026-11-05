"""Assignment, merges and the review report on synthetic data (made-up lyrics)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from bapu import report
from bapu.models.registry import Registry
from bapu.progress import StageProgress, null_progress
from bapu.stages import assign, splits
from bapu.stages.base import StageContext
from bapu.workdir import StageRecord, WorkDir

TEXT = ["天", "空", "海", "洋", "hello"]  # fictional


def test_dp_extra_note_becomes_sustain():
    onsets = np.array([0.0, 0.5, 1.0, 2.0])
    ends = np.array([0.5, 1.0, 1.5, 2.5])
    path = assign.dp_assign(np.array([0.02, 0.98, 2.01]), onsets, ends,
                            gap_pen=0.3, lead_pen=2.0, cap=1.5, rest_gap=0.25)
    assert path == [0, 2, 3]  # note 2 (0.5 s) is a "-"


def test_dp_constraints():
    onsets = np.array([0.0, 0.5, 1.0, 2.0])
    ends = np.array([0.5, 1.0, 1.5, 2.5])
    kw = dict(gap_pen=0.3, lead_pen=2.0, cap=1.5, rest_gap=0.25)
    syl = np.array([0.02, 0.98, 2.01])
    forbid = np.array([False, False, True, False])
    assert assign.dp_assign(syl, onsets, ends, forbid=forbid, **kw) == [0, 1, 3]
    require = np.array([False, True, False, False])
    assert 1 in assign.dp_assign(syl, onsets, ends, require=require, **kw)
    with pytest.raises(ValueError):
        assign.dp_assign(np.array([0.0, 0.1, 0.2, 0.3, 0.4]), onsets, ends, **kw)


def test_safe_merge_waits_for_equal_consumption():
    auto = ["天", "-", "空", "海"]
    manual = {0: "天", 1: "空", 2: "-"}  # same words, "-" moved
    out, late = assign.safe_merge(auto, manual, {0, 1, 2})
    assert out == ["天", "空", "-", "海"]
    assert late == 0
    # Asking for manual from note 1 only: streams differ there, switch waits.
    out, late = assign.safe_merge(auto, {1: "空", 2: "-"}, {1, 2})
    assert [x for x in out if x != "-"] == ["天", "空", "海"]


def test_apply_merges_takes_pitch_of_longest():
    notes = [{"start": 0.0, "dur": 0.1, "pitch": 61.0}, {"start": 0.1, "dur": 0.5, "pitch": 62.2},
             {"start": 0.6, "dur": 0.2, "pitch": 64.0}, {"start": 1.0, "dur": 0.3, "pitch": 60.0}]
    rows = [{"first": 0, "time": 0.1, "score": 0.01, "merge": True, "source": "auto"},
            {"first": 1, "time": 0.6, "score": 0.02, "merge": True, "source": "user"}]
    out = splits.apply_merges(notes, rows)
    assert len(out) == 2
    assert out[0]["pitch"] == 62.2 and out[0]["dur"] == pytest.approx(0.8)
    assert out[0]["merged_from"] == [1, 2, 3]


def test_decide_uses_corrections():
    rows = [{"first": 0, "time": 1.0, "score": 0.01}, {"first": 3, "time": 4.0, "score": 0.9}]
    corr = [{"type": "merge", "at": 1.01, "action": "reject", "_id": 1},
            {"type": "merge", "at": 4.0, "action": "confirm", "_id": 2},
            {"type": "merge", "at": 9.0, "action": "confirm", "_id": 3}]
    rows, used = splits.decide(rows, 0.1, corr, 0.03)
    assert [r["merge"] for r in rows] == [False, True]
    assert used == {1, 2}


def write(p: Path, data) -> Path:
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture
def stage_inputs(tmp_path: Path):
    # 6 notes: syllables at 0, 1, 2, 3.5, then "hello" (2 syllables) at 5.0-6.0
    notes = [{"start": t, "dur": 0.45, "pitch": 60.3 + i, "merged_from": [i + 1], "merges": []}
             for i, t in enumerate([0.0, 1.0, 2.0, 2.5, 3.5, 5.0, 5.5])]
    # The aligner's output carries the final tokens with their timing.
    words = [("天", "yue", 1, 1, 0.0, 0.4, 0.9), ("空", "yue", 1, 1, 1.0, 1.4, 0.8), ("海", "yue", 1, 1, 2.0, 2.4, 0.7),
             ("洋", "yue", 1, 2, 3.5, 3.9, 0.05), ("hello", "en", 2, 2, 5.0, 6.0, 0.6)]
    ali = {"lang": "yue", "mode": "fused",
           "tokens": [{"text": w, "lang": lg, "syll": sy, "line": li, "t0": a, "t1": b, "conf": c,
                       "source": "both", "flags": []} for w, lg, sy, li, a, b, c in words],
           "lines": [{"line": 1, "start": 0.0, "end": 2.9, "t0": 0.0, "t1": 2.4, "conf": 0.7},
                     {"line": 2, "start": 3.4, "end": 6.0, "t0": 3.5, "t1": 6.0, "conf": 0.05}],
           "decisions": []}
    tempo = {"bpm": 125.0, "first_beat": 0.0, "beat_phase_s": 0.0, "confidence": "high", "time_signature": "4/4"}
    d = tmp_path / "in"
    d.mkdir()
    return {
        "merged_notes": write(d / "notes.json", {"notes": notes}),
        "boundaries": write(d / "b.json", [{"first": 2, "time": 2.45, "score": 0.9, "merge": False, "source": "none"}]),
        "alignment": write(d / "alignment.json", ali),
        "tempo": write(d / "tempo.json", tempo),
    }


def run_assign(tmp_path: Path, inputs, corrections=()):
    out = tmp_path / "08_assign"
    out.mkdir(exist_ok=True)
    ctx = StageContext(inputs=inputs, out_dir=out, params=dict(assign.STAGE.defaults), models={},
                       registry=Registry({}, tmp_path), progress=StageProgress("assign", null_progress),
                       corrections=list(corrections))
    res = assign.STAGE.run(ctx)
    return res, json.loads((out / "score.json").read_text(encoding="utf-8"))


def test_assign_builds_score(tmp_path, stage_inputs):
    res, score = run_assign(tmp_path, stage_inputs)
    kinds = [n["kind"] for n in score["notes"]]
    assert kinds == ["word", "word", "word", "sustain", "word", "word", "cont"]
    assert [n["line"] for n in score["notes"]] == [1, 1, 1, 1, 2, 2, 2]
    assert score["notes"][6]["lang"] == "en"
    assert score["notes"][0]["pitch"] == 60 and score["notes"][0]["cents"] == 30
    assert res.metrics["sequence_ok"]
    assert "low_conf_line" in score["notes"][4]["flags"]
    assert "sustain_after_rest" not in score["notes"][3]["flags"]


def test_assign_line_version_and_conflict(tmp_path, stage_inputs):
    good = {"type": "line_version", "line": 1, "_id": 1, "notes": [
        {"at": 0.0, "lyric": "天"}, {"at": 1.0, "lyric": "-"}, {"at": 2.0, "lyric": "空"}, {"at": 2.5, "lyric": "海"}]}
    bad = {"type": "line_version", "line": 2, "_id": 2, "notes": [
        {"at": 3.5, "lyric": "天"}, {"at": 5.0, "lyric": "hello"}, {"at": 5.5, "lyric": "+"}]}
    pitch = {"type": "pitch", "at": 5.0, "pitch": 70, "_id": 3}
    _, score = run_assign(tmp_path, stage_inputs, [good, bad, pitch])
    assert [n["lyric"] for n in score["notes"][:4]] == ["天", "-", "空", "海"]
    assert score["lines"][0]["version"] == "manual"
    assert score["lines"][1]["version"] == "auto"
    assert score["notes"][5]["pitch"] == 70 and score["notes"][5]["cents"] == 0
    conflicts = json.loads((tmp_path / "08_assign" / "conflicts.json").read_text(encoding="utf-8"))
    assert [c["id"] for c in conflicts] == [2]


def test_review_has_no_lyric_text(tmp_path, stage_inputs):
    _, score = run_assign(tmp_path, stage_inputs, [{"type": "kind", "at": 9.0, "kind": "word", "_id": 1}])
    wd = WorkDir(tmp_path)
    wd.corrections_path.write_text("- {type: kind, at: 9.0, kind: word}\n", encoding="utf-8")
    wd.record("assign", StageRecord(key="k", dir="08_assign", outputs={"score": "08_assign/score.json"},
                                    params=dict(assign.STAGE.defaults)))
    (tmp_path / "08_assign" / "corrections_used.json").write_text("[]", encoding="utf-8")
    written = report.finalize(wd)
    md = written["review_md"].read_text(encoding="utf-8")
    review = (tmp_path / "review.json").read_text(encoding="utf-8")
    for word in TEXT:
        assert word not in md and word not in review
    assert "失效" in md  # the kind entry at 9.0 s matches no note
    assert json.loads(review)["summary"]["stale_corrections"] == 1


def test_review_flags_possible_missing_words(tmp_path, stage_inputs):
    # Two extra notes after "海" whose starts look like new syllables.
    notes = json.loads(stage_inputs["merged_notes"].read_text(encoding="utf-8"))["notes"]
    notes[3:4] = [{"start": 2.5, "dur": 0.3, "pitch": 63.0, "merged_from": [4], "merges": []},
                  {"start": 2.8, "dur": 0.3, "pitch": 62.0, "merged_from": [5], "merges": []}]
    write(stage_inputs["merged_notes"], {"notes": notes})
    write(stage_inputs["boundaries"], [{"first": 2, "time": 2.45, "score": 0.2, "merge": False, "source": "none"},
                                      {"first": 3, "time": 2.8, "score": 0.9, "merge": False, "source": "none"}])
    _, score = run_assign(tmp_path, stage_inputs)
    assert [x["kind"] for x in score["notes"][3:5]] == ["sustain", "sustain"]
    assert score["notes"][4]["conf"]["onset"] == 0.9
    wd = WorkDir(tmp_path)
    wd.record("assign", StageRecord(key="k", dir="08_assign", outputs={"score": "08_assign/score.json"},
                                    params=dict(assign.STAGE.defaults)))
    report.finalize(wd)
    review = json.loads((tmp_path / "review.json").read_text(encoding="utf-8"))
    flagged = {ln["line"]: ln["maybe_missing"] for ln in review["lines"] if ln["maybe_missing"]}
    assert flagged == {1: {"notes": [4, 5], "time": 2.5}}


# --- several syllables on one note; more syllables than notes (made-up morae) ---

def test_split_notes_cuts_at_confident_onsets():
    notes = [{"start": 0.0, "dur": 1.0, "pitch": 60.0, "merged_from": [1], "merges": [{"source": "auto"}]},
             {"start": 1.0, "dur": 0.5, "pitch": 62.0, "merged_from": [2], "merges": []}]
    syl = [(0.0, 0.9), (0.4, 0.8), (0.95, 0.9), (1.0, 0.7), (1.2, 0.05)]  # 0.95: too close to the end
    out, cuts = assign.split_notes(notes, syl, min_s=0.07, min_conf=0.1, rejected=[], confirmed=[], tol=0.03)
    assert [(n["start"], n["dur"], n["pitch"]) for n in out] == [(0.0, 0.4, 60.0), (0.4, 0.6, 60.0), (1.0, 0.5, 62.0)]
    assert out[0]["merges"] and not out[1]["merges"] and out[1]["split"]["of"] == 1
    assert [(c["time"], c["source"]) for c in cuts] == [(0.4, "auto")]  # 1.2 is not confident enough
    out, cuts = assign.split_notes(notes, syl, min_s=0.07, min_conf=0.1, rejected=[0.41], confirmed=[1.25], tol=0.03)
    assert [n["start"] for n in out] == [0.0, 1.0, 1.25] and [c["source"] for c in cuts] == ["user"]


def test_dp_dropping_leaves_the_least_supported_syllables():
    onsets = np.array([0.0, 1.0, 2.0])
    ends = onsets + 0.5
    kw = dict(gap_pen=0.3, lead_pen=2.0, cap=1.5, rest_gap=0.25)
    syl = np.array([0.0, 0.5, 1.0, 2.0])
    drop = 1.5 + 3.0 * np.array([0.9, 0.0, 0.8, 0.7])  # the second syllable: the aligner never found it
    assert assign.dp_assign_dropping(syl, onsets, ends, drop, **kw) == [0, -1, 1, 2]
    # Enough notes and dear drops: the same as without dropping.
    syl = np.array([0.02, 0.98, 2.01])
    assert assign.dp_assign_dropping(syl, onsets, ends, np.full(3, 9.0), **kw) == assign.dp_assign(syl, onsets, ends, **kw)


def japanese_inputs(tmp_path: Path):
    """Morae (made up) where two share the first note, and a line the lead does not sing."""
    notes = [{"start": t, "dur": d, "pitch": 60.0, "merged_from": [i + 1], "merges": []}
             for i, (t, d) in enumerate([(0.0, 0.8), (1.0, 0.4), (1.5, 0.4)])]
    toks = [("か", 1, 0.0, 0.3, 0.8), ("な", 1, 0.4, 0.7, 0.7), ("た", 1, 1.0, 1.3, 0.9), ("ま", 1, 1.5, 1.8, 0.6),
            ("zorbq", 2, 1.95, 1.97, 0.0), ("quexil", 2, 1.97, 1.99, 0.01)]
    ali = {"lang": "ja", "mode": "fused",
           "tokens": [{"text": w, "lang": "en" if w.isascii() else "ja", "syll": 1, "line": li, "t0": a, "t1": b,
                       "conf": c, "source": "both", "flags": []} for w, li, a, b, c in toks],
           "lines": [{"line": 1, "start": None, "end": None, "t0": 0.0, "t1": 1.8, "conf": 0.6},
                     {"line": 2, "start": None, "end": None, "t0": 1.95, "t1": 1.99, "conf": 0.0}],
           "decisions": []}
    tempo = {"bpm": 120.0, "first_beat": 0.0, "beat_phase_s": 0.0, "confidence": "high", "time_signature": "4/4"}
    d = tmp_path / "in"
    d.mkdir()
    return {"merged_notes": write(d / "notes.json", {"notes": notes}), "boundaries": write(d / "b.json", []),
            "alignment": write(d / "alignment.json", ali), "tempo": write(d / "tempo.json", tempo)}


def test_assign_splits_and_leaves_unsung_syllables(tmp_path):
    inputs = japanese_inputs(tmp_path)
    res, score = run_assign(tmp_path, inputs)
    assert [n["lyric"] for n in score["notes"]] == ["か", "な", "た", "ま"]
    assert [round(n["start"], 2) for n in score["notes"]] == [0.0, 0.4, 1.0, 1.5]
    assert "auto_split" in score["notes"][1]["flags"]
    assert res.metrics["splits"] == 1 and res.metrics["unplaced_syllables"] == 2 and res.metrics["sequence_ok"]
    assert [(u["line"], u["syllables"]) for u in score["unplaced"]] == [(2, 1), (2, 1)]
    assert [ln["unplaced"] for ln in score["lines"]] == [0, 2]
    # The review lists both, with times and no text.
    wd = WorkDir(tmp_path)
    wd.record("assign", StageRecord(key="k", dir="08_assign", outputs={"score": "08_assign/score.json"},
                                    params=dict(assign.STAGE.defaults)))
    (tmp_path / "08_assign" / "corrections_used.json").write_text("[]", encoding="utf-8")
    written = report.finalize(wd)
    review = json.loads((tmp_path / "review.json").read_text(encoding="utf-8"))
    md = written["review_md"].read_text(encoding="utf-8")
    assert review["summary"]["auto_splits"] == 1 and review["summary"]["unplaced_syllables"] == 2
    assert [(x["note"], x["line"]) for x in review["splits"]] == [(2, 1)]
    assert "拆开的音符" in md and "没分到音符的音节" in md
    for word in ("か", "な", "た", "ま", "zorbq", "quexil"):
        assert word not in md and word not in json.dumps(review, ensure_ascii=False)


def test_assign_split_corrections(tmp_path):
    inputs = japanese_inputs(tmp_path)
    reject = {"type": "split", "at": 0.4, "action": "reject", "_id": 1}
    res, score = run_assign(tmp_path, inputs, [reject])
    assert res.metrics["splits"] == 0 and len(score["notes"]) == 3
    assert json.loads((tmp_path / "08_assign" / "corrections_used.json").read_text()) == [1]
    # Not Japanese and enough notes: no automatic split, but a confirmed one is made.
    tokens = json.loads(inputs["alignment"].read_text(encoding="utf-8"))
    for t in tokens["tokens"]:
        t["lang"] = "yue"
    tokens["tokens"] = tokens["tokens"][:3]
    write(inputs["alignment"], tokens)
    res, score = run_assign(tmp_path, inputs)
    assert res.metrics["splits"] == 0 and len(score["notes"]) == 3
    res, score = run_assign(tmp_path, inputs, [{"type": "split", "at": 0.4, "action": "confirm", "_id": 2}])
    assert res.metrics["splits"] == 1 and "user_split" in score["notes"][1]["flags"]


def test_corrections_accept_split(tmp_path):
    from bapu import corrections

    p = tmp_path / "c.yaml"
    p.write_text("- {type: split, at: 1.5, action: reject}\n", encoding="utf-8")
    assert corrections.load(p)[0]["action"] == "reject"
    p.write_text("- {type: split, at: 1.5, action: maybe}\n", encoding="utf-8")
    with pytest.raises(corrections.CorrectionsError):
        corrections.load(p)


def test_crowded_cuts():
    notes = [{"start": 0.0, "dur": 0.13, "pitch": 60.0, "merged_from": [1], "merges": []},
             {"start": 0.13, "dur": 0.5, "pitch": 62.0, "merged_from": [2], "merges": []}]
    # A left-out syllable 30 ms into a short note: cut at the note's middle; one in the long note: cut at it.
    out, cuts = assign.crowded_cuts(notes, [(0.03, 0.5), (0.4, 0.5)], min_s=0.06, rejected=[], tol=0.03)
    assert [(round(n["start"], 3), round(n["dur"], 3)) for n in out] == [(0.0, 0.065), (0.065, 0.065), (0.13, 0.27), (0.4, 0.23)]
    assert all(n.get("split", {}).get("crowded") for n in (out[1], out[3])) and len(cuts) == 2
    out, cuts = assign.crowded_cuts(notes, [(0.4, 0.5)], min_s=0.06, rejected=[0.4], tol=0.03)
    assert len(out) == 2 and not cuts  # the user rejected that cut
