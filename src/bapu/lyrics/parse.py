"""Read the user's lyric file (.ass / .lrc / .txt) into timed lines.

Lyrics come only from files the user supplies. Nothing here logs lyric text.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from bapu.lyrics import japanese

CJK = "㐀-䶿一-鿿豈-﫿"
TOKEN = re.compile(r"[A-Za-z']+|[" + CJK + "]")
# Japanese: Latin words (macron vowels included, for romaji), and runs of kanji / kana
# (々 〆 included) that are read as one phrase each.
KANA = "ぁ-ゖゝゞァ-ヺー-ヾ々〆"
LATIN = "A-Za-z'āīūēōâîûêôĀĪŪĒŌÂÎÛÊÔ"
JA_TOKEN = re.compile(r"[" + LATIN + "]+|[" + CJK + KANA + "]+")
ASCII_VOWELS = str.maketrans("āīūēōâîûêôĀĪŪĒŌÂÎÛÊÔ",
                             "aiueoaiueoAIUEOAIUEO")
# Common English words that are also valid romaji ("take", "made", "you"). They
# do not vote when a line is judged romaji or English; they follow the decision.
ENGLISH_ALSO_ROMAJI = frozenset("""
a i an in on no so to do go me be he we she you are see sea tea too one use sure pure
time take make made wake fake home hope more some done gone none same game name fame
side ride hide wide inside mine fine nine wine shine rise rose nose tone bone zone
run sun fun man ten men open even seven heaven given woman women human
moon soon noon hero area idea tune future nature desire fire tire wire
someone anyone guide began begin again rain pain gain main remain gonna wanna
""".split())
ROMAJI_SHARE = 0.5  # a line is romaji if at least this share of its voting words parse
LATIN_FIRST = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'āīūēōâîûêôĀĪŪĒŌÂÎÛÊÔ")


@dataclass
class Line:
    text: str
    start: float | None = None
    end: float | None = None
    tokens: list[str] = field(default_factory=list)
    latin: dict | None = None  # Japanese songs: how the line's Latin words were read (counts only)


def decode(raw: bytes) -> str:
    # UTF-16 only with a BOM: without one, almost any even-length byte string
    # "decodes" as UTF-16, so GB18030 files would come out as garbage.
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("歌词文件编码无法识别（试过 UTF-8、UTF-16、GB18030）")


def is_japanese(langs: str | Sequence[str] | None) -> bool:
    """Whether the song's languages (a list, or one code) include Japanese."""
    if langs is None:
        return False
    return langs == "ja" if isinstance(langs, str) else "ja" in langs


def tokenize(text: str, langs: str | Sequence[str] | None = None, romaji: bool | None = None) -> list[str]:
    """CJK characters one by one; Latin words (with apostrophes) whole.
    Digits and punctuation are dropped.

    With Japanese among the song's `langs`, kanji, kana and romaji are read
    instead (see `tokenize_line`). Without it, kana are not tokens."""
    return tokenize_line(text, langs, romaji)[0]


def romaji_plan(words: list[str], romaji: bool | None = None) -> tuple[list[str | None], dict]:
    """Which Latin words of one line are romaji: hiragana for those, None for
    English words; and the counts behind the decision.

    A word votes romaji if it parses completely, English if it does not;
    common English words that also parse (ENGLISH_ALSO_ROMAJI) do not vote.
    The line is romaji if it has a vote and at least ROMAJI_SHARE of the votes
    are romaji; then every word that parses is read as Japanese and the rest
    stay English. Otherwise the whole line stays English. `romaji` True / False
    forces the decision (a `line_lang` correction: ja / en)."""
    kana = [japanese.romaji_to_kana(w) for w in words]
    votes = [k is not None for w, k in zip(words, kana)
             if w.lower().strip("'").translate(ASCII_VOWELS) not in ENGLISH_ALSO_ROMAJI]
    use = romaji if romaji is not None else bool(votes) and sum(votes) >= ROMAJI_SHARE * len(votes)
    plan = [k if use else None for k in kana]
    return plan, {"latin_words": len(words), "votes": len(votes), "romaji_votes": sum(votes),
                  "romaji_words": sum(k is not None for k in plan), "read_as": "ja" if use else "en",
                  "forced": romaji is not None}


def tokenize_line(text: str, langs: str | Sequence[str] | None = None, romaji: bool | None = None
                  ) -> tuple[list[str], dict | None]:
    """Tokens of one line, and (Japanese songs with Latin words) how its Latin
    words were read.

    Japanese: each run of kanji / kana between spaces, punctuation and Latin
    words is one phrase, and every mora of its hiragana reading is a token
    (bapu.lyrics.japanese). Latin words are romaji or English per line
    (`romaji_plan`); consecutive romaji words form one phrase (so a word
    starting with a doubled consonant joins the mora before it, as っ does in
    kana), and become hiragana morae the same way."""
    if not is_japanese(langs):
        return [t for t in TOKEN.findall(text) if t.strip("'")], None
    text = unicodedata.normalize("NFKC", text)
    found = [m for m in JA_TOKEN.finditer(text) if m.group().strip("'")]
    latin = [m.group() for m in found if m.group()[0] in LATIN_FIRST]
    plan, stats = romaji_plan(latin, romaji)
    plan_it = iter(plan)
    out: list[str] = []
    phrase = ""  # hiragana of consecutive romaji words
    prev_end = 0
    for m in found:
        t = m.group()
        if phrase and text[prev_end:m.start()].strip(" \t-"):
            out += japanese.split_morae(phrase)  # punctuation ends a phrase
            phrase = ""
        prev_end = m.end()
        if t[0] not in LATIN_FIRST:
            out += japanese.split_morae(phrase) + japanese.morae(t)
            phrase = ""
            continue
        kana = next(plan_it)
        if kana is not None:
            phrase += kana
            continue
        out += japanese.split_morae(phrase)
        phrase = ""
        out.append(t.translate(ASCII_VOWELS))  # English (macrons, if any, dropped)
    out += japanese.split_morae(phrase)
    return out, stats if latin else None


def _ass_time(t: str) -> float:
    h, m, s = t.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_ass(text: str) -> list[Line]:
    fmt: list[str] | None = None
    lines = []
    for ln in text.splitlines():
        if ln.startswith("Format:") and fmt is None and "Text" in ln and "Start" in ln:
            fmt = [x.strip() for x in ln[7:].split(",")]
        elif ln.startswith("Dialogue:") and fmt:
            parts = ln[9:].split(",", len(fmt) - 1)
            d = dict(zip(fmt, parts))
            body = re.sub(r"\{[^}]*\}", "", d["Text"]).replace(r"\N", " ").replace(r"\n", " ").strip()
            if body:
                lines.append(Line(body, _ass_time(d["Start"]), _ass_time(d["End"])))
    if fmt is None:
        raise ValueError(".ass 文件里没有 [Events] 的 Format 行")
    lines.sort(key=lambda x: x.start or 0.0)
    return lines


LRC_TAG = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")


def parse_lrc(text: str) -> list[Line]:
    stamped = []
    for ln in text.splitlines():
        tags = LRC_TAG.findall(ln)
        body = LRC_TAG.sub("", ln).strip()
        body = re.sub(r"<\d+:\d+(?:\.\d+)?>", "", body)  # enhanced-LRC word stamps
        for m, s in tags:
            if body:
                stamped.append((int(m) * 60 + float(s), body))
    stamped.sort()
    lines = [Line(b, t) for t, b in stamped]
    for a, b in zip(lines, lines[1:]):
        a.end = b.start
    return lines


def parse_txt(text: str) -> list[Line]:
    return [Line(ln.strip()) for ln in text.splitlines() if ln.strip()]


def parse_file(path: Path, langs: str | Sequence[str] | None = None) -> list[Line]:
    """Timed lines with their tokens; `langs` are the song's languages (see `tokenize`)."""
    text = decode(Path(path).read_bytes())
    suffix = Path(path).suffix.lower()
    if suffix == ".ass" or suffix == ".ssa":
        lines = parse_ass(text)
    elif suffix == ".lrc":
        lines = parse_lrc(text)
    elif suffix == ".txt":
        lines = parse_txt(text)
    else:
        raise ValueError(f"不支持的歌词格式：{suffix}（支持 .ass / .lrc / .txt）")
    for line in lines:
        line.tokens, line.latin = tokenize_line(line.text, langs)
    return [ln for ln in lines if ln.tokens]
