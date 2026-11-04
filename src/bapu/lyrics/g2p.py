"""Tokens -> letters for the aligner, and syllable counts.

Cantonese: OpenCC `s2hk` to Hong Kong characters, then ToJyutping on the whole
line (polyphones depend on context), tone digits removed. English: lower-case
letters; syllables by vowel groups.
"""

from __future__ import annotations

import re
from functools import lru_cache


def is_latin(token: str) -> bool:
    return token[0].isascii()


def english_syllables(word: str) -> int:
    w = word.lower().strip("'")
    n = len(re.findall(r"[aeiouy]+", w))
    if n > 1 and w.endswith("e") and not w.endswith(("le", "ee")):
        n -= 1
    return max(n, 1)


def english_letters(word: str) -> str:
    return re.sub(r"[^a-z']", "", word.lower())


@lru_cache(maxsize=1)
def _s2hk():
    from opencc import OpenCC

    return OpenCC("s2hk")


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
