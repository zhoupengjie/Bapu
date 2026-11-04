from bapu.stages import lead, separate, tempo, transcribe
from bapu.stages.base import Stage, StageContext, StageResult

STAGES: tuple[Stage, ...] = (separate.STAGE, lead.STAGE, tempo.STAGE, transcribe.STAGE)

__all__ = ["STAGES", "Stage", "StageContext", "StageResult"]
