"""Full-song regression against the numbers in docs/pipeline-notes.md.

Needs the validation recording (kept outside the repo) and the GPU models:

    BAPU_REGRESSION_AUDIO="D:/.../My Jealousy.wav" uv run pytest -m regression

Skipped when the variable is not set.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bapu import pipeline
from bapu.models.registry import Registry
from bapu.workdir import WorkDir

AUDIO = os.environ.get("BAPU_REGRESSION_AUDIO")

pytestmark = [
    pytest.mark.regression,
    pytest.mark.skipif(not AUDIO or not Path(AUDIO).exists(), reason="BAPU_REGRESSION_AUDIO not set"),
]


@pytest.fixture(scope="module")
def m1(tmp_path_factory):
    out = Path(os.environ.get("BAPU_REGRESSION_WORK") or tmp_path_factory.mktemp("m1"))
    wd = WorkDir(out)
    pipeline.run(wd, Path(AUDIO), registry=Registry.load(), until="transcribe")
    return wd


def test_tempo(m1):
    info = json.loads((m1.root / "03_tempo" / "tempo.json").read_text(encoding="utf-8"))
    assert info["bpm"] == 125.0  # not 123: see pipeline notes §3
    assert abs(info["first_beat"] - 1.463) < 0.02


def test_transcription(m1):
    rec = m1.get("transcribe")
    assert rec.metrics["notes"] == 353
    assert rec.metrics["overlaps"] == 0
    assert rec.metrics["chunks"] == 16
