"""Fuse dictation (what is sung, how many syllables) with a reference lyric file
(which characters).

Both token streams are reduced to sounds (toneless Jyutping for Chinese
characters, lower-case letters for English words) and aligned globally. Between
two matching sounds lies a gap with reference tokens R and dictated tokens D:

- R empty, D Chinese only (at most `max_insert`): the reference probably
  missed words. D is inserted as optional tokens; the aligner keeps them only
  if the recording supports them.
- D empty: keep R (dictation often drops syllables).
- English on either side: keep R (dictation of sung English is poor).
- Same length, Chinese only: each R token gets its dictated token as an
  alternative; the aligner picks whichever the recording supports better.
  Near-homophones (same final, or same initial) pair up directly as such
  alternatives, so they never merge into a gap and hide an insertion.
- Otherwise: keep R and report the gap.

Lines whose text the user corrected (`line_text`) are taken as they are.
Without a reference, dictation is used as it is, one line per phrase.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

from bapu.lyrics import g2p

SUB_SAME_SCRIPT = 1.0
SUB_MIXED = 1.5
INDEL = 1.0
SUB_EPS = 0.01
_GROUPS = itertools.count(1)


@dataclass
class Tok:
    text: str
    key: str  # sound key; also the aligner's letters
    line: int
    time: float | None = None
    source: str = "ref"  # ref | dictation | both | user
    optional: bool = False
    alt: "Tok | None" = None
    group: int | None = None  # words inserted together are kept or dropped together
    flags: list[str] = field(default_factory=list)

    @property
    def latin(self) -> bool:
        return g2p.is_latin(self.text)


def keys_for(tokens: list[str]) -> list[str]:
    cjk = [t for t in tokens if not g2p.is_latin(t)]
    jy = iter(g2p.jyutping_letters(cjk))
    return [g2p.english_letters(t) if g2p.is_latin(t) else next(jy) for t in tokens]


def dictation_tokens(phrases: list[dict[str, Any]], tokenize) -> list[Tok]:
    out = []
    for p_no, p in enumerate(phrases, 1):
        toks = tokenize(p["text"])
        for k, (t, key) in enumerate(zip(toks, keys_for(toks))):
            when = p["t0"] + (k + 0.5) / max(len(toks), 1) * (p["t1"] - p["t0"])
            out.append(Tok(t, key, p_no, round(when, 3), source="dictation"))
    return out


INITIALS = ("gw", "kw", "ng", "b", "p", "m", "f", "d", "t", "n", "l", "g", "k", "h", "z", "c", "s", "j", "w")
CLOSE = 0.75  # substitutions cheaper than this are near-homophones


def split_jyutping(key: str) -> tuple[str, str]:
    for ini in INITIALS:
        if key.startswith(ini) and len(key) > len(ini):
            return ini, key[len(ini):]
    return "", key


def sub_cost(x: str, y: str, x_latin: bool, y_latin: bool) -> float:
    """0 for the same sound; near-homophones (same final, or same initial) cost
    less than an insertion so they pair up instead of opening a gap."""
    if x == y:
        return 0.0
    # SUB_EPS breaks ties toward paths with more exact matches.
    if x_latin != y_latin:
        return SUB_MIXED + SUB_EPS
    if x_latin:
        return SUB_SAME_SCRIPT + SUB_EPS
    (xi, xf), (yi, yf) = split_jyutping(x), split_jyutping(y)
    if xf == yf:
        return 0.4
    if xi and xi == yi:
        return 0.7
    return SUB_SAME_SCRIPT + SUB_EPS


def align(a: list[str], b: list[str], a_latin: list[bool], b_latin: list[bool], anchored: bool = False
          ) -> list[tuple[int | None, int | None]]:
    """Global alignment; pairs of indices (None = gap). With `anchored`, only
    exact sounds and near-homophones may pair; anything else becomes an
    insertion plus a deletion."""
    far = 2 * INDEL + 1

    def cost(i: int, j: int) -> float:
        c = sub_cost(a[i], b[j], a_latin[i], b_latin[j])
        return far if anchored and c >= CLOSE else c

    n, m = len(a), len(b)
    D = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        D[i][0] = i * INDEL
    for j in range(1, m + 1):
        D[0][j] = j * INDEL
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            D[i][j] = min(D[i - 1][j - 1] + cost(i - 1, j - 1), D[i - 1][j] + INDEL, D[i][j - 1] + INDEL)
    pairs: list[tuple[int | None, int | None]] = []
    i, j = n, m
    while i or j:
        if i and j and abs(D[i][j] - (D[i - 1][j - 1] + cost(i - 1, j - 1))) < 1e-9:
            pairs.append((i - 1, j - 1)); i, j = i - 1, j - 1
        elif i and abs(D[i][j] - (D[i - 1][j] + INDEL)) < 1e-9:
            pairs.append((i - 1, None)); i -= 1
        else:
            pairs.append((None, j - 1)); j -= 1
    return pairs[::-1]


def prefer_exact(pairs: list[tuple[int | None, int | None]], ref: list["Tok"], dic: list["Tok"]
                 ) -> list[tuple[int | None, int | None]]:
    """Re-pair (r, d) near-homophones when the next dictated token is r's exact
    sound: d is then an extra word before r. ("A B C" dictated against "A C",
    where B sounds like C, must insert B rather than pair B with C.)"""
    out = list(pairs)
    for k, (i, j) in enumerate(out):
        if i is None or j is None or ref[i].key == dic[j].key or j + 1 >= len(dic):
            continue
        if dic[j + 1].key != ref[i].key:
            continue
        nxt = next((m for m in range(k + 1, len(out)) if out[m][1] == j + 1), None)
        if nxt is None:
            continue
        other = out[nxt][0]
        if other is not None and ref[other].key == dic[j + 1].key:
            continue  # j+1 already has an exact partner
        out[k] = (None, j)
        out[nxt] = (other, None) if other is not None else (None, None)
        out.insert(k + 1, (i, j + 1))
    return [p for p in out if p != (None, None)]


def fuse(ref: list[Tok], dic: list[Tok], *, locked_lines: set[int] = frozenset(), max_insert: int = 4,
         anchored: bool = False) -> tuple[list[Tok], list[dict[str, Any]]]:
    """Fused token stream and events for the review report (no lyric text).

    A gap of Chinese tokens is settled again with `anchored=True`: exact and
    near-homophone pairs inside it become anchors, and each stretch between
    anchors is an insertion, a deletion, or (same length) a substitution.
    """
    pairs = prefer_exact(align([t.key for t in ref], [t.key for t in dic], [t.latin for t in ref],
                               [t.latin for t in dic], anchored=anchored), ref, dic)
    out: list[Tok] = []
    events: list[dict[str, Any]] = []
    gap_r: list[Tok] = []
    gap_d: list[Tok] = []

    def flush() -> None:
        nonlocal gap_r, gap_d
        R, Dg = gap_r, gap_d
        gap_r, gap_d = [], []
        if not R and not Dg:
            return
        prev_line = out[-1].line if out else (ref[0].line if ref else 1)
        line = R[0].line if R else prev_line
        when = Dg[0].time if Dg else None
        if not R:
            if line in locked_lines:
                events.append({"type": "extra_ignored", "line": line, "time": when, "count": len(Dg), "reason": "locked"})
            elif all(not t.latin for t in Dg) and len(Dg) <= max_insert:
                gid = next(_GROUPS)
                for t in Dg:
                    out.append(Tok(t.text, t.key, line, t.time, source="dictation", optional=True, flags=["added"],
                                   group=gid))
                events.append({"type": "insert", "line": line, "time": when, "count": len(Dg)})
            else:
                events.append({"type": "extra_ignored", "line": line, "time": when, "count": len(Dg),
                               "reason": "english" if any(t.latin for t in Dg) else "too_long"})
            return
        if Dg and not anchored and not any(t.latin for t in R) and not any(t.line in locked_lines for t in R):
            # A Chinese passage: drop stray English from the dictation and
            # settle the gap anchored on exact and near-homophone matches.
            sub_out, sub_events = fuse(R, [t for t in Dg if not t.latin], locked_lines=locked_lines,
                                       max_insert=max_insert, anchored=True)
            out.extend(sub_out)
            events.extend(sub_events)
            return
        out.extend(R)
        if not Dg:
            return
        if any(t.line in locked_lines for t in R):
            return
        if any(t.latin for t in R) or any(t.latin for t in Dg):
            events.append({"type": "differs", "line": line, "time": when, "ref": len(R), "dictation": len(Dg),
                           "reason": "english"})
        elif len(R) == len(Dg):
            for r, d in zip(R, Dg):
                r.alt = Tok(d.text, d.key, r.line, d.time, source="dictation")
            events.append({"type": "alternatives", "line": line, "time": when, "count": len(R)})
        else:
            events.append({"type": "differs", "line": line, "time": when, "ref": len(R), "dictation": len(Dg),
                           "reason": "length"})

    for i, j in pairs:
        cost = sub_cost(ref[i].key, dic[j].key, ref[i].latin, dic[j].latin) if i is not None and j is not None else None
        if cost is not None and cost < CLOSE:
            flush()
            t = ref[i]
            if cost == 0.0:
                if t.source == "ref":
                    t.source = "both"
            elif not t.latin and t.line not in locked_lines:
                t.alt = Tok(dic[j].text, dic[j].key, t.line, dic[j].time, source="dictation")
                events.append({"type": "alternatives", "line": t.line, "time": dic[j].time, "count": 1})
            out.append(t)
        else:
            if i is not None:
                gap_r.append(ref[i])
            if j is not None:
                gap_d.append(dic[j])
    flush()
    return out, events
