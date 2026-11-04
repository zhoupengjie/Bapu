"""The contract every stage implements.

A stage reads named artifacts from earlier stages, writes files into its own
folder and returns which artifact names map to which files, plus a few numbers
for `bapu status` and the review report (never lyric text).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from bapu.models.registry import ModelSpec, Registry
from bapu.progress import StageProgress


@dataclass
class StageContext:
    inputs: dict[str, Path]
    out_dir: Path
    params: dict[str, Any]
    models: dict[str, ModelSpec | None]  # model param name -> spec (None = disabled)
    registry: Registry
    progress: StageProgress
    corrections: list[dict[str, Any]] = field(default_factory=list)  # entries of this stage's types


@dataclass
class StageResult:
    outputs: dict[str, Path]
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Stage:
    name: str
    index: int
    version: str  # bump when the code changes its output
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    defaults: dict[str, Any]
    run: Callable[[StageContext], StageResult]
    # parameter name -> registry stage tag the chosen model must carry
    model_params: dict[str, str] = field(default_factory=dict)
    title: str = ""
    # correction entry types this stage applies; they are part of its cache key
    corrections: tuple[str, ...] = ()
