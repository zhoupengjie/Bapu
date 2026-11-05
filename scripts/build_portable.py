"""Build the portable Bapu app for the platform this runs on.

Windows:

    uv run python scripts/build_portable.py --out "<program folder>"

Result (about 40 MB; Python, packages and models are not included):

    Bapu/
      Bapu.exe              desktop shell (Tauri)
      tools/uv.exe          sets up the Python environment on first launch
      app/src/bapu/         Bapu's Python code
      app/web/              the web UI
      app/requirements.txt  every package except torch, pinned from uv.lock
      app/torch.json        the PyTorch builds to choose from (CUDA / AMD ROCm / Intel XPU / CPU)
      README.txt

`runtime/`, `models/` and `data/` in an existing output folder are kept, so
rebuilding updates the program without reinstalling Python or the models.

macOS (Apple chips only; PyTorch has no Intel-Mac builds):

    uv run python scripts/build_portable.py       # dist/Bapu.app and dist/Bapu-mac-<version>.dmg

    Bapu.app/Contents/
      MacOS/Bapu            desktop shell (Tauri)
      Resources/tools/uv    the `uv` on PATH (must be an arm64 build)
      Resources/app/        as on Windows; torch.json offers Apple chips (Metal) / CPU

The bundle is ad-hoc signed and never written to at run time: Python, models
and data go to ~/Library/Application Support/Bapu, where the shell links `app`
and `tools` to the bundle. The disk image (.dmg) holds Bapu.app, a link to
/Applications to drag it onto, and README.txt.
"""

from __future__ import annotations

import argparse
import filecmp
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TORCH = ("torch", "torchaudio", "torchvision")

ROCM = "https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1"

# Windows
VARIANTS = {
    "cuda": {"label": "NVIDIA 显卡（CUDA 12.8）", "detail": "RTX 20 系及以后，驱动 570 以上；已在 RTX 5090 上验证",
             "index": "https://download.pytorch.org/whl/cu128", "deps": False, "size_gb": 3.8, "experimental": False},
    # AMD's own Windows build (ROCm 7.2.1): direct wheel links, the ROCm SDK first, then torch; it brings its
    # own torch version (2.9), recorded in "packages" so an update knows when to reinstall
    "rocm": {"label": "AMD 显卡（ROCm）", "detail": "RX 9000 / RX 7000 系列和部分 Ryzen AI，需要 Windows 11 和 AMD 驱动 26.2.2 以上；还没在 AMD 显卡上测过",
             "wheels": [[f"{ROCM}/rocm_sdk_core-7.2.1-py3-none-win_amd64.whl", f"{ROCM}/rocm_sdk_devel-7.2.1-py3-none-win_amd64.whl",
                         f"{ROCM}/rocm_sdk_libraries_custom-7.2.1-py3-none-win_amd64.whl", f"{ROCM}/rocm-7.2.1.tar.gz"],
                        [f"{ROCM}/torch-2.9.1%2Brocm7.2.1-cp312-cp312-win_amd64.whl",
                         f"{ROCM}/torchaudio-2.9.1%2Brocm7.2.1-cp312-cp312-win_amd64.whl",
                         f"{ROCM}/torchvision-0.24.1%2Brocm7.2.1-cp312-cp312-win_amd64.whl"]],
             "packages": ["torch==2.9.1", "torchaudio==2.9.1", "torchvision==0.24.1"],
             "deps": False, "size_gb": 3.0, "experimental": True},
    "xpu": {"label": "Intel Arc 显卡（XPU）", "detail": "还没在 Intel 显卡上测过；人声分离那两步会用 CPU",
            "index": "https://download.pytorch.org/whl/xpu", "deps": True, "size_gb": 2.6, "experimental": True},
    "cpu": {"label": "只用 CPU", "detail": "任何电脑都能用，但处理一首歌会慢很多",
            "index": "https://download.pytorch.org/whl/cpu", "deps": False, "size_gb": 1.1, "experimental": False},
}

# macOS: both from the package index (PyPI's wheels for Apple chips include Metal); "device" makes the
# shell set BAPU_DEVICE, since the CPU choice installs the same build and would otherwise pick Metal
VARIANTS_MAC = {
    "mps": {"label": "Apple 芯片（Metal）", "detail": "M1 及以后的 Mac，需要 macOS 14 以上；还没在 Mac 上测过",
            "from_pypi": True, "deps": False, "size_gb": 1.0, "experimental": True},
    "cpu": {"label": "只用 CPU", "detail": "任何电脑都能用，但处理一首歌会慢很多",
            "from_pypi": True, "device": "cpu", "deps": False, "size_gb": 1.0, "experimental": False},
}

# what the markers in pyproject.toml see on each target
MACHINE = {"win32": "AMD64", "darwin": "arm64"}

README = """Bapu 扒谱（便携版）

双击 Bapu.exe 运行。第一次用时，在“环境”页点“安装运行环境”：Bapu 会在这个文件夹里装好自己用的 Python 和推理用的包
（需要联网，几个 GB），再在同一页下载模型。不改系统设置，不写注册表，不碰电脑上已有的 Python。

文件夹：
  app\\      程序代码和界面（升级时替换）
  tools\\    uv（安装 Python 环境用）
  runtime\\  Python 和依赖包（在“环境”页装；删掉它就要重新装）
  models\\   模型
  data\\     最近的工程列表、日志

整个文件夹可以移动到别的位置；要放在有写入权限的地方（不要放在 C:\\Program Files 里）。
卸载：删掉整个文件夹。你的工程（.bapu 文件和它旁边的 .bapu 文件夹）在你自己选的位置，不受影响。
双击 .bapu 打开：在“环境”页打开“关联 .bapu 文件”，或在 Windows 的“打开方式”里选 Bapu.exe。

uv: https://github.com/astral-sh/uv (MIT / Apache-2.0)
"""

README_MAC = """Bapu 扒谱（macOS 版）

需要 Apple 芯片（M1 及以后）的 Mac 和 macOS 14 以上。

安装：打开 dmg，把 Bapu 拖到旁边的“应用程序”文件夹上。

第一次打开：Bapu 没有经过 Apple 公证，直接双击会被拦下。在“应用程序”里右键点 Bapu.app，选“打开”，再点“打开”；
或者双击被拦下后，到“系统设置 → 隐私与安全性”里点“仍要打开”。以后直接双击就行。
如果还是打不开（或装运行环境时提示 uv 无法打开），在“终端”里运行：
  xattr -dr com.apple.quarantine /Applications/Bapu.app

第一次用时，在“环境”页点“安装运行环境”：Bapu 会装好自己用的 Python 和推理用的包（需要联网），
再在同一页下载模型。不碰电脑上已有的 Python。

Python、模型、设置和日志都在 ~/Library/Application Support/Bapu
（在访达里按 Shift-Command-G，粘贴这个路径就能打开）。
卸载：删掉 Bapu.app 和上面这个文件夹。你的工程（.bapu 文件和它旁边的 .bapu 文件夹）在你自己选的位置，不受影响。

uv: https://github.com/astral-sh/uv (MIT / Apache-2.0)
"""


def run(cmd: list[str], **kw) -> str:
    print("$", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=True, text=True, encoding="utf-8", capture_output=kw.pop("capture", False), **kw).stdout or ""


def _marker_holds(marker: str, target: str) -> bool:
    """`sys_platform` / `platform_machine` comparisons joined by `and` (what pyproject.toml uses for torch)."""
    env = {"sys_platform": target, "platform_machine": MACHINE.get(target, "")}
    for clause in filter(None, (c.strip() for c in re.split(r"\band\b", marker))):
        m = re.fullmatch(r"(sys_platform|platform_machine)\s*(==|!=)\s*['\"]([^'\"]*)['\"]", clause)
        if not m:
            sys.exit(f"pyproject.toml 里 torch 的条件看不懂：{marker.strip()}")
        if (env[m.group(1)] == m.group(3)) != (m.group(2) == "=="):
            return False
    return True


def torch_pins(target: str = sys.platform) -> list[str]:
    """torch / torchaudio / torchvision pins for `target` (win32 / darwin): pyproject.toml's dependencies
    whose sys_platform markers hold there; each must also be the version uv.lock has."""
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    found: dict[str, str] = {}
    for dep in deps:
        req, _, marker = dep.partition(";")
        m = re.fullmatch(r"\s*(torch|torchaudio|torchvision)\s*==\s*([\w.]+)\s*", req)
        if not m or not _marker_holds(marker, target):
            continue
        name, version = m.groups()
        if name in found:
            sys.exit(f"pyproject.toml 给 {target} 写了两个 {name}")
        if not re.search(rf'\[\[package\]\]\nname = "{name}"\nversion = "{re.escape(version)}(\+[^"]*)?"', lock):
            sys.exit(f"uv.lock 里没有 {name} {version}：先运行 uv lock")
        found[name] = f"{name}=={version}"
    missing = [n for n in TORCH if n not in found]
    if missing:
        sys.exit(f"pyproject.toml 里没有 {target} 用的 {'、'.join(missing)}")
    return [found[n] for n in TORCH]


def requirements() -> str:
    out = run(["uv", "export", "--format", "requirements-txt", "--no-hashes", "--no-dev", "--no-emit-project",
               "--no-annotate", "--no-header", "--frozen"], cwd=ROOT, capture=True)
    keep = []
    for line in out.splitlines():
        name = re.split(r"[=<>;\s\[]", line.strip(), maxsplit=1)[0].lower()
        if not line.strip() or line.startswith(("#", "-")) or name in TORCH:
            continue
        keep.append(line)
    return "\n".join(keep) + "\n"


def replace_dir(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def fill_app(app: Path, target: str) -> None:
    """`app/`: the Python code, the web UI, the package list and the PyTorch choices for `target`."""
    app.mkdir(parents=True, exist_ok=True)
    replace_dir(ROOT / "src" / "bapu", app / "src" / "bapu")
    replace_dir(ROOT / "web" / "dist", app / "web")
    (app / "requirements.txt").write_text(requirements(), encoding="utf-8")
    variants = VARIANTS_MAC if target == "darwin" else VARIANTS
    (app / "torch.json").write_text(json.dumps({"python": "3.12", "packages": torch_pins(target), "variants": variants},
                                               ensure_ascii=False, indent=2), encoding="utf-8")


def version() -> str:
    text = (ROOT / "src" / "bapu" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def build_windows(args: argparse.Namespace) -> None:
    out: Path = args.out or ROOT / "dist" / "Bapu"
    npm = shutil.which("npm") or "npm"
    if not args.skip_build:
        run([npm, "--prefix", str(ROOT / "web"), "run", "build"])
        run([npm, "--prefix", str(ROOT / "desktop"), "run", "build"])
    exe = ROOT / "desktop" / "src-tauri" / "target" / "release" / "Bapu.exe"
    uv = shutil.which("uv")
    if not exe.exists() or not (ROOT / "web" / "dist" / "index.html").exists() or not uv:
        sys.exit("缺 Bapu.exe、web/dist 或 uv")

    out.mkdir(parents=True, exist_ok=True)
    (out / "tools").mkdir(exist_ok=True)
    for src, dest in ((exe, out / "Bapu.exe"), (Path(uv), out / "tools" / "uv.exe")):
        if dest.exists() and filecmp.cmp(src, dest, shallow=False):
            continue  # unchanged: also works while Bapu is running
        shutil.copy2(src, dest)
    fill_app(out / "app", "win32")
    (out / "README.txt").write_text(README, encoding="utf-8-sig")
    for d in ("models", "data"):
        (out / d).mkdir(exist_ok=True)
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file() and not p.is_relative_to(out / "runtime")
               and not p.is_relative_to(out / "models"))
    print(f"便携版：{out}（程序 {size / 1e6:.0f} MB，不含 runtime 和 models）")


def build_macos(args: argparse.Namespace) -> None:
    if platform.machine() != "arm64":
        sys.exit("macOS 版只支持 Apple 芯片：要在 Apple 芯片的 Mac 上构建")
    out: Path = (args.out or ROOT / "dist" / "Bapu.app").resolve()
    if out.suffix != ".app":
        sys.exit("macOS 上 --out 要以 .app 结尾")
    npm = shutil.which("npm") or "npm"
    if not args.skip_build:
        run([npm, "--prefix", str(ROOT / "web"), "run", "build"])
        run([npm, "--prefix", str(ROOT / "desktop"), "run", "bundle", "--", "--bundles", "app"])  # tauri build
    built = ROOT / "desktop" / "src-tauri" / "target" / "release" / "bundle" / "macos" / "Bapu.app"
    uv = shutil.which("uv")
    if not built.is_dir() or not (ROOT / "web" / "dist" / "index.html").exists() or not uv:
        sys.exit("缺 Bapu.app、web/dist 或 uv")
    archs = run(["lipo", "-archs", uv], capture=True).split()
    if "arm64" not in archs:
        sys.exit(f"{uv} 不是 Apple 芯片（arm64）的 uv：{' '.join(archs) or '?'}")

    # the bundle is rebuilt as a whole: nothing of the user's is ever in it
    if out.exists():
        shutil.rmtree(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(built, out, symlinks=True)
    res = out / "Contents" / "Resources"
    fill_app(res / "app", "darwin")
    tools = res / "tools"
    tools.mkdir(exist_ok=True)
    shutil.copy2(uv, tools / "uv")
    os.chmod(tools / "uv", 0o755)

    # ad-hoc signatures (no Apple identity): uv on its own, then the whole bundle, last: anything
    # changed in the bundle after this breaks its seal
    run(["xattr", "-cr", str(out)])
    run(["codesign", "--force", "--sign", "-", str(tools / "uv")])
    run(["codesign", "--force", "--deep", "--sign", "-", str(out)])
    run(["codesign", "--verify", "--deep", "--strict", str(out)])

    # the disk image: Bapu.app next to a link to /Applications (drag one onto the other) and the README
    dmg = out.parent / f"Bapu-mac-{version()}.dmg"
    dmg.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "Bapu"
        stage.mkdir()
        run(["ditto", str(out), str(stage / "Bapu.app")])  # keeps the signature, links and attributes
        (stage / "Applications").symlink_to("/Applications")
        (stage / "README.txt").write_bytes(README_MAC.encode("utf-8-sig"))  # BOM: Quick Look shows the Chinese right
        run(["hdiutil", "create", "-volname", "Bapu", "-srcfolder", str(stage), "-fs", "HFS+", "-format", "UDZO",
             "-ov", str(dmg)])
    run(["hdiutil", "verify", str(dmg)])
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"macOS 版：{out}（{size / 1e6:.0f} MB）；安装盘：{dmg}（{dmg.stat().st_size / 1e6:.0f} MB）")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, help="输出位置（Windows 默认 dist/Bapu；macOS 默认 dist/Bapu.app，压缩包放在它旁边）")
    ap.add_argument("--skip-build", action="store_true", help="用已经构建好的网页和 Bapu.exe（macOS：Bapu.app）")
    args = ap.parse_args()
    if sys.platform == "win32":
        build_windows(args)
    elif sys.platform == "darwin":
        build_macos(args)
    else:
        sys.exit("便携版只能在 Windows 或 macOS 上构建")


if __name__ == "__main__":
    main()
