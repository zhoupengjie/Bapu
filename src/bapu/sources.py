"""Download source: official servers or China mirrors (see mirrors.json).

The choice is stored in `<data>/settings.json` ("auto", "official" or "china",
plus what "auto" measured). The first-launch setup writes it; the 环境 page can
change it; model downloads and updates follow it. A mirror only changes where
a file is fetched from: every file is still checked against its SHA-256, and
the original address is always tried as a fallback.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from importlib.resources import files as pkg_files
from typing import Any

from bapu import paths
from bapu.workdir import write_json_atomic

CHOICES = ("auto", "official", "china")


def table() -> dict[str, Any]:
    return json.loads(pkg_files("bapu").joinpath("mirrors.json").read_text(encoding="utf-8"))


def _settings_path():
    return paths.data_dir() / "settings.json"


def settings() -> dict[str, Any]:
    p = _settings_path()
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except ValueError:
        return {}


def save(**changes: Any) -> dict[str, Any]:
    s = {**settings(), **changes}
    write_json_atomic(_settings_path(), s)
    return s


def choice() -> str:
    c = settings().get("source", "auto")
    return c if c in CHOICES else "auto"


def resolved() -> str:
    """official or china: the choice, or what "auto" last measured (official until measured)."""
    c = choice()
    if c != "auto":
        return c
    r = settings().get("resolved")
    return r if r in ("official", "china") else "official"


DEFAULT_THREADS = 8
MAX_THREADS = 16


def threads() -> int:
    """Parallel connections per download (the 环境 page's 下载线程; uv uses it too)."""
    try:
        n = int(settings().get("threads", DEFAULT_THREADS))
    except (TypeError, ValueError):
        n = DEFAULT_THREADS
    return max(1, min(MAX_THREADS, n))


def mirror_urls(urls: list[str] | tuple[str, ...], source: str | None = None) -> list[str]:
    """Download addresses to try, in order: mirrored ones first (china), then the originals."""
    source = source or resolved()
    rules = table()[source].get("rewrite", []) if source in ("official", "china") else []
    out: list[str] = []
    for u in urls:
        for a, b in rules:
            if u.startswith(a):
                out.append(b + u[len(a):])
                break
    for u in urls:
        if u not in out:
            out.append(u)
    return out


def _speed(url: str, nbytes: int = 512 * 1024, timeout: float = 8.0) -> float | None:
    """Bytes per second for the first `nbytes` of `url` (None: unreachable)."""
    req = urllib.request.Request(url, headers={"Range": f"bytes=0-{nbytes - 1}", "User-Agent": "bapu"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            got = len(r.read(nbytes))
    except OSError:
        return None
    dt = max(time.time() - t, 1e-3)
    return got / dt if got else None


def measure() -> dict[str, Any]:
    """Time the official servers and the mirrors; remember which is faster."""
    t = table()
    probe = t.get("probe_macos", t["probe"]) if sys.platform == "darwin" else t["probe"]
    speeds = {k: _speed(probe[k]) for k in ("official", "china")}
    o, c = speeds["official"], speeds["china"]
    best = "china" if (c and (not o or c > o * 1.2)) else "official"
    save(resolved=best, measured={k: v for k, v in speeds.items()}, measured_at=time.time())
    return {"speeds": speeds, "best": best}


def describe() -> dict[str, Any]:
    t = table()
    s = settings()
    return {
        "choice": choice(), "resolved": resolved(),
        "options": {k: {"label": t[k]["label"], "detail": t[k]["detail"]} for k in ("official", "china")},
        "measured": s.get("measured"), "measured_at": s.get("measured_at"),
        "threads": threads(),
    }
