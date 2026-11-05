"""Sources soundfile cannot read (MP4 and other video / AAC files) are decoded with PyAV."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest
import soundfile as sf

from bapu.audio import decode_to_wav, read_mono, readable

SR = 44100


def make_mp4(path: Path, *, seconds: float = 1.0, audio: bool = True, video: bool = True) -> Path:
    """A small MP4: a 440 Hz stereo tone as AAC, plus a few grey video frames."""
    with av.open(str(path), "w") as out:
        if video:
            vs = out.add_stream("mpeg4", rate=10)
            vs.width, vs.height, vs.pix_fmt = 64, 48, "yuv420p"
        if audio:
            as_ = out.add_stream("aac", rate=SR, layout="stereo")
            t = np.arange(int(SR * seconds)) / SR
            tone = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
            pcm = np.stack([tone, tone])
            for k in range(0, pcm.shape[1], 1024):
                frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(pcm[:, k:k + 1024]), format="fltp", layout="stereo")
                frame.sample_rate, frame.pts, frame.time_base = SR, k, Fraction(1, SR)
                for pkt in as_.encode(frame):
                    out.mux(pkt)
            for pkt in as_.encode(None):
                out.mux(pkt)
        if video:
            for i in range(int(10 * seconds)):
                img = av.VideoFrame.from_ndarray(np.full((48, 64, 3), 128, np.uint8), format="rgb24")
                img.pts = i
                for pkt in vs.encode(img):
                    out.mux(pkt)
            for pkt in vs.encode(None):
                out.mux(pkt)
    return path


def test_wav_is_read_directly(tmp_path):
    p = tmp_path / "a.wav"
    sf.write(str(p), np.zeros((SR, 2), np.float32), SR)
    assert readable(p)


def test_mp4_audio_track_is_decoded(tmp_path):
    src = make_mp4(tmp_path / "song.mp4", seconds=1.0)
    assert not readable(src)
    wav = decode_to_wav(src, tmp_path / "out" / "source.wav")
    info = sf.info(str(wav))
    assert (info.samplerate, info.channels, info.subtype) == (SR, 2, "FLOAT")
    assert abs(info.duration - 1.0) < 0.1  # AAC adds a little padding
    y, _ = read_mono(wav)
    spectrum = np.abs(np.fft.rfft(y))
    assert abs(np.argmax(spectrum) * SR / len(y) - 440) < 5  # still the same tone


def test_video_without_sound_is_rejected(tmp_path):
    src = make_mp4(tmp_path / "silent.mp4", audio=False)
    with pytest.raises(ValueError, match="没有音轨"):
        decode_to_wav(src, tmp_path / "x.wav")


def test_garbage_is_rejected(tmp_path):
    src = tmp_path / "not-audio.mp4"
    src.write_bytes(b"this is not a media file" * 10)
    assert not readable(src)
    with pytest.raises(ValueError):
        decode_to_wav(src, tmp_path / "x.wav")
