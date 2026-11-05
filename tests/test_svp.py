"""Reading .svp projects and turning SV edits into corrections (made-up lyrics)."""

from __future__ import annotations

import json

import pytest

from bapu import compare, svp
from bapu.workdir import StageRecord, WorkDir

Q = svp.QUARTER


def project(notes, tempo=None):
    return {
        "version": 203,
        "time": {"meter": [{"index": 0, "numerator": 4, "denominator": 4}],
                 "tempo": tempo or [{"position": 0, "bpm": 120.0}]},
        "library": [{"uuid": "g1", "notes": notes}],
        "tracks": [{"mainGroup": {"notes": []}, "mainRef": {},
                    "groups": [{"groupID": "g1", "blickOffset": 0, "pitchOffset": 0}]}],
    }


def n(beat, beats, pitch, lyric):
    return {"onset": int(beat * Q), "duration": int(beats * Q), "pitch": pitch, "lyrics": lyric}


def test_tempo_map_piecewise():
    tm = svp.TempoMap([{"position": 0, "bpm": 120.0}, {"position": 4 * Q, "bpm": 60.0}])
    assert tm.seconds(2 * Q) == pytest.approx(1.0)
    assert tm.seconds(4 * Q) == pytest.approx(2.0)
    assert tm.seconds(5 * Q) == pytest.approx(3.0)


def test_vocal_notes_reads_group_with_offsets(tmp_path):
    p = project([n(2, 1, 60, "天"), n(0, 1, 62, "空")])
    p["tracks"][0]["groups"][0].update(blickOffset=Q, pitchOffset=2)
    f = tmp_path / "x.svp"
    f.write_bytes(json.dumps(p, ensure_ascii=False).encode("utf-8") + b"\x00")
    notes = svp.vocal_notes(svp.load_project(f))
    assert [(round(x.start, 3), x.pitch, x.lyric) for x in notes] == [(0.5, 64, "空"), (1.5, 62, "天")]


def make_run(tmp_path):
    """A work dir whose plan has notes at 0, 0.5, 1.0, 1.5 s (120 BPM beats)."""
    wd = WorkDir(tmp_path / "work")
    (wd.root / "05_splits").mkdir(parents=True)
    (wd.root / "05_splits" / "boundaries.json").write_text(json.dumps([
        {"first": 0, "time": 0.5, "score": 0.05, "merge": False, "source": "none"},
        {"first": 2, "time": 1.5, "score": 0.02, "merge": True, "source": "auto"},
    ]), encoding="utf-8")
    wd.record("splits", StageRecord(key="k", dir="05_splits", outputs={"boundaries": "05_splits/boundaries.json"}))
    score = {"notes": [
        {"index": 1, "start": 0.0, "dur": 0.5, "pitch": 60, "lyric": "天", "auto_lyric": "天", "line": 1},
        {"index": 2, "start": 0.5, "dur": 0.5, "pitch": 62, "lyric": "-", "auto_lyric": "-", "line": 1},
        {"index": 3, "start": 1.0, "dur": 1.0, "pitch": 64, "lyric": "空", "auto_lyric": "空", "line": 1},
    ]}
    (wd.root / "score.json").write_text(json.dumps(score, ensure_ascii=False), encoding="utf-8")
    return wd


def test_from_svp_merges_first_then_lines(tmp_path):
    wd = make_run(tmp_path)
    f = tmp_path / "a.svp"
    # User merged the first two notes and kept the auto-merged pair apart.
    f.write_text(json.dumps(project([n(0, 2, 60, "天"), n(2, 1, 64, "空"), n(3, 1, 64, "-")]), ensure_ascii=False),
                 encoding="utf-8")
    entries, stats = compare.from_svp(wd, f)
    assert stats["needs_rerun"] == 1
    assert {(e["action"], e["at"]) for e in entries} == {("confirm", 0.5), ("reject", 1.5)}


def test_from_svp_line_version_and_eval(tmp_path):
    wd = make_run(tmp_path)
    f = tmp_path / "b.svp"
    f.write_text(json.dumps(project([n(0, 1, 60, "天"), n(1, 1, 61, "空"), n(2, 2, 64, "-")]), ensure_ascii=False),
                 encoding="utf-8")
    # Same notes, but the "-" moved and one pitch differs.
    (wd.root / "05_splits" / "boundaries.json").write_text("[]", encoding="utf-8")
    entries, stats = compare.from_svp(wd, f)
    assert stats["line_version"] == 1 and stats["pitch"] == 1
    lv = next(e for e in entries if e["type"] == "line_version")
    assert [x["lyric"] for x in lv["notes"]] == ["天", "空", "-"]
    res = compare.evaluate(wd, f)
    assert res["total"] == {"notes": 3, "matched": 3, "same_lyric": 1, "same_kind": 1}


def test_join_tokens_round_trips():
    from bapu.lyrics.parse import tokenize

    for words in (["天", "空"], ["I'm", "my", "天", "hello", "海"], ["hello", "world"]):
        assert tokenize(compare.join_tokens(words)) == words


def test_from_svp_fixes_missing_subtitle_words(tmp_path):
    wd = make_run(tmp_path)
    (wd.root / "05_splits" / "boundaries.json").write_text("[]", encoding="utf-8")
    (wd.root / "08_align").mkdir()
    (wd.root / "08_align" / "alignment.json").write_text(json.dumps({"tokens": [
        {"text": "天", "line": 1}, {"text": "空", "line": 1}]}, ensure_ascii=False), encoding="utf-8")
    wd.record("align", StageRecord(key="k", dir="08_align", outputs={"alignment": "08_align/alignment.json"}))
    f = tmp_path / "c.svp"
    # The user sang-in a word the subtitle missed on the "-" note.
    f.write_text(json.dumps(project([n(0, 1, 60, "天"), n(1, 1, 62, "海"), n(2, 2, 64, "空")]), ensure_ascii=False),
                 encoding="utf-8")
    entries, stats = compare.from_svp(wd, f)
    assert [e["type"] for e in entries] == ["line_text", "line_version"]
    assert entries[0]["text"] == "天海空"


def test_from_svp_split_decisions(tmp_path):
    wd = make_run(tmp_path)
    (wd.root / "05_splits" / "boundaries.json").write_text("[]", encoding="utf-8")
    score = json.loads((wd.root / "score.json").read_text(encoding="utf-8"))
    score["notes"][1]["flags"] = ["auto_split"]  # the note at 0.5 s was cut from the one at 0.0 s
    (wd.root / "score.json").write_text(json.dumps(score, ensure_ascii=False), encoding="utf-8")
    joined = tmp_path / "joined.svp"
    joined.write_text(json.dumps(project([n(0, 2, 60, "天"), n(2, 2, 64, "空")]), ensure_ascii=False), encoding="utf-8")
    entries, stats = compare.from_svp(wd, joined)
    assert entries == [{"type": "split", "at": 0.5, "action": "reject"}] and stats["needs_rerun"] == 1
    kept = tmp_path / "kept.svp"
    kept.write_text(json.dumps(project([n(0, 1, 60, "天"), n(1, 1, 62, "-"), n(2, 2, 64, "空")]), ensure_ascii=False),
                    encoding="utf-8")
    entries, stats = compare.from_svp(wd, kept)
    assert entries == [{"type": "split", "at": 0.5, "action": "confirm"}] and "needs_rerun" not in stats
