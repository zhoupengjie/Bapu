"""Japanese: readings, morae, romaji, fusion, export and a synthetic run of
lyrics -> align -> assign. All Japanese and romaji text here is made up."""

from __future__ import annotations

import json
import string
import types
from pathlib import Path

import numpy as np
import pytest

from bapu import compare, report
from bapu.export import midi
from bapu.export import svp as svp_export
from bapu.lyrics import g2p, japanese
from bapu.lyrics.fuse import Tok, fuse, held_vowels, keys_for, sub_cost
from bapu.lyrics.parse import parse_file, romaji_plan, tokenize, tokenize_line
from bapu.models.registry import Registry
from bapu.progress import StageProgress, null_progress
from bapu.stages import align as align_stage
from bapu.stages import assign, lyrics
from bapu.stages.base import StageContext
from bapu.stages.dictate import asr_language
from bapu.workdir import StageRecord, WorkDir

JA = ["ja", "en"]
# Kanji, hiragana, katakana with ー, ん, っ, small ょ, a comma and an English word.
MIXED = "猫とコーヒーを飲んで、ちょっと待って hello"
MIXED_MORAE = ["ね", "こ", "と", "こ", "ひ", "を", "の", "ん", "で", "ちょっ", "と", "まっ", "て", "hello"]


def mms_letters() -> set[str]:
    from torchaudio.pipelines import MMS_FA

    return set(MMS_FA.get_dict(star=None))


# --- languages ---------------------------------------------------------------

def test_check_langs_and_asr_language():
    assert lyrics.check_langs(["ja", "en"]) == ["ja", "en"]
    assert lyrics.check_langs("ja") == ["ja"]
    for bad in (["ja", "yue"], ["zh", "ja"], "ja,yue,en"):
        with pytest.raises(ValueError, match="日语"):
            lyrics.check_langs(bad)
    assert lyrics.main_lang(["ja", "en"]) == "ja" and lyrics.main_lang(["en"]) == "yue"
    assert asr_language(["ja"]) == "Japanese" and asr_language(["ja", "en"]) == "Japanese"
    assert asr_language(["yue", "en"]) == "Cantonese"  # unchanged


# --- readings and morae ------------------------------------------------------

def test_split_morae_rules():
    assert japanese.split_morae("きゃっきゃーん") == ["きゃっ", "きゃ", "ん"]  # small kana, っ, ー, ん
    assert japanese.split_morae("っかあ") == ["っか", "あ"]  # っ starting a phrase joins the next mora
    assert japanese.split_morae("っ") == []
    assert japanese.split_morae("ぁあ") == ["ぁ", "あ"]  # a small kana with nothing before stands alone
    assert japanese.to_hiragana("ティーカップ") == "てぃーかっぷ"
    assert japanese.to_hiragana("ヴァ") == "ゔぁ"


def test_tokenize_reads_kanji_and_katakana_into_morae():
    assert tokenize(MIXED, JA) == MIXED_MORAE
    assert tokenize("僕は駅へ行く", JA) == ["ぼ", "く", "わ", "え", "き", "え", "い", "く"]  # particles は / へ as sung
    assert tokenize("っていうか", JA) == ["って", "い", "う", "か"]
    assert tokenize("ｶﾞﾗｽ", JA) == ["が", "ら", "す"]  # half-width katakana
    # Without Japanese, kana are not tokens (as before).
    assert tokenize("猫とコーヒー hello") == ["猫", "hello"]


def test_romaji_letters_per_mora():
    cases = {"し": "shi", "つ": "tsu", "ふ": "fu", "ん": "n", "きゃ": "kya", "かっ": "ka", "っか": "ka",
             "しゃ": "sha", "ちょ": "cho", "じゅ": "ju", "ふぁ": "fa", "てぃ": "ti", "ゔぁ": "va", "うぃ": "wi",
             "を": "o", "ぢ": "ji", "づ": "zu"}
    assert g2p.japanese_letters(list(cases)) == list(cases.values())
    assert g2p.cjk_letters(["ね", "こ"], "ja") == ["ne", "ko"]
    assert keys_for(["ね", "こ", "hello"], "ja") == ["ne", "ko", "hello"]
    assert keys_for(["ね"], "yue") == ["ne"]  # a kana token is Japanese whatever the line
    # Every kana and every small-kana combination gives letters the aligner has.
    allowed = mms_letters() & set(string.ascii_lowercase)
    assert allowed == set(string.ascii_lowercase)
    morae = list(japanese.ROMAJI) + [b + s for b in japanese.ROMAJI if b not in japanese.SMALL
                                     for s in japanese.SMALL] + [b + "っ" for b in japanese.ROMAJI]
    for m in morae:
        letters = japanese.romaji(m)
        assert letters and set(letters) <= allowed, m


def test_token_language():
    assert g2p.token_lang("ね", "yue") == "ja"
    assert g2p.token_lang("空", "ja") == "ja"
    assert g2p.token_lang("空", "yue") == "yue"
    assert g2p.token_lang("hello", "ja") == "en"


# --- romaji ------------------------------------------------------------------

def test_romaji_words():
    expect = {
        "kin'iro": "きんいろ", "hon'ya": "ほんや", "minna": "みんな", "konnbanwa": "こんばんわ",
        "zutto": "ずっと", "matcha": "まっちゃ", "issho": "いっしょ",
        "tōku": "とく", "ookii": "おき", "toukyou": "とうきょう",  # ō / oo hold one vowel; ou stays two
        "shimbun": "しんぶん", "wo": "を",
        "si": "し", "ti": "ち", "tu": "つ", "hu": "ふ", "zi": "じ", "di": "ぢ", "sya": "しゃ", "tya": "ちゃ",
        "zya": "じゃ", "jya": "じゃ",
    }
    assert {w: japanese.romaji_to_kana(w) for w in expect} == expect
    for english in ("dancing", "hello", "light", "don't", "tomorrow"):
        assert japanese.romaji_to_kana(english) is None


def test_romaji_lines():
    # n', a macron and a doubled consonant; the particle "e" as heard.
    toks, latin = tokenize_line("kin'iro no neko e tōku hashitte", JA)
    assert toks == ["き", "ん", "い", "ろ", "の", "ね", "こ", "え", "と", "く", "は", "しっ", "て"]
    assert latin["read_as"] == "ja" and latin["romaji_votes"] == latin["votes"]
    # An English word in a romaji line stays English; the line is mixed, so it is uncertain.
    toks, latin = tokenize_line("kimi to dancing shiyou", JA)
    assert toks == ["き", "み", "と", "dancing", "し", "よ", "う"]
    assert (latin["votes"], latin["romaji_votes"], latin["read_as"]) == (3, 2, "ja")
    assert lyrics.latin_uncertain(latin)
    # An English line: common words that also parse as romaji follow the line.
    toks, latin = tokenize_line("Take me home tonight", JA)
    assert toks == ["Take", "me", "home", "tonight"] and latin["read_as"] == "en"
    toks, latin = tokenize_line("one more time", JA)
    assert toks == ["one", "more", "time"] and latin["votes"] == 0 and latin["read_as"] == "en"
    # A word starting with a doubled consonant joins the mora before, as っ does in kana.
    assert tokenize("kimi tte", JA) == ["き", "みっ", "て"] == tokenize("きみって", JA)
    # Forced either way (a line_lang correction).
    assert tokenize("Take me home", JA, romaji=True) == ["た", "け", "め", "ほ", "め"]
    assert tokenize("kimi no koe", JA, romaji=False) == ["kimi", "no", "koe"]
    # Not Japanese: romaji is plain English.
    assert tokenize("kimi no koe") == ["kimi", "no", "koe"]


def test_romaji_plan_threshold():
    _, st = romaji_plan(["kimi", "smile"])  # one romaji, one English vote: a tie reads as romaji
    assert (st["votes"], st["romaji_votes"], st["read_as"]) == (2, 1, "ja")
    _, st = romaji_plan(["sayonara", "my", "love"])  # one of three
    assert st["read_as"] == "en"


def test_parse_lrc_japanese(tmp_path):
    lrc = tmp_path / "x.lrc"
    lrc.write_text("[00:01.00]猫とコーヒー\n[00:04.00]kimi to dancing shiyou\n[00:08.00]♪\n", encoding="utf-8")
    lines = parse_file(lrc, JA)
    assert [(ln.start, ln.end) for ln in lines] == [(1.0, 4.0), (4.0, 8.0)]
    assert lines[0].tokens == ["ね", "こ", "と", "こ", "ひ"] and lines[0].latin is None
    assert lines[1].tokens == ["き", "み", "と", "dancing", "し", "よ", "う"] and lines[1].latin["read_as"] == "ja"
    # The same file read as a Cantonese song keeps the old behaviour.
    assert parse_file(lrc)[0].tokens == ["猫"]


def test_join_tokens_round_trips_japanese():
    for words in (["き", "み", "って", "い", "う"], ["あ", "あ", "っか", "hello", "ね"], ["きゃっ", "きゃ", "ん"]):
        assert tokenize(compare.join_tokens(words), JA) == words


# --- fusion -----------------------------------------------------------------

def ja_toks(words, source="ref", line=1):
    return [Tok(w, k, line, time=float(i), source=source, lang="ja") for i, (w, k) in
            enumerate(zip(words, keys_for(words, "ja")))]


def test_japanese_near_homophones():
    assert sub_cost("ka", "ga", False, False, japanese=True) == 0.4  # voicing
    assert sub_cost("ka", "ko", False, False, japanese=True) == 0.7  # same consonant
    assert sub_cost("ka", "to", False, False, japanese=True) > 1
    assert sub_cost("ka", "a", False, False, japanese=True) > 1  # sharing only the vowel is not enough
    assert held_vowels("to", ["u"]) and held_vowels("ka", ["a", "a"]) and held_vowels("se", ["i"])
    assert not held_vowels("ka", ["i"]) and not held_vowels("ka", ["ka"])


def test_fuse_romaji_reference_with_kana_dictation():
    # Reference "tōku made" (one o); dictation reads the kanji as と お く ま で.
    ref = ja_toks(tokenize("tōku made", JA))
    dic = ja_toks(tokenize("遠くまで", JA), source="dictation")
    assert len(dic) == len(ref) + 1
    out, events = fuse(ref, dic)
    assert [t.text for t in out] == [t.text for t in ref] and not any(t.optional for t in out)
    assert [(e["type"], e["reason"]) for e in events] == [("extra_ignored", "long_vowel")]
    # A real extra word is still offered to the aligner.
    out, events = fuse(ja_toks(tokenize("kimi to", JA)), ja_toks(tokenize("君と猫", JA), source="dictation"))
    assert [t.optional for t in out] == [False, False, False, True, True]
    assert [e["type"] for e in events] == ["insert"]


# --- export -------------------------------------------------------------------

SCORE = {
    "tempo": {"bpm": 120.0, "time_signature": "4/4"},
    "lang": "ja",
    "notes": [
        {"start": 0.5, "dur": 0.5, "pitch": 60, "lyric": "きゃっ", "lang": "ja", "line": 1},
        {"start": 1.0, "dur": 0.5, "pitch": 62, "lyric": "-", "lang": "ja", "line": 1},
        {"start": 1.5, "dur": 0.5, "pitch": 64, "lyric": "ん", "lang": "ja", "line": 1},
        {"start": 2.0, "dur": 0.5, "pitch": 65, "lyric": "hello", "lang": "en", "line": 2},
        {"start": 2.5, "dur": 0.5, "pitch": 67, "lyric": "+", "lang": "en", "line": 2},
    ],
}


def test_svp_and_midi_export_japanese(tmp_path):
    proj = svp_export.build(SCORE)
    notes = proj["tracks"][0]["mainGroup"]["notes"]
    assert [n["lyrics"] for n in notes] == ["きゃっ", "-", "ん", "hello", "+"]
    assert [n["attributes"]["languageOverride"] for n in notes] == ["japanese"] * 3 + ["english"] * 2
    assert proj["tracks"][0]["mainRef"]["database"]["languageOverride"] == "japanese"
    back = midi.read_back(midi.write(SCORE, tmp_path / "x.mid"))
    assert [n["lyric"] for n in back] == ["きゃっ", "-", "ん", "hello", "+"]


# --- synthetic run: lyrics -> align -> assign -> export / review ---------------

def write(p: Path, data) -> Path:
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def ctx_for(stage, tmp_path: Path, inputs, params=None, models=None, registry=None, corrections=()):
    out = tmp_path / f"{stage.index:02d}_{stage.name}"
    out.mkdir(exist_ok=True)
    return StageContext(inputs=inputs, out_dir=out, params=dict(stage.defaults, **(params or {})), models=models or {},
                        registry=registry or Registry({}, tmp_path), progress=StageProgress(stage.name, null_progress),
                        corrections=list(corrections))


def synthetic_emissions(tokens: list[dict], starts: list[float], frames: int, dictionary: dict[str, int],
                        spacing: dict[int, float] | None = None):
    """Log-probabilities where each token's letters are sung one per 40 ms (or
    `spacing[token index]` s) from its start."""
    import torch

    em = torch.full((frames, len(dictionary)), -20.0)
    em[:, 0] = 0.0  # blank everywhere else
    for i, (t, s) in enumerate(zip(tokens, starts)):
        for k, ch in enumerate(t["letters"]):
            f = round((s + k * (spacing or {}).get(i, 0.04)) / 0.02)
            em[f, 0] = -20.0
            em[f, dictionary[ch]] = 0.0
    return torch.log_softmax(em, dim=-1)


def test_synthetic_run_japanese(tmp_path, monkeypatch):
    import soundfile as sf
    import torch
    from torchaudio.pipelines import MMS_FA

    # Reference: a kanji line and a romaji line with one English word (made up).
    ref = tmp_path / "ref.lrc"
    ref.write_text("[00:01.00]猫とコーヒー\n[00:05.00]zutto matte hello\n", encoding="utf-8")
    # Dictation heard one more word in line 1 (熱い) that the reference lacks.
    dictation = write(tmp_path / "dictation.json", {"phrases": [
        {"t0": 0.8, "t1": 4.9, "notes": [1, 9], "text": "猫と熱いコーヒー", "language": "Japanese"},
        {"t0": 4.9, "t1": 8.0, "notes": [10, 15], "text": "ずっと待って hello", "language": "Japanese"}]})
    res = lyrics.STAGE.run(ctx_for(lyrics.STAGE, tmp_path, {"dictation": dictation, "lyrics": ref},
                                   {"langs": JA}))
    lyr = json.loads(res.outputs["lyrics_lines"].read_text(encoding="utf-8"))
    toks = [t for ln in lyr["lines"] for t in ln["tokens"]]
    assert lyr["lang"] == "ja" and lyr["lines"][1]["latin"]["read_as"] == "ja"
    assert [t["text"] for t in toks] == ["ね", "こ", "と", "あ", "つ", "い", "こ", "ひ", "ずっ", "と", "まっ", "て", "hello"]
    assert [t["optional"] for t in toks].count(True) == 3
    assert {t["lang"] for t in toks[:-1]} == {"ja"} and toks[-1]["lang"] == "en"
    assert res.metrics["no_reading"] == 0 and res.metrics["romaji_lines"] == 1

    # Align on synthetic letter posteriors with the real CTC forced alignment.
    starts = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.6, 5.0, 5.5, 6.0, 6.5, 7.0]
    frames = 450
    dictionary = MMS_FA.get_dict(star=None)
    tokens = [t for ln in lyr["lines"] for t in ln["tokens"]]
    em = synthetic_emissions(tokens, starts, frames, dictionary, spacing={12: 0.4 / 3})  # "hello": llo at 7.4 s
    wav = tmp_path / "lead.wav"
    sf.write(str(wav), np.zeros(frames * 320, dtype=np.float32), 16000)
    monkeypatch.setenv("BAPU_DEVICE", "cpu")
    monkeypatch.setattr(torch.hub, "set_dir", lambda d: None)
    monkeypatch.setattr(type(MMS_FA), "get_model", lambda self, **kw: types.SimpleNamespace(
        to=lambda d: types.SimpleNamespace(eval=lambda: object())))
    monkeypatch.setattr(align_stage, "emissions", lambda *a, **kw: em)
    fake_registry = types.SimpleNamespace(ensure_ready=lambda spec: tmp_path, model_dir=lambda spec: tmp_path / "hub" / "x")
    spec = types.SimpleNamespace(id="mms-fa", name="MMS")
    res = align_stage.STAGE.run(ctx_for(align_stage.STAGE, tmp_path, {"lead_dry": wav, "lyrics_lines": res.outputs["lyrics_lines"]},
                                        models={"model": spec}, registry=fake_registry))
    ali = json.loads(res.outputs["alignment"].read_text(encoding="utf-8"))
    assert res.metrics["inserted_kept"] == 3  # the recording supports 熱い
    assert [t["t0"] for t in ali["tokens"]] == pytest.approx(starts, abs=0.021)

    # Notes: one per mora, an extra one where こ is held (コー), two for "hello".
    onsets = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.3, 4.6, 5.0, 5.5, 6.0, 6.5, 7.0, 7.4]
    durs = [0.45] * 6 + [0.3, 0.3, 0.3] + [0.45] * 4 + [0.4, 0.4]
    notes = [{"start": s, "dur": d, "pitch": 60.0 + i % 5, "merged_from": [i + 1], "merges": []}
             for i, (s, d) in enumerate(zip(onsets, durs))]
    inputs = {"merged_notes": write(tmp_path / "notes.json", {"notes": notes}),
              "boundaries": write(tmp_path / "b.json", []), "alignment": res.outputs["alignment"],
              "tempo": write(tmp_path / "tempo.json", {"bpm": 120.0, "first_beat": 0.0, "beat_phase_s": 0.0,
                                                         "confidence": "high", "time_signature": "4/4"})}
    res = assign.STAGE.run(ctx_for(assign.STAGE, tmp_path, inputs))
    score = json.loads(res.outputs["score"].read_text(encoding="utf-8"))
    assert [n["lyric"] for n in score["notes"]] == ["ね", "こ", "と", "あ", "つ", "い", "こ", "-", "ひ",
                                                   "ずっ", "と", "まっ", "て", "hello", "+"]
    assert score["lang"] == "ja" and res.metrics["sequence_ok"]
    assert [n["lang"] for n in score["notes"]] == ["ja"] * 13 + ["en"] * 2
    sv = svp_export.build(score)["tracks"][0]["mainGroup"]["notes"]
    assert [n["attributes"]["languageOverride"] for n in sv] == ["japanese"] * 13 + ["english"] * 2

    # The review counts morae and carries no lyric text (no kana at all).
    wd = WorkDir(tmp_path)
    for st, name, out in ((lyrics.STAGE, "lyrics_lines", "lyrics.json"), (align_stage.STAGE, "alignment", "alignment.json"),
                          (assign.STAGE, "score", "score.json")):
        wd.record(st.name, StageRecord(key="k", dir=f"{st.index:02d}_{st.name}",
                                       outputs={name: f"{st.index:02d}_{st.name}/{out}"}, params=dict(st.defaults)))
    written = report.finalize(wd)
    md = written["review_md"].read_text(encoding="utf-8")
    review = (tmp_path / "review.json").read_text(encoding="utf-8")
    for text in (md, review):
        assert not any(japanese.is_kana_char(ch) for ch in text)
        assert "hello" not in text
    lines = json.loads(review)["lines"]
    assert [(ln["syllables"], ln["notes"]) for ln in lines] == [(8, 9), (6, 6)]
    assert "音拍" in md


def test_line_lang_settles_a_romaji_line(tmp_path):
    ref = tmp_path / "ref.txt"
    ref.write_text("kimi to dancing shiyou\nTake me home\n", encoding="utf-8")
    dictation = write(tmp_path / "dictation.json", {"phrases": []})
    corrections = [{"type": "line_lang", "line": 1, "lang": "en", "_id": 1},
                   {"type": "line_lang", "line": 2, "lang": "ja", "_id": 2},
                   {"type": "line_lang", "line": 2, "lang": "yue", "_id": 3}]  # not for a Japanese song
    res = lyrics.STAGE.run(ctx_for(lyrics.STAGE, tmp_path, {"dictation": dictation, "lyrics": ref}, {"langs": JA},
                                   corrections=corrections))
    lyr = json.loads(res.outputs["lyrics_lines"].read_text(encoding="utf-8"))
    assert [t["text"] for t in lyr["lines"][0]["tokens"]] == ["kimi", "to", "dancing", "shiyou"]
    assert [t["text"] for t in lyr["lines"][1]["tokens"]] == ["た", "け", "め", "ほ", "め"]
    assert all(ln["latin"]["forced"] for ln in lyr["lines"])
    used = json.loads((Path(res.outputs["lyrics_lines"]).parent / "corrections_used.json").read_text())
    assert used == [1, 2]
