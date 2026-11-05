"""Build the portable Bapu folder.

    uv run python scripts/build_portable.py --out "D:/Program Files/Bapu"

Result (about 40 MB; Python, packages and models are not included):

    Bapu/
      Bapu.exe              desktop shell (Tauri)
      tools/uv.exe          sets up the Python environment on first launch
      app/src/bapu/         Bapu's Python code
      app/web/              the web UI
      app/requirements.txt  every package except torch, pinned from uv.lock
      app/torch.json        the PyTorch builds to choose from (CUDA / Intel XPU / CPU)
      README.txt

`runtime/`, `models/` and `data/` in an existing output folder are kept, so
rebuilding updates the program without reinstalling Python or the models.
"""

from __future__ import annotations

import argparse
import filecmp
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TORCH = ("torch", "torchaudio", "torchvision")

VARIANTS = {
    "cuda": {"label": "NVIDIA 显卡（CUDA 12.8）", "detail": "RTX 20 系及以后，驱动 570 以上；已在 RTX 5090 上验证",
             "index": "https://download.pytorch.org/whl/cu128", "deps": False, "size_gb": 3.8, "experimental": False},
    "xpu": {"label": "Intel Arc 显卡（XPU）", "detail": "还没在 Intel 显卡上测过；人声分离那两步会用 CPU",
            "index": "https://download.pytorch.org/whl/xpu", "deps": True, "size_gb": 2.6, "experimental": True},
    "cpu": {"label": "只用 CPU", "detail": "任何电脑都能用，但处理一首歌会慢很多",
            "index": "https://download.pytorch.org/whl/cpu", "deps": False, "size_gb": 1.1, "experimental": False},
}

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


def run(cmd: list[str], **kw) -> str:
    print("$", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=True, text=True, encoding="utf-8", capture_output=kw.pop("capture", False), **kw).stdout or ""


def torch_pins() -> list[str]:
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    pins = []
    for name in TORCH:
        m = re.search(rf'\[\[package\]\]\nname = "{name}"\nversion = "([^"+]+)', lock)
        if not m:
            sys.exit(f"uv.lock 里没有 {name}")
        pins.append(f"{name}=={m.group(1)}")
    return pins


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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "dist" / "Bapu")
    ap.add_argument("--skip-build", action="store_true", help="用已经构建好的网页和 Bapu.exe")
    args = ap.parse_args()
    out: Path = args.out

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
    app = out / "app"
    app.mkdir(exist_ok=True)
    replace_dir(ROOT / "src" / "bapu", app / "src" / "bapu")
    replace_dir(ROOT / "web" / "dist", app / "web")
    (app / "requirements.txt").write_text(requirements(), encoding="utf-8")
    (app / "torch.json").write_text(json.dumps({"python": "3.12", "packages": torch_pins(), "variants": VARIANTS},
                                               ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "README.txt").write_text(README, encoding="utf-8-sig")
    for d in ("models", "data"):
        (out / d).mkdir(exist_ok=True)
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file() and not p.is_relative_to(out / "runtime")
               and not p.is_relative_to(out / "models"))
    print(f"便携版：{out}（程序 {size / 1e6:.0f} MB，不含 runtime 和 models）")


if __name__ == "__main__":
    main()
