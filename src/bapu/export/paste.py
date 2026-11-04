"""Fallback: a paste file for SV's "paste lyrics onto selected notes".

One subtitle line per text line, one space-separated token per note (`-` and
`+` included), UTF-8 with BOM and CRLF so Windows editors keep it intact.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def write(score: dict[str, Any], path: Path) -> Path:
    rows: dict[int, list[str]] = {}
    for n in score["notes"]:
        rows.setdefault(n["line"], []).append(n["lyric"])
    text = "\n".join(" ".join(rows[k]) for k in sorted(rows)) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(text)
    return path
