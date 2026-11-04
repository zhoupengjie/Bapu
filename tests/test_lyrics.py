"""Lyric parsing and g2p. All lyric text here is made up."""

from __future__ import annotations

from bapu.lyrics import g2p
from bapu.lyrics.parse import parse_file, tokenize
from bapu.stages.lyrics import build

ASS = """[Script Info]
Title: test

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:05.00,0:00:07.50,Default,,0,0,0,,{\\an8}你好 hello
Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,天空\\N海洋
Dialogue: 0,0:00:09.00,0:00:10.00,Default,,0,0,0,,♪
"""


def test_tokenize():
    assert tokenize("天空 don't stop！123") == ["天", "空", "don't", "stop"]
    assert tokenize("'") == []


def test_parse_ass_sorts_and_drops_empty(tmp_path):
    p = tmp_path / "x.ass"
    p.write_text(ASS, encoding="utf-8-sig")
    lines = parse_file(p)
    assert [ln.start for ln in lines] == [1.0, 5.0]
    assert lines[0].tokens == ["天", "空", "海", "洋"]
    assert lines[1].tokens == ["你", "好", "hello"]
    assert lines[1].end == 7.5


def test_parse_lrc_and_txt(tmp_path):
    lrc = tmp_path / "x.lrc"
    lrc.write_text("[00:01.00]天空\n[00:03.50][00:10.00]海洋\n", encoding="utf-8")
    lines = parse_file(lrc)
    assert [(ln.start, ln.end) for ln in lines] == [(1.0, 3.5), (3.5, 10.0), (10.0, None)]
    txt = tmp_path / "x.txt"
    txt.write_text("天空\n\n海洋\n", encoding="gb18030")
    assert [ln.tokens for ln in parse_file(txt)] == [["天", "空"], ["海", "洋"]]


def test_g2p():
    assert g2p.jyutping_letters(["你", "好"]) == ["nei", "hou"]
    assert g2p.english_syllables("hello") == 2
    assert g2p.english_syllables("stone") == 1
    assert g2p.english_letters("Don't") == "don't"


def test_build_applies_text_and_lang_fixes(tmp_path):
    p = tmp_path / "x.ass"
    p.write_text(ASS, encoding="utf-8")
    lines = parse_file(p)
    fixes = [{"type": "line_text", "line": 1, "text": "天海", "_id": 1},
             {"type": "line_lang", "line": 2, "lang": "en", "_id": 2},
             {"type": "line_text", "line": 9, "text": "x", "_id": 3}]
    data, used = build(lines, "yue", fixes)
    assert [t["text"] for t in data[0]["tokens"]] == ["天", "海"]
    assert {t["lang"] for t in data[1]["tokens"]} == {"en"}
    assert data[1]["tokens"][2]["syll"] == 2
    assert used == {1, 2}  # line 9 does not exist: stale
