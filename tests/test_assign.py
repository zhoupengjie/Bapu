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
