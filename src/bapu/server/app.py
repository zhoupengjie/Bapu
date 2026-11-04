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

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from bapu import compare, jobs, pipeline
from bapu import corrections as corr_mod
from bapu.service import JobService, RunRequest, ServiceError

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
    harmony: bool = True


class PullBody(BaseModel):
    accept_license: bool = False


def create_app(service: JobService, static_dir: Path | None = None) -> FastAPI:
    """`static_dir`: a built front end (web/dist) served at `/`."""
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        service.start()
        yield
        service.stop()

    app = FastAPI(title="Bapu", version="0.1", lifespan=lifespan)

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
    ) -> dict[str, Any]:
        """Either upload the files, or (local use) give their paths."""
        if audio is None and not audio_path:
            raise ServiceError("需要音频：上传文件或给出 audio_path")
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(audio_path) if audio_path else Path(tmp) / Path(audio.filename or "audio").name
            if audio is not None:
                src.write_bytes(await audio.read())
            job_id = service.create(name or src.stem, src, Path(lyrics_path) if lyrics_path else None)
            if audio is not None:
                service.save_upload(job_id, "audio", src.name, src.read_bytes())
        if lyrics is not None:
            service.save_upload(job_id, "lyrics", lyrics.filename or "lyrics.txt", await lyrics.read())
        return service.detail(job_id)

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str) -> dict[str, Any]:
        return service.detail(job_id)

    @app.post("/api/jobs/{job_id}/lyrics")
    async def set_lyrics(job_id: str, lyrics: UploadFile = File(...)) -> dict[str, Any]:
        service.save_upload(job_id, "lyrics", lyrics.filename or "lyrics.txt", await lyrics.read())
        return service.detail(job_id)

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
            written = jobs.export(wd, body.formats, body.audio, body.harmony)
        except jobs.JobError as e:
            raise ServiceError(str(e)) from None
        return {"files": [{"format": f, "name": p.name, "size": p.stat().st_size} for f, p in written]}

    @app.get("/api/jobs/{job_id}/export/{name}")
    def download(job_id: str, name: str) -> FileResponse:
        folder = service.workdir(job_id).root / "export"
        p = (folder / name).resolve()
        if p.parent != folder.resolve() or not p.is_file():
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
