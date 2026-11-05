"""Job service behind the API: a queue, one worker thread (one GPU), and an
event hub that fans progress out to listeners.

A job is one project folder (see bapu.project): anywhere on disk, found through
the recent-projects index, or directly under `root` for older jobs. Jobs
survive restarts because everything lives on disk (`<name>.bapu`, `manifest.json`,
stage folders); only queue state and the live event log are in memory.
Events carry stage names, fractions and short messages, never lyric text.
"""

from __future__ import annotations

import asyncio
import json
import queue
import re
import shutil
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from bapu import corrections as corr
from bapu import jobs, pipeline, project, report
from bapu.monitor import LoadMonitor
from bapu.models.registry import ModelSpec, Registry, RegistryError
from bapu.progress import ProgressEvent
from bapu.stages import STAGES
from bapu.stages.base import Stage
from bapu.workdir import WorkDir

JOB_ID = re.compile(r"^[\w][\w\-.]{0,99}$")


class ServiceError(Exception):
    status = 400


class NotFound(ServiceError):
    status = 404


class Conflict(ServiceError):
    status = 409

    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__(message)
        self.detail = detail


class Cancelled(Exception):
    pass


# -- events ---------------------------------------------------------------------
class EventHub:
    """Per-topic event log with async listeners (thread-safe publish)."""

    def __init__(self, keep: int = 500) -> None:
        self._lock = threading.Lock()
        self._logs: dict[str, deque[dict[str, Any]]] = {}
        self._seq: dict[str, int] = {}
        self._listeners: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = {}
        self._keep = keep

    def publish(self, topic: str, event: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            seq = self._seq.get(topic, 0) + 1
            self._seq[topic] = seq
            ev = {"seq": seq, "at": time.time(), **event}
            self._logs.setdefault(topic, deque(maxlen=self._keep)).append(ev)
            listeners = list(self._listeners.get(topic, []))
        for loop, q in listeners:
            try:
                loop.call_soon_threadsafe(q.put_nowait, ev)
            except RuntimeError:  # loop closed
                pass
        return ev

    def history(self, topic: str, after: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return [e for e in self._logs.get(topic, []) if e["seq"] > after]

    def subscribe(self, topic: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._listeners.setdefault(topic, []).append((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, topic: str, q: asyncio.Queue) -> None:
        with self._lock:
            self._listeners[topic] = [x for x in self._listeners.get(topic, []) if x[1] is not q]


# -- jobs -----------------------------------------------------------------------
@dataclass
class RunRequest:
    from_stage: str | None = None
    until: str | None = None
    overrides: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class JobState:
    state: str = "idle"  # idle | queued | running | done | failed | cancelled
    stage: str | None = None
    fraction: float | None = None
    error: str | None = None
    queued_at: float | None = None
    started_at: float | None = None
    finished_at: float | None = None
    cancel: bool = False


class JobService:
    def __init__(self, root: Path, registry: Registry | None = None, stages: Sequence[Stage] = STAGES,
                 finalize: Callable[[WorkDir], Any] = report.finalize, index: Path | None = None) -> None:
        """`root`: default place for projects created without a folder (and older
        jobs); `index`: the recent-projects list (default `<root>/projects.json`)."""
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index = project.ProjectIndex(index or self.root / "projects.json")
        self.monitor = LoadMonitor()
        self.registry = registry or Registry.load()
        self.stages = stages
        self.finalize = finalize
        self.hub = EventHub()
        self._states: dict[str, JobState] = {}
        self._queue: queue.Queue[tuple[str, RunRequest] | None] = queue.Queue()
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._pulls: dict[str, threading.Thread] = {}

    # -- lifecycle -------------------------------------------------------------
    def start(self) -> None:
        if self._worker is None:
            self._worker = threading.Thread(target=self._work, name="bapu-worker", daemon=True)
            self._worker.start()

    def stop(self) -> None:
        if self._worker is not None:
            self._queue.put(None)
            self._worker.join(timeout=5)
            self._worker = None

    # -- projects ------------------------------------------------------------------
    # A job is a project folder: anywhere the user chose (found through the
    # index) or, for older jobs and tests, a folder directly under `root`.
    def _folder(self, job_id: str) -> Path | None:
        if not JOB_ID.match(job_id):
            return None
        folder = self.index.get(job_id) or self.root / job_id
        return folder if folder.is_dir() and project.is_project(folder) else None

    def workdir(self, job_id: str) -> WorkDir:
        """The project's data folder (`<folder>/.bapu`); older layouts are moved
        there first, unless the project is being processed right now."""
        folder = self._folder(job_id)
        if folder is None:
            raise NotFound(f"没有工程 {job_id}")
        if self._state(job_id).state not in ("queued", "running"):
            try:
                project.migrate(folder)
            except (OSError, project.ProjectError, ValueError) as e:
                raise ServiceError(f"整理工程文件夹失败：{e}") from None
        return WorkDir(project.data_dir(folder))

    def _meta(self, wd: WorkDir) -> dict[str, Any]:
        try:
            return project.read(project.folder_of(wd.root))
        except (project.ProjectError, ValueError) as e:
            raise ServiceError(str(e)) from None

    def _state(self, job_id: str) -> JobState:
        with self._lock:
            return self._states.setdefault(job_id, JobState())

    def _folder_id(self, folder: Path) -> str:
        """Id of a project in a folder the user chose: from its path, not its
        name (names often come from file names that contain lyric text, and ids
        appear in URLs)."""
        import hashlib

        base = "p" + hashlib.sha1(str(Path(folder).resolve()).lower().encode("utf-8")).hexdigest()[:8]
        return self._new_id(base)

    def _new_id(self, name: str) -> str:
        base = jobs.slug(name)
        taken = {e["id"] for e in self.index.items()}
        job_id, k = base, 2
        while job_id in taken or (self.root / job_id).exists():
            job_id, k = f"{base}-{k}", k + 1
        return job_id

    def create(self, name: str, audio: Path, lyrics: Path | None = None, folder: Path | None = None) -> str:
        """New project. `folder` is where the user wants it (default: a new
        folder under `root`); the inputs are copied into `<folder>/inputs/`."""
        audio = Path(audio)
        if not audio.is_file():
            raise ServiceError(f"找不到音频：{audio}")
        if lyrics is not None and not Path(lyrics).is_file():
            raise ServiceError(f"找不到歌词文件：{lyrics}")
        name = name or audio.stem
        job_id = self._folder_id(Path(folder)) if folder else self._new_id(name)
        folder = Path(folder) if folder else self.root / job_id
        if not folder.is_absolute():
            raise ServiceError(f"工程文件夹要写完整路径：{folder}")
        if folder.exists() and project.is_project(folder):
            f = project.project_file(folder)
            raise ServiceError(f"这个文件夹里已经有工程了{f'（{f.name}）' if f else ''}：换一个文件夹，或者直接打开它")
        try:
            folder.mkdir(parents=True, exist_ok=True)
            project.make_data_dir(folder)
            sources = {"source": project.copy_input(folder, audio)}
            if lyrics is not None:
                sources["lyrics"] = project.copy_input(folder, Path(lyrics))
            project.write(folder, {"name": name, "created_at": time.time(), "sources": sources})
        except OSError as e:
            raise ServiceError(f"写不进工程文件夹：{e}") from None
        self.index.add(job_id, folder)
        return job_id

    def open_project(self, path: Path) -> str:
        """Register a project (its `.bapu` file or its folder) and return its id."""
        path = Path(path)
        folder = path.parent if path.suffix.lower() == project.EXT else path
        if not folder.is_dir() or not project.is_project(folder):
            raise ServiceError(f"这里没有 Bapu 工程：{path}")
        if (known := self.index.find(folder)) is not None:
            self.index.touch(known)
            return known
        for d in self.root.iterdir() if self.root.exists() else []:
            if d.is_dir() and d.resolve() == folder.resolve():  # an older job under root
                project.ensure(folder)
                self.index.add(d.name, folder)
                return d.name
        job_id = self._folder_id(folder)
        project.ensure(folder)
        self.index.add(job_id, folder)
        return job_id

    def save_upload(self, job_id: str, kind: str, filename: str, data: bytes) -> Path:
        """Store an uploaded input inside the project (`inputs/`) and point the project at it."""
        wd = self.workdir(job_id)
        safe = Path(filename).name or kind
        dest = wd.root / "inputs" / safe
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return self._set_source(wd, kind, dest)

    def set_input(self, job_id: str, kind: str, path: Path) -> Path:
        """Copy a local file into the project and use it as its audio / lyrics."""
        wd = self.workdir(job_id)
        if not Path(path).is_file():
            raise ServiceError(f"找不到文件：{path}")
        return self._set_source(wd, kind, project.copy_input(project.folder_of(wd.root), Path(path)))

    def _set_source(self, wd: WorkDir, kind: str, dest: Path) -> Path:
        meta = self._meta(wd)
        meta.setdefault("sources", {})["source" if kind == "audio" else "lyrics"] = str(dest.resolve())
        project.write(project.folder_of(wd.root), meta)
        return dest

    def sources(self, wd: WorkDir) -> dict[str, Path]:
        """Inputs for the next run: the project file, falling back to the last run's."""
        out: dict[str, Path] = {}
        for name, p in (self._meta(wd).get("sources") or {}).items():
            if Path(p).exists():
                out[name] = Path(p)
        for name in ("source", "lyrics"):
            if name not in out and (p := jobs.stored_source(wd, name)) is not None:
                out[name] = p
        return out

    def list(self) -> list[dict[str, Any]]:
        """Projects opened here (most recent first), then older jobs under `root`."""
        out, seen = [], set()
        for e in self.index.items():
            if self._folder(e["id"]) is not None:
                out.append(self.summary(e["id"]))
                seen.add(str(Path(e["path"]).resolve()).lower())
        if self.root.exists():
            for d in sorted(self.root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                if (d.is_dir() and JOB_ID.match(d.name) and project.is_project(d)
                        and str(d.resolve()).lower() not in seen and self.index.get(d.name) is None):
                    out.append(self.summary(d.name))
        return out

    def summary(self, job_id: str) -> dict[str, Any]:
        wd = self.workdir(job_id)
        st = self._state(job_id)
        meta = self._meta(wd)
        review = wd.root / "review.json"
        attention = None
        if review.exists():
            attention = json.loads(review.read_text(encoding="utf-8"))["summary"]["attention"]
        score = wd.root / "score.json"
        exports = self.exports(wd)
        exported_at = max((f["mtime"] for f in exports), default=None)
        folder = project.folder_of(wd.root)
        pfile = project.project_file(folder)
        return {"id": job_id, "name": meta.get("name", job_id), "folder": str(folder.resolve()),
                "project_file": pfile.name if pfile else None, "state": st.state, "stage": st.stage,
                "fraction": st.fraction, "error": st.error, "has_score": score.exists(),
                "attention": attention, "has_lyrics": "lyrics" in self.sources(wd),
                "exported_at": exported_at,
                "export_stale": bool(exported_at and score.exists() and score.stat().st_mtime > exported_at)}

    @staticmethod
    def exports(wd: WorkDir) -> list[dict[str, Any]]:
        """Files written by exports (next to the .bapu file), newest first."""
        files = project.exports(project.folder_of(wd.root), wd.root)
        return [{"name": f.name, "size": f.stat().st_size, "mtime": f.stat().st_mtime} for f in files]

    def detail(self, job_id: str) -> dict[str, Any]:
        wd = self.workdir(job_id)
        rows = pipeline.status(wd, self.registry, self.stages) if wd.manifest_path.exists() else []
        return {**self.summary(job_id), "stages": rows, "params": wd.load_params(),
                "sources": {k: Path(v).name for k, v in self.sources(wd).items()}, "exports": self.exports(wd)}

    # -- running -----------------------------------------------------------------
    def enqueue(self, job_id: str, req: RunRequest) -> None:
        wd = self.workdir(job_id)
        st = self._state(job_id)
        if st.state in ("queued", "running"):
            raise Conflict("任务已经在排队或运行中")
        sources = self.sources(wd)
        if "source" not in sources:
            raise ServiceError("这个任务没有音频")
        overrides = jobs.merge(wd.load_params(), req.overrides)
        try:
            missing = jobs.missing_models(sources, overrides, self.registry, req.until, self.stages)
        except pipeline.PipelineError as e:
            raise ServiceError(str(e)) from None
        if missing:
            raise Conflict("缺少模型，先下载", [m.id for m in missing])
        wd.save_params(overrides)
        with self._lock:
            st.state, st.error, st.cancel = "queued", None, False
            st.stage, st.fraction, st.queued_at = None, None, time.time()
        self.hub.publish(f"job:{job_id}", {"type": "state", "state": "queued"})
        self._queue.put((job_id, req))

    def cancel(self, job_id: str) -> None:
        self.workdir(job_id)
        st = self._state(job_id)
        with self._lock:
            if st.state == "queued":
                st.state = "cancelled"
            elif st.state == "running":
                st.cancel = True  # takes effect at the next progress report
            else:
                raise Conflict("任务没有在运行")
        self.hub.publish(f"job:{job_id}", {"type": "state", "state": st.state, "cancel_requested": st.cancel})

    def _work(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            job_id, req = item
            st = self._state(job_id)
            if st.state != "queued":
                continue  # cancelled while waiting
            self._run(job_id, req, st)

    def _run(self, job_id: str, req: RunRequest, st: JobState) -> None:
        topic = f"job:{job_id}"
        with self._lock:
            st.state, st.started_at = "running", time.time()
        self.hub.publish(topic, {"type": "state", "state": "running"})

        def progress(e: ProgressEvent) -> None:
            with self._lock:
                st.stage, st.fraction = e.stage, e.fraction
                cancel = st.cancel
            self.hub.publish(topic, {"type": "progress", "stage": e.stage, "kind": e.kind,
                                     "fraction": e.fraction, "message": e.message})
            if cancel:
                raise Cancelled()

        try:
            wd = self.workdir(job_id)
            pipeline.run(wd, self.sources(wd), registry=self.registry, overrides=wd.load_params(),
                         from_stage=req.from_stage, until=req.until, progress=progress, stages=self.stages)
            self.finalize(wd)
            final, error = "done", None
        except Cancelled:
            final, error = "cancelled", None
        except Exception as e:  # noqa: BLE001 - reported to the client
            final, error = "failed", f"{type(e).__name__}: {e}"
        with self._lock:
            st.state, st.error, st.finished_at, st.cancel = final, error, time.time(), False
        self.hub.publish(topic, {"type": "state", "state": final, "error": error})

    # -- corrections -----------------------------------------------------------------
    def corrections(self, job_id: str) -> dict[str, Any]:
        wd = self.workdir(job_id)
        text = wd.corrections_path.read_text(encoding="utf-8") if wd.corrections_path.exists() else ""
        return {"text": text, "version": _version(text)}

    def save_corrections(self, job_id: str, text: str, version: str) -> dict[str, Any]:
        """Replace the file only if nobody changed it since `version` was read."""
        wd = self.workdir(job_id)
        current = self.corrections(job_id)
        if version != current["version"]:
            raise Conflict("修正文件在别处被改过，请重新读取", current["version"])
        tmp = wd.root / "corrections.yaml.check"
        tmp.write_text(text, encoding="utf-8")
        try:
            corr.load(tmp)
        except corr.CorrectionsError as e:
            raise ServiceError(str(e)) from None
        finally:
            tmp.unlink(missing_ok=True)
        wd.corrections_path.write_text(text, encoding="utf-8")
        return {"text": text, "version": _version(text)}

    # -- models ----------------------------------------------------------------------
    def models(self) -> list[dict[str, Any]]:
        out = []
        for spec in self.registry.models.values():
            out.append({
                "id": spec.id, "stage": spec.stage, "name": spec.name, "backend": spec.backend,
                "verified": spec.verified, "size": spec.total_size, "status": self.registry.status(spec),
                "license": vars(spec.license), "license_accepted": self.registry.license_accepted(spec),
                "downloading": spec.id in self._pulls and self._pulls[spec.id].is_alive(),
                "download": self.registry.download_info(spec),
            })
        return out

    def pull(self, model_id: str, accept_license: bool) -> None:
        try:
            spec = self.registry.get(model_id)
        except RegistryError as e:
            raise NotFound(str(e)) from None
        if not (accept_license or self.registry.license_accepted(spec)):
            raise Conflict("下载前需要接受许可证", vars(spec.license))
        if model_id in self._pulls and self._pulls[model_id].is_alive():
            raise Conflict("正在下载")
        t = threading.Thread(target=self._pull, args=(spec,), name=f"pull-{model_id}", daemon=True)
        self._pulls[model_id] = t
        t.start()

    def _pull(self, spec: ModelSpec) -> None:
        topic = f"model:{spec.id}"
        last = [0.0]

        def progress(name: str, done: int, total: int | None) -> None:
            now = time.time()
            if now - last[0] >= 0.5 or (total and done >= total):
                last[0] = now
                self.hub.publish(topic, {"type": "progress", "file": name, "done": done, "total": total})

        self.hub.publish(topic, {"type": "state", "state": "downloading"})
        try:
            self.registry.pull(spec, accept_license=True, progress=progress)
            self.hub.publish(topic, {"type": "state", "state": "ready"})
        except Exception as e:  # noqa: BLE001
            self.hub.publish(topic, {"type": "state", "state": "failed", "error": str(e)})

    def remove_model(self, model_id: str) -> int:
        try:
            return self.registry.remove(self.registry.get(model_id))
        except RegistryError as e:
            raise NotFound(str(e)) from None

    # -- system ----------------------------------------------------------------------
    def system(self) -> dict[str, Any]:
        info: dict[str, Any] = {"queue": self._queue.qsize(),
                                "running": [k for k, s in self._states.items() if s.state == "running"]}
        try:
            import torch

            if torch.cuda.is_available():
                free, total = torch.cuda.mem_get_info()
                info["gpu"] = {"name": torch.cuda.get_device_name(0), "free": free, "total": total}
        except Exception:  # noqa: BLE001
            pass
        cache = self.registry.cache_root
        info["model_cache_bytes"] = sum(p.stat().st_size for p in cache.rglob("*") if p.is_file()) if cache.exists() else 0
        info["disk_free"] = shutil.disk_usage(self.root).free
        return info


def _version(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
