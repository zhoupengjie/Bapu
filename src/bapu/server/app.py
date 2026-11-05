"""HTTP API over the job service (FastAPI). Meant to listen on 127.0.0.1 only.

Long work runs in the service's worker thread; clients follow it through
Server-Sent Events (`/events`). Responses that carry lyric text (score,
corrections) are for the local user's front end; nothing here logs them.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from bapu import __version__, compare, jobs, pipeline, project, sources
from bapu import corrections as corr_mod
from bapu.server import dialogs
from bapu.service import JobService, RunRequest, ServiceError
from bapu.stages.separate import DECODED

DESKTOP_ORIGINS = ("http://tauri.localhost", "https://tauri.localhost", "tauri://localhost")

AUDIO_STEMS = {
    "vocals": ("separate", "vocals"), "instrumental": ("separate", "instrumental"),
    "lead": ("lead", "lead"), "backing": ("lead", "backing"), "lead_dry": ("lead", "lead_dry"),
}


class RunBody(BaseModel):
    from_stage: str | None = Field(None, alias="from")
    until: str | None = None
    set: dict[str, dict[str, Any]] = {}
    models: dict[str, dict[str, str]] = {}  # stage -> {param: model id}


class CorrectionsBody(BaseModel):
    text: str
    version: str


class ExportBody(BaseModel):
    formats: list[str] = list(jobs.EXPORT_FORMATS)
    audio: str = "accompaniment,lead_dry"
    grid: int = 0  # 8 / 16 / 32: line up with SV's grid and snap the notes; 0: as transcribed


class PullBody(BaseModel):
    accept_license: bool = False


class SourceBody(BaseModel):
    choice: str | None = None  # auto | official | china
    threads: int | None = None  # parallel connections per download, 1..16


class DownloadBody(BaseModel):
    ids: list[str] | None = None  # default: every model the pipeline needs that is missing


class AssociationBody(BaseModel):
    on: bool


class OpenBody(BaseModel):
    path: str  # a .bapu file or a project folder


class DialogBody(BaseModel):
    kind: str  # audio | lyrics | project | folder
    initial: str | None = None


def create_app(service: JobService, static_dir: Path | None = None) -> FastAPI:
    """`static_dir`: a built front end (web/dist) served at `/`."""
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        service.start()
        yield
        service.stop()

    app = FastAPI(title="Bapu", version=__version__, lifespan=lifespan)
    # The desktop window shows the UI built into Bapu.exe (origin tauri.localhost) and calls this
    # service on 127.0.0.1; no other site may.
    app.add_middleware(CORSMiddleware, allow_origins=list(DESKTOP_ORIGINS), allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["Content-Range", "Content-Length", "Content-Disposition", "Accept-Ranges"])

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError) -> JSONResponse:
        body: dict[str, Any] = {"error": str(exc)}
        if getattr(exc, "detail", None) is not None:
            body["detail"] = exc.detail
        return JSONResponse(body, status_code=exc.status)

    # -- system and models -------------------------------------------------------------
    @app.get("/api/system")
    def system() -> dict[str, Any]:
        return service.system()

    # -- download source and updates ----------------------------------------------------------
    @app.get("/api/system/sources")
    def get_sources() -> dict[str, Any]:
        return sources.describe()

    @app.put("/api/system/sources")
    def set_sources(body: SourceBody) -> dict[str, Any]:
        if body.choice is not None:
            if body.choice not in sources.CHOICES:
                raise ServiceError(f"没有这种下载源：{body.choice}")
            sources.save(source=body.choice)
        if body.threads is not None:
            sources.save(threads=max(1, min(sources.MAX_THREADS, body.threads)))
        return sources.describe()

    @app.post("/api/system/sources/measure")
    def measure_sources() -> dict[str, Any]:
        """Time the official servers and the China mirrors from this machine."""
        return {**sources.measure(), **sources.describe()}

    @app.get("/api/update")
    def update_status() -> dict[str, Any]:
        return service.updater.status()

    @app.post("/api/update/check")
    def update_check() -> dict[str, Any]:
        return service.updater.check()

    @app.post("/api/update/download", status_code=202)
    def update_download() -> dict[str, Any]:
        try:
            service.updater.download()
        except ValueError as e:
            raise ServiceError(str(e)) from None
        return service.updater.status()

    @app.get("/api/system/environment")
    def environment() -> dict[str, Any]:
        return service.environment()

    @app.post("/api/system/association")
    def association(body: AssociationBody) -> dict[str, Any]:
        """Optional .bapu file association (Windows, current user)."""
        service.set_association(body.on)
        return service.environment()

    @app.get("/api/system/load")
    def load() -> dict[str, Any]:
        """CPU, RAM, GPU and VRAM right now (for the progress page)."""
        return service.monitor.sample()

    @app.get("/api/stages")
    def stages() -> list[dict[str, Any]]:
        return [{"name": s.name, "title": s.title, "index": s.index, "defaults": s.defaults,
                 "model_params": s.model_params, "optional_inputs": list(s.optional_inputs)} for s in service.stages]

    @app.get("/api/models")
    def models() -> list[dict[str, Any]]:
        return service.models()

    @app.post("/api/models/{model_id}/pull", status_code=202)
    def pull(model_id: str, body: PullBody) -> dict[str, str]:
        service.pull(model_id, body.accept_license)
        return {"state": "downloading"}

    # one queue for the 环境 page: the models the pipeline needs, resumable
    @app.get("/api/downloads")
    def downloads() -> dict[str, Any]:
        return service.downloads.status()

    @app.post("/api/downloads/start")
    def downloads_start(body: DownloadBody) -> dict[str, Any]:
        return service.downloads.start(body.ids)

    @app.post("/api/downloads/pause")
    def downloads_pause() -> dict[str, Any]:
        return service.downloads.pause()

    @app.delete("/api/models/{model_id}")
    def remove_model(model_id: str) -> dict[str, int]:
        return {"freed": service.remove_model(model_id)}

    @app.get("/api/models/{model_id}/events")
    async def model_events(model_id: str, request: Request, after: int = 0) -> StreamingResponse:
        return _sse(service, f"model:{model_id}", request, after)

    # -- jobs ------------------------------------------------------------------------------
    @app.get("/api/jobs")
    def list_jobs() -> list[dict[str, Any]]:
        return service.list()

    @app.post("/api/jobs", status_code=201)
    async def create_job(
        name: str = Form(""),
        audio: UploadFile | None = File(None),
        lyrics: UploadFile | None = File(None),
        audio_path: str = Form(""),
        lyrics_path: str = Form(""),
        folder: str = Form(""),
    ) -> dict[str, Any]:
        """A new project in `folder`. The audio is uploaded or (local use) given
        by path; either way it is copied into the project's inputs/."""
        if audio is None and not audio_path:
            raise ServiceError("需要音频：上传文件或给出 audio_path")
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(audio_path) if audio_path else Path(tmp) / Path(audio.filename or "audio").name
            if audio is not None:
                src.write_bytes(await audio.read())
            job_id = service.create(name or src.stem, src, Path(lyrics_path) if lyrics_path else None,
                                    Path(folder) if folder else None)
        if lyrics is not None:
            service.save_upload(job_id, "lyrics", lyrics.filename or "lyrics.txt", await lyrics.read())
        return service.detail(job_id)

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str) -> dict[str, Any]:
        return service.detail(job_id)

    @app.post("/api/jobs/{job_id}/lyrics")
    async def set_lyrics(job_id: str, lyrics: UploadFile | None = File(None), lyrics_path: str = Form("")) -> dict[str, Any]:
        """Upload the reference lyrics, or (local use) give their path."""
        if lyrics is not None:
            service.save_upload(job_id, "lyrics", lyrics.filename or "lyrics.txt", await lyrics.read())
        elif lyrics_path:
            service.set_input(job_id, "lyrics", Path(lyrics_path))
        else:
            raise ServiceError("需要歌词文件：上传或给出 lyrics_path")
        return service.detail(job_id)

    # -- projects ----------------------------------------------------------------------------
    @app.post("/api/projects/open")
    def open_project(body: OpenBody) -> dict[str, Any]:
        return service.detail(service.open_project(Path(body.path)))

    @app.post("/api/dialog")
    def dialog(body: DialogBody) -> dict[str, Any]:
        """Show a system file / folder dialog on this machine; returns the chosen path."""
        try:
            path = dialogs.ask(body.kind, body.initial)
        except dialogs.DialogError as e:
            raise ServiceError(str(e)) from None
        out: dict[str, Any] = {"path": path}
        if path and body.kind == "audio":
            out["folder"] = str(dialogs.suggest_folder(Path(path)))
        return out

    @app.post("/api/jobs/{job_id}/run", status_code=202)
    def run(job_id: str, body: RunBody) -> dict[str, Any]:
        overrides = jobs.merge(body.set, body.models)
        for stage in overrides:
            try:
                pipeline.find_stage(stage, service.stages)
            except pipeline.PipelineError as e:
                raise ServiceError(str(e)) from None
        service.enqueue(job_id, RunRequest(body.from_stage, body.until, overrides))
        return service.summary(job_id)

    @app.post("/api/jobs/{job_id}/save")
    def save(job_id: str) -> dict[str, Any]:
        return service.save(job_id)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str) -> dict[str, Any]:
        service.cancel(job_id)
        return service.summary(job_id)

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request, after: int = 0) -> StreamingResponse:
        service.workdir(job_id)
        return _sse(service, f"job:{job_id}", request, after)

    def _json_file(job_id: str, name: str) -> Any:
        p = service.workdir(job_id).root / name
        if not p.exists():
            raise HTTPException(404, f"还没有 {name}")
        return json.loads(p.read_text(encoding="utf-8"))

    @app.get("/api/jobs/{job_id}/score")
    def score(job_id: str) -> Any:
        return _json_file(job_id, "score.json")

    @app.get("/api/jobs/{job_id}/review")
    def review(job_id: str) -> Any:
        return _json_file(job_id, "review.json")

    @app.get("/api/jobs/{job_id}/audio/{stem}")
    def audio(job_id: str, stem: str) -> FileResponse:
        wd = service.workdir(job_id)
        if stem == "source":
            p = service.sources(wd).get("source")
            rec = wd.get("separate")
            decoded = wd.root / rec.dir / DECODED if rec else None
            if decoded is not None and decoded.exists():  # an MP4 etc.: play its decoded audio
                p = decoded
        elif stem in AUDIO_STEMS:
            stage, artifact = AUDIO_STEMS[stem]
            rec = wd.get(stage)
            p = (wd.root / rec.outputs[artifact]) if rec and artifact in rec.outputs else None
        else:
            raise HTTPException(404, f"没有音轨 {stem}；可用：source、{'、'.join(AUDIO_STEMS)}")
        if p is None or not Path(p).exists():
            raise HTTPException(404, "这条音轨还没有生成")
        return FileResponse(p)  # supports Range requests for seeking

    # -- corrections ---------------------------------------------------------------------
    @app.get("/api/jobs/{job_id}/corrections")
    def get_corrections(job_id: str) -> dict[str, Any]:
        return service.corrections(job_id)

    @app.put("/api/jobs/{job_id}/corrections")
    def put_corrections(job_id: str, body: CorrectionsBody) -> dict[str, Any]:
        return service.save_corrections(job_id, body.text, body.version)

    @app.post("/api/jobs/{job_id}/corrections/from-svp")
    async def corrections_from_svp(job_id: str, project: UploadFile = File(...), apply: bool = Form(False),
                                   track: int = Form(1)) -> dict[str, Any]:
        wd = service.workdir(job_id)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "project.svp"
            p.write_bytes(await project.read())
            try:
                entries, stats = compare.from_svp(wd, p, track)
            except (FileNotFoundError, KeyError, ValueError) as e:
                raise ServiceError(str(e)) from None
        lines = sorted({e["line"] for e in entries if e["type"] == "line_version"})
        if apply and entries:
            corr_mod.append(wd.corrections_path, entries, comment=f"from-svp {project.filename} (web)")
        return {"stats": stats, "lines": lines, "applied": bool(apply and entries), "entries": len(entries)}

    # -- export ------------------------------------------------------------------------------
    @app.post("/api/jobs/{job_id}/export")
    def export(job_id: str, body: ExportBody) -> dict[str, Any]:
        wd = service.workdir(job_id)
        try:
            written = jobs.export(wd, body.formats, body.audio, body.grid)
        except jobs.JobError as e:
            raise ServiceError(str(e)) from None
        return {"files": [{"format": f, "name": p.name, "size": p.stat().st_size} for f, p in written]}

    @app.get("/api/jobs/{job_id}/export/{name}")
    def download(job_id: str, name: str) -> FileResponse:
        wd = service.workdir(job_id)
        files = {f["name"] for f in service.exports(wd)}  # only what Bapu exported
        p = (project.folder_of(wd.root) / name).resolve()
        if name not in files or not p.is_file():
            raise HTTPException(404, "没有这个文件")
        return FileResponse(p, filename=p.name)

    if static_dir is not None and (static_dir / "index.html").exists():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
    return app


def _sse(service: JobService, topic: str, request: Request, after: int) -> StreamingResponse:
    """Replay events after `after` (or Last-Event-ID), then stream new ones."""
    last = request.headers.get("last-event-id")
    start = int(last) if last and last.isdigit() else after

    async def stream() -> AsyncIterator[str]:
        q = service.hub.subscribe(topic)
        try:
            seen = start
            for ev in service.hub.history(topic, start):
                seen = ev["seq"]
                yield _frame(ev)
            while not await request.is_disconnected():
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if ev["seq"] > seen:
                    seen = ev["seq"]
                    yield _frame(ev)
        finally:
            service.hub.unsubscribe(topic, q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _frame(ev: dict[str, Any]) -> str:
    return f"id: {ev['seq']}\nevent: {ev.get('type', 'message')}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
