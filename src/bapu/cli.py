"""Command line entry point: `bapu run | status | models`."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import click
import yaml

from bapu import pipeline
from bapu.models.registry import LicenseNotAccepted, ModelSpec, Registry, RegistryError
from bapu.progress import PrintProgress
from bapu.stages import STAGES
from bapu.workdir import WorkDir


def _fmt_size(n: int | None) -> str:
    if n is None:
        return "—"
    if n >= 1e9:
        return f"{n / 1e9:.2f} GB"
    if n >= 1e6:
        return f"{n / 1e6:.0f} MB"
    return f"{n / 1e3:.0f} KB"


def _slug(name: str) -> str:
    s = re.sub(r"[\s_]+", "-", name.strip().lower())
    s = re.sub(r"[^\w\-.]", "", s)
    return s.strip("-.") or "song"


def _parse_sets(values: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in values:
        if "=" not in item or "." not in item.split("=", 1)[0]:
            raise click.BadParameter(f"要写成 步骤.参数=值：{item}")
        lhs, rhs = item.split("=", 1)
        stage, param = lhs.split(".", 1)
        pipeline.find_stage(stage)
        out.setdefault(stage, {})[param] = yaml.safe_load(rhs)
    return out


def _parse_models(values: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """`--model separate=id` or `--model lead.dereverb_model=none`."""
    out: dict[str, dict[str, Any]] = {}
    for item in values:
        if "=" not in item:
            raise click.BadParameter(f"要写成 步骤=模型ID：{item}")
        lhs, mid = item.split("=", 1)
        stage, _, param = lhs.partition(".")
        st = pipeline.find_stage(stage)
        param = param or "model"
        if param not in st.model_params:
            raise click.BadParameter(f"{stage} 的模型参数是：{', '.join(st.model_params)}")
        out.setdefault(stage, {})[param] = mid
    return out


def _merge(*layers: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for layer in layers:
        for stage, params in layer.items():
            merged.setdefault(stage, {}).update(params)
    return merged


def _show_license(spec: ModelSpec) -> None:
    lic = spec.license
    click.echo(f"\n模型：{spec.name}（{spec.id}）")
    click.echo(f"大小：{_fmt_size(spec.total_size)}")
    click.echo(f"许可证：{lic.name}" + (f"  {lic.url}" if lic.url else ""))
    commercial = {"yes": "允许", "no": "不允许", "unknown": "未知，请自行确认"}[lic.commercial]
    click.echo(f"商用：{commercial}")
    if lic.note:
        click.echo(f"说明：{lic.note}")


class _DownloadPrinter:
    def __init__(self) -> None:
        self.last: tuple[str, int] = ("", -1)

    def __call__(self, name: str, done: int, total: int | None) -> None:
        pct = int(done * 100 / total) if total else -1
        if (name, pct) != self.last and (pct % 5 == 0 or pct == -1):
            self.last = (name, pct)
            shown = f"{pct}%" if pct >= 0 else _fmt_size(done)
            click.echo(f"  下载 {name} {shown}", err=True)


def _pull(registry: Registry, spec: ModelSpec, accept: bool) -> None:
    if not registry.license_accepted(spec) and not accept:
        _show_license(spec)
        if not click.confirm("接受这个许可证并下载？", default=False):
            raise click.Abort()
    registry.pull(spec, accept_license=True, progress=_DownloadPrinter())
    click.echo(f"{spec.id}：已下载，校验通过")


@click.group()
def main() -> None:
    """Bapu（扒谱）：录音 + 歌词 → 带歌词的人声乐谱。"""
    # A Windows console gets Unicode directly; a pipe would otherwise get the
    # legacy code page (cp936), which other tools read as mojibake.
    for stream in (sys.stdout, sys.stderr):
        if not stream.isatty() and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


@main.command()
@click.argument("audio", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out", type=click.Path(file_okay=False, path_type=Path), help="工作目录，默认 work/<文件名>")
@click.option("--from", "from_stage", help="从这一步开始强制重跑")
@click.option("--until", help="跑到这一步为止")
@click.option("--set", "sets", multiple=True, help="步骤.参数=值，会保存在工作目录里")
@click.option("--model", "models", multiple=True, help="步骤=模型ID（或 步骤.参数=模型ID）")
@click.option("--accept-licenses", is_flag=True, help="缺模型时不逐个询问，直接接受许可证并下载")
def run(audio: Path, out: Path | None, from_stage: str | None, until: str | None,
        sets: tuple[str, ...], models: tuple[str, ...], accept_licenses: bool) -> None:
    """处理一首歌。没变的步骤直接用缓存。"""
    wd = WorkDir(out or Path("work") / _slug(audio.stem))
    registry = Registry.load()
    try:
        overrides = _merge(wd.load_params(), _parse_sets(sets), _parse_models(models))
        # Check every model the run needs before starting, so a missing one is
        # downloaded (after its license is accepted) instead of failing mid-way.
        for st in STAGES:
            for spec in pipeline.resolve_models(st, pipeline.resolve_params(st, overrides.get(st.name)), registry).values():
                if spec is not None and registry.status(spec) not in ("ready", "builtin"):
                    _pull(registry, spec, accept_licenses)
            if until and st.name == until:
                break
        wd.root.mkdir(parents=True, exist_ok=True)
        wd.save_params(overrides)
        outcomes = pipeline.run(wd, audio, registry=registry, overrides=overrides,
                                from_stage=from_stage, until=until, progress=PrintProgress())
    except (pipeline.PipelineError, RegistryError) as e:
        raise click.ClickException(str(e)) from None
    click.echo(f"\n工作目录：{wd.root}")
    for o in outcomes:
        state = "缓存" if o.status == "cached" else f"{o.record.seconds:.1f} s"
        click.echo(f"  {o.stage:<11} {state:<9} {_metrics_line(o.record.metrics)}")


def _metrics_line(m: dict[str, Any]) -> str:
    return "  ".join(f"{k}={v}" for k, v in m.items() if not isinstance(v, (list, dict)))


@main.command()
@click.argument("out", type=click.Path(exists=True, file_okay=False, path_type=Path))
def status(out: Path) -> None:
    """每一步是否最新、耗时和关键数字。"""
    rows = pipeline.status(WorkDir(out), Registry.load())
    label = {"fresh": "最新", "stale": "需重跑", "missing": "未运行"}
    for r in rows:
        secs = f"{r['seconds']:.1f} s" if r["seconds"] is not None else "—"
        click.echo(f"{r['index']:02d} {r['stage']:<11} {label[r['state']]:<5} {secs:<9} {_metrics_line(r['metrics'])}")


@main.group()
def models() -> None:
    """查看、下载、删除模型。"""


@models.command("list")
@click.option("--stage", help="只看某一步")
def models_list(stage: str | None) -> None:
    registry = Registry.load()
    label = {"ready": "已下载", "missing": "未下载", "corrupt": "校验失败", "builtin": "无需下载"}
    for spec in registry.models.values():
        if stage and not spec.stage.startswith(stage):
            continue
        st = registry.status(spec)
        flag = "已验证" if spec.verified else "实验"
        click.echo(f"{spec.id:<30} {spec.stage:<14} {label[st]:<6} {_fmt_size(spec.total_size):>8}  {flag}  {spec.license.name}")


@models.command("pull")
@click.argument("model_id")
@click.option("--accept-license", is_flag=True, help="不询问，直接接受许可证")
def models_pull(model_id: str, accept_license: bool) -> None:
    registry = Registry.load()
    try:
        _pull(registry, registry.get(model_id), accept_license)
    except (RegistryError, LicenseNotAccepted) as e:
        raise click.ClickException(str(e)) from None


@models.command("rm")
@click.argument("model_id")
@click.confirmation_option(prompt="删除这个模型的权重文件？")
def models_rm(model_id: str) -> None:
    registry = Registry.load()
    try:
        freed = registry.remove(registry.get(model_id))
    except RegistryError as e:
        raise click.ClickException(str(e)) from None
    click.echo(f"已删除，释放 {_fmt_size(freed)}")


if __name__ == "__main__":
    sys.exit(main())
