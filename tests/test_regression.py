"""Full-song regression against the numbers in docs/pipeline-notes.md.

Needs the validation recording and its subtitle file (both kept outside the
repo) and the GPU models:

    BAPU_REGRESSION_AUDIO="D:/.../My Jealousy.wav" \
    BAPU_REGRESSION_LYRICS="D:/.../....ass" uv run pytest -m regression

Skipped when the variables are not set. BAPU_REGRESSION_WORK reuses a work dir.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bapu import pipeline, report
from bapu.models.registry import Registry
from bapu.workdir import WorkDir

AUDIO = os.environ.get("BAPU_REGRESSION_AUDIO")
LYRICS = os.environ.get("BAPU_REGRESSION_LYRICS")

pytestmark = [
    pytest.mark.regression,
    pytest.mark.skipif(not AUDIO or not Path(AUDIO).exists(), reason="BAPU_REGRESSION_AUDIO not set"),
]

# The user's decisions on the 10 auto-merged boundaries of the validation song
# (times only): 8 merged by ear, 2 kept apart (notes §5).
MERGES = """\
- {type: merge, at: 7.7, action: confirm}
- {type: merge, at: 44.04, action: confirm}
- {type: merge, at: 50.58, action: confirm}
- {type: merge, at: 61.82, action: confirm}
- {type: merge, at: 69.16, action: confirm}
- {type: merge, at: 81.61, action: confirm}
- {type: merge, at: 117.05, action: confirm}
- {type: merge, at: 146.0, action: confirm}
- {type: merge, at: 6.75, action: reject}
- {type: merge, at: 14.52, action: reject}
"""


@pytest.fixture(scope="module")
def wd(tmp_path_factory):
    out = Path(os.environ.get("BAPU_REGRESSION_WORK") or tmp_path_factory.mktemp("song"))
    w = WorkDir(out)
    if not w.corrections_path.exists():
        out.mkdir(parents=True, exist_ok=True)
        w.corrections_path.write_text(MERGES, encoding="utf-8")
    sources = {"source": Path(AUDIO)}
    if LYRICS and Path(LYRICS).exists():
        sources["lyrics"] = Path(LYRICS)
    pipeline.run(w, sources, registry=Registry.load())
    report.finalize(w)
    return w


def test_tempo(wd):
    info = json.loads((wd.root / "03_tempo" / "tempo.json").read_text(encoding="utf-8"))
    assert info["bpm"] == 125.0  # not 123: see pipeline notes §3
    assert abs(info["first_beat"] - 1.463) < 0.02


def test_transcription(wd):
    rec = wd.get("transcribe")
    assert rec.metrics["notes"] == 353
    assert rec.metrics["overlaps"] == 0
    assert rec.metrics["chunks"] == 16


def test_merges(wd):
    rec = wd.get("splits")
    assert rec.metrics["notes"] == 345
    assert rec.metrics["user_decided"] == 10


@pytest.mark.skipif(not LYRICS, reason="BAPU_REGRESSION_LYRICS not set")
def test_lyrics_and_assignment(wd):
    assert wd.get("lyrics").metrics["lines"] == 45
    assert wd.get("lyrics").metrics["syllables"] == 286
    m = wd.get("assign").metrics
    assert m["sequence_ok"]
    assert m["notes"] == 345
    assert m["onset_dev_median_ms"] <= 60
    review = json.loads((wd.root / "review.json").read_text(encoding="utf-8"))
    assert review["summary"]["stale_corrections"] == 0
