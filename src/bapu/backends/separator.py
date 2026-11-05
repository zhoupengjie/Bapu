"""audio-separator backend (BS-RoFormer / Mel-Band RoFormer checkpoints).

audio-separator 0.48 needs the `ffmpeg` program on PATH twice: its constructor
runs `ffmpeg -version`, and it writes stems through pydub, which calls ffmpeg
for WAV too (it passes `-sample_fmt`). Without ffmpeg both fail with
FileNotFoundError ([WinError 2] on Windows). Bapu never asks it to decode
anything soundfile cannot read (stages/separate.py decodes those with PyAV), so
it skips the check and writes the stems with soundfile, sample for sample the
same as the pydub path.
"""

from __future__ import annotations

import gc
import logging
import os
import types
import warnings
from pathlib import Path

from bapu.models.registry import ModelSpec, Registry

# Input bit depth -> WAV subtype, as audio-separator's pydub writer picks it.
# Writing int16 samples at 24 / 32 bits shifts them left like ffmpeg does.
_SUBTYPES = {16: "PCM_16", 24: "PCM_24", 32: "PCM_32"}


def _separator_class():
    with warnings.catch_warnings():  # pydub warns on import when ffmpeg is missing; we do not use it
        warnings.filterwarnings("ignore", message="Couldn't find ffmpeg", category=RuntimeWarning)
        from audio_separator.separator import Separator

    class NoFFmpegSeparator(Separator):
        def check_ffmpeg_installed(self):
            pass

        def load_model(self, *args, **kwargs):
            super().load_model(*args, **kwargs)
            self.model_instance.write_audio = types.MethodType(write_stem, self.model_instance)

    return NoFFmpegSeparator


def write_stem(self, stem_path: str, stem_source) -> None:
    """`CommonSeparator.write_audio` for WAV output, without ffmpeg.

    Same samples as audio-separator's pydub writer: normalise, truncate to
    16-bit, then write at the input's bit depth (16, 24 or 32).
    """
    import numpy as np
    import soundfile as sf
    from audio_separator.separator.audio_io import atomic_output_path, validate_audio_source
    from audio_separator.separator.uvr_lib_v5 import spec_utils

    source = validate_audio_source(stem_source)
    source = spec_utils.normalize(wave=source, max_peak=self.normalization_threshold,
                                  min_peak=self.amplification_threshold)
    if source.dtype != np.int16:
        source = (source * 32767).astype(np.int16)
    path = os.path.join(self.output_dir, stem_path) if self.output_dir else stem_path
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with atomic_output_path(path, "soundfile") as tmp:
        sf.write(tmp, source, self.sample_rate, format="WAV",
                 subtype=_SUBTYPES.get(self.input_bit_depth or 16, "PCM_16"))


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
    Separator = _separator_class()

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
