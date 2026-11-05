"""Model registry: what can be downloaded, from where, under which license.

Only models in `registry.toml` (or a file passed explicitly) can be pulled.
Every file is checked against its SHA-256 before it is used; verified hashes are
remembered by size and mtime so large weights are not rehashed on every run.
Downloading requires accepting the model's license, and the acceptance is
recorded in `<cache>/models/state.json`.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import tomllib
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from importlib.resources import files as pkg_files
from pathlib import Path
from typing import Callable, Literal

from bapu import __version__, paths
from bapu.audio import sha256_file

Status = Literal["ready", "missing", "corrupt", "builtin"]
DownloadProgress = Callable[[str, int, int | None], None]  # file name, bytes done, total

USER_AGENT = f"bapu/{__version__}"


class RegistryError(Exception):
    pass


class LicenseNotAccepted(RegistryError):
    pass


class ModelNotReady(RegistryError):
    def __init__(self, spec: "ModelSpec", status: Status) -> None:
        self.spec = spec
        self.status = status
        super().__init__(f"模型 {spec.id} 不可用（{status}）：先运行 `bapu models pull {spec.id}`")


@dataclass(frozen=True)
class License:
    name: str
    url: str = ""
    commercial: Literal["yes", "no", "unknown"] = "unknown"
    note: str = ""


@dataclass(frozen=True)
class FileSpec:
    name: str
    sha256: str
    size: int | None = None
    urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class Archive:
    url: str
    size: int | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class ModelSpec:
    id: str
    stage: str
    name: str
    backend: str
    license: License
    verified: bool = False
    dir: str = ""
    entry: str = ""
    files: tuple[FileSpec, ...] = ()
    archive: Archive | None = None

    @property
    def needs_files(self) -> bool:
        return bool(self.files)

    @property
    def total_size(self) -> int | None:
        if self.archive and self.archive.size:
            return self.archive.size
        sizes = [f.size for f in self.files]
        return sum(sizes) if sizes and all(s is not None for s in sizes) else None


def _parse(raw: dict) -> ModelSpec:
    lic = License(**raw.get("license", {"name": "unknown"}))
    files = tuple(
        FileSpec(name=f["name"], sha256=f["sha256"].lower(), size=f.get("size"), urls=tuple(f.get("urls", ())))
        for f in raw.get("files", ())
    )
    arch = Archive(**raw["archive"]) if "archive" in raw else None
    if files and not raw.get("dir"):
        raise RegistryError(f"{raw['id']}: files need a dir")
    for f in files:
        if not f.urls and arch is None:
            raise RegistryError(f"{raw['id']}/{f.name}: no urls and no archive")
    return ModelSpec(
        id=raw["id"], stage=raw["stage"], name=raw["name"], backend=raw["backend"], license=lic,
        verified=bool(raw.get("verified", False)), dir=raw.get("dir", ""), entry=raw.get("entry", ""),
        files=files, archive=arch,
    )


@dataclass
class Registry:
    models: dict[str, ModelSpec]
    cache_root: Path = field(default_factory=paths.cache_dir)
    # state.json is shared by API requests and download threads
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    # -- loading ------------------------------------------------------------
    @classmethod
    def load(cls, path: Path | None = None, cache_root: Path | None = None) -> "Registry":
        text = Path(path).read_text(encoding="utf-8") if path else (
            pkg_files("bapu.models").joinpath("registry.toml").read_text(encoding="utf-8")
        )
        specs = [_parse(m) for m in tomllib.loads(text).get("model", [])]
        ids = [s.id for s in specs]
        if len(ids) != len(set(ids)):
            raise RegistryError("duplicate model id in registry")
        return cls({s.id: s for s in specs}, Path(cache_root) if cache_root else paths.cache_dir())

    def get(self, model_id: str) -> ModelSpec:
        try:
            return self.models[model_id]
        except KeyError:
            raise RegistryError(f"注册表里没有模型 {model_id}") from None

    def for_stage(self, stage: str) -> list[ModelSpec]:
        return [m for m in self.models.values() if m.stage == stage]

    # -- paths and state ------------------------------------------------------
    def model_dir(self, spec: ModelSpec) -> Path:
        return self.cache_root / spec.dir

    def entry_path(self, spec: ModelSpec) -> Path:
        return self.model_dir(spec) / spec.entry

    @property
    def state_path(self) -> Path:
        return self.cache_root / "models" / "state.json"

    def _state(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return {"accepted": {}, "verified": {}}

    def _save_state(self, state: dict) -> None:
        with self._lock:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.state_path)

    def license_accepted(self, spec: ModelSpec) -> bool:
        return spec.id in self._state()["accepted"]

    def accept_license(self, spec: ModelSpec) -> None:
        with self._lock:
            state = self._state()
            state["accepted"][spec.id] = {"license": spec.license.name, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            self._save_state(state)

    # -- verification ---------------------------------------------------------
    def _file_ok(self, path: Path, f: FileSpec, state: dict) -> bool | None:
        """True = matches, False = present but wrong, None = missing."""
        if not path.exists():
            return None
        st = path.stat()
        if f.size is not None and st.st_size != f.size:
            return False
        key = str(path.resolve())
        seen = state["verified"].get(key)
        if seen and seen["size"] == st.st_size and seen["mtime_ns"] == st.st_mtime_ns and seen["sha256"] == f.sha256:
            return True
        ok = sha256_file(path) == f.sha256
        if ok:
            state["verified"][key] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": f.sha256}
        return ok

    def status(self, spec: ModelSpec) -> Status:
        if not spec.needs_files:
            return "builtin"
        with self._lock:
            state = self._state()
            before = json.dumps(state["verified"], sort_keys=True)
            results = [self._file_ok(self.model_dir(spec) / f.name, f, state) for f in spec.files]
            if json.dumps(state["verified"], sort_keys=True) != before:
                self._save_state(state)
        if any(r is False for r in results):
            return "corrupt"
        if any(r is None for r in results):
            return "missing"
        return "ready"

    def ensure_ready(self, spec: ModelSpec) -> Path | None:
        """Entry path of a ready model; raises ModelNotReady otherwise."""
        st = self.status(spec)
        if st == "builtin":
            return None
        if st != "ready":
            raise ModelNotReady(spec, st)
        return self.entry_path(spec)

    # -- download ---------------------------------------------------------------
    def pull(self, spec: ModelSpec, *, accept_license: bool = False, progress: DownloadProgress | None = None) -> Path | None:
        if not spec.needs_files:
            return None
        if not (accept_license or self.license_accepted(spec)):
            raise LicenseNotAccepted(f"下载 {spec.id} 前需要接受许可证：{spec.license.name}")
        if accept_license:
            self.accept_license(spec)
        if self.status(spec) == "ready":
            return self.entry_path(spec)

        dest_dir = self.model_dir(spec)
        dest_dir.mkdir(parents=True, exist_ok=True)
        state = self._state()
        if spec.archive is not None:
            self._pull_archive(spec, dest_dir, progress)
        else:
            for f in spec.files:
                target = dest_dir / f.name
                if self._file_ok(target, f, state):
                    continue
                self._download_file(f, target, progress)
        st = self.status(spec)
        if st != "ready":
            raise RegistryError(f"{spec.id}: 下载后校验失败（{st}）")
        return self.entry_path(spec)

    def _download_file(self, f: FileSpec, target: Path, progress: DownloadProgress | None) -> None:
        errors = []
        for url in f.urls:
            part = target.with_name(target.name + ".part")
            try:
                _fetch(url, part, f.size, lambda done, total: progress and progress(f.name, done, total))
            except (urllib.error.URLError, OSError) as e:
                errors.append(f"{url}: {e}")
                continue
            if sha256_file(part) != f.sha256 or (f.size is not None and part.stat().st_size != f.size):
                part.unlink(missing_ok=True)
                errors.append(f"{url}: SHA-256 或大小不符")
                continue
            os.replace(part, target)
            return
        raise RegistryError(f"{f.name}: 所有地址都下载失败\n" + "\n".join(errors))

    def _pull_archive(self, spec: ModelSpec, dest_dir: Path, progress: DownloadProgress | None) -> None:
        arch = spec.archive
        assert arch is not None
        dl_dir = self.cache_root / "models" / ".downloads"  # partial downloads, next to the models
        dl_dir.mkdir(parents=True, exist_ok=True)
        zip_name = arch.url.rsplit("/", 1)[-1]
        part = dl_dir / (zip_name + ".part")
        _fetch(arch.url, part, arch.size, lambda done, total: progress and progress(zip_name, done, total))
        if arch.sha256 and sha256_file(part) != arch.sha256.lower():
            part.unlink(missing_ok=True)
            raise RegistryError(f"{zip_name}: SHA-256 不符")
        with tempfile.TemporaryDirectory(dir=dl_dir) as tmp:
            tmp_path = Path(tmp)
            with zipfile.ZipFile(part) as zf:
                for info in zf.infolist():
                    # Refuse absolute paths and `..` so extraction stays inside tmp.
                    p = Path(info.filename)
                    if p.is_absolute() or ".." in p.parts:
                        raise RegistryError(f"{zip_name}: 不安全的路径 {info.filename}")
                zf.extractall(tmp_path)
            for f in spec.files:
                found = [p for p in tmp_path.rglob(f.name) if p.is_file()]
                if len(found) != 1:
                    raise RegistryError(f"{zip_name}: 找到 {len(found)} 个 {f.name}")
                if sha256_file(found[0]) != f.sha256:
                    raise RegistryError(f"{zip_name}: {f.name} 的 SHA-256 不符")
                shutil.move(str(found[0]), dest_dir / f.name)
        part.unlink(missing_ok=True)

    def download_info(self, spec: ModelSpec) -> dict:
        """Everything needed to fetch a model by hand: where each file comes
        from, where it must go, and the checksum it must match."""
        folder = self.model_dir(spec).resolve() if spec.needs_files else None
        files = []
        for f in spec.files:
            path = folder / f.name if folder else None
            files.append({"name": f.name, "size": f.size, "sha256": f.sha256, "urls": list(f.urls),
                          "path": str(path) if path else None, "present": bool(path and path.exists())})
        info = {"dir": str(folder) if folder else None, "files": files, "archive": None}
        if spec.archive is not None:
            info["archive"] = {"url": spec.archive.url, "size": spec.archive.size,
                               "name": spec.archive.url.rsplit("/", 1)[-1]}
        return info

    def remove(self, spec: ModelSpec) -> int:
        """Delete the model's files; returns bytes freed."""
        freed = 0
        for f in spec.files:
            p = self.model_dir(spec) / f.name
            if p.exists():
                freed += p.stat().st_size
                p.unlink()
        return freed


def _fetch(url: str, part: Path, size: int | None, progress: Callable[[int, int | None], object]) -> None:
    """Download `url` into `part`, resuming when the server honours Range."""
    have = part.stat().st_size if part.exists() else 0
    if size is not None and have >= size:
        if have > size:
            part.unlink()
            have = 0
        else:
            return
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if have and url.startswith(("http://", "https://")):
        req.add_header("Range", f"bytes={have}-")
    with urllib.request.urlopen(req, timeout=60) as resp:
        status = getattr(resp, "status", 200)
        mode = "ab" if have and status == 206 else "wb"
        done = have if mode == "ab" else 0
        length = resp.headers.get("Content-Length")
        total = size if size is not None else (done + int(length) if length else None)
        with open(part, mode) as out:
            while block := resp.read(1 << 20):
                out.write(block)
                done += len(block)
                progress(done, total)
