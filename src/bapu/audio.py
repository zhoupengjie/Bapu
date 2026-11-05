"""Audio I/O. torchaudio 2.11 `load()` needs torchcodec, so read with soundfile + soxr.

Formats soundfile cannot open (MP4 / M4A / AAC, video files, ...) are decoded
once with PyAV, which ships its own FFmpeg, into a float WAV.
"""

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


def readable(path: Path) -> bool:
    """Can soundfile (libsndfile) read this file directly? WAV, FLAC, OGG, MP3 can."""
    try:
        sf.info(str(path))
        return True
    except (sf.LibsndfileError, RuntimeError):
        return False


def decode_to_wav(src: Path, dest: Path) -> Path:
    """Decode the first audio track of `src` (any FFmpeg format, video included)
    to a 32-bit float WAV at its own sample rate; mono stays mono, more than two
    channels are downmixed to stereo."""
    import av

    try:
        container = av.open(str(src))
    except av.FFmpegError as e:
        raise ValueError(f"读不了这个文件：{Path(src).name}（{e}）") from None
    with container:
        streams = container.streams.audio
        if not streams:
            raise ValueError(f"这个文件里没有音轨：{Path(src).name}")
        stream = streams[0]
        layout = "mono" if stream.channels == 1 else "stereo"
        resampler = av.AudioResampler(format="fltp", layout=layout, rate=stream.rate)
        chunks: list[np.ndarray] = []
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray())
        for out in resampler.resample(None):  # flush
            chunks.append(out.to_ndarray())
        rate = stream.rate
    if not chunks:
        raise ValueError(f"音轨是空的：{Path(src).name}")
    data = np.concatenate(chunks, axis=1).T  # (samples, channels)
    dest.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(dest), data, rate, subtype="FLOAT")
    return dest
