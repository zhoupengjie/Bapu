"""Updates for the portable app.

A release on GitHub (zhoupengjie/Bapu) carries `Bapu-app-<version>.zip`
(Bapu.exe, app/, tools/) and `latest.json`:

    {"version": "0.3.0", "date": "...", "notes": "...",
     "zip": {"name": "...", "url": "...", "size": 123, "sha256": "..."},
     "signature": "<Ed25519 over 'bapu-update:<version>:<sha256>'>"}

Here Bapu only finds and downloads it (through the chosen download source)
and checks the SHA-256; the desktop shell checks the signature with the
built-in public key and swaps the files when Bapu closes (desktop/src-tauri
/src/updater.rs). `runtime/`, `models/` and `data/` are never touched.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from bapu import __version__, paths
from bapu.sources import mirror_urls
from bapu.workdir import write_json_atomic

LATEST = "https://github.com/zhoupengjie/Bapu/releases/latest/download/latest.json"


def parse_version(v: str) -> tuple[int, ...]:
    out = []
    for part in v.strip().lstrip("v").split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out)


def _get(urls: list[str], timeout: float = 15) -> bytes:
    errors = []
    for url in urls:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": f"bapu/{__version__}"}),
                                        timeout=timeout) as r:
                return r.read()
        except (urllib.error.URLError, OSError) as e:
            errors.append(f"{url}: {e}")
    raise OSError("；".join(errors) or "没有地址")


# Bapu.exe swaps app/ on exit; the Mac app is replaced from the release page
SELF_UPDATE = sys.platform == "win32"


class Updater:
    def __init__(self) -> None:
        self._lock = threading.RLock()  # status() is also called while the lock is held
        self.state = "idle"  # idle | checking | latest | available | downloading | ready | error
        self.latest: dict[str, Any] | None = None
        self.error: str | None = None
        self.done = 0
        self.total: int | None = None
        self.checked_at: float | None = None

    @property
    def folder(self) -> Path:
        return paths.data_dir() / "update"

    def can_install(self) -> bool:
        """Only the Windows portable app (with its desktop shell) can swap its own files;
        on macOS a new version is announced and downloaded from the release page."""
        return paths.home() is not None and SELF_UPDATE

    def status(self) -> dict[str, Any]:
        last = None
        p = self.folder / "last.json"
        try:
            last = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        except ValueError:
            pass
        if last and time.time() - float(last.get("at", 0)) > 3 * 86400:
            last = None  # only the latest update's result, for a few days
        with self._lock:
            return {"current": __version__, "state": self.state, "latest": self.latest, "error": self.error,
                    "done": self.done, "total": self.total, "checked_at": self.checked_at,
                    "can_install": self.can_install(), "staged": (self.folder / "staged.json").exists(), "last": last}

    def check(self) -> dict[str, Any]:
        with self._lock:
            if self.state in ("checking", "downloading"):
                return self.status()
            self.state, self.error = "checking", None
        try:
            url = os.environ.get("BAPU_UPDATE_URL", LATEST)
            latest = json.loads(_get(mirror_urls([url])).decode("utf-8"))
            newer = parse_version(latest["version"]) > parse_version(__version__)
            with self._lock:
                self.latest, self.checked_at = latest, time.time()
                self.state = "available" if newer else "latest"
                if newer and (self.folder / "staged.json").exists():
                    staged = json.loads((self.folder / "staged.json").read_text(encoding="utf-8"))
                    if staged.get("version") == latest["version"]:
                        self.state = "ready"
        except (OSError, ValueError, KeyError) as e:
            with self._lock:
                if "404" in str(e):  # no release published yet
                    self.state, self.latest, self.checked_at = "latest", None, time.time()
                else:
                    self.state, self.error = "error", f"检查更新失败：{e}"
        return self.status()

    def download(self) -> None:
        with self._lock:
            if self.state != "available" or not self.latest:
                raise ValueError("没有可以下载的新版本")
            if not self.can_install():
                raise ValueError("开发版请用 git pull 更新")
            self.state, self.error, self.done = "downloading", None, 0
            self.total = self.latest["zip"].get("size")
        threading.Thread(target=self._download, name="bapu-update", daemon=True).start()

    def _download(self) -> None:
        latest = self.latest or {}
        z = latest.get("zip", {})
        dest = self.folder / z.get("name", "update.zip")
        part = dest.with_name(dest.name + ".part")
        self.folder.mkdir(parents=True, exist_ok=True)
        errors = []
        for url in mirror_urls([z.get("url", "")]):
            try:
                h = hashlib.sha256()
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": f"bapu/{__version__}"}),
                                            timeout=30) as r, open(part, "wb") as f:
                    self.done = 0
                    while block := r.read(1 << 20):
                        f.write(block)
                        h.update(block)
                        self.done += len(block)
                if h.hexdigest() != z.get("sha256"):
                    errors.append(f"{url}: SHA-256 不对")
                    continue
                os.replace(part, dest)
                write_json_atomic(self.folder / "staged.json", {
                    "version": latest["version"], "zip": dest.name, "sha256": z["sha256"], "signature": latest["signature"]})
                with self._lock:
                    self.state = "ready"
                return
            except (urllib.error.URLError, OSError) as e:
                errors.append(f"{url}: {e}")
        part.unlink(missing_ok=True)
        with self._lock:
            self.state, self.error = "error", "下载更新失败：" + "；".join(errors)
