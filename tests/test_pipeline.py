"""Cache behaviour of the pipeline, using fake stages (no models, no GPU)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bapu import pipeline
from bapu.models.registry import Registry
from bapu.stages.base import Stage, StageContext, StageResult
from bapu.workdir import WorkDir, cache_key

REGISTRY_TOML = """
[[model]]
id = "algo-a"
stage = "b"
name = "A"
backend = "test"
license = { name = "MIT", commercial = "yes" }

[[model]]
id = "algo-other"
stage = "elsewhere"
name = "Other"
backend = "test"
license = { name = "MIT", commercial = "yes" }
"""


def make_stages(calls: list[str]) -> tuple[Stage, ...]:
    def first(ctx: StageContext) -> StageResult:
        calls.append("a")
        p = ctx.out_dir / "a.txt"
        p.write_text(ctx.inputs["source"].read_text() + f"|a{ctx.params['k']}")
        return StageResult({"x": p}, {"len": len(p.read_text())})

    def second(ctx: StageContext) -> StageResult:
        calls.append("b")
        p = ctx.out_dir / "b.txt"
        p.write_text(ctx.inputs["x"].read_text() + f"|b{ctx.params['k']}")
        return StageResult({"y": p})

    def third(ctx: StageContext) -> StageResult:
        calls.append("c")
        p = ctx.out_dir / "c.txt"
        p.write_text(ctx.inputs["y"].read_text() + "|c")
        return StageResult({"z": p})

    return (
        Stage("a", 1, "1", ("source",), ("x",), {"k": 1}, first),
        Stage("b", 2, "1", ("x",), ("y",), {"k": 1, "model": "algo-a"}, second, model_params={"model": "b"}),
        Stage("c", 3, "1", ("y",), ("z",), {}, third),
    )


@pytest.fixture
def env(tmp_path: Path):
    reg_file = tmp_path / "registry.toml"
    reg_file.write_text(REGISTRY_TOML, encoding="utf-8")
    registry = Registry.load(reg_file, cache_root=tmp_path / "cache")
    src = tmp_path / "song.wav"
    src.write_text("fictional-audio")
    calls: list[str] = []
    return WorkDir(tmp_path / "work"), src, registry, calls, make_stages(calls)


def test_cache_key_is_order_independent():
    assert cache_key({"a": 1, "b": [1, 2]}) == cache_key({"b": [1, 2], "a": 1})
    assert cache_key({"a": 1}) != cache_key({"a": 2})


def test_second_run_uses_cache(env):
    wd, src, reg, calls, stages = env
    first = pipeline.run(wd, {"source": src}, registry=reg, stages=stages)
    assert [o.status for o in first] == ["ran", "ran", "ran"]
    second = pipeline.run(wd, {"source": src}, registry=reg, stages=stages)
    assert [o.status for o in second] == ["cached", "cached", "cached"]
    assert calls == ["a", "b", "c"]
    assert (wd.root / "03_c" / "c.txt").read_text() == "fictional-audio|a1|b1|c"


def test_param_change_reruns_only_from_that_stage(env):
    wd, src, reg, calls, stages = env
    pipeline.run(wd, {"source": src}, registry=reg, stages=stages)
    calls.clear()
    out = pipeline.run(wd, {"source": src}, registry=reg, stages=stages, overrides={"b": {"k": 2}})
    assert [o.status for o in out] == ["cached", "ran", "ran"]
    assert calls == ["b", "c"]


def test_source_change_reruns_everything(env):
    wd, src, reg, calls, stages = env
    pipeline.run(wd, {"source": src}, registry=reg, stages=stages)
    src.write_text("other-audio")
    calls.clear()
    pipeline.run(wd, {"source": src}, registry=reg, stages=stages)
    assert calls == ["a", "b", "c"]


def test_from_forces_rerun_downstream(env):
    wd, src, reg, calls, stages = env
    pipeline.run(wd, {"source": src}, registry=reg, stages=stages)
    calls.clear()
    out = pipeline.run(wd, {"source": src}, registry=reg, stages=stages, from_stage="b")
    assert [o.status for o in out] == ["cached", "ran", "ran"]


def test_deleted_output_reruns_that_stage(env):
    wd, src, reg, calls, stages = env
    pipeline.run(wd, {"source": src}, registry=reg, stages=stages)
    (wd.root / "01_a" / "a.txt").unlink()
    calls.clear()
    pipeline.run(wd, {"source": src}, registry=reg, stages=stages)
    assert calls == ["a"]


def test_until_stops_early(env):
    wd, src, reg, calls, stages = env
    out = pipeline.run(wd, {"source": src}, registry=reg, stages=stages, until="b")
    assert [o.stage for o in out] == ["a", "b"]


def test_unknown_param_is_rejected(env):
    wd, src, reg, calls, stages = env
    with pytest.raises(pipeline.PipelineError):
        pipeline.run(wd, {"source": src}, registry=reg, stages=stages, overrides={"a": {"nope": 1}})


def test_model_from_wrong_stage_is_rejected(env):
    wd, src, reg, calls, stages = env
    with pytest.raises(pipeline.PipelineError):
        pipeline.run(wd, {"source": src}, registry=reg, stages=stages, overrides={"b": {"model": "algo-other"}})


def test_status_reports_stale_after_param_change(env):
    wd, src, reg, calls, stages = env
    pipeline.run(wd, {"source": src}, registry=reg, stages=stages)
    wd.save_params({"c": {}, "b": {"k": 5}})
    states = {r["stage"]: r["state"] for r in pipeline.status(wd, reg, stages)}
    assert states == {"a": "fresh", "b": "stale", "c": "stale"}


def test_user_supplied_artifact_skips_its_stage(env, tmp_path):
    """`x` given as a source: stage a is skipped, b runs on the file with its
    external defaults, and status reports the skip."""
    wd, src, reg, calls, stages = env
    stages = (stages[0],
              Stage("b", 2, "1", ("x",), ("y",), {"k": 1, "model": "algo-a"}, stages[1].run,
                    model_params={"model": "b"}, external_defaults={"x": {"k": 7}}),
              stages[2])
    x = tmp_path / "given.txt"
    x.write_text("given")
    out = pipeline.run(wd, {"source": src, "x": x}, registry=reg, stages=stages)
    assert [(o.stage, o.status) for o in out] == [("a", "skipped"), ("b", "ran"), ("c", "ran")]
    assert calls == ["b", "c"]
    assert (wd.root / "03_c" / "c.txt").read_text() == "given|b7|c"
    states = {r["stage"]: r["state"] for r in pipeline.status(wd, reg, stages)}
    assert states == {"a": "skipped", "b": "fresh", "c": "fresh"}
    # an explicit override still wins over the external default
    out = pipeline.run(wd, {"source": src, "x": x}, registry=reg, stages=stages, overrides={"b": {"k": 2}})
    assert [o.status for o in out] == ["skipped", "ran", "ran"]
    assert (wd.root / "03_c" / "c.txt").read_text() == "given|b2|c"
    # without the file, stage a runs again (its old record is simply missing)
    calls.clear()
    out = pipeline.run(wd, {"source": src}, registry=reg, stages=stages)
    assert calls == ["a", "b", "c"]
    assert pipeline.status(wd, reg, stages, {"source": src, "x": x})[0]["state"] == "skipped"
