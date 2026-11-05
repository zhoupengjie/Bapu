"""API tests with fake stages: no GPU, no models, made-up lyrics."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bapu.models.registry import Registry
from bapu.server import create_app
from bapu.service import JobService
from bapu.stages.base import Stage, StageContext, StageResult
from bapu.workdir import WorkDir, write_json_atomic

SCORE = {
    "tempo": {"bpm": 120.0, "time_signature": "4/4", "source": "auto"}, "lang": "yue",
    "notes": [{"start": 0.5, "dur": 0.5, "pitch": 60, "lyric": "天", "lang": "yue", "line": 1},
              {"start": 1.0, "dur": 0.5, "pitch": 62, "lyric": "-", "lang": "yue", "line": 1}],
}


def make_stages(delay: float = 0.0):
    def first(ctx: StageContext) -> StageResult:
        p = ctx.out_dir / "x.txt"
        p.write_text(ctx.inputs["source"].read_text())
        return StageResult({"x": p}, {"n": 1})

    def slow(ctx: StageContext) -> StageResult:
        for k in range(10):
            ctx.progress(f"step {k + 1}", (k + 1) / 10)
            time.sleep(delay)
        p = ctx.out_dir / "y.txt"
        p.write_text(ctx.inputs["x"].read_text() + (ctx.inputs["lyrics"].read_text() if "lyrics" in ctx.inputs else ""))
        return StageResult({"y": p})

    return (
        Stage("first", 1, "1", ("source",), ("x",), {}, first, title="一"),
        Stage("slow", 2, "1", ("x", "lyrics"), ("y",), {"model": "algo"}, slow, model_params={"model": "algo"},
              optional_inputs=("lyrics",), title="二"),
    )


def fake_finalize(wd: WorkDir) -> None:
    write_json_atomic(wd.root / "score.json", SCORE)  # like report.finalize: unchanged content is not rewritten
    (wd.root / "review.json").write_text(json.dumps({"summary": {"attention": 2}}), encoding="utf-8")


def registry_toml(tmp: Path) -> Path:
    blob = tmp / "upstream.bin"
    blob.write_bytes(b"weights" * 100)
    sha = hashlib.sha256(blob.read_bytes()).hexdigest()
    f = tmp / "registry.toml"
    f.write_text(f"""
[[model]]
id = "algo"
stage = "algo"
name = "Algorithm"
backend = "test"
license = {{ name = "MIT", commercial = "yes" }}

[[model]]
id = "net"
stage = "algo"
name = "Network"
backend = "test"
dir = "models/net"
entry = "w.bin"
license = {{ name = "CC BY-NC 4.0", commercial = "no" }}

[[model.files]]
name = "w.bin"
size = {blob.stat().st_size}
sha256 = "{sha}"
urls = ["{blob.as_uri()}"]
""", encoding="utf-8")
    return f


@pytest.fixture
def env(tmp_path: Path):
    def build(delay: float = 0.0):
        reg = Registry.load(registry_toml(tmp_path), cache_root=tmp_path / "cache")
        svc = JobService(tmp_path / "work", registry=reg, stages=make_stages(delay), finalize=fake_finalize)
        audio = tmp_path / "song.wav"
        audio.write_text("fictional-audio")
        return svc, audio
    return build


def wait_state(client: TestClient, job_id: str, states=("done", "failed", "cancelled"), timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        d = client.get(f"/api/jobs/{job_id}").json()
        if d["state"] in states:
            return d
        time.sleep(0.05)
    raise AssertionError(f"job stuck in {d['state']}")


def test_create_run_and_results(env, tmp_path):
    svc, audio = env()
    with TestClient(create_app(svc)) as client:
        r = client.post("/api/jobs", data={"audio_path": str(audio), "name": "Test Song"})
        assert r.status_code == 201, r.text
        job = r.json()
        assert job["id"] == "test-song" and job["state"] == "idle" and job["has_lyrics"] is False
        assert client.get(f"/api/jobs/{job['id']}/score").status_code == 404

        r = client.post(f"/api/jobs/{job['id']}/run", json={"set": {}})
        assert r.status_code == 202, r.text
        d = wait_state(client, job["id"])
        assert d["state"] == "done", d
        assert [s["state"] for s in d["stages"]] == ["fresh", "fresh"]
        assert client.get(f"/api/jobs/{job['id']}/score").json()["notes"][0]["lyric"] == "天"
        assert client.get("/api/jobs").json()[0]["attention"] == 2

        # Events can be replayed after the fact.
        with client.stream("GET", f"/api/jobs/{job['id']}/events") as s:
            states = []
            for line in s.iter_lines():
                if line.startswith("data: "):
                    ev = json.loads(line[6:])
                    if ev["type"] == "state":
                        states.append(ev["state"])
                        if ev["state"] == "done":
                            break
        assert states == ["queued", "running", "done"]


def test_upload_creates_inputs_and_lyrics_are_used(env):
    svc, _ = env()
    with TestClient(create_app(svc)) as client:
        r = client.post("/api/jobs", data={"name": "Up"},
                        files={"audio": ("a.wav", b"audio-bytes"), "lyrics": ("l.txt", "天空".encode())})
        job = r.json()
        assert job["has_lyrics"] and job["sources"] == {"source": "a.wav", "lyrics": "l.txt"}
        client.post(f"/api/jobs/{job['id']}/run", json={})
        assert wait_state(client, job["id"])["state"] == "done"
        out = svc.root / job["id"] / ".bapu" / "02_slow" / "y.txt"  # intermediate files live in .bapu/
        assert out.read_text(encoding="utf-8") == "audio-bytes天空"

        # dropping the lyrics: the last run's copy is not picked up again
        d = client.delete(f"/api/jobs/{job['id']}/lyrics").json()
        assert d["has_lyrics"] is False and [s["state"] for s in d["stages"]] == ["fresh", "stale"]
        client.post(f"/api/jobs/{job['id']}/run", json={})
        assert wait_state(client, job["id"])["state"] == "done"
        assert out.read_text(encoding="utf-8") == "audio-bytes"


def test_conflicts_and_bad_ids(env):
    svc, audio = env(delay=0.05)
    with TestClient(create_app(svc)) as client:
        job = client.post("/api/jobs", data={"audio_path": str(audio)}).json()
        assert client.post(f"/api/jobs/{job['id']}/run", json={}).status_code == 202
        assert client.post(f"/api/jobs/{job['id']}/run", json={}).status_code == 409
        assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 200
        assert wait_state(client, job["id"])["state"] == "cancelled"
        assert client.get("/api/jobs/..%2Fetc").status_code == 404
        assert client.post(f"/api/jobs/{job['id']}/run", json={"set": {"nope": {}}}).status_code == 400


def test_missing_model_blocks_run_until_pulled(env):
    svc, audio = env()
    with TestClient(create_app(svc)) as client:
        job = client.post("/api/jobs", data={"audio_path": str(audio)}).json()
        r = client.post(f"/api/jobs/{job['id']}/run", json={"models": {"slow": {"model": "net"}}})
        assert r.status_code == 409 and r.json()["detail"] == ["net"]
        r = client.post("/api/models/net/pull", json={})
        assert r.status_code == 409  # license not accepted yet
        assert client.post("/api/models/net/pull", json={"accept_license": True}).status_code == 202
        end = time.time() + 5
        while next(m for m in client.get("/api/models").json() if m["id"] == "net")["status"] != "ready":
            assert time.time() < end
            time.sleep(0.05)
        assert client.post(f"/api/jobs/{job['id']}/run", json={"models": {"slow": {"model": "net"}}}).status_code == 202
        assert wait_state(client, job["id"])["state"] == "done"


def test_corrections_round_trip(env):
    svc, audio = env()
    with TestClient(create_app(svc)) as client:
        job = client.post("/api/jobs", data={"audio_path": str(audio)}).json()
        c = client.get(f"/api/jobs/{job['id']}/corrections").json()
        assert c["text"] == ""
        text = "# mine\n- {type: merge, at: 1.0, action: reject}\n"
        r = client.put(f"/api/jobs/{job['id']}/corrections", json={"text": text, "version": c["version"]})
        assert r.status_code == 200
        stale = client.put(f"/api/jobs/{job['id']}/corrections", json={"text": "", "version": c["version"]})
        assert stale.status_code == 409
        bad = client.put(f"/api/jobs/{job['id']}/corrections",
                         json={"text": "- {type: nope}\n", "version": r.json()["version"]})
        assert bad.status_code == 400
        assert client.get(f"/api/jobs/{job['id']}/corrections").json()["text"] == text


def test_export_and_download(env):
    svc, audio = env()
    with TestClient(create_app(svc)) as client:
        job = client.post("/api/jobs", data={"audio_path": str(audio)}).json()
        client.post(f"/api/jobs/{job['id']}/run", json={})
        wait_state(client, job["id"])
        r = client.post(f"/api/jobs/{job['id']}/export", json={"formats": ["mid", "paste", "svp"], "audio": "none"})
        assert r.status_code == 200, r.text
        names = [f["name"] for f in r.json()["files"]]
        assert names == ["song.mid", "song.paste.txt", "song.svp"]
        d = client.get(f"/api/jobs/{job['id']}/export/song.paste.txt")
        assert d.status_code == 200 and d.content.decode("utf-8-sig").strip() == "天 -"
        assert client.get(f"/api/jobs/{job['id']}/export/..%2Fjob.json").status_code == 404
        assert client.post(f"/api/jobs/{job['id']}/export", json={"formats": ["wav"]}).status_code == 400


def test_export_state_survives_a_cached_rerun(env):
    svc, audio = env()
    with TestClient(create_app(svc)) as client:
        job = client.post("/api/jobs", data={"audio_path": str(audio)}).json()
        assert job["exported_at"] is None and job["export_stale"] is False
        client.post(f"/api/jobs/{job['id']}/run", json={})
        wait_state(client, job["id"])
        client.post(f"/api/jobs/{job['id']}/export", json={"formats": ["mid"], "audio": "none"})
        d = client.get(f"/api/jobs/{job['id']}").json()
        assert d["exported_at"] and not d["export_stale"]
        assert [f["name"] for f in d["exports"]] == ["song.mid"]
        # an all-cached rerun rewrites nothing, so the export stays current
        client.post(f"/api/jobs/{job['id']}/run", json={})
        wait_state(client, job["id"])
        assert client.get(f"/api/jobs/{job['id']}").json()["export_stale"] is False


def test_audio_supports_range(env):
    svc, audio = env()
    with TestClient(create_app(svc)) as client:
        job = client.post("/api/jobs", data={"audio_path": str(audio)}).json()
        r = client.get(f"/api/jobs/{job['id']}/audio/source", headers={"Range": "bytes=0-9"})
        assert r.status_code == 206 and r.content == b"fictional-"
        assert client.get(f"/api/jobs/{job['id']}/audio/lead_dry").status_code == 404


def test_models_carry_manual_download_info(env):
    svc, _ = env()
    with TestClient(create_app(svc)) as client:
        net = next(m for m in client.get("/api/models").json() if m["id"] == "net")
        d = net["download"]
        assert d["dir"].endswith(str(Path("models") / "net"))
        assert [f["name"] for f in d["files"]] == ["w.bin"]
        f = d["files"][0]
        assert f["urls"][0].startswith("file:") and len(f["sha256"]) == 64 and f["present"] is False
        assert f["path"] == str(Path(d["dir"]) / "w.bin")
        # A file placed by hand at that path is picked up by the next listing.
        Path(d["dir"]).mkdir(parents=True)
        (svc.root.parent / "upstream.bin").replace(f["path"])
        net = next(m for m in client.get("/api/models").json() if m["id"] == "net")
        assert net["status"] == "ready" and net["download"]["files"][0]["present"]
        algo = next(m for m in client.get("/api/models").json() if m["id"] == "algo")
        assert algo["download"]["dir"] is None


def test_load_reports_cpu_ram_and_gpu_when_there_is_one(env):
    svc, _ = env()
    with TestClient(create_app(svc)) as client:
        d = client.get("/api/system/load").json()
        assert 0 <= d["cpu"] <= 100 and d["cpu_count"] >= 1
        assert 0 < d["ram"]["used"] <= d["ram"]["total"]
        if d["gpu"] is not None:  # NVIDIA driver present
            assert 0 <= d["gpu"]["util"] <= 100 and d["gpu"]["mem_used"] <= d["gpu"]["mem_total"]


def test_only_the_desktop_ui_may_call_from_another_origin(env):
    svc, _ = env()
    with TestClient(create_app(svc)) as client:
        ok = client.get("/api/system", headers={"Origin": "http://tauri.localhost"})
        assert ok.headers.get("access-control-allow-origin") == "http://tauri.localhost"
        other = client.get("/api/system", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in other.headers


def test_download_queue_and_thread_setting(env, tmp_path, monkeypatch):
    monkeypatch.setenv("BAPU_DATA_DIR", str(tmp_path / "data"))
    svc, _ = env()
    from bapu.downloads import Downloads

    svc.downloads = Downloads(svc.registry, ["algo", "net"])
    with TestClient(create_app(svc)) as client:
        d = client.get("/api/downloads").json()
        assert [(i["id"], i["status"], i["done"]) for i in d["items"]] == [("algo", "builtin", 0), ("net", "missing", 0)]
        assert d["threads"] == 8
        assert client.put("/api/system/sources", json={"threads": 40}).json()["threads"] == 16
        assert client.put("/api/system/sources", json={"threads": 3}).json()["threads"] == 3
        assert client.post("/api/downloads/start", json={}).status_code == 200
        end = time.time() + 5
        while (d := client.get("/api/downloads").json())["state"] != "idle":
            assert time.time() < end, d
            time.sleep(0.05)
        net = d["items"][1]
        assert net["status"] == "ready" and net["done"] == net["size"] > 0
        assert svc.registry.license_accepted(svc.registry.get("net"))


def test_save_project_and_network_load(env):
    svc, audio = env()
    with TestClient(create_app(svc)) as client:
        job = client.post("/api/jobs", data={"audio_path": str(audio)}).json()
        r = client.post(f"/api/jobs/{job['id']}/save")
        assert r.status_code == 200, r.text
        assert client.get(f"/api/jobs/{job['id']}").json()["project_file"]
        net = client.get("/api/system/load").json()["net"]
        assert net["up"] >= 0 and net["down"] >= 0


def notes_stages():
    """A transcription-like pair: `first` makes notes, `second` reads them."""
    def first(ctx: StageContext) -> StageResult:
        p = ctx.out_dir / "notes.json"
        p.write_text(json.dumps({"notes": [{"start": 0.0, "dur": 1.0, "pitch": 60}]}))
        return StageResult({"notes": p})

    def second(ctx: StageContext) -> StageResult:
        p = ctx.out_dir / "y.txt"
        p.write_text(f"{len(json.loads(ctx.inputs['notes'].read_text())['notes'])}|{ctx.params['threshold']}")
        return StageResult({"y": p})

    return (
        Stage("first", 1, "1", ("source",), ("notes",), {}, first, title="转谱"),
        Stage("second", 2, "1", ("notes",), ("y",), {"threshold": 0.5}, second, title="合并",
              external_defaults={"notes": {"threshold": 0.0}}),
    )


def svp_file(tmp_path: Path, name: str, tracks: list[tuple[str, int]]) -> Path:
    """An .svp with one note track per (name, note count) (no lyrics)."""
    q = 705_600_000
    out = []
    for name_, n in tracks:
        notes = [{"onset": k * q, "duration": q // 2, "pitch": 60 + k, "lyrics": ""} for k in range(n)]
        out.append({"name": name_, "mainGroup": {"notes": notes}, "mainRef": {"blickOffset": 0, "pitchOffset": 0}, "groups": []})
    p = tmp_path / name
    p.write_bytes(json.dumps({"version": 203, "time": {"meter": [], "tempo": [{"position": 0, "bpm": 120.0}]},
                              "library": [], "tracks": out}).encode("utf-8") + b"\x00")
    return p


def test_notes_from_an_sv_project_replace_the_transcription(env, tmp_path):
    svc, audio = env()
    svc.stages = notes_stages()
    with TestClient(create_app(svc)) as client:
        job = client.post("/api/jobs", data={"audio_path": str(audio), "name": "N"}).json()
        client.post(f"/api/jobs/{job['id']}/run", json={})
        d = wait_state(client, job["id"])
        assert [s["state"] for s in d["stages"]] == ["fresh", "fresh"] and d["notes"] is None
        assert (svc.root / job["id"] / ".bapu" / "02_second" / "y.txt").read_text() == "1|0.5"

        # two tracks with notes: the service asks which one
        two = svp_file(tmp_path, "two.svp", [("Bapu vocal", 1), ("extracted", 3)])
        r = client.post(f"/api/jobs/{job['id']}/notes", data={"notes_path": str(two)})
        assert r.status_code == 409, r.text
        assert [(t["index"], t["notes"]) for t in r.json()["detail"]["tracks"]] == [(1, 1), (2, 3)]
        r = client.post(f"/api/jobs/{job['id']}/notes", data={"notes_path": str(two), "track": "2"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["has_notes"] and d["sources"]["notes"] == "two.svp" and d["notes"]["notes"] == 3 and d["notes"]["track"] == 2
        assert [s["state"] for s in d["stages"]] == ["skipped", "stale"]
        assert client.get("/api/jobs").json()[0]["has_notes"] is True

        client.post(f"/api/jobs/{job['id']}/run", json={})
        d = wait_state(client, job["id"])
        assert d["state"] == "done" and [s["state"] for s in d["stages"]] == ["skipped", "fresh"]
        assert (svc.root / job["id"] / ".bapu" / "02_second" / "y.txt").read_text() == "3|0.0"
        assert (svc.root / job["id"] / ".bapu" / "inputs" / "two.notes.json").exists()

        # a single-track file needs no choice; a bad one is a plain error
        one = svp_file(tmp_path, "one.svp", [("extracted", 2)])
        assert client.post(f"/api/jobs/{job['id']}/notes", data={"notes_path": str(one)}).json()["notes"]["notes"] == 2
        assert client.post(f"/api/jobs/{job['id']}/notes", data={"notes_path": str(tmp_path / "missing.svp")}).status_code == 400
        assert client.post(f"/api/jobs/{job['id']}/notes", data={}).status_code == 400

        # back to the automatic transcription: its earlier result is still cached
        d = client.delete(f"/api/jobs/{job['id']}/notes").json()
        assert not d["has_notes"] and d["notes"] is None
        assert [s["state"] for s in d["stages"]] == ["fresh", "stale"]


def small_wav(path: Path) -> Path:
    import wave

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 800)
    return path


def test_project_from_the_dry_lead_with_sv_notes(env, tmp_path):
    """Lyrics mode: no song, the dry lead and an SV project are given. The stage
    that needs the song is blocked, the SV project's tempo stands in, and a typed
    tempo overrides it."""
    svc, _ = env()
    svc.stages = notes_stages()
    lead = small_wav(tmp_path / "lead.wav")
    with TestClient(create_app(svc)) as client:
        assert client.post("/api/jobs", data={"mode": "lyrics", "name": "L"}).status_code == 400  # needs the dry lead
        r = client.post("/api/jobs", data={"mode": "lyrics", "lead_dry_path": str(lead), "name": "L"})
        assert r.status_code == 201, r.text
        job = r.json()
        assert job["mode"] == "lyrics" and job["sources"] == {"lead_dry": "lead.wav"}
        assert [s["state"] for s in job["stages"]] == ["blocked", "blocked"]

        one = svp_file(tmp_path, "one.svp", [("extracted", 2)])
        d = client.post(f"/api/jobs/{job['id']}/inputs/notes", data={"path": str(one)}).json()
        assert d["sources"]["notes"] == "one.svp" and d["tempo"] == {"bpm": 120.0, "time_signature": "4/4", "origin": "notes"}
        assert [s["state"] for s in d["stages"]] == ["skipped", "missing"]  # the notes stand in for `first`

        client.post(f"/api/jobs/{job['id']}/run", json={})
        d = wait_state(client, job["id"])
        assert d["state"] == "done", d
        assert [s["state"] for s in d["stages"]] == ["skipped", "fresh"]
        assert (svc.root / job["id"] / ".bapu" / "02_second" / "y.txt").read_text() == "2|0.0"
        # the dry lead the user gave is what the review page plays
        assert client.get(f"/api/jobs/{job['id']}/audio/lead_dry").status_code == 200

        d = client.put(f"/api/jobs/{job['id']}/tempo", json={"bpm": 90}).json()
        assert d["tempo"]["bpm"] == 90 and d["tempo"]["origin"] == "user"
        assert client.put(f"/api/jobs/{job['id']}/tempo", json={"bpm": 5}).status_code == 400
        d = client.delete(f"/api/jobs/{job['id']}/tempo").json()
        assert d["tempo"] is None
        # without the notes, the stage that would make them needs the song: the run says so
        assert [s["state"] for s in client.delete(f"/api/jobs/{job['id']}/inputs/notes").json()["stages"]] == ["blocked", "blocked"]
        r = client.post(f"/api/jobs/{job['id']}/run", json={"until": "first"})
        assert r.status_code == 202
        d = wait_state(client, job["id"])
        assert d["state"] == "failed" and "原曲" in d["error"]


def test_notes_mode_starts_from_the_dry_lead(env, tmp_path):
    svc, _ = env()
    lead = small_wav(tmp_path / "lead.wav")
    with TestClient(create_app(svc)) as client:
        job = client.post("/api/jobs", data={"mode": "notes", "lead_dry_path": str(lead), "folder": str(tmp_path / "p")}).json()
        assert job["mode"] == "notes" and job["sources"] == {"lead_dry": "lead.wav"}
        assert (tmp_path / "p" / ".bapu" / "inputs" / "lead.wav").exists()
        assert client.get("/api/jobs").json()[0]["mode"] == "notes"
