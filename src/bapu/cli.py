"""Command line entry point: `bapu run | status | models`."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import click
import yaml

from bapu import compare, jobs, pipeline, report
from bapu import corrections as corr_mod
from bapu.corrections import CorrectionsError
from bapu.stages.lyrics import check_langs
from bapu.models.registry import LicenseNotAccepted, ModelSpec, Registry, RegistryError
from bapu.progress import PrintProgress
from bapu.workdir import WorkDir


def _fmt_size(n: int | None) -> str:
    if n is None:
        return "—"
    if n >= 1e9:
        return f"{n / 1e9:.2f} GB"
    if n >= 1e6:
        return f"{n / 1e6:.0f} MB"
    return f"{n / 1e3:.0f} KB"


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
@click.option("--lyrics", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="参考歌词（.ass / .lrc / .txt），用来校正听写的用字；不给时沿用上次的")
@click.option("--lang", help="歌曲里的语言，可多选，逗号分隔：yue（粤语）、zh（普通话）、en（英语）；默认 yue,en")
def run(audio: Path, out: Path | None, from_stage: str | None, until: str | None,
        sets: tuple[str, ...], models: tuple[str, ...], accept_licenses: bool,
        lyrics: Path | None, lang: str | None) -> None:
    """处理一首歌。没变的步骤直接用缓存。"""
    wd = WorkDir(out or Path("work") / jobs.slug(audio.stem))
    registry = Registry.load()
    sources: dict[str, Path] = {"source": audio}
    lyrics = lyrics or jobs.stored_source(wd, "lyrics")
    if lyrics:
        sources["lyrics"] = lyrics
    try:
        extra: dict[str, dict[str, Any]] = {}
        if lang:
            try:
                langs = check_langs(lang)
            except ValueError as e:
                raise click.BadParameter(str(e)) from None
            extra = {"lyrics": {"langs": langs}, "dictate": {"langs": langs}}
        overrides = jobs.merge(wd.load_params(), _parse_sets(sets), _parse_models(models), extra)
        # Check every model the run will need before starting, so a missing one
        # is downloaded (after its license is accepted) instead of failing mid-way.
        for spec in jobs.missing_models(sources, overrides, registry, until):
            _pull(registry, spec, accept_licenses)
        wd.root.mkdir(parents=True, exist_ok=True)
        wd.save_params(overrides)
        outcomes = pipeline.run(wd, sources, registry=registry, overrides=overrides,
                                from_stage=from_stage, until=until, progress=PrintProgress())
        written = report.finalize(wd)
    except (pipeline.PipelineError, RegistryError, CorrectionsError) as e:
        raise click.ClickException(str(e)) from None
    click.echo(f"\n工作目录：{wd.root}")
    for o in outcomes:
        state = "缓存" if o.status == "cached" else f"{o.record.seconds:.1f} s"
        click.echo(f"  {o.stage:<11} {state:<9} {_metrics_line(o.record.metrics)}")
    if "lyrics" not in sources:
        click.echo("\n没有参考歌词：歌词全部来自听写，审查报告里每句都要确认。可以加 --lyrics <文件> 作参考。")
    if written:
        click.echo(f"\n审查报告：{written['review_md']}（{written['summary']}）")


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


@main.command()
@click.argument("out", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--format", "formats", default="mid,svp,paste", show_default=True, help="mid / svp / paste，逗号分隔")
@click.option("--audio", "audio", default="accompaniment,lead_dry", show_default=True,
              help=".svp 里加哪些音频轨：accompaniment / lead_dry / none，逗号分隔")
@click.option("--no-harmony", is_flag=True, help="不导出和声轨")
def export(out: Path, formats: str, audio: str, no_harmony: bool) -> None:
    """把 score.json 导出到 <工作目录>/export/。"""
    import json

    wd = WorkDir(out)
    try:
        written = jobs.export(wd, [f.strip() for f in formats.split(",") if f.strip()], audio, not no_harmony)
    except jobs.JobError as e:
        raise click.ClickException(str(e)) from None
    for f, p in written:
        click.echo(f"{f:<6} {p}")
    score = json.loads((wd.root / "score.json").read_text(encoding="utf-8"))
    click.echo(f"{len(score['notes'])} 个音符，{score['tempo']['bpm']:g} BPM")


@main.command()
@click.option("--work", "work_root", default="work", show_default=True, type=click.Path(file_okay=False, path_type=Path),
              help="任务目录的根目录")
@click.option("--host", default="127.0.0.1", show_default=True, help="只建议本机地址")
@click.option("--port", default=8765, show_default=True)
@click.option("--web", "web_dir", default="web/dist", show_default=True, type=click.Path(file_okay=False, path_type=Path),
              help="构建好的前端（cd web && npm run build）；不存在时只提供 API")
def serve(work_root: Path, host: str, port: int, web_dir: Path) -> None:
    """启动本机 API 服务（给前端用）。"""
    import uvicorn

    from bapu.server import create_app
    from bapu.service import JobService

    if host not in ("127.0.0.1", "localhost", "::1"):
        click.echo("注意：服务会返回歌词和音频，不要监听本机以外的地址。", err=True)
    has_web = (web_dir / "index.html").exists()
    app = create_app(JobService(work_root), static_dir=web_dir if has_web else None)
    if has_web:
        click.echo(f"Bapu：http://{host}:{port}/   （API 文档：/docs）")
    else:
        click.echo(f"Bapu API：http://{host}:{port}/docs   （没有找到 {web_dir}，前端请先 npm run build）")
    uvicorn.run(app, host=host, port=port, log_level="info")


@main.group()
def corrections() -> None:
    """修正文件 corrections.yaml。"""


@corrections.command("from-svp")
@click.argument("out", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("project", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--track", default=1, show_default=True, help="人声轨（从 1 开始）")
@click.option("--dry-run", is_flag=True, help="只显示会加哪些修正，不写文件")
def corrections_from_svp(out: Path, project: Path, track: int, dry_run: bool) -> None:
    """把在 SV 里改过的地方读成修正，追加到 corrections.yaml。"""
    wd = WorkDir(out)
    try:
        entries, stats = compare.from_svp(wd, project, track)
    except (FileNotFoundError, KeyError, ValueError) as e:
        raise click.ClickException(str(e)) from None
    click.echo(f"确认合并 {stats['merge_confirm']}，拒绝合并 {stats['merge_reject']}，字幕修正 {stats.get('line_text', 0)}，"
               f"人工版本的句子 {stats['line_version']}，音高 {stats['pitch']}")
    lines = sorted({e["line"] for e in entries if e["type"] == "line_version"})
    if lines:
        click.echo("人工版本：" + " ".join(f"L{li:02d}" for li in lines))
    if dry_run or not entries:
        return
    corr_mod.append(wd.corrections_path, entries, comment=f"from-svp {project.name} {time.strftime('%Y-%m-%d %H:%M')}")
    click.echo(f"已追加到 {wd.corrections_path}；重跑 `bapu run` 生效")
    if stats.get("needs_rerun"):
        click.echo("这次只写了合并修正。音符对齐之后才能比较歌词：重跑 `bapu run`，再运行一次 from-svp。")


@main.command("eval")
@click.argument("out", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("project", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--lines", "only", help="只看这些句，如 2,10")
@click.option("--final", is_flag=True, help="比较最终版本（含人工版本），默认比较自动版本")
@click.option("--track", default=1, show_default=True)
def eval_cmd(out: Path, project: Path, only: str | None, final: bool, track: int) -> None:
    """和 SV 工程里已确认的版本对比：每句有几个音符一致。"""
    try:
        res = compare.evaluate(WorkDir(out), project, track, use="lyric" if final else "auto_lyric")
    except (FileNotFoundError, KeyError, ValueError) as e:
        raise click.ClickException(str(e)) from None
    want = {int(x) for x in only.split(",")} if only else None
    click.echo(f"SV 音符 {res['sv_notes']}，计划音符 {res['plan_notes']}")
    for li in sorted(res["lines"]):
        if want and li not in want:
            continue
        r = res["lines"][li]
        click.echo(f"L{li:02d}  字一致 {r['same_lyric']}/{r['notes']}  类型一致 {r['same_kind']}/{r['notes']}"
                   + ("" if r["matched"] == r["notes"] else f"  （{r['notes'] - r['matched']} 个音符时间对不上）"))
    t = res["total"]
    click.echo(f"全曲  字一致 {t['same_lyric']}/{t['notes']}  类型一致 {t['same_kind']}/{t['notes']}")


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


@models.command("info")
@click.argument("model_id")
def models_info(model_id: str) -> None:
    """手动下载用：下载地址、放到哪个目录、文件名和 SHA-256。"""
    registry = Registry.load()
    try:
        spec = registry.get(model_id)
    except RegistryError as e:
        raise click.ClickException(str(e)) from None
    _show_license(spec)
    info = registry.download_info(spec)
    if info["dir"] is None:
        click.echo("\n随程序安装，不需要下载。")
        return
    click.echo(f"\n放到目录：{info['dir']}")
    if info["archive"]:
        a = info["archive"]
        click.echo(f"下载压缩包：{a['url']}（{_fmt_size(a['size'])}）")
        click.echo("解压后，把下面的文件放进上面的目录（在压缩包里按文件名找）：")
    for f in info["files"]:
        mark = "已在" if f["present"] else "缺少"
        click.echo(f"\n  [{mark}] {f['name']}  {_fmt_size(f['size'])}")
        click.echo(f"    SHA-256：{f['sha256']}")
        for url in f["urls"]:
            click.echo(f"    下载：{url}")
    click.echo(f"\n放好后运行 `bapu models list` 检查，状态应为“已下载”。")


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
