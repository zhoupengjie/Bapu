"""Tokens -> letters for the aligner, and syllable counts.

Cantonese: OpenCC `s2hk` to Hong Kong characters, then ToJyutping on the whole
line (polyphones depend on context), tone digits removed. English: lower-case
letters; syllables by vowel groups. Japanese: tokens are already hiragana
morae (bapu.lyrics.japanese); each gets its Hepburn letters.
"""

from __future__ import annotations

import re
from functools import lru_cache

from bapu.lyrics import japanese


def is_latin(token: str) -> bool:
    return token[0].isascii()


def is_kana(token: str) -> bool:
    return japanese.has_kana(token)


def token_lang(token: str, lang: str) -> str:
    """`en` for a Latin word, `ja` for a kana token or any token of a Japanese
    line, otherwise the line's Chinese language."""
    if is_latin(token):
        return "en"
    return "ja" if lang == "ja" or is_kana(token) else lang


def japanese_letters(morae: list[str]) -> list[str]:
    """Hepburn letters (a-z) per mora; empty for a kanji without a reading."""
    return [japanese.romaji(m) for m in morae]


def english_syllables(word: str) -> int:
    w = word.lower().strip("'")
    n = len(re.findall(r"[aeiouy]+", w))
    if n > 1 and w.endswith("e") and not w.endswith(("le", "ee")):
        n -= 1
    return max(n, 1)


def syllable_starts(word: str) -> list[int]:
    """Index into `english_letters(word)` where each syllable starts.

    Vowel groups mark syllables (same rule as `english_syllables`); a syllable
    after the first starts at the consonant just before its vowel group, so
    "jealousy" splits as j-ea | l-ou | s-y.
    """
    letters = english_letters(word)
    n = english_syllables(word)
    groups = [m.start() for m in re.finditer(r"[aeiouy]+", letters)]
    starts = [0]
    for g in groups[1:n]:
        k = g - 1 if g > 0 and letters[g - 1] not in "aeiouy'" else g
        starts.append(max(k, starts[-1] + 1))
    while len(starts) < n:  # fewer vowel groups than syllables: spread the rest
        starts.append(min(len(letters) - 1, starts[-1] + 1))
    return starts


def english_letters(word: str) -> str:
    return re.sub(r"[^a-z']", "", word.lower())


@lru_cache(maxsize=1)
def _s2hk():
    from opencc import OpenCC

    return OpenCC("s2hk")


def pinyin_letters(chars: list[str]) -> list[str]:
    """Mandarin: toneless pinyin per character, read in context (ü is `v`)."""
    if not chars:
        return []
    from pypinyin import Style, lazy_pinyin

    out = lazy_pinyin("".join(chars), style=Style.NORMAL, errors=lambda x: [""] * len(x))
    if len(out) != len(chars):
        out = [(lazy_pinyin(c, style=Style.NORMAL, errors="ignore") or [""])[0] for c in chars]
    return [re.sub(r"[^a-z]", "", p.lower()) for p in out]


def cjk_letters(chars: list[str], lang: str) -> list[str]:
    """Letters for the aligner: Jyutping for Cantonese (`yue`), pinyin for
    Mandarin (`zh`), Hepburn for Japanese morae (`ja`)."""
    if lang == "ja":
        return japanese_letters(chars)
    return pinyin_letters(chars) if lang == "zh" else jyutping_letters(chars)


def jyutping_letters(chars: list[str]) -> list[str]:
    """Letters (no tones) for each CJK character, read in context."""
    if not chars:
        return []
    import ToJyutping

    converted = _s2hk().convert("".join(chars))
    pairs = ToJyutping.get_jyutping_list(converted)
    if len(pairs) != len(chars):
        # s2hk is one-to-one for characters; fall back per character if not.
        pairs = [ToJyutping.get_jyutping_list(_s2hk().convert(c))[0] for c in chars]
    out = []
    for _, reading in pairs:
        first = reading.split()[0] if reading else ""
        out.append(re.sub(r"[^a-z]", "", first))
    return out
