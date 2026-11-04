"""02 lead: vocals -> lead vocal (karaoke) -> dry lead (de-reverb).

Transcribing the vocal stem directly gives many overlapping notes because of
backing vocals (pipeline notes §2), so the lead is extracted first.
"""

from __future__ import annotations

import shutil

from bapu.backends import separator
from bapu.stages.base import Stage, StageContext, StageResult


def _run(ctx: StageContext) -> StageResult:
    norm = float(ctx.params["normalization_threshold"])
    kar = ctx.models["karaoke_model"]
    assert kar is not None
    ctx.progress(f"主唱：{kar.name}", 0.0)
    out = separator.run_model(
        ctx.registry, kar, ctx.inputs["vocals"], ctx.out_dir,
        {"vocals": "lead", "instrumental": "backing"}, normalization_threshold=norm,
    )
    lead, backing = out["vocals"], out["instrumental"]

    drv = ctx.models["dereverb_model"]
    if drv is None:
        dry = ctx.out_dir / "lead_dry.wav"
        shutil.copyfile(lead, dry)
    else:
        ctx.progress(f"去混响：{drv.name}", 0.5)
        out = separator.run_model(
            ctx.registry, drv, lead, ctx.out_dir,
            {"noreverb": "lead_dry", "reverb": "lead_reverb"}, normalization_threshold=norm,
        )
        dry = out["noreverb"]
    return StageResult(outputs={"lead": lead, "backing": backing, "lead_dry": dry})


STAGE = Stage(
    name="lead",
    title="主唱",
    index=2,
    version="1",
    inputs=("vocals",),
    outputs=("lead", "backing", "lead_dry"),
    defaults={
        "karaoke_model": "mel-karaoke-aufr33-viperx",
        "dereverb_model": "mel-dereverb-anvuew",
        "normalization_threshold": 1.0,
    },
    model_params={"karaoke_model": "lead.karaoke", "dereverb_model": "lead.dereverb"},
    run=_run,
)
