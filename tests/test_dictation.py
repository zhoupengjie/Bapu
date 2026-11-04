"""Dictation phrases and alignment-based resolution (no models; made-up words)."""

from __future__ import annotations

from bapu.stages import align as align_stage
from bapu.stages.dictate import phrases


def test_phrases_split_at_rests():
    notes = [{"start": 0.0, "dur": 0.5}, {"start": 0.5, "dur": 0.5}, {"start": 1.5, "dur": 0.5}]
    assert phrases(notes, rest=0.3, pad=0.2) == [(0.0, 1.2, 1, 2), (1.3, 2.2, 3, 3)]


def test_resolve_judges_inserted_words_as_a_group(monkeypatch):
    tokens = [
        {"text": "天", "letters": "tin", "line": 1},
        {"text": "大", "letters": "daai", "line": 1, "optional": True, "group": 7},
        {"text": "大", "letters": "daai", "line": 1, "optional": True, "group": 7},
        {"text": "海", "letters": "hoi", "line": 1, "alt": {"text": "开", "letters": "hoi", "lang": "yue", "syll": 1}},
    ]
    confs = {"A": [0.8, 0.0, 0.5, 0.2], "B": [0.8, 0.6]}  # pass A all tokens; pass B without inserts, with alt
    calls = []

    def fake_align(em, seq, dictionary):
        key = "A" if len(calls) == 0 else "B"
        calls.append(key)
        return [{"t0": float(i), "t1": float(i) + 0.5, "conf": c} for i, c in enumerate(confs[key])]

    monkeypatch.setattr(align_stage, "align_tokens", fake_align)
    final, decisions = align_stage.resolve(None, tokens, {}, keep_conf=0.15, alt_margin=0.15, alt_min=0.3)
    assert [t["text"] for t in final] == ["天", "大", "大", "开"]  # group mean 0.25 keeps both; alt 0.6 >= 0.2+0.15
    assert all("added_from_dictation" in t["flags"] for t in final[1:3])
    assert [d["type"] for d in decisions] == ["insert", "insert", "alternative"]


def test_resolve_keeps_reference_without_strong_evidence(monkeypatch):
    tokens = [{"text": "海", "letters": "hoi", "line": 1, "alt": {"text": "开", "letters": "hoi", "lang": "yue", "syll": 1}}]
    seq = iter([[{"t0": 0.0, "t1": 0.5, "conf": 0.005}], [{"t0": 0.0, "t1": 0.5, "conf": 0.2}]])
    monkeypatch.setattr(align_stage, "align_tokens", lambda em, s, d: next(seq))
    final, _ = align_stage.resolve(None, tokens, {}, keep_conf=0.15, alt_margin=0.15, alt_min=0.3)
    assert final[0]["text"] == "海"  # 0.2 is better than 0.005 but below alt_min
