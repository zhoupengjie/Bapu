"""Progress events shared by the CLI and (later) the API server.

A stage reports through a `Progress` callable. The CLI prints the events; the
server will forward them as SSE. Messages must never contain lyric text.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Literal

Kind = Literal["start", "update", "done", "skip", "error"]


@dataclass(frozen=True)
class ProgressEvent:
    stage: str
    kind: Kind
    fraction: float | None = None  # 0..1 when known
    message: str = ""
    at: float = field(default_factory=time.time)


Progress = Callable[[ProgressEvent], None]


def null_progress(event: ProgressEvent) -> None:  # noqa: ARG001 - matches Progress
    return None


class StageProgress:
    """Bound helper so a stage does not repeat its own name."""

    def __init__(self, stage: str, sink: Progress) -> None:
        self.stage = stage
        self._sink = sink

    def __call__(self, message: str = "", fraction: float | None = None) -> None:
        self._sink(ProgressEvent(self.stage, "update", fraction, message))


class PrintProgress:
    """Plain line-per-event output for the terminal."""

    def __init__(self, stream=None) -> None:
        self.stream = stream or sys.stderr

    def __call__(self, e: ProgressEvent) -> None:
        pct = f" {e.fraction * 100:3.0f}%" if e.fraction is not None else ""
        label = {"start": "开始", "update": "    ", "done": "完成", "skip": "跳过", "error": "失败"}[e.kind]
        msg = f" {e.message}" if e.message else ""
        print(f"[{e.stage}] {label}{pct}{msg}", file=self.stream, flush=True)
