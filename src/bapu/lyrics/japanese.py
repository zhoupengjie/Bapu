"""Japanese lyrics: readings (hiragana), morae and Hepburn letters.

A run of Japanese text (kanji and kana) is read with SudachiPy and the
`sudachidict_core` dictionary (both Apache-2.0). A word written in kana keeps
its own spelling (so kana text round-trips unchanged); a word with kanji takes
the dictionary's reading. Katakana become hiragana. The particles は and へ are
sung "wa" and "e", so they become わ and え (SV voices sing kana as written).

Morae, one token each: a small ゃゅょぁぃぅぇぉゎ joins the kana before it
(きゃ), ん is its own mora, っ joins the mora before it (かっ) unless it starts
a phrase (then it joins the next one), and the long-vowel mark ー is dropped:
the assign stage already writes `-` for a held vowel.

A kanji the dictionary cannot read stays one token without letters (the
lyrics stage counts these as `no_reading`). Nothing here logs lyric text.
"""

from __future__ import annotations

import threading
import unicodedata
from functools import lru_cache

SMALL = frozenset("ゃゅょぁぃぅぇぉゎ")
SOKUON = "っ"
CHOON = "ー"
PARTICLE_SOUND = {"は": "わ", "へ": "え"}
SYMBOL_POS = ("補助記号", "空白")

# Hepburn letters (lower-case a-z only: all of them are in the MMS_FA dictionary).
_ROWS = {
    "あいうえお": "a i u e o",
    "かきくけこ": "ka ki ku ke ko", "がぎぐげご": "ga gi gu ge go",
    "さしすせそ": "sa shi su se so", "ざじずぜぞ": "za ji zu ze zo",
    "たちつてと": "ta chi tsu te to", "だぢづでど": "da ji zu de do",
    "なにぬねの": "na ni nu ne no",
    "はひふへほ": "ha hi fu he ho", "ばびぶべぼ": "ba bi bu be bo", "ぱぴぷぺぽ": "pa pi pu pe po",
    "まみむめも": "ma mi mu me mo",
    "やゆよ": "ya yu yo",
    "らりるれろ": "ra ri ru re ro",
    "わゐゑを": "wa i e o",
    "んゔゕゖ": "n vu ka ke",
    "ぁぃぅぇぉ": "a i u e o", "ゃゅょゎ": "ya yu yo wa",
}
ROMAJI: dict[str, str] = {k: v for kana, rom in _ROWS.items() for k, v in zip(kana, rom.split())}
VOWELS = "aeiou"

# Romaji -> kana: Hepburn plus common variants (Kunrei si/ti/tu/hu/zi/sya,
# Nihon-shiki di/du, IME-style jya/nn). Particles are spelled as heard (wa, e, wo).
_SYLLABLES = """
a あ i い u う e え o お
ka か ki き ku く ke け ko こ kya きゃ kyu きゅ kyo きょ
ga が gi ぎ gu ぐ ge げ go ご gya ぎゃ gyu ぎゅ gyo ぎょ
sa さ si し shi し su す se せ so そ sha しゃ shu しゅ sho しょ she しぇ sya しゃ syu しゅ syo しょ
za ざ zi じ ji じ zu ず ze ぜ zo ぞ ja じゃ ju じゅ jo じょ je じぇ
zya じゃ zyu じゅ zyo じょ jya じゃ jyu じゅ jyo じょ
ta た ti ち chi ち tu つ tsu つ te て to と cha ちゃ chu ちゅ cho ちょ che ちぇ tya ちゃ tyu ちゅ tyo ちょ tsa つぁ
da だ di ぢ du づ dzu づ de で do ど
na な ni に nu ぬ ne ね no の nya にゃ nyu にゅ nyo にょ
ha は hi ひ hu ふ fu ふ he へ ho ほ hya ひゃ hyu ひゅ hyo ひょ fa ふぁ fi ふぃ fe ふぇ fo ふぉ
ba ば bi び bu ぶ be べ bo ぼ bya びゃ byu びゅ byo びょ
pa ぱ pi ぴ pu ぷ pe ぺ po ぽ pya ぴゃ pyu ぴゅ pyo ぴょ
ma ま mi み mu む me め mo も mya みゃ myu みゅ myo みょ
ya や yu ゆ yo よ ye いぇ
ra ら ri り ru る re れ ro ろ rya りゃ ryu りゅ ryo りょ
wa わ wo を wi うぃ we うぇ
va ゔぁ vi ゔぃ vu ゔ ve ゔぇ vo ゔぉ
""".split()
SYLLABLES: dict[str, str] = dict(zip(_SYLLABLES[::2], _SYLLABLES[1::2]))
MACRONS = str.maketrans("āīūēōâîûêô", "aiueoaiueo")


def is_kana_char(ch: str) -> bool:
    """Hiragana, katakana, ー and the kana iteration marks (not ・ or the separate ゛゜)."""
    c = ord(ch)
    return 0x3041 <= c <= 0x3096 or 0x309D <= c <= 0x309E or 0x30A1 <= c <= 0x30FA or 0x30FC <= c <= 0x30FE


def has_kana(token: str) -> bool:
    return any(is_kana_char(ch) for ch in token)


def to_hiragana(text: str) -> str:
    """Katakana -> hiragana (ヷヸヹヺ become ゔ + a small vowel); ゝ/ゞ repeat the kana before."""
    out: list[str] = []
    for ch in text:
        c = ord(ch)
        if 0x30A1 <= c <= 0x30F6 or c in (0x30FD, 0x30FE):  # ァ..ヶ, ヽ ヾ
            ch = chr(c - 0x60)
        elif 0x30F7 <= c <= 0x30FA:  # ヷ ヸ ヹ ヺ
            ch = "ゔ" + "ぁぃぇぉ"[c - 0x30F7]
        if ch in "ゝゞ":
            prev = out[-1][-1] if out else ""
            voiced = unicodedata.normalize("NFC", prev + "゙") if ch == "ゞ" and prev else prev
            ch = voiced if len(voiced) <= 1 else prev
        out.append(ch)
    return "".join(out)


@lru_cache(maxsize=1)
def _dictionary():
    from sudachipy import Dictionary

    return Dictionary(dict="core")


_local = threading.local()


def _tokenizer():
    """The dictionary is loaded once; each thread gets its own tokenizer from it
    (the server runs jobs on a worker thread)."""
    tok = getattr(_local, "tokenizer", None)
    if tok is None:
        tok = _local.tokenizer = _dictionary().tokenizer()
    return tok


def reading(run: str) -> str:
    """Hiragana reading of a run of Japanese text; kanji without a reading stay as they are."""
    from sudachipy import SplitMode

    out = []
    for m in _tokenizer().tokenize(run, SplitMode.C):
        surface = m.surface()
        if all(is_kana_char(ch) for ch in surface):
            kana = to_hiragana(surface)  # written in kana: keep the spelling
            if m.part_of_speech()[0] == "助詞" and kana in PARTICLE_SOUND:
                kana = PARTICLE_SOUND[kana]
        elif m.part_of_speech()[0] in SYMBOL_POS:
            continue
        else:
            kana = to_hiragana(m.reading_form())
        out.append(kana)
    return "".join(out)


def split_morae(kana: str) -> list[str]:
    """Morae of a hiragana phrase (see the module notes). Any other character
    (an unread kanji) starts a mora of its own."""
    out: list[str] = []
    lead = ""  # っ at the start of the phrase, waiting for the next mora
    for ch in kana:
        if ch == CHOON:
            continue
        if ch == SOKUON:
            if out:
                out[-1] += ch
            else:
                lead += ch
        elif ch in SMALL and out:
            out[-1] += ch
        else:
            out.append(lead + ch)
            lead = ""
    return out  # a lone っ is not a sung mora


def morae(run: str) -> list[str]:
    """Tokens of one run (phrase) of Japanese text: hiragana morae, and unread kanji."""
    return split_morae(reading(run))


def romaji_to_kana(word: str) -> str | None:
    """Hiragana for one romaji word, or None if it does not parse completely.

    - A long vowel written with a macron (ā) or doubled (aa ii uu ee oo) keeps
      one vowel: the held part is a `-` note, as for ー. `ou` and `ei` stay two
      morae (おう, えい), as their kana spelling reads (omou is おもう).
    - ん: `n` before a consonant or at the end, `n'` before a vowel or y, `m`
      before b/p/m (shimbun). `nn` before a consonant or at the end is ん; before
      a vowel or y it is ん + the な row, as Hepburn writes minna (みんな).
    - A doubled consonant (tt, kk, ssh, tch) is っ.
    """
    w = word.lower().strip("'").translate(MACRONS)
    if not w:
        return None
    w = "".join(ch for k, ch in enumerate(w) if not (ch in VOWELS and k and w[k - 1] == ch))
    out: list[str] = []
    i = 0
    while i < len(w):
        c, nxt = w[i], w[i + 1:i + 2]
        if c == "n" and (not nxt or nxt not in VOWELS + "y"):
            if nxt == "'":
                i += 2
            elif nxt == "n":
                after = w[i + 2:i + 3]
                i += 1 if after and after in VOWELS + "y" else 2
            else:
                i += 1
            out.append("ん")
            continue
        if c == "m" and nxt and nxt in "bpm":
            out.append("ん")
            i += 1
            continue
        if c not in VOWELS + "n'" and (nxt == c or c == "t" and w[i + 1:i + 3] == "ch"):
            out.append(SOKUON)
            i += 1
            continue
        for size in (3, 2, 1):
            kana = SYLLABLES.get(w[i:i + size])
            if kana:
                out.append(kana)
                i += size
                break
        else:
            return None
    return "".join(out)


def romaji(mora: str) -> str:
    """Hepburn letters of one mora. っ adds none (the pause before a doubled
    consonant is left to the aligner's blank); an unread kanji has none."""
    m = mora.replace(SOKUON, "")
    if not m or m[0] not in ROMAJI:
        return ""
    first, base = m[0], ROMAJI[m[0]]
    small = next((ch for ch in m[1:] if ch in SMALL), None)  # more small kana only lengthen the vowel
    if small is None:
        return base
    cons, add = base.rstrip(VOWELS), ROMAJI[small]
    if small in "ゃゅょ":
        return cons + add[1:] if cons in ("sh", "ch", "j") else cons + add
    if small == "ゎ":
        return cons + "wa"
    if first == "う":
        return "w" + add  # うぃ wi, うぇ we, うぉ wo
    if first == "い" and small == "ぇ":
        return "ye"
    if first in "くぐ":
        return cons + "w" + add  # くぁ kwa
    return cons + add  # ふぁ fa, てぃ ti, でぃ di, とぅ tu, しぇ she, ちぇ che, じぇ je, つぁ tsa, ゔぁ va
