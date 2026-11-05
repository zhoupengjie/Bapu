"""Make a signed Bapu release (portable app) and, with --publish, put it on GitHub.

    uv run python scripts/release.py --notes "这次改了什么"            # build + sign into dist/release/
    uv run python scripts/release.py --notes "..." --publish          # also: gh release create v<version>

The release has two files:
  Bapu-app-<version>.zip   Bapu.exe, app/, tools/, README.txt (also the download for new users:
                           unzip anywhere, run Bapu.exe; it sets up Python and the models itself)
  latest.json              version, notes, zip url / size / SHA-256, and an Ed25519 signature over
                           "bapu-update:<version>:<sha256>"

The signing key is read from $BAPU_SIGNING_KEY_FILE (default ~/.bapu/release-signing-key, hex) and
never leaves this machine; its public half is built into Bapu.exe (desktop/src-tauri/src/updater.rs).
For a local test: --base-url http://127.0.0.1:8000 --version 0.2.1 (patches the packaged version).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "zhoupengjie/Bapu"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))


def sign(message: bytes) -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key_file = Path(os.environ.get("BAPU_SIGNING_KEY_FILE", Path.home() / ".bapu" / "release-signing-key"))
    if not key_file.exists():
        sys.exit(f"没有签名私钥：{key_file}")
    sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(key_file.read_text().strip()))
    return sk.sign(message).hex()


def built_in_public_key() -> str:
    text = (ROOT / "desktop" / "src-tauri" / "src" / "updater.rs").read_text(encoding="utf-8")
    return re.search(r'PUBLIC_KEY: &str = "([0-9a-f]{64})"', text).group(1)


def check_key_pair() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key_file = Path(os.environ.get("BAPU_SIGNING_KEY_FILE", Path.home() / ".bapu" / "release-signing-key"))
    sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(key_file.read_text().strip()))
    pk = sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    if pk != built_in_public_key():
        sys.exit("签名私钥和 Bapu.exe 里内置的公钥对不上")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--notes", default="", help="更新说明（会显示在“环境”页）")
    ap.add_argument("--publish", action="store_true", help="用 gh 在 GitHub 上建发布")
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--base-url", help="本地测试：zip 的下载地址前缀（默认 GitHub 发布页）")
    ap.add_argument("--version", help="本地测试：改写打包进去的版本号")
    ap.add_argument("--out", type=Path, default=ROOT / "dist" / "release")
    args = ap.parse_args()
    check_key_pair()

    import build_portable

    from bapu import __version__

    version = args.version or __version__
    stage = ROOT / "dist" / "Bapu"
    sys.argv = ["build_portable", "--out", str(stage)] + (["--skip-build"] if args.skip_build else [])
    build_portable.main()

    args.out.mkdir(parents=True, exist_ok=True)
    zname = f"Bapu-app-{version}.zip"
    zpath = args.out / zname
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for item in ("Bapu.exe", "README.txt"):
            z.write(stage / item, item)
        for folder in ("app", "tools"):
            for f in sorted((stage / folder).rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts:
                    rel = f.relative_to(stage).as_posix()
                    if args.version and rel == "app/src/bapu/__init__.py":  # local test only
                        text = re.sub(r'__version__ = "[^"]+"', f'__version__ = "{version}"', f.read_text(encoding="utf-8"))
                        z.writestr(rel, text)
                    else:
                        z.write(f, rel)
    sha = hashlib.sha256(zpath.read_bytes()).hexdigest()
    base = (args.base_url or f"https://github.com/{REPO}/releases/download/v{version}").rstrip("/")
    latest = {
        "version": version,
        "date": datetime.date.today().isoformat(),
        "notes": args.notes,
        "zip": {"name": zname, "url": f"{base}/{zname}", "size": zpath.stat().st_size, "sha256": sha},
        "signature": sign(f"bapu-update:{version}:{sha}".encode()),
    }
    (args.out / "latest.json").write_text(json.dumps(latest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{zname}: {zpath.stat().st_size / 1e6:.1f} MB, sha256 {sha[:12]}…, 已签名")

    if args.publish:
        tag = f"v{version}"
        subprocess.run(["gh", "release", "create", tag, str(zpath), str(args.out / "latest.json"), "-R", REPO,
                        "--title", f"Bapu {version}", "--notes", args.notes or f"Bapu {version}"], check=True)
        print(f"已发布：https://github.com/{REPO}/releases/tag/{tag}")


if __name__ == "__main__":
    main()
