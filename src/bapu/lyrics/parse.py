"""Read the user's lyric file (.ass / .lrc / .txt) into timed lines.

Lyrics come only from files the user supplies. Nothing here logs lyric text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

CJK = "㐀-䶿一-鿿豈-﫿"
TOKEN = re.compile(r"[A-Za-z']+|[" + CJK + "]")


@dataclass
class Line:
    text: str
    start: float | None = None
    end: float | None = None
    tokens: list[str] = field(default_factory=list)


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


def tokenize(text: str) -> list[str]:
    """CJK characters one by one; Latin words (with apostrophes) whole.
    Digits and punctuation are dropped."""
    return [t for t in TOKEN.findall(text) if t.strip("'")]


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


def parse_file(path: Path) -> list[Line]:
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
        line.tokens = tokenize(line.text)
    return [ln for ln in lines if ln.tokens]
