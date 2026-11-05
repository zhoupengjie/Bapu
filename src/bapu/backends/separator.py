"""audio-separator backend (BS-RoFormer / Mel-Band RoFormer checkpoints)."""

from __future__ import annotations

import gc
import logging
from pathlib import Path

from bapu.models.registry import ModelSpec, Registry


def run_model(
    registry: Registry,
    spec: ModelSpec,
    src: Path,
    out_dir: Path,
    names: dict[str, str],
    normalization_threshold: float = 1.0,
) -> dict[str, Path]:
    """Run one model on `src`; `names` maps stem name -> output basename.

    `normalization_threshold=1.0` only scales a stem down if it would clip, so
    the stems keep their level relative to each other (the default 0.9 rescales
    each stem separately; see pipeline notes §1).
    """
    from audio_separator.separator import Separator

    registry.ensure_ready(spec)
    # Stem keys differ in case between models; accept every common spelling.
    wanted: dict[str, str] = {}
    for key, value in names.items():
        for variant in {key, key.lower(), key.capitalize(), key.title(), key.upper()}:
            wanted[variant] = value

    out_dir.mkdir(parents=True, exist_ok=True)
    sep = Separator(
        model_file_dir=str(registry.model_dir(spec)),
        output_dir=str(out_dir),
        output_format="WAV",
        normalization_threshold=normalization_threshold,
        log_level=logging.WARNING,
    )
    try:
        sep.load_model(model_filename=spec.entry)
        sep.separate(str(src), wanted)
    finally:
        del sep
        gc.collect()
        try:
            from bapu.device import empty_cache, pick

            empty_cache(pick())
        except ImportError:
            pass

    result = {}
    for stem, base in names.items():
        p = out_dir / f"{base}.wav"
        if not p.exists():
            raise RuntimeError(f"{spec.id}: 没有生成 {stem} 轨（{p.name}）")
        result[stem] = p
    return result
