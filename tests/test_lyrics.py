"""Lyric parsing and g2p. All lyric text here is made up."""

from __future__ import annotations

import pytest

from bapu.lyrics import g2p
from bapu.lyrics.parse import parse_file, tokenize
from bapu.lyrics.fuse import Tok, fuse, keys_for, prefer_exact, split_jyutping, sub_cost

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


def test_syllable_starts():
    assert g2p.syllable_starts("jealousy") == [0, 3, 6]  # j-ea | l-ou | s-y
    assert g2p.syllable_starts("hello") == [0, 3]
    assert g2p.syllable_starts("stone") == [0]
    for w in ("beautiful", "every", "rhythm"):
        assert len(g2p.syllable_starts(w)) == g2p.english_syllables(w)


def toks(words, line=1, source="ref"):
    return [Tok(w, k, line, source=source) for w, k in zip(words, keys_for(words))]


def dict_toks(words):
    return [Tok(w, k, 1, time=float(i), source="dictation") for i, (w, k) in enumerate(zip(words, keys_for(words)))]


def test_near_homophones():
    assert split_jyutping("hung") == ("h", "ung")
    assert split_jyutping("ngo") == ("ng", "o")
    assert sub_cost("hung", "fung", False, False) < 0.75  # same final
    assert sub_cost("hung", "sap", False, False) > 1


def test_fuse_inserts_words_the_reference_missed():
    out, events = fuse(toks(["天", "空", "海"]), dict_toks(["天", "空", "大", "大", "海"]))
    assert [t.text for t in out] == ["天", "空", "大", "大", "海"]
    added = [t for t in out if t.optional]
    assert len(added) == 2 and len({t.group for t in added}) == 1
    assert [e["type"] for e in events] == ["insert"]


def test_fuse_prefers_exact_over_near_homophone():
    # dictation "山 花 班 间": 班 (baan) rhymes with 间 (gaan) but 间 itself follows.
    out, _ = fuse(toks(["山", "间"]), dict_toks(["山", "花", "班", "间"]))
    assert [t.text for t in out] == ["山", "花", "班", "间"]
    assert [t.optional for t in out] == [False, True, True, False]


def test_fuse_substitution_becomes_alternative():
    out, events = fuse(toks(["天", "空"]), dict_toks(["天", "风"]))
    assert [t.text for t in out] == ["天", "空"]
    assert out[1].alt is not None and out[1].alt.text == "风"


def test_fuse_keeps_reference_english_and_locked_lines():
    out, events = fuse(toks(["hello", "海"]), dict_toks(["哈", "佬", "海"]))
    assert [t.text for t in out] == ["hello", "海"]
    assert events and events[0]["reason"] == "english"
    out, events = fuse(toks(["天", "海"]), dict_toks(["天", "大", "海"]), locked_lines={1})
    assert [t.text for t in out] == ["天", "海"]


def test_fuse_drops_stray_english_in_chinese_passage():
    out, _ = fuse(toks(["天", "空", "海", "洋"]), dict_toks(["天", "ok", "大", "海", "洋"]))
    assert [t.text for t in out if not t.optional] == ["天", "空", "海", "洋"]
    assert all(not t.latin for t in out)


def test_pinyin_and_language_choice():
    from bapu.lyrics.fuse import dictation_tokens
    from bapu.lyrics.parse import tokenize
    from bapu.stages.dictate import asr_language
    from bapu.stages.lyrics import check_langs, chinese_of

    assert g2p.pinyin_letters(["你", "好", "绿"]) == ["ni", "hao", "lv"]
    assert g2p.cjk_letters(["你"], "zh") == ["ni"] and g2p.cjk_letters(["你"], "yue") == ["nei"]
    assert check_langs("yue, en") == ["yue", "en"]
    with pytest.raises(ValueError):
        check_langs(["fr"])
    assert chinese_of(["en"]) == ["yue"] and chinese_of(["zh", "yue"]) == ["zh", "yue"]
    assert asr_language(["yue", "en"]) == "Cantonese"
    assert asr_language(["zh"]) == "Chinese"
    assert asr_language(["yue", "zh", "en"]) is None
    assert asr_language(["en"]) == "English"
    # Dictated tokens take the language of the line sung at their time.
    phrases = [{"t0": 0.0, "t1": 2.0, "text": "你好"}, {"t0": 3.0, "t1": 5.0, "text": "你好"}]
    toks = dictation_tokens(phrases, tokenize, lambda t: "zh" if t > 2.5 else "yue")
    assert [(x.key, x.lang) for x in toks] == [("nei", "yue"), ("hou", "yue"), ("ni", "zh"), ("hao", "zh")]


def test_fuse_in_mandarin_uses_pinyin():
    ref = [Tok(w, k, 1, lang="zh") for w, k in zip(["天", "海"], keys_for(["天", "海"], "zh"))]
    dic = [Tok(w, k, 1, time=float(i), source="dictation", lang="zh")
           for i, (w, k) in enumerate(zip(["天", "大", "海"], keys_for(["天", "大", "海"], "zh")))]
    out, _ = fuse(ref, dic)
    assert [t.text for t in out] == ["天", "大", "海"] and out[1].optional and out[1].lang == "zh"
