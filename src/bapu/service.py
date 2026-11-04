"""Job service behind the API: a queue, one worker thread (one GPU), and an
event hub that fans progress out to listeners.

A job is one work directory under `root`; its id is the directory name. Jobs
survive restarts because everything lives on disk (`job.json`, `manifest.json`,
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
from bapu import jobs, pipeline, report
from bapu.models.registry import ModelSpec, Registry, RegistryError
from bapu.progress import ProgressEvent
from bapu.stages import STAGES
from bapu.stages.base import Stage
from bapu.workdir import WorkDir, write_json_atomic

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
                 finalize: Callable[[WorkDir], Any] = report.finalize) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
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

    # -- job directory -----------------------------------------------------------
    def workdir(self, job_id: str) -> WorkDir:
        if not JOB_ID.match(job_id):
            raise NotFound(f"没有任务 {job_id}")
        wd = WorkDir(self.root / job_id)
        if not (wd.root / "job.json").exists() and not wd.manifest_path.exists():
            raise NotFound(f"没有任务 {job_id}")
        return wd

    def _meta(self, wd: WorkDir) -> dict[str, Any]:
        p = wd.root / "job.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"id": wd.root.name, "name": wd.root.name}

    def _state(self, job_id: str) -> JobState:
        with self._lock:
            return self._states.setdefault(job_id, JobState())

    def create(self, name: str, audio: Path, lyrics: Path | None = None) -> str:
        """New job from local files; uploads are saved into the job first."""
        audio = Path(audio)
        if not audio.is_file():
            raise ServiceError(f"找不到音频：{audio}")
        if lyrics is not None and not Path(lyrics).is_file():
            raise ServiceError(f"找不到歌词文件：{lyrics}")
        base = jobs.slug(name or audio.stem)
        job_id, k = base, 2
        while (self.root / job_id).exists():
            job_id, k = f"{base}-{k}", k + 1
        wd = WorkDir(self.root / job_id)
        wd.root.mkdir(parents=True)
        sources = {"source": str(audio.resolve())}
        if lyrics is not None:
            sources["lyrics"] = str(Path(lyrics).resolve())
        write_json_atomic(wd.root / "job.json", {"id": job_id, "name": name or audio.stem,
                                                 "created_at": time.time(), "sources": sources})
        return job_id

    def save_upload(self, job_id: str, kind: str, filename: str, data: bytes) -> Path:
        """Store an uploaded input inside the job (`inputs/`) and point the job at it."""
        wd = self.workdir(job_id)
        safe = Path(filename).name or kind
        dest = wd.root / "inputs" / safe
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        meta = self._meta(wd)
        meta.setdefault("sources", {})["source" if kind == "audio" else "lyrics"] = str(dest.resolve())
        write_json_atomic(wd.root / "job.json", meta)
        return dest

    def sources(self, wd: WorkDir) -> dict[str, Path]:
        """Inputs for the next run: job.json, falling back to the last run's."""
        out: dict[str, Path] = {}
        for name, p in (self._meta(wd).get("sources") or {}).items():
            if Path(p).exists():
                out[name] = Path(p)
        for name in ("source", "lyrics"):
            if name not in out and (p := jobs.stored_source(wd, name)) is not None:
                out[name] = p
        return out

    def list(self) -> list[dict[str, Any]]:
        out = []
        for d in sorted(self.root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if d.is_dir() and JOB_ID.match(d.name) and ((d / "job.json").exists() or (d / "manifest.json").exists()):
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
        return {"id": job_id, "name": meta.get("name", job_id), "state": st.state, "stage": st.stage,
                "fraction": st.fraction, "error": st.error, "has_score": (wd.root / "score.json").exists(),
                "attention": attention, "has_lyrics": "lyrics" in self.sources(wd)}

    def detail(self, job_id: str) -> dict[str, Any]:
        wd = self.workdir(job_id)
        rows = pipeline.status(wd, self.registry, self.stages) if wd.manifest_path.exists() else []
        return {**self.summary(job_id), "stages": rows, "params": wd.load_params(),
                "sources": {k: Path(v).name for k, v in self.sources(wd).items()}}

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
            raise Conflict("缺少模型，先在模型页下载", [m.id for m in missing])
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
