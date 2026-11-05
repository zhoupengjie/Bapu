"""The models the pipeline needs, downloaded one after another in the background.

One queue for the 环境 page: start (accepting the licenses), pause, resume.
Progress is read from disk (finished files plus partial ones), so a paused or
interrupted download shows how far it got and continues from there. Large
files are fetched in parallel ranges; the number of threads is a setting.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Iterable

from bapu import sources
from bapu.models.registry import DownloadPaused, ModelSpec, Registry


class Downloads:
    def __init__(self, registry: Registry, needed: Iterable[str]) -> None:
        self.registry = registry
        self.needed = list(dict.fromkeys(needed))  # model ids the default pipeline uses, in stage order
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._queue: list[str] = []
        self.state = "idle"  # idle | downloading | paused | failed
        self.current: str | None = None
        self.error: str | None = None
        self._bytes = 0  # written in this session, for the speed
        self._rate = 0.0
        self._last = (time.monotonic(), 0)
        self.modes_of = None  # model id -> the modes that use it (set by the service), for the 环境 page

    # -- control ------------------------------------------------------------------
    def specs(self) -> list[ModelSpec]:
        return [self.registry.models[i] for i in self.needed if i in self.registry.models]

    def missing(self) -> list[str]:
        return [s.id for s in self.specs() if self.registry.status(s) in ("missing", "corrupt")]

    def start(self, ids: list[str] | None = None) -> dict[str, Any]:
        ids = [i for i in (ids or self.missing()) if i in self.registry.models]
        with self._lock:
            for i in ids:
                if i not in self._queue and i != self.current:
                    self._queue.append(i)
            if self._thread is not None and self._thread.is_alive():
                return self.status()
            self._stop.clear()
            self.state, self.error = "downloading", None
            self._thread = threading.Thread(target=self._run, name="bapu-downloads", daemon=True)
            self._thread.start()
        return self.status()

    def pause(self) -> dict[str, Any]:
        with self._lock:
            if self.state == "downloading":
                self._stop.set()
                self.state = "paused"
        return self.status()

    def _run(self) -> None:
        while True:
            with self._lock:
                if self._stop.is_set() or not self._queue:
                    self.current = None
                    if not self._stop.is_set() and self.state == "downloading":
                        self.state = "idle"
                    return
                self.current = self._queue.pop(0)
            spec = self.registry.models[self.current]
            seen: dict[str, int] = {}

            def progress(name: str, done: int, _total: int | None) -> None:
                # per file, cumulative; the first report only sets the baseline (resumed bytes are not speed)
                before = seen.get(name)
                seen[name] = done
                if before is not None and done > before:
                    with self._lock:
                        self._bytes += done - before

            try:
                self.registry.pull(spec, accept_license=True, progress=progress,
                                   threads=sources.threads(), stop=self._stop)
            except DownloadPaused:
                with self._lock:
                    self._queue.insert(0, spec.id)
                    self.current = None
                    self.state = "paused"
                return
            except Exception as e:  # noqa: BLE001 - shown on the page; the rest of the queue waits
                with self._lock:
                    self._queue.insert(0, spec.id)
                    self.current = None
                    self.state, self.error = "failed", f"{spec.name}：{e}"
                return

    # -- what the page shows ------------------------------------------------------------
    def _speed(self) -> float:
        now = time.monotonic()
        with self._lock:
            t0, b0 = self._last
            if now - t0 >= 0.5:
                inst = (self._bytes - b0) / (now - t0)
                self._rate = inst if self._rate == 0 else 0.5 * self._rate + 0.5 * inst
                self._last = (now, self._bytes)
            if self.state != "downloading":
                self._rate = 0.0
            return self._rate

    def status(self) -> dict[str, Any]:
        items = []
        for s in self.specs():
            st = self.registry.status(s)
            total = s.total_size or 0
            done = total if st in ("ready", "builtin") else min(total, self.registry.downloaded(s)) if total else 0
            items.append({"id": s.id, "stage": s.stage, "name": s.name, "status": st, "size": total, "done": done,
                          "license": vars(s.license), "modes": self.modes_of(s.id) if self.modes_of else []})
        return {"state": self.state, "current": self.current, "queue": list(self._queue), "error": self.error,
                "speed": self._speed(), "threads": sources.threads(), "items": items}
