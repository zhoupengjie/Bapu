"""Project folders with a .bapu file: create anywhere, open, move, older folders."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bapu import project
from bapu.server import create_app, dialogs
from tests.test_server import env, wait_state  # noqa: F401 - fixture


def test_write_read_keeps_inputs_relative(tmp_path):
    folder = tmp_path / "My Song - Bapu"
    song = tmp_path / "My Song.wav"
    song.write_text("audio")
    copied = project.copy_input(folder, song)
    assert copied == (folder / ".bapu" / "inputs" / "My Song.wav").resolve() and song.exists()
    f = project.write(folder, {"name": "My Song", "sources": {"source": copied}})
    assert f.name == "My Song.bapu"
    raw = json.loads(f.read_text(encoding="utf-8"))
    assert raw["format"] == "bapu-project" and raw["sources"] == {"source": ".bapu/inputs/My Song.wav"}
    # the folder can move: sources follow it
    moved = shutil.move(str(folder), str(tmp_path / "elsewhere"))
    assert project.read(Path(moved))["sources"]["source"] == str((Path(moved) / ".bapu" / "inputs" / "My Song.wav").resolve())


def test_older_job_folder_gets_a_project_file(tmp_path):
    folder = tmp_path / "old"
    folder.mkdir()
    (folder / "job.json").write_text(json.dumps({"id": "old", "name": "Old Song", "sources": {"source": str(tmp_path / "a.wav")}}))
    f = project.ensure(folder)
    assert f.name == "Old Song.bapu" and not (folder / "job.json").exists()
    assert project.read(folder)["sources"]["source"] == str((tmp_path / "a.wav").resolve())


def test_newer_or_foreign_project_file_is_refused(tmp_path):
    (tmp_path / "x.bapu").write_text(json.dumps({"format": "something-else"}))
    with pytest.raises(project.ProjectError):
        project.read(tmp_path)
    (tmp_path / "x.bapu").write_text(json.dumps({"format": "bapu-project", "version": 99}))
    with pytest.raises(project.ProjectError, match="升级"):
        project.read(tmp_path)


def test_create_in_chosen_folder_then_open_from_a_fresh_service(env, tmp_path):
    svc, audio = env()
    folder = tmp_path / "songs" / "song - Bapu"
    with TestClient(create_app(svc)) as client:
        r = client.post("/api/jobs", data={"audio_path": str(audio), "folder": str(folder)})
        assert r.status_code == 201, r.text
        job = r.json()
        assert job["folder"] == str(folder.resolve()) and job["project_file"] == "song.bapu"
        assert (folder / ".bapu" / "inputs" / "song.wav").exists()
        assert sorted(x.name for x in folder.iterdir()) == [".bapu", "song.bapu"]  # nothing else outside
        # one project per folder
        again = client.post("/api/jobs", data={"audio_path": str(audio), "folder": str(folder)})
        assert again.status_code == 409 and "已经有工程" in again.json()["error"]
        assert again.json()["detail"] == {"folder": str(folder.resolve()), "project_file": "song.bapu"}
        # a relative folder is refused
        assert client.post("/api/jobs", data={"audio_path": str(audio), "folder": "rel/x"}).status_code == 400
        # lyrics by path are copied in
        lyr = tmp_path / "ref.lrc"
        lyr.write_text("[00:01.00]x", encoding="utf-8")
        assert client.post(f"/api/jobs/{job['id']}/lyrics", data={"lyrics_path": str(lyr)}).json()["has_lyrics"]
        assert (folder / ".bapu" / "inputs" / "ref.lrc").exists()
        client.post(f"/api/jobs/{job['id']}/run", json={})
        assert wait_state(client, job["id"])["state"] == "done"

    # another machine / a reinstall: an empty index, open by the .bapu file
    svc2, _ = env()
    svc2.index = project.ProjectIndex(tmp_path / "other-index.json")
    with TestClient(create_app(svc2)) as client:
        d = client.post("/api/projects/open", json={"path": str(folder / "song.bapu")}).json()
        assert d["has_score"] and d["has_lyrics"] and d["folder"] == str(folder.resolve())
        assert client.post("/api/projects/open", json={"path": str(folder)}).json()["id"] == d["id"]
        assert [j["id"] for j in client.get("/api/jobs").json()][0] == d["id"]
        bad = client.post("/api/projects/open", json={"path": str(tmp_path / "nothing-here")})
        assert bad.status_code == 400


def test_dialog_endpoint_suggests_a_folder_next_to_the_song(env, monkeypatch):
    svc, _ = env()
    picked = r"D:\Songs\My Song.mp4"
    monkeypatch.setattr(dialogs, "ask", lambda kind, initial=None: picked if kind == "audio" else None)
    with TestClient(create_app(svc)) as client:
        r = client.post("/api/dialog", json={"kind": "audio"}).json()
        assert r["path"] == picked and r["folder"] == str(Path(r"D:\Songs") / "My Song - Bapu")
        assert client.post("/api/dialog", json={"kind": "folder"}).json() == {"path": None}


def test_old_layout_moves_into_dot_bapu_and_keeps_the_cache(tmp_path):
    folder = tmp_path / "song"
    (folder / "inputs").mkdir(parents=True)
    (folder / "inputs" / "a.wav").write_text("audio")
    (folder / "01_separate").mkdir()
    (folder / "01_separate" / "vocals.wav").write_text("v")
    (folder / "manifest.json").write_text(json.dumps({"version": 2, "stages": {"separate": {
        "key": "k", "dir": "01_separate", "outputs": {"vocals": "01_separate/vocals.wav"}}}}))
    (folder / "corrections.yaml").write_text("[]\n")
    (folder / "export").mkdir()
    (folder / "export" / "song.mid").write_text("midi")
    (folder / "My Notes.txt").write_text("the user's own file")
    project.write(folder, {"name": "Song", "sources": {"source": folder / "inputs" / "a.wav"}})
    # a v1 project file
    f = project.project_file(folder)
    raw = json.loads(f.read_text(encoding="utf-8"))
    raw["version"] = 1
    f.write_text(json.dumps(raw), encoding="utf-8")

    assert project.migrate(folder)
    assert sorted(x.name for x in folder.iterdir()) == [".bapu", "My Notes.txt", "Song.bapu", "song.mid"]
    data = folder / ".bapu"
    assert (data / "01_separate" / "vocals.wav").exists() and (data / "manifest.json").exists()
    assert project.read(folder)["sources"]["source"] == str((data / "inputs" / "a.wav").resolve())
    assert [p.name for p in project.exports(folder, data)] == ["song.mid"]
    assert json.loads(project.project_file(folder).read_text(encoding="utf-8"))["version"] == project.VERSION
    assert project.data_dir(folder) == data
    assert not project.migrate(folder)  # nothing left to move


def test_export_never_overwrites_a_file_bapu_did_not_write(tmp_path):
    folder = tmp_path / "p"
    data = project.make_data_dir(folder)
    mine = project.export_target(folder, data, "Song.svp")
    assert mine == folder / "Song.svp"
    mine.write_text("v1")
    project.record_export(data, mine)
    assert project.export_target(folder, data, "Song.svp") == mine  # ours and unchanged: overwrite
    mine.write_text("edited in SV and saved")  # the user changed it
    assert project.export_target(folder, data, "Song.svp") == folder / "Song (2).svp"
    (folder / "Song.mid").write_text("someone else's")
    assert project.export_target(folder, data, "Song.mid") == folder / "Song (2).mid"


def test_new_project_where_the_bapu_file_was_removed(env, tmp_path):
    """The user moved the .bapu file away: creating a project there again is allowed.
    Same audio keeps the finished steps; other audio sets the old results aside."""
    svc, audio = env()
    folder = tmp_path / "song folder"
    with TestClient(create_app(svc)) as client:
        job = client.post("/api/jobs", data={"audio_path": str(audio), "folder": str(folder)}).json()
        client.post(f"/api/jobs/{job['id']}/run", json={})
        assert wait_state(client, job["id"])["state"] == "done"
        project.project_file(folder).unlink()  # moved away by the user

        again = client.post("/api/jobs", data={"audio_path": str(audio), "folder": str(folder)})
        assert again.status_code == 201, again.text
        d = again.json()
        assert [s["state"] for s in d["stages"]] == ["fresh", "fresh"]  # nothing to recompute
        assert len([e for e in svc.index.items() if e["path"] == str(folder.resolve())]) == 1

        project.project_file(folder).unlink()
        other = tmp_path / "other.wav"
        other.write_text("another song")
        third = client.post("/api/jobs", data={"audio_path": str(other), "folder": str(folder)})
        assert third.status_code == 201, third.text
        assert third.json()["stages"] == []  # a fresh start: nothing has run yet
        old = [p.name for p in folder.iterdir() if p.name.startswith(".bapu-old-")]
        assert len(old) == 1 and (folder / old[0] / "manifest.json").exists()  # kept, not deleted


def test_data_folder_is_visible(tmp_path):
    import sys

    d = project.make_data_dir(tmp_path / "p")
    if sys.platform == "win32":
        import ctypes

        assert not ctypes.windll.kernel32.GetFileAttributesW(str(d)) & 0x2


def test_a_removed_project_file_is_not_brought_back(env, tmp_path):
    svc, audio = env()
    folder = tmp_path / "f"
    with TestClient(create_app(svc)) as client:
        job = client.post("/api/jobs", data={"audio_path": str(audio), "folder": str(folder)}).json()
        project.project_file(folder).unlink()
        assert client.get(f"/api/jobs/{job['id']}").status_code == 404
        assert project.project_file(folder) is None  # still gone
        assert job["id"] not in [j["id"] for j in client.get("/api/jobs").json()]
        # opening the folder on purpose restores it
        assert client.post("/api/projects/open", json={"path": str(folder)}).status_code == 200
        assert project.project_file(folder) is not None
