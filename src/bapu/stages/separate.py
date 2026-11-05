"""01 separate: song -> vocals + instrumental."""

from __future__ import annotations

from bapu.audio import decode_to_wav, readable
from bapu.backends import separator
from bapu.stages.base import Stage, StageContext, StageResult


DECODED = "source.wav"  # the decoded audio of a source soundfile cannot read


def _run(ctx: StageContext) -> StageResult:
    spec = ctx.models["model"]
    assert spec is not None
    source = ctx.inputs["source"]
    if not readable(source):  # MP4 / M4A / video: take the audio track out first
        ctx.progress("从文件里取出音轨")
        source = decode_to_wav(source, ctx.out_dir / DECODED)
    ctx.progress(f"载入 {spec.name}")
    out = separator.run_model(
        ctx.registry, spec, source, ctx.out_dir,
        {"vocals": "vocals", "instrumental": "instrumental"},
        normalization_threshold=float(ctx.params["normalization_threshold"]),
    )
    return StageResult(outputs={"vocals": out["vocals"], "instrumental": out["instrumental"]})


STAGE = Stage(
    name="separate",
    title="分离",
    index=1,
    version="1",
    inputs=("source",),
    outputs=("vocals", "instrumental"),
    # 0.9 (audio-separator's default) peak-normalises each stem separately. It
    # breaks the vocal/instrumental level ratio, but the validated transcription
    # (353 notes, the 8 confirmed merges) was produced from these stems, and the
    # karaoke model is not gain-invariant: 1.0 changes 8 of 353 notes. Set 1.0
    # when the stems must sum back to the mix.
    defaults={"model": "bs-roformer-ep317", "normalization_threshold": 0.9},
    model_params={"model": "separate"},
    run=_run,
)
