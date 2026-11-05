"""Bapu project files.

A project is a folder the user chooses (by default next to the song):

    <folder>/
      <name>.bapu        the project file the user double-clicks (small JSON)
      <name>.svp, .mid   exports
      .bapu/             everything else (a normal folder): copied inputs, corrections,
                         manifest, every stage's output, score and review

Paths in the `.bapu` file are relative to the folder when they are inside it,
so a project can be moved or copied as a whole. Older layouts (stage folders
directly in the project folder, a `job.json`, a bare `bapu run` work dir) are
moved into `.bapu/` the first time the project is opened.

The index (`projects.json`) remembers where projects are, for the recent list
and for the short ids used in URLs. It holds paths only, never lyric text.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

from bapu.workdir import write_json_atomic

EXT = ".bapu"
DATA = ".bapu"  # the data folder inside the project folder
FORMAT = "bapu-project"
VERSION = 2  # 2: intermediate files in .bapu/
LEGACY = "job.json"  # before .bapu files (v0.1 M4–M5)
EXPORTS = "exports.json"  # in .bapu/: the exports Bapu wrote (never overwrite anything else)

# what lived directly in the project folder before .bapu/ (layout 1)
_STATE_FILES = ("manifest.json", "params.json", "corrections.yaml", "score.json", "review.json", "review.md")
_STAGE_DIR = re.compile(r"^\d\d_[a-z_]+$")


class ProjectError(ValueError):
    pass


def safe_name(name: str) -> str:
    """A file name Windows accepts, close to what the user typed."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().rstrip(".")
    return s[:120] or "song"


def project_file(folder: Path) -> Path | None:
    files = sorted(p for p in Path(folder).glob(f"*{EXT}") if p.is_file())
    return files[0] if files else None


def _legacy_markers(folder: Path) -> bool:
    folder = Path(folder)
    return (folder / LEGACY).exists() or (folder / "manifest.json").exists()


def is_project(folder: Path) -> bool:
    folder = Path(folder)
    return project_file(folder) is not None or (folder / DATA).is_dir() or _legacy_markers(folder)


def data_dir(folder: Path) -> Path:
    """Where the manifest and stage outputs are: `.bapu/`, or the folder itself
    for a layout-1 folder that has not been migrated yet."""
    folder = Path(folder)
    if (folder / DATA).is_dir() or not _legacy_markers(folder):
        return folder / DATA
    return folder


def folder_of(data: Path) -> Path:
    """The project folder for a data folder (or a layout-1 folder itself)."""
    data = Path(data)
    return data.parent if data.name == DATA else data


def make_data_dir(folder: Path) -> Path:
    d = Path(folder) / DATA
    d.mkdir(parents=True, exist_ok=True)
    unhide(d)
    return d


def unhide(path: Path) -> None:
    """The data folder is a normal, visible folder (the user asked for it; earlier builds hid it)."""
    if sys.platform == "win32":
        import ctypes

        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if attrs != -1 and attrs & 0x2:
            ctypes.windll.kernel32.SetFileAttributesW(str(path), attrs & ~0x2)  # clear FILE_ATTRIBUTE_HIDDEN


def _rel(folder: Path, p: Path) -> str:
    p = Path(p).resolve()
    try:
        return p.relative_to(Path(folder).resolve()).as_posix()
    except ValueError:
        return str(p)


def read(folder: Path) -> dict[str, Any]:
    """The project's metadata with absolute source paths. Older folders (a
    `job.json`, or only a manifest from the CLI) are read the same way."""
    folder = Path(folder)
    f = project_file(folder)
    if f is not None:
        meta = json.loads(f.read_text(encoding="utf-8"))
        if meta.get("format") != FORMAT:
            raise ProjectError(f"不是 Bapu 工程文件：{f.name}")
        if int(meta.get("version", 0)) > VERSION:
            raise ProjectError(f"{f.name} 是更新版本的 Bapu 做的，请先升级 Bapu")
    elif (folder / LEGACY).exists():
        meta = json.loads((folder / LEGACY).read_text(encoding="utf-8"))
    else:  # a work dir from `bapu run`: named after its song
        meta = {"name": folder.name}
        manifest = data_dir(folder) / "manifest.json"
        if manifest.exists():
            srcs = json.loads(manifest.read_text(encoding="utf-8")).get("sources") or {}
            meta["sources"] = {n: s["path"] for n, s in srcs.items()}
            if "source" in meta["sources"]:
                meta["name"] = Path(meta["sources"]["source"]).stem
        inputs = data_dir(folder) / "inputs"
        if not meta.get("sources") and inputs.is_dir():  # never run: the copied inputs tell what it was
            files = sorted(f for f in inputs.iterdir() if f.is_file())
            lyrics = [f for f in files if f.suffix.lower() in (".ass", ".ssa", ".lrc", ".txt")]
            notes = [f for f in files if f.name.endswith(".notes.json")]
            audio = [f for f in files if f not in lyrics and f.suffix.lower() not in (".json", ".svp", ".mid", ".midi")]
            meta["sources"] = {k: str(v[0]) for k, v in (("source", audio), ("lyrics", lyrics), ("notes", notes)) if v}
            if audio:
                meta["name"] = audio[0].stem
    meta["sources"] = {n: str((folder / p).resolve()) for n, p in (meta.get("sources") or {}).items()}
    meta.setdefault("name", folder.name)
    meta["mode"] = meta.get("mode") or "standard"  # standard | separate | notes | lyrics (see bapu.service)
    return meta


def write(folder: Path, meta: dict[str, Any]) -> Path:
    """Write `<name>.bapu` (replacing an older project file or job.json)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    old = project_file(folder)
    dest = old or folder / f"{safe_name(meta.get('name') or folder.name)}{EXT}"
    data = {
        "format": FORMAT, "version": VERSION, "name": meta.get("name") or folder.name,
        "created_at": meta.get("created_at", time.time()),
        "mode": meta.get("mode") or "standard",
        "sources": {n: _rel(folder, Path(p)) for n, p in (meta.get("sources") or {}).items()},
    }
    write_json_atomic(dest, data)
    if (folder / LEGACY).exists():
        (folder / LEGACY).unlink()
    return dest


def migrate(folder: Path) -> bool:
    """Bring a project folder to the current layout: a `.bapu` file, and every
    intermediate file in `.bapu/`. Returns True when something was moved."""
    folder = Path(folder)
    meta = read(folder)
    moved = False
    if (folder / DATA).is_dir():
        unhide(folder / DATA)
    if not (folder / DATA).is_dir() and _legacy_markers(folder):
        data = make_data_dir(folder)
        names = [n for n in _STATE_FILES if (folder / n).exists()]
        names += [d.name for d in folder.iterdir() if d.is_dir() and (_STAGE_DIR.match(d.name) or d.name == "inputs")]
        for n in names:
            shutil.move(str(folder / n), str(data / n))
        # inputs moved with the rest: point the project file at their new place
        for k, p in list(meta["sources"].items()):
            rel = _rel(folder, Path(p))
            if not Path(rel).is_absolute() and rel.split("/")[0] == "inputs":
                meta["sources"][k] = str(data / rel)
        # old exports: next to the .bapu file when the name is free
        old = folder / "export"
        if old.is_dir():
            records = load_exports(data)
            for f in old.iterdir():
                if f.is_file() and not (folder / f.name).exists():
                    shutil.move(str(f), str(folder / f.name))
                    records[f.name] = _stamp(folder / f.name)
            save_exports(data, records)
            if not any(old.iterdir()):
                old.rmdir()
        moved = True
    if moved or project_file(folder) is None or json.loads(project_file(folder).read_text(encoding="utf-8")).get("version") != VERSION:
        write(folder, meta)
    return moved


def ensure(folder: Path) -> Path:
    """Give an older project folder its `.bapu` file and the current layout."""
    migrate(folder)
    return project_file(folder)  # type: ignore[return-value]


def leftover(folder: Path) -> bool:
    """`.bapu/` without a project file: the user moved or deleted the .bapu file."""
    folder = Path(folder)
    return project_file(folder) is None and not _legacy_markers(folder) and (folder / DATA).is_dir()


def take_over_leftover(folder: Path, primary: Path) -> str:
    """Before a new project goes into a folder with leftover `.bapu/`: keep it
    when it was made from the same file (the song or the dry lead: its finished
    steps stay valid), else set it aside as `.bapu-old-<time>/` (nothing is
    deleted). Returns "reused" or "set_aside"."""
    from bapu.audio import sha256_file

    folder = Path(folder)
    data = folder / DATA
    manifest = data / "manifest.json"
    same = False
    if manifest.exists():
        try:
            srcs = json.loads(_retry(lambda: manifest.read_text(encoding="utf-8"))).get("sources") or {}
            sha = sha256_file(Path(primary))
            same = any(isinstance(s, dict) and s.get("sha256") == sha for s in srcs.values())
        except (ValueError, OSError):
            same = False
    if same:
        return "reused"
    _retry(lambda: data.rename(folder / f"{DATA}-old-{time.strftime('%Y%m%d-%H%M%S')}"))
    return "set_aside"


def _retry(fn, attempts: int = 3, wait: float = 0.1):
    """Windows: antivirus or the search indexer may hold a just-written file for a moment."""
    for k in range(attempts):
        try:
            return fn()
        except PermissionError:
            if k == attempts - 1:
                raise
            time.sleep(wait)


def copy_input(folder: Path, src: Path) -> Path:
    """Copy an input into `<folder>/.bapu/inputs/` (unless it is already inside the project)."""
    src = Path(src).resolve()
    folder = Path(folder).resolve()
    try:
        src.relative_to(folder)
        return src
    except ValueError:
        pass
    dest = make_data_dir(folder) / "inputs" / src.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists() or dest.stat().st_size != src.stat().st_size:
        shutil.copy2(src, dest)
    return dest


# -- exports: next to the .bapu file, never over a file Bapu did not write -------------------
def _stamp(p: Path) -> dict[str, Any]:
    st = p.stat()
    return {"size": st.st_size, "mtime": st.st_mtime}


def load_exports(data: Path) -> dict[str, dict[str, Any]]:
    p = Path(data) / EXPORTS
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except ValueError:
        return {}


def save_exports(data: Path, records: dict[str, dict[str, Any]]) -> None:
    write_json_atomic(Path(data) / EXPORTS, records)


def export_target(folder: Path, data: Path, name: str) -> Path:
    """Where to write export `name`: the name itself if it is free or an
    unchanged earlier export of ours; else `name (2)`, `name (3)`, ... (so a
    project the user edited in SV and saved under that name is kept)."""
    records = load_exports(data)
    stem, ext = Path(name).stem, Path(name).suffix
    k = 1
    while True:
        cand = folder / (name if k == 1 else f"{stem} ({k}){ext}")
        if not cand.exists() or records.get(cand.name) == _stamp(cand):
            return cand
        k += 1


def record_export(data: Path, path: Path) -> None:
    records = load_exports(data)
    records[Path(path).name] = _stamp(Path(path))
    save_exports(data, records)


def exports(folder: Path, data: Path) -> list[Path]:
    """Exports Bapu wrote that are still there, newest first."""
    out = [Path(folder) / n for n in load_exports(data) if (Path(folder) / n).is_file()]
    return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)


class ProjectIndex:
    """id -> folder for projects opened or created here, most recent first."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            return list(json.loads(self.path.read_text(encoding="utf-8")).get("projects", []))
        except (ValueError, OSError):
            return []

    def _save(self, items: list[dict[str, Any]]) -> None:
        write_json_atomic(self.path, {"projects": items})

    def items(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted(self._load(), key=lambda e: e.get("opened_at", 0), reverse=True)

    def get(self, pid: str) -> Path | None:
        for e in self.items():
            if e["id"] == pid:
                return Path(e["path"])
        return None

    def find(self, folder: Path) -> str | None:
        key = str(Path(folder).resolve()).lower()
        for e in self.items():
            if str(Path(e["path"]).resolve()).lower() == key:
                return e["id"]
        return None

    def add(self, pid: str, folder: Path) -> None:
        with self._lock:
            items = [e for e in self._load() if e["id"] != pid]
            items.append({"id": pid, "path": str(Path(folder).resolve()), "opened_at": time.time()})
            self._save(items)

    def touch(self, pid: str) -> None:
        with self._lock:
            items = self._load()
            for e in items:
                if e["id"] == pid:
                    e["opened_at"] = time.time()
            self._save(items)

    def remove(self, pid: str) -> None:
        with self._lock:
            self._save([e for e in self._load() if e["id"] != pid])
