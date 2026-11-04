from bapu.stages import align, assign, lead, lyrics, separate, splits, tempo, transcribe
from bapu.stages.base import Stage, StageContext, StageResult

STAGES: tuple[Stage, ...] = (
    separate.STAGE, lead.STAGE, tempo.STAGE, transcribe.STAGE,
    splits.STAGE, lyrics.STAGE, align.STAGE, assign.STAGE,
)

__all__ = ["STAGES", "Stage", "StageContext", "StageResult"]
