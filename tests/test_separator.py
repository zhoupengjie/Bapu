"""audio-separator backend without the ffmpeg program (no weights needed)."""

from __future__ import annotations

import logging
import shutil
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from bapu.backends.separator import _separator_class, write_stem


def _no_ffmpeg(monkeypatch):
    """What a machine without ffmpeg on PATH looks like to audio-separator."""
    def check_output(cmd, *a, **kw):
        if cmd and cmd[0] == "ffmpeg":
            raise FileNotFoundError(2, "The system cannot find the file specified")
        return real(cmd, *a, **kw)

    real = subprocess.check_output
    monkeypatch.setattr(subprocess, "check_output", check_output)
    # audio-separator only raises when this is unset, which hid the bug from tests.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)


def test_separator_starts_without_ffmpeg(monkeypatch, tmp_path):
    from audio_separator.separator import Separator

    _no_ffmpeg(monkeypatch)
    kw = dict(output_dir=str(tmp_path), output_format="WAV", log_level=logging.WARNING)
    with pytest.raises(FileNotFoundError):
        Separator(**kw)
    _separator_class()(**kw)


def _model(tmp_path, bit_depth):
    return SimpleNamespace(
        output_dir=str(tmp_path), sample_rate=44100, normalization_threshold=0.9, amplification_threshold=0.0,
        input_bit_depth=bit_depth, output_bitrate=None, logger=logging.getLogger("test"),
    )


def _stem(channels):
    rng = np.random.default_rng(0)
    x = rng.uniform(-1.2, 1.2, size=(4410, channels)).astype(np.float32)
    return x[:, 0] if channels == 1 else x


@pytest.mark.parametrize("bit_depth,subtype", [(16, "PCM_16"), (24, "PCM_24"), (32, "PCM_32"), (None, "PCM_16")])
def test_write_stem_without_ffmpeg(monkeypatch, tmp_path, bit_depth, subtype):
    monkeypatch.setenv("PATH", str(tmp_path))
    stem = _stem(2)
    write_stem(_model(tmp_path, bit_depth), "vocals.wav", stem.copy())  # normalises in place

    info = sf.info(tmp_path / "vocals.wav")
    assert (info.samplerate, info.channels, info.frames, info.subtype) == (44100, 2, 4410, subtype)
    got, _ = sf.read(tmp_path / "vocals.wav", dtype="int16")
    stem *= 0.9 / np.abs(stem).max()  # the peak (about 1.2) comes down to the 0.9 threshold
    assert np.array_equal(got, (stem * 32767).astype(np.int16))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg to compare with audio-separator's writer")
@pytest.mark.parametrize("bit_depth", [16, 24, 32])
@pytest.mark.parametrize("channels", [1, 2])
def test_write_stem_matches_pydub_writer(tmp_path, bit_depth, channels):
    from audio_separator.separator.common_separator import CommonSeparator

    stem = _stem(channels)
    model = _model(tmp_path, bit_depth)
    CommonSeparator.write_audio_pydub(model, "pydub.wav", stem.copy())
    write_stem(model, "ours.wav", stem.copy())

    a, sr_a = sf.read(tmp_path / "pydub.wav", dtype="int32")
    b, sr_b = sf.read(tmp_path / "ours.wav", dtype="int32")
    assert sr_a == sr_b and sf.info(tmp_path / "pydub.wav").subtype == sf.info(tmp_path / "ours.wav").subtype
    assert np.array_equal(a, b)
