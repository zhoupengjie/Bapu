"""Per-song work directory: stage folders, `manifest.json`, cache keys.

Layout::

    <root>/
      manifest.json      # what ran, with which key, outputs and metrics
      params.json        # parameter overrides that persist across runs
      01_separate/ 02_lead/ ...

A stage's cache key covers its input fingerprints, its parameters (model IDs
included) and its code version. If the key and all recorded outputs are still
there, the stage is skipped.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_VERSION = 1


def cache_key(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class StageRecord:
    key: str
    dir: str
    outputs: dict[str, str]  # artifact name -> path relative to the work dir
    metrics: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    version: str = ""
    seconds: float = 0.0
    finished_at: float = field(default_factory=time.time)


class WorkDir:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.manifest_path = self.root / "manifest.json"
        self.params_path = self.root / "params.json"

    # -- manifest ---------------------------------------------------------
    def load(self) -> dict[str, Any]:
        if self.manifest_path.exists():
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if data.get("version") == MANIFEST_VERSION:
                return data
        return {"version": MANIFEST_VERSION, "source": None, "stages": {}}

    def save(self, data: dict[str, Any]) -> None:
        write_json_atomic(self.manifest_path, data)

    def record(self, name: str, rec: StageRecord) -> None:
        data = self.load()
        data["stages"][name] = asdict(rec)
        self.save(data)

    def set_source(self, path: Path, sha256: str) -> None:
        data = self.load()
        data["source"] = {"path": str(Path(path).resolve()), "sha256": sha256}
        self.save(data)

    def get(self, name: str) -> StageRecord | None:
        raw = self.load()["stages"].get(name)
        return StageRecord(**raw) if raw else None

    def fresh(self, name: str, key: str) -> StageRecord | None:
        """The stored record if it matches `key` and its outputs still exist."""
        rec = self.get(name)
        if rec is None or rec.key != key:
            return None
        if not all((self.root / p).exists() for p in rec.outputs.values()):
            return None
        return rec

    # -- persisted parameter overrides --------------------------------------
    def load_params(self) -> dict[str, dict[str, Any]]:
        if self.params_path.exists():
            return json.loads(self.params_path.read_text(encoding="utf-8"))
        return {}

    def save_params(self, params: dict[str, dict[str, Any]]) -> None:
        write_json_atomic(self.params_path, params)

    # -- folders ------------------------------------------------------------
    def stage_dir(self, index: int, name: str) -> Path:
        d = self.root / f"{index:02d}_{name}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def rel(self, path: Path) -> str:
        return Path(os.path.relpath(Path(path).resolve(), self.root.resolve())).as_posix()
