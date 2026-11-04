"""User corrections: `<work dir>/corrections.yaml`.

Only the user edits this file (by hand, or by running a command such as
`bapu corrections from-svp`, which appends). Pipeline runs never rewrite it.

The file is a YAML list. Every entry has a `type`; notes are found by time
(onset in seconds, `tolerance` 30 ms by default), not by index, so entries
still apply after an upstream rerun renumbers the notes. An entry that matches
nothing is reported as stale in the review report, never dropped.

    - type: merge          # boundary between two touching notes
      at: 44.04            # = onset of the second note
      action: confirm      # confirm | reject
    - type: kind           # force a note's role
      at: 76.70
      kind: sustain        # word | sustain ("-") | cont ("+")
    - type: line_version   # use this exact version of a line (locks it)
      line: 2
      notes:               # one entry per note, in order; lyric is "-", "+" or a word
        - {at: 5.12, lyric: "..."}
    - type: pitch
      at: 12.34
      pitch: 64
    - type: tempo
      bpm: 125
    - type: line_lang
      line: 7
      lang: en
    - type: line_text      # subtitle typo fix (contains lyric text; stays local)
      line: 9
      text: "..."

Line numbers are 1-based subtitle lines.
"""

from __future__ import annotations

import bisect
import copy
from pathlib import Path
from typing import Any, Iterable

import yaml

from bapu.workdir import cache_key

TYPES: dict[str, tuple[str, ...]] = {
    "merge": ("at", "action"),
    "kind": ("at", "kind"),
    "line_version": ("line", "notes"),
    "pitch": ("at", "pitch"),
    "tempo": ("bpm",),
    "line_lang": ("line", "lang"),
    "line_text": ("line", "text"),
}
CHOICES = {"action": {"confirm", "reject"}, "kind": {"word", "sustain", "cont"}}

HEADER = """\
# Bapu corrections. Only you edit this file; reruns never overwrite it.
# Notes are matched by time (seconds), so entries survive renumbering.
# See `src/bapu/corrections.py` for every entry type.
"""


class CorrectionsError(ValueError):
    pass


def load(path: Path) -> list[dict[str, Any]]:
    """Entries with a 1-based `_id` (their position in the file)."""
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if not isinstance(data, list):
        raise CorrectionsError(f"{path.name}: 顶层应该是列表")
    out = []
    for i, entry in enumerate(data, 1):
        if not isinstance(entry, dict) or entry.get("type") not in TYPES:
            raise CorrectionsError(f"{path.name} 第 {i} 条：type 应该是 {', '.join(TYPES)} 之一")
        missing = [k for k in TYPES[entry["type"]] if k not in entry]
        if missing:
            raise CorrectionsError(f"{path.name} 第 {i} 条（{entry['type']}）缺少 {', '.join(missing)}")
        for k, allowed in CHOICES.items():
            if k in entry and entry["type"] in ("merge", "kind") and entry[k] not in allowed:
                raise CorrectionsError(f"{path.name} 第 {i} 条：{k} 应该是 {', '.join(sorted(allowed))} 之一")
        e = copy.deepcopy(entry)
        e["_id"] = i
        out.append(e)
    return out


def select(entries: Iterable[dict[str, Any]], types: Iterable[str]) -> list[dict[str, Any]]:
    wanted = set(types)
    return [e for e in entries if e["type"] in wanted]


def fingerprint(entries: list[dict[str, Any]]) -> str:
    """Cache-key part for a stage: changes only when its own entries change."""
    return cache_key({"entries": [{k: v for k, v in e.items() if k != "_id"} for e in entries]})


def match_time(times: list[float], t: float, tolerance: float) -> int | None:
    """Index of the time in sorted `times` nearest to `t`, if within tolerance."""
    if not times:
        return None
    k = bisect.bisect_left(times, t)
    best = None
    for j in (k - 1, k):
        if 0 <= j < len(times) and (best is None or abs(times[j] - t) < abs(times[best] - t)):
            best = j
    return best if best is not None and abs(times[best] - t) <= tolerance else None


def append(path: Path, entries: list[dict[str, Any]], comment: str = "") -> None:
    """Append entries as text, so the user's comments and layout stay intact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else HEADER
    if existing and not existing.endswith("\n"):
        existing += "\n"
    block = yaml.safe_dump([{k: v for k, v in e.items() if k != "_id"} for e in entries],
                           allow_unicode=True, sort_keys=False, default_flow_style=None)
    note = f"\n# {comment}\n" if comment else "\n"
    path.write_text(existing + note + block, encoding="utf-8")
