"""Run the stages in order, skipping those whose cache key has not changed.

Sources are the user's files: `source` (the recording), `lyrics` (the lyric
file) and, optionally, artifacts the user supplies in place of a stage's output
(`notes` from an SV project, see bapu.external). A stage whose downstream
outputs are all supplied that way is skipped; a stage whose inputs are not
available (no lyrics given yet) ends the run there.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from bapu import corrections as corr
from bapu.audio import sha256_file
from bapu.models.registry import ModelSpec, Registry, RegistryError
from bapu.progress import Progress, ProgressEvent, StageProgress, null_progress
from bapu.stages import STAGES
from bapu.stages.base import Stage, StageContext
from bapu.workdir import StageRecord, WorkDir, cache_key


class PipelineError(Exception):
    pass


@dataclass
class Outcome:
    stage: str
    status: str  # "ran" | "cached" | "skipped" (its output came from the user)
    record: StageRecord | None


@dataclass
class Plan:
    keys: dict[str, str] = field(default_factory=dict)  # stages that run, with their cache keys
    skipped: list[str] = field(default_factory=list)  # stages replaced by a user-supplied artifact


def find_stage(name: str, stages: Sequence[Stage] = STAGES) -> Stage:
    for s in stages:
        if s.name == name:
            return s
    raise PipelineError(f"没有叫 {name} 的步骤；可用：{', '.join(s.name for s in stages)}")


def resolve_params(stage: Stage, overrides: dict[str, Any] | None, external: Iterable[str] = ()) -> dict[str, Any]:
    """Defaults, then the stage's defaults for user-supplied inputs, then the overrides."""
    params = dict(stage.defaults)
    for name in external:
        params.update(stage.external_defaults.get(name, {}))
    for k, v in (overrides or {}).items():
        if k not in params:
            raise PipelineError(f"{stage.name} 没有参数 {k}；可用：{', '.join(params)}")
        params[k] = v
    return params


def resolve_models(stage: Stage, params: dict[str, Any], registry: Registry) -> dict[str, ModelSpec | None]:
    models: dict[str, ModelSpec | None] = {}
    for pname, tag in stage.model_params.items():
        mid = params.get(pname)
        if mid in (None, "none"):
            models[pname] = None
            continue
        try:
            spec = registry.get(str(mid))
        except RegistryError as e:
            raise PipelineError(str(e)) from None
        if spec.stage != tag:
            raise PipelineError(f"{stage.name}.{pname}: 模型 {spec.id} 属于 {spec.stage}，不能用在 {tag}")
        models[pname] = spec
    return models


def load_corrections(workdir: WorkDir) -> list[dict[str, Any]]:
    try:
        return corr.load(workdir.corrections_path)
    except corr.CorrectionsError as e:
        raise PipelineError(str(e)) from None


def supplied(stage: Stage, external: set[str], stages: Sequence[Stage] = STAGES) -> bool:
    """True when every output of `stage` that a later stage reads is a user-supplied artifact."""
    consumed = {o for o in stage.outputs if any(o in s.inputs for s in stages if s.index > stage.index)}
    return bool(consumed) and consumed <= external


def plan(
    source_fps: dict[str, str],
    overrides: dict[str, dict[str, Any]],
    entries: list[dict[str, Any]] | None = None,
    stages: Sequence[Stage] = STAGES,
) -> Plan:
    """Cache key of every stage whose inputs are available, chained through
    upstream keys; stops at the first stage that lacks an input. Stages whose
    outputs the user supplied are listed as skipped instead."""
    fps = dict(source_fps)
    external = set(source_fps)
    out = Plan()
    for st in stages:
        if supplied(st, external, stages):
            out.skipped.append(st.name)
            continue
        params = resolve_params(st, overrides.get(st.name), external & set(st.inputs))
        if any(n not in fps for n in st.inputs if n not in st.optional_inputs):
            break
        payload: dict[str, Any] = {
            "stage": st.name, "version": st.version, "params": params,
            "inputs": {n: fps.get(n) for n in st.inputs},
        }
        if st.corrections:
            payload["corrections"] = corr.fingerprint(corr.select(entries or [], st.corrections))
        key = cache_key(payload)
        out.keys[st.name] = key
        for o in st.outputs:
            fps[o] = f"{key}:{o}"
    return out


def plan_keys(
    source_fps: dict[str, str],
    overrides: dict[str, dict[str, Any]],
    entries: list[dict[str, Any]] | None = None,
    stages: Sequence[Stage] = STAGES,
) -> dict[str, str]:
    return plan(source_fps, overrides, entries, stages).keys


def run(
    workdir: WorkDir,
    sources: dict[str, Path],
    *,
    registry: Registry,
    overrides: dict[str, dict[str, Any]] | None = None,
    from_stage: str | None = None,
    until: str | None = None,
    progress: Progress = null_progress,
    stages: Sequence[Stage] = STAGES,
) -> list[Outcome]:
    overrides = overrides or {}
    sources = {k: Path(v) for k, v in sources.items() if v is not None}
    if "source" not in sources:
        raise PipelineError("没有给出音频")
    for name, p in sources.items():
        if not p.exists():
            raise PipelineError(f"找不到文件：{p}")
    if from_stage:
        find_stage(from_stage, stages)
    last = find_stage(until, stages).index if until else None

    fps = {name: sha256_file(p) for name, p in sources.items()}
    workdir.root.mkdir(parents=True, exist_ok=True)
    workdir.set_sources({name: (p, fps[name]) for name, p in sources.items()})
    entries = load_corrections(workdir)
    planned = plan(fps, overrides, entries, stages)
    keys, skipped = planned.keys, set(planned.skipped)
    if last is not None:
        blocked = [s.name for s in stages if s.index <= last and s.name not in keys and s.name not in skipped]
        if blocked:
            raise PipelineError(f"{blocked[0]} 缺少输入")

    artifacts: dict[str, Path] = {n: p.resolve() for n, p in sources.items()}
    outcomes: list[Outcome] = []
    forced = False
    for st in stages:
        if last is not None and st.index > last:
            break
        if st.name == from_stage:
            forced = True
        if st.name in skipped:
            progress(ProgressEvent(st.name, "skip", message="用的是你给的文件"))
            outcomes.append(Outcome(st.name, "skipped", None))
            continue
        if st.name not in keys:
            break
        params = resolve_params(st, overrides.get(st.name), set(sources) & set(st.inputs))
        models = resolve_models(st, params, registry)
        key = keys[st.name]

        rec = None if forced else workdir.fresh(st.name, key)
        if rec is not None:
            progress(ProgressEvent(st.name, "skip", message="缓存命中"))
            outcomes.append(Outcome(st.name, "cached", rec))
        else:
            for spec in models.values():
                if spec is not None:
                    registry.ensure_ready(spec)  # fail before touching the stage folder
            out_dir = workdir.root / f"{st.index:02d}_{st.name}"
            if out_dir.exists():
                shutil.rmtree(out_dir)
            out_dir = workdir.stage_dir(st.index, st.name)
            progress(ProgressEvent(st.name, "start"))
            ctx = StageContext(
                inputs={n: artifacts[n] for n in st.inputs if n in artifacts}, out_dir=out_dir, params=params,
                models=models, registry=registry, progress=StageProgress(st.name, progress),
                corrections=corr.select(entries, st.corrections),
            )
            t0 = time.perf_counter()
            try:
                result = st.run(ctx)
            except Exception as e:
                progress(ProgressEvent(st.name, "error", message=str(e)))
                raise
            seconds = time.perf_counter() - t0
            missing = set(st.outputs) - set(result.outputs)
            if missing:
                raise PipelineError(f"{st.name} 没有给出输出：{sorted(missing)}")
            rec = StageRecord(
                key=key, dir=workdir.rel(out_dir),
                outputs={n: workdir.rel(p) for n, p in result.outputs.items()},
                metrics=result.metrics, params=params, version=st.version, seconds=round(seconds, 2),
            )
            workdir.record(st.name, rec)
            progress(ProgressEvent(st.name, "done", 1.0, f"{seconds:.1f} s"))
            outcomes.append(Outcome(st.name, "ran", rec))
        for n, rel in rec.outputs.items():
            artifacts[n] = workdir.root / rel
    return outcomes


def status(workdir: WorkDir, registry: Registry, stages: Sequence[Stage] = STAGES,
           sources: dict[str, Path] | None = None) -> list[dict[str, Any]]:
    """Per-stage state for `bapu status`: fresh / stale / missing / skipped.
    `sources`: the files the next run would use (default: the last run's)."""
    if sources is None:
        srcs = {n: Path(p) for n, (p, _) in workdir.sources().items()}
    else:
        srcs = {n: Path(p) for n, p in sources.items()}
    overrides = workdir.load_params()
    planned = Plan()
    if "source" in srcs and all(p.exists() for p in srcs.values()):
        fps = {n: sha256_file(p) for n, p in srcs.items()}
        planned = plan(fps, overrides, load_corrections(workdir), stages)
    rows = []
    for st in stages:
        rec = workdir.get(st.name)
        if st.name in planned.skipped:
            state = "skipped"
        elif rec is None:
            state = "missing"
        elif st.name in planned.keys and workdir.fresh(st.name, planned.keys[st.name]) is not None:
            state = "fresh"
        else:
            state = "stale"
        rows.append({"stage": st.name, "title": st.title, "index": st.index, "state": state,
                     "seconds": rec.seconds if rec else None, "metrics": rec.metrics if rec else {},
                     "params": rec.params if rec else resolve_params(st, overrides.get(st.name), set(srcs) & set(st.inputs))})
    return rows
