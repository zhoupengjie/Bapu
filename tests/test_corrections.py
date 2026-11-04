from __future__ import annotations

import pytest

from bapu import corrections as corr


def test_load_validates(tmp_path):
    p = tmp_path / "corrections.yaml"
    p.write_text("- {type: merge, at: 1.0, action: confirm}\n- {type: kind, at: 2.0, kind: sustain}\n", encoding="utf-8")
    entries = corr.load(p)
    assert [e["_id"] for e in entries] == [1, 2]
    p.write_text("- {type: merge, at: 1.0, action: maybe}\n", encoding="utf-8")
    with pytest.raises(corr.CorrectionsError):
        corr.load(p)
    p.write_text("- {type: nonsense}\n", encoding="utf-8")
    with pytest.raises(corr.CorrectionsError):
        corr.load(p)
    p.write_text("- {type: pitch, at: 1.0}\n", encoding="utf-8")
    with pytest.raises(corr.CorrectionsError):
        corr.load(p)


def test_match_time():
    times = [1.0, 2.0, 3.0]
    assert corr.match_time(times, 2.02, 0.03) == 1
    assert corr.match_time(times, 2.05, 0.03) is None
    assert corr.match_time([], 1.0, 0.03) is None
    assert corr.match_time(times, 0.99, 0.03) == 0


def test_fingerprint_ignores_position():
    a = [{"type": "merge", "at": 1.0, "action": "confirm", "_id": 1}]
    b = [{"type": "merge", "at": 1.0, "action": "confirm", "_id": 7}]
    assert corr.fingerprint(a) == corr.fingerprint(b)
    assert corr.fingerprint(a) != corr.fingerprint([])


def test_append_keeps_user_comments(tmp_path):
    p = tmp_path / "corrections.yaml"
    p.write_text("# my note\n- type: tempo  # keep this\n  bpm: 125\n", encoding="utf-8")
    corr.append(p, [{"type": "merge", "at": 1.5, "action": "reject"}], comment="from-svp")
    text = p.read_text(encoding="utf-8")
    assert text.startswith("# my note\n- type: tempo  # keep this\n")
    assert [e["type"] for e in corr.load(p)] == ["tempo", "merge"]
