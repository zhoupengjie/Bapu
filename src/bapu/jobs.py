"""Job-level helpers shared by the CLI and the API server.

A "job" is one song's work directory. These helpers know where its inputs
are, which models a run will need, and how to export results. They never
print and never log lyric text.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Sequence

from bapu import pipeline, project
from bapu.models.registry import ModelSpec, Registry
from bapu.stages import STAGES
from bapu.stages.base import Stage
from bapu.workdir import WorkDir

AUDIO_TRACKS = {"accompaniment": ("separate", "instrumental"), "lead_dry": ("lead", "lead_dry")}
EXPORT_FORMATS = ("mid", "svp", "paste")


class JobError(ValueError):
    pass


def slug(name: str) -> str:
    s = re.sub(r"[\s_]+", "-", name.strip().lower())
    s = re.sub(r"[^\w\-.]", "", s)
    return s.strip("-.") or "song"


def merge(*layers: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for layer in layers:
        for stage, params in layer.items():
            merged.setdefault(stage, {}).update(params)
    return merged


def stored_source(wd: WorkDir, name: str) -> Path | None:
    """A source file recorded by an earlier run, if it still exists."""
    if not wd.manifest_path.exists():
        return None
    p = wd.sources().get(name)
    return Path(p[0]) if p and Path(p[0]).exists() else None


def missing_models(sources: dict[str, Path], overrides: dict[str, dict[str, Any]], registry: Registry,
                   until: str | None = None, stages: Sequence[Stage] = STAGES) -> list[ModelSpec]:
    """Models the run will need that are not downloaded and verified yet."""
    runnable = pipeline.plan_keys({n: "?" for n in sources}, overrides, stages=stages)
    out: list[ModelSpec] = []
    for st in stages:
        if st.name not in runnable:
            break
        params = pipeline.resolve_params(st, overrides.get(st.name))
        for spec in pipeline.resolve_models(st, params, registry).values():
            if spec is not None and registry.status(spec) not in ("ready", "builtin") and spec not in out:
                out.append(spec)
        if until and st.name == until:
            break
    return out


def audio_tracks(wd: WorkDir, spec: str) -> list[tuple[str, Path]]:
    """Audio tracks for the .svp: the separated accompaniment and the dry lead
    (for comparing the synthesised vocal with the original)."""
    out = []
    for key in [x.strip() for x in spec.split(",") if x.strip() and x.strip() != "none"]:
        if key not in AUDIO_TRACKS:
            raise JobError(f"不认识的音频轨：{key}（可用：{', '.join(AUDIO_TRACKS)}、none）")
        stage, artifact = AUDIO_TRACKS[key]
        rec = wd.get(stage)
        if rec and artifact in rec.outputs:
            out.append((key.replace("_", " "), (wd.root / rec.outputs[artifact]).resolve()))
    return out


def export(wd: WorkDir, formats: Sequence[str] = EXPORT_FORMATS, audio: str = "accompaniment,lead_dry"
           ) -> list[tuple[str, Path]]:
    """Write the requested formats. In a project they go next to the .bapu
    file, named after the project, without overwriting a file Bapu did not
    write; in a bare work dir (tests, old CLI runs) into `<work dir>/export/`."""
    from bapu.export import midi, paste
    from bapu.export import svp as svp_export

    score_p = wd.root / "score.json"
    if not score_p.exists():
        raise JobError("还没有 score.json：先跑完整个流程")
    unknown = [f for f in formats if f not in EXPORT_FORMATS]
    if unknown:
        raise JobError(f"不认识的格式：{', '.join(unknown)}")
    score = json.loads(score_p.read_text(encoding="utf-8"))
    in_project = wd.root.name == project.DATA
    if in_project:
        folder = project.folder_of(wd.root)
        name = project.safe_name(project.read(folder)["name"])

        def target(fn: str) -> Path:
            return project.export_target(folder, wd.root, fn)
    else:
        name = slug(Path(wd.sources().get("source", ("song",))[0]).stem)

        def target(fn: str) -> Path:
            return wd.root / "export" / fn
    written = []
    for f in formats:
        if f == "mid":
            out = midi.write(score, target(f"{name}.mid"))
        elif f == "paste":
            out = paste.write(score, target(f"{name}.paste.txt"))
        else:
            out = svp_export.write(score, target(f"{name}.svp"), audio_tracks=audio_tracks(wd, audio), name=name)
        if in_project:
            project.record_export(wd.root, out)
        written.append((f, out))
    return written
