"""Audio I/O. torchaudio 2.11 `load()` needs torchcodec, so read with soundfile + soxr."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr


def read_mono(path: Path, sr: int | None = None, quality: str = "HQ") -> tuple[np.ndarray, int]:
    """Read a file as float32 mono, optionally resampled to `sr`."""
    data, file_sr = sf.read(str(path), dtype="float32", always_2d=True)
    y = data.mean(axis=1)
    if sr is not None and sr != file_sr:
        y = soxr.resample(y, file_sr, sr, quality=quality).astype(np.float32)
        file_sr = sr
    return y, file_sr


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()
