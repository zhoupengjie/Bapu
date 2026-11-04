from bapu.stages import (
    align, assign, backing, dictate, harmony, lead, lyrics, separate, splits, tempo, transcribe,
)
from bapu.stages.base import Stage, StageContext, StageResult

STAGES: tuple[Stage, ...] = (
    separate.STAGE, lead.STAGE, tempo.STAGE, transcribe.STAGE, splits.STAGE,
    dictate.STAGE, lyrics.STAGE, align.STAGE, assign.STAGE,
    backing.STAGE, harmony.STAGE,
)

__all__ = ["STAGES", "Stage", "StageContext", "StageResult"]
