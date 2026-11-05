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
EXPORT_FORMATS = ("mid", "svp", "paste")  # from the finished score
# From the separation / notes steps alone, before any lyrics: an .svp with only the
# audio tracks (for SV2 to extract the pitch from), one with the notes (no lyrics), the stems.
STAGE_FORMATS = ("svp-audio", "svp-notes", "wav")
WAV_LABELS = {"lead_dry": "主唱干声", "accompaniment": "伴奏"}
PLACEHOLDER_LYRIC = "la"  # notes exported before the lyrics are known


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
    planned = pipeline.plan({n: "?" for n in sources}, overrides, stages=stages)
    out: list[ModelSpec] = []
    for st in stages:
        if st.name in planned.skipped:
            continue  # its output comes from the user: no model needed
        if st.name not in planned.keys:
            break
        params = pipeline.resolve_params(st, overrides.get(st.name), set(sources) & set(st.inputs))
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


def _stage_file(wd: WorkDir, stage: str, artifact: str, what: str) -> Path:
    rec = wd.get(stage)
    p = (wd.root / rec.outputs[artifact]) if rec and artifact in rec.outputs else None
    if p is None or not p.exists():
        raise JobError(f"还没有{what}：先跑完它那一步")
    return p


def partial_score(wd: WorkDir, with_notes: bool) -> dict[str, Any]:
    """A score with the tempo (and the merged notes, lyric-less) from the stage
    outputs, for the .svp exports that come before the lyrics."""
    from bapu import corrections as corr

    tempo = json.loads(_stage_file(wd, "tempo", "tempo", "拍速").read_text(encoding="utf-8"))
    bpm = float(tempo["bpm"])
    fixes = [c for c in (corr.load(wd.corrections_path) if wd.corrections_path.exists() else []) if c["type"] == "tempo"]
    if fixes:
        bpm = float(fixes[-1]["bpm"])
    langs = (wd.load_params().get("lyrics") or {}).get("langs") or ["yue"]
    lang = str(langs[0] if isinstance(langs, list) else langs).split(",")[0]
    notes: list[dict[str, Any]] = []
    if with_notes:
        raw = json.loads(_stage_file(wd, "splits", "merged_notes", "音符").read_text(encoding="utf-8"))["notes"]
        notes = [{"start": n["start"], "dur": n["dur"], "pitch": int(round(n["pitch"])), "lyric": PLACEHOLDER_LYRIC,
                  "lang": lang} for n in raw]
    return {
        "format": "bapu.score/1",
        "tempo": {"bpm": bpm, "source": "user" if fixes else "auto", "first_beat": tempo.get("first_beat"),
                  "beat_phase_s": tempo.get("beat_phase_s", 0.0), "confidence": tempo.get("confidence"),
                  "time_signature": tempo.get("time_signature", "4/4")},
        "lang": lang, "lines": [], "notes": notes,
    }


def export(wd: WorkDir, formats: Sequence[str] = EXPORT_FORMATS, audio: str = "accompaniment,lead_dry",
           grid: int = 0) -> list[tuple[str, Path]]:
    """Write the requested formats. In a project they go next to the .bapu
    file, named after the project, without overwriting a file Bapu did not
    write; in a bare work dir (tests, old CLI runs) into `<work dir>/export/`."""
    import shutil

    from bapu.export import midi, paste
    from bapu.export import svp as svp_export
    from bapu.export.grid import GRIDS

    if grid and grid not in GRIDS:
        raise JobError(f"网格只能是 1/{', 1/'.join(map(str, GRIDS))}（或 0：不对齐）")
    unknown = [f for f in formats if f not in EXPORT_FORMATS + STAGE_FORMATS]
    if unknown:
        raise JobError(f"不认识的格式：{', '.join(unknown)}")
    score = None
    if any(f in EXPORT_FORMATS for f in formats):
        score_p = wd.root / "score.json"
        if not score_p.exists():
            raise JobError("还没有 score.json：先跑完整个流程")
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
        outs: list[Path] = []
        if f == "mid":
            outs = [midi.write(score, target(f"{name}.mid"), grid=grid)]
        elif f == "paste":
            outs = [paste.write(score, target(f"{name}.paste.txt"))]
        elif f == "svp":
            outs = [svp_export.write(score, target(f"{name}.svp"), audio_tracks=audio_tracks(wd, audio), name=name, grid=grid)]
        elif f in ("svp-audio", "svp-notes"):
            tracks = audio_tracks(wd, audio)
            if f == "svp-audio" and not tracks:
                raise JobError("还没有可以放进工程的音轨：先跑完分离这一步")
            label = "音轨" if f == "svp-audio" else "音符"
            outs = [svp_export.write(partial_score(wd, f == "svp-notes"), target(f"{name} {label}.svp"),
                                     audio_tracks=tracks, name=name, grid=grid)]
        elif f == "wav":
            for key, label in WAV_LABELS.items():
                stage, artifact = AUDIO_TRACKS[key]
                src = _stage_file(wd, stage, artifact, label)
                dest = target(f"{name} {label}.wav")
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dest)
                outs.append(dest)
        for out in outs:
            if in_project:
                project.record_export(wd.root, out)
            written.append((f, out))
    return written
